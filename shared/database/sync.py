"""
Data Synchronization using Outbox Pattern for Dual Neon PostgreSQL.

This module implements reliable data synchronization between primary and standby
using a transactional outbox table. Changes are captured in the same transaction
as the business data, ensuring no data loss.
"""

import os
import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Generator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from .models import SyncStatus, SyncState
from .manager import get_database_manager

logger = logging.getLogger(__name__)


class OutboxSync:
    """
    Outbox-based synchronization for reliable data replication.

    The outbox pattern ensures:
    - Changes captured in same transaction as business data
    - At-least-once delivery with idempotency
    - Ordered replay on standby
    - Resume capability after interruption
    """

    OUTBOX_TABLE = "sync_outbox"
    CHECKPOINT_TABLE = "sync_checkpoint"

    def __init__(self, manager: Optional["DatabaseManager"] = None) -> None:
        self._manager = manager or get_database_manager()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._sync_interval = int(os.getenv("DATABASE_SYNC_INTERVAL", "60"))
        self._batch_size = int(os.getenv("DATABASE_SYNC_BATCH_SIZE", "100"))

    def initialize_schema(self) -> None:
        """Create outbox and checkpoint tables on both databases."""
        self._create_outbox_table(self._manager._primary_pool, "primary")
        if self._manager._standby_pool:
            self._create_outbox_table(self._manager._standby_pool, "standby")

    def _create_outbox_table(self, pool: Optional[psycopg.pool.ThreadedConnectionPool], db_name: str) -> None:
        """Create outbox and checkpoint tables."""
        if not pool:
            return

        try:
            with pool.connection() as conn:
                with conn.transaction():
                    # Outbox table for capturing changes
                    conn.execute(f"""
                        CREATE TABLE IF NOT EXISTS {self.OUTBOX_TABLE} (
                            id BIGSERIAL PRIMARY KEY,
                            table_name TEXT NOT NULL,
                            operation TEXT NOT NULL CHECK (operation IN ('INSERT', 'UPDATE', 'DELETE')),
                            payload JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            processed_at TIMESTAMPTZ,
                            retry_count INTEGER NOT NULL DEFAULT 0,
                            last_error TEXT
                        )
                    """)

                    # Index for efficient polling
                    conn.execute(f"""
                        CREATE INDEX IF NOT EXISTS idx_{self.OUTBOX_TABLE}_unprocessed
                        ON {self.OUTBOX_TABLE} (created_at)
                        WHERE processed_at IS NULL
                    """)

                    # Checkpoint table for tracking replication progress
                    conn.execute(f"""
                        CREATE TABLE IF NOT EXISTS {self.CHECKPOINT_TABLE} (
                            id INTEGER PRIMARY KEY DEFAULT 1,
                            last_processed_id BIGINT NOT NULL DEFAULT 0,
                            last_processed_at TIMESTAMPTZ,
                            lag_seconds DOUBLE PRECISION,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                    """)

                    # Initialize checkpoint
                    conn.execute(f"""
                        INSERT INTO {self.CHECKPOINT_TABLE} (id, last_processed_id)
                        VALUES (1, 0)
                        ON CONFLICT (id) DO NOTHING
                    """)

            logger.info(f"Outbox schema initialized on {db_name}")
        except Exception as e:
            logger.error(f"Failed to initialize outbox schema on {db_name}: {e}")
            raise

    def record_change(
        self,
        table_name: str,
        operation: str,
        payload: Dict[str, Any],
        conn: Optional[psycopg.Connection] = None
    ) -> None:
        """
        Record a change in the outbox table.

        Must be called within the same transaction as the business data change.

        Args:
            table_name: Name of the table that changed
            operation: INSERT, UPDATE, or DELETE
            payload: Dictionary with the row data (including primary key)
            conn: Existing connection (if called within a transaction)
        """
        if not conn:
            with self._manager.transaction() as tx_conn:
                self._insert_outbox(tx_conn, table_name, operation, payload)
        else:
            self._insert_outbox(conn, table_name, operation, payload)

    def _insert_outbox(
        self,
        conn: psycopg.Connection,
        table_name: str,
        operation: str,
        payload: Dict[str, Any]
    ) -> None:
        """Insert a record into the outbox table."""
        conn.execute(
            f"""
            INSERT INTO {self.OUTBOX_TABLE} (table_name, operation, payload)
            VALUES (%s, %s, %s)
            """,
            (table_name, operation, psycopg.types.json.Json(payload))
        )

    def sync_once(self) -> Dict[str, Any]:
        """
        Perform a single synchronization cycle from active to standby.

        Returns:
            Dict with sync results
        """
        if not self._manager._standby_pool:
            return {"success": False, "error": "No standby database configured"}

        if self._manager.active_role != DatabaseRole.PRIMARY:
            return {"success": False, "error": "Primary not active, cannot sync"}

        self._manager._sync_state.record_sync_start()

        try:
            processed = self._replicate_changes()
            self._update_checkpoint()
            self._calculate_replication_lag()

            self._manager._sync_state.record_sync_success(pending=0)
            return {"success": True, "processed": processed}
        except Exception as e:
            error_msg = str(e)
            self._manager._sync_state.record_sync_failure(error_msg)
            logger.error(f"Sync failed: {error_msg}")
            return {"success": False, "error": error_msg}

    def _replicate_changes(self) -> int:
        """Replicate unprocessed changes from primary to standby."""
        last_processed_id = self._get_last_processed_id()

        with self._manager._primary_pool.connection() as src_conn:
            rows = src_conn.execute(
                f"""
                SELECT id, table_name, operation, payload
                FROM {self.OUTBOX_TABLE}
                WHERE id > %s AND processed_at IS NULL
                ORDER BY id
                LIMIT %s
                """,
                (last_processed_id, self._batch_size)
            ).fetchall()

        if not rows:
            return 0

        processed = 0
        for row in rows:
            try:
                self._apply_change(row)
                self._mark_processed(row["id"])
                processed += 1
            except Exception as e:
                self._record_retry(row["id"], str(e))
                logger.error(f"Failed to replicate change {row['id']}: {e}")
                break  # Stop on first failure to maintain order

        return processed

    def _get_last_processed_id(self) -> int:
        """Get the last processed outbox ID from checkpoint."""
        if not self._manager._standby_pool:
            return 0

        with self._manager._standby_pool.connection() as conn:
            row = conn.execute(
                f"SELECT last_processed_id FROM {self.CHECKPOINT_TABLE} WHERE id = 1"
            ).fetchone()

        return row["last_processed_id"] if row else 0

    def _apply_change(self, row: Dict[str, Any]) -> None:
        """Apply a single change to the standby database."""
        table_name = row["table_name"]
        operation = row["operation"]
        payload = row["payload"]

        if not self._manager._standby_pool:
            raise RuntimeError("No standby pool")

        with self._manager._standby_pool.connection() as conn:
            with conn.transaction():
                if operation == "INSERT":
                    self._apply_insert(conn, table_name, payload)
                elif operation == "UPDATE":
                    self._apply_update(conn, table_name, payload)
                elif operation == "DELETE":
                    self._apply_delete(conn, table_name, payload)

    def _apply_insert(self, conn: psycopg.Connection, table: str, payload: Dict) -> None:
        """Apply INSERT operation with conflict handling."""
        columns = list(payload.keys())
        placeholders = ", ".join(["%s"] * len(columns))
        column_list = ", ".join(columns)

        # Use ON CONFLICT DO NOTHING for idempotency
        pk_columns = self._get_primary_key_columns(conn, table)
        if pk_columns:
            conflict_cols = ", ".join(pk_columns)
            update_cols = ", ".join([f"{c} = EXCLUDED.{c}" for c in columns if c not in pk_columns])
            if update_cols:
                conn.execute(
                    f"""
                    INSERT INTO {table} ({column_list}) VALUES ({placeholders})
                    ON CONFLICT ({conflict_cols}) DO UPDATE SET {update_cols}
                    """,
                    tuple(payload.values())
                )
            else:
                conn.execute(
                    f"""
                    INSERT INTO {table} ({column_list}) VALUES ({placeholders})
                    ON CONFLICT ({conflict_cols}) DO NOTHING
                    """,
                    tuple(payload.values())
                )
        else:
            conn.execute(
                f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
                tuple(payload.values())
            )

    def _apply_update(self, conn: psycopg.Connection, table: str, payload: Dict) -> None:
        """Apply UPDATE operation."""
        pk_columns = self._get_primary_key_columns(conn, table)
        if not pk_columns:
            raise ValueError(f"Table {table} has no primary key for UPDATE")

        pk_values = {k: payload[k] for k in pk_columns}
        update_columns = {k: v for k, v in payload.items() if k not in pk_columns}

        if not update_columns:
            return

        set_clause = ", ".join([f"{k} = %s" for k in update_columns])
        where_clause = " AND ".join([f"{k} = %s" for k in pk_columns])

        conn.execute(
            f"UPDATE {table} SET {set_clause} WHERE {where_clause}",
            tuple(update_columns.values()) + tuple(pk_values.values())
        )

    def _apply_delete(self, conn: psycopg.Connection, table: str, payload: Dict) -> None:
        """Apply DELETE operation."""
        pk_columns = self._get_primary_key_columns(conn, table)
        if not pk_columns:
            raise ValueError(f"Table {table} has no primary key for DELETE")

        pk_values = {k: payload[k] for k in pk_columns}
        where_clause = " AND ".join([f"{k} = %s" for k in pk_columns])

        conn.execute(
            f"DELETE FROM {table} WHERE {where_clause}",
            tuple(pk_values.values())
        )

    def _get_primary_key_columns(self, conn: psycopg.Connection, table: str) -> List[str]:
        """Get primary key columns for a table."""
        row = conn.execute(
            """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = %s::regclass AND i.indisprimary
            """,
            (table,)
        ).fetchall()

        return [r["attname"] for r in row] if row else []

    def _mark_processed(self, outbox_id: int) -> None:
        """Mark an outbox entry as processed on primary."""
        with self._manager._primary_pool.connection() as conn:
            conn.execute(
                f"""
                UPDATE {self.OUTBOX_TABLE}
                SET processed_at = NOW()
                WHERE id = %s
                """,
                (outbox_id,)
            )

    def _record_retry(self, outbox_id: int, error: str) -> None:
        """Record a retry attempt for a failed outbox entry."""
        with self._manager._primary_pool.connection() as conn:
            conn.execute(
                f"""
                UPDATE {self.OUTBOX_TABLE}
                SET retry_count = retry_count + 1, last_error = %s
                WHERE id = %s
                """,
                (error, outbox_id)
            )

    def _update_checkpoint(self) -> None:
        """Update the checkpoint on standby with last processed ID."""
        if not self._manager._standby_pool:
            return

        last_id = self._get_last_processed_id_from_primary()

        with self._manager._standby_pool.connection() as conn:
            conn.execute(
                f"""
                UPDATE {self.CHECKPOINT_TABLE}
                SET last_processed_id = %s,
                    last_processed_at = NOW(),
                    updated_at = NOW()
                WHERE id = 1
                """,
                (last_id,)
            )

    def _get_last_processed_id_from_primary(self) -> int:
        """Get the maximum processed ID from primary."""
        with self._manager._primary_pool.connection() as conn:
            row = conn.execute(
                f"SELECT COALESCE(MAX(id), 0) as max_id FROM {self.OUTBOX_TABLE} WHERE processed_at IS NOT NULL"
            ).fetchone()

        return row["max_id"] if row else 0

    def _calculate_replication_lag(self) -> None:
        """Calculate and update replication lag in seconds."""
        if not self._manager._primary_pool or not self._manager._standby_pool:
            return

        with self._manager._primary_pool.connection() as src_conn:
            src_row = src_conn.execute(
                f"SELECT COALESCE(MAX(created_at), NOW()) as max_created FROM {self.OUTBOX_TABLE}"
            ).fetchone()

        with self._manager._standby_pool.connection() as dst_conn:
            dst_row = dst_conn.execute(
                f"SELECT last_processed_at FROM {self.CHECKPOINT_TABLE} WHERE id = 1"
            ).fetchone()

        if src_row and dst_row and src_row["max_created"] and dst_row["last_processed_at"]:
            lag = (src_row["max_created"] - dst_row["last_processed_at"]).total_seconds()
            self._manager.update_sync_state(lag_seconds=max(0, lag))

            with self._manager._standby_pool.connection() as conn:
                conn.execute(
                    f"""
                    UPDATE {self.CHECKPOINT_TABLE}
                    SET lag_seconds = %s
                    WHERE id = 1
                    """,
                    (lag,)
                )

    def start_background_sync(self) -> None:
        """Start background synchronization thread."""
        with self._lock:
            if self._running:
                return

            self._running = True
            self._thread = threading.Thread(target=self._sync_loop, daemon=True)
            self._thread.start()
            logger.info("Background sync started")

    def stop_background_sync(self) -> None:
        """Stop background synchronization thread."""
        with self._lock:
            self._running = False

        if self._thread:
            self._thread.join(timeout=10)
            logger.info("Background sync stopped")

    def _sync_loop(self) -> None:
        """Background sync loop."""
        while self._running:
            try:
                if self._manager._sync_enabled and self._manager.active_role == DatabaseRole.PRIMARY:
                    self.sync_once()
            except Exception as e:
                logger.error(f"Background sync error: {e}")

            time.sleep(self._sync_interval)

    def get_sync_status(self) -> Dict[str, Any]:
        """Get detailed sync status."""
        primary_pending = 0
        standby_checkpoint = 0
        lag = None

        if self._manager._primary_pool:
            with self._manager._primary_pool.connection() as conn:
                row = conn.execute(
                    f"SELECT COUNT(*) as cnt FROM {self.OUTBOX_TABLE} WHERE processed_at IS NULL"
                ).fetchone()
                primary_pending = row["cnt"] if row else 0

        if self._manager._standby_pool:
            with self._manager._standby_pool.connection() as conn:
                row = conn.execute(
                    f"SELECT last_processed_id, lag_seconds FROM {self.CHECKPOINT_TABLE} WHERE id = 1"
                ).fetchone()
                if row:
                    standby_checkpoint = row["last_processed_id"]
                    lag = row["lag_seconds"]

        return {
            "primary_pending_changes": primary_pending,
            "standby_checkpoint_id": standby_checkpoint,
            "replication_lag_seconds": lag,
            "sync_interval_seconds": self._sync_interval,
            "batch_size": self._batch_size,
            "running": self._running,
        }


class ChangeLogSync:
    """
    Alternative synchronization using PostgreSQL's logical replication
    (if available on Neon) or trigger-based change capture.

    Note: Neon Free tier may not support logical replication slots.
    This is a fallback for when outbox pattern is not suitable.
    """

    def __init__(self, manager: Optional["DatabaseManager"] = None) -> None:
        self._manager = manager or get_database_manager()

    def setup_logical_replication(self) -> bool:
        """
        Attempt to set up logical replication.

        Returns:
            bool: True if logical replication is available and configured
        """
        # Neon Free tier typically doesn't support logical replication
        # This is a placeholder for future enhancement
        logger.warning("Logical replication not available on Neon Free tier")
        return False

    def setup_trigger_based_capture(self) -> None:
        """Set up trigger-based change capture on primary."""
        # This would create triggers on each table to populate outbox
        # Implemented as part of outbox pattern above
        pass


# Convenience functions for use in application code
def record_insert(table_name: str, payload: Dict[str, Any]) -> None:
    """Record an INSERT operation in the outbox."""
    get_database_manager()  # Ensure initialized
    # This would be called within a transaction context
    # Implementation depends on how the app manages transactions


def record_update(table_name: str, payload: Dict[str, Any]) -> None:
    """Record an UPDATE operation in the outbox."""
    pass


def record_delete(table_name: str, payload: Dict[str, Any]) -> None:
    """Record a DELETE operation in the outbox."""
    pass