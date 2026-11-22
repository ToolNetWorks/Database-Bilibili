"""
Core Database Manager for Dual Neon PostgreSQL.

Provides connection pooling, automatic failover, and safe failback.
"""

import os
import threading
import time
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional, Any, Generator, List, Dict
from urllib.parse import urlparse

import psycopg
from psycopg_pool import ThreadedConnectionPool
from psycopg.rows import dict_row

from .models import (
    DatabaseConfig,
    DatabaseState,
    DatabaseRole,
    DatabaseStatus,
    SyncStatus,
    SyncState,
    FailoverEvent,
)

logger = logging.getLogger(__name__)


class _PooledConnection:
    """Wrapper to provide psycopg 3 compatible interface for pooled connections."""
    
    def __init__(self, pool: ThreadedConnectionPool, conn: psycopg.Connection):
        self._pool = pool
        self._conn = conn
    
    def __enter__(self) -> psycopg.Connection:
        return self._conn
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if exc_type is not None:
                self._conn.rollback()
            else:
                self._conn.commit()
        finally:
            self._pool.putconn(self._conn)
    
    def cursor(self, **kwargs):
        return self._conn.cursor(**kwargs)
    
    def execute(self, query: str, params: tuple = ()):
        with self._conn.cursor() as cur:
            return cur.execute(query, params)
    
    def fetchall(self):
        with self._conn.cursor() as cur:
            return cur.fetchall()
    
    def fetchone(self):
        with self._conn.cursor() as cur:
            return cur.fetchone()
    
    def rollback(self):
        self._conn.rollback()
    
    def commit(self):
        self._conn.commit()


class DatabaseManager:
    """
    Centralized database connection manager with automatic failover.

    Features:
    - Dual database support (primary + standby)
    - Connection pooling per database
    - Automatic failover with safety checks
    - Health monitoring
    - Manual failback support
    - Thread-safe operations
    """

    _instance: Optional["DatabaseManager"] = None
    _init_lock = threading.Lock()

    def __new__(cls) -> "DatabaseManager":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self._primary_state: Optional[DatabaseState] = None
        self._standby_state: Optional[DatabaseState] = None
        self._primary_pool: Optional[ThreadedConnectionPool] = None
        self._standby_pool: Optional[ThreadedConnectionPool] = None
        self._active_role: DatabaseRole = DatabaseRole.UNKNOWN
        self._sync_state = SyncState()
        self._failover_history: List[FailoverEvent] = []
        self._failover_lock = threading.Lock()
        self._config_lock = threading.Lock()
        self._initialized = True
        self._shutdown = False

        self._load_config()
        self._initialize_pools()
        self._determine_initial_active()

    def _load_config(self) -> None:
        """Load configuration from environment variables."""
        primary_url = os.getenv("DATABASE_PRIMARY_URL", "").strip()
        standby_url = os.getenv("DATABASE_STANDBY_URL", "").strip()
        fallback_url = os.getenv("DATABASE_URL", "").strip()

        if not primary_url and fallback_url:
            primary_url = fallback_url
            logger.warning("Using DATABASE_URL as primary (DATABASE_PRIMARY_URL not set)")

        if not primary_url:
            raise RuntimeError("No primary database URL configured (DATABASE_PRIMARY_URL or DATABASE_URL)")

        self._primary_config = DatabaseConfig(
            url=primary_url,
            role=DatabaseRole.PRIMARY,
            pool_min_size=int(os.getenv("DB_POOL_MIN", "1")),
            pool_max_size=int(os.getenv("DB_POOL_MAX", "5")),
            connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
            command_timeout=int(os.getenv("DB_COMMAND_TIMEOUT", "30")),
        )

        if standby_url:
            self._standby_config = DatabaseConfig(
                url=standby_url,
                role=DatabaseRole.STANDBY,
                pool_min_size=int(os.getenv("DB_POOL_MIN", "1")),
                pool_max_size=int(os.getenv("DB_POOL_MAX", "5")),
                connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
                command_timeout=int(os.getenv("DB_COMMAND_TIMEOUT", "30")),
            )
        else:
            self._standby_config = None
            logger.warning("No standby database configured (DATABASE_STANDBY_URL not set)")

        self._failover_enabled = os.getenv("DATABASE_FAILOVER_ENABLED", "true").lower() == "true"
        self._failback_mode = os.getenv("DATABASE_FAILBACK_MODE", "manual").lower()
        self._health_timeout = int(os.getenv("DATABASE_HEALTH_TIMEOUT", "5"))
        self._failure_threshold = int(os.getenv("DATABASE_FAILURE_THRESHOLD", "3"))
        self._sync_enabled = os.getenv("DATABASE_SYNC_ENABLED", "true").lower() == "true"
        self._sync_interval = int(os.getenv("DATABASE_SYNC_INTERVAL", "60"))
        self._max_replication_lag = int(os.getenv("DATABASE_MAX_REPLICATION_LAG_SECONDS", "30"))

    def _initialize_pools(self) -> None:
        """Initialize connection pools for both databases."""
        self._primary_state = DatabaseState(config=self._primary_config)
        self._primary_pool = self._create_pool(self._primary_config)

        if self._standby_config:
            self._standby_state = DatabaseState(config=self._standby_config)
            self._standby_pool = self._create_pool(self._standby_config)

    def _create_pool(self, config: DatabaseConfig) -> ThreadedConnectionPool:
        """Create a connection pool for the given config."""
        return ThreadedConnectionPool(
            min_size=config.pool_min_size,
            max_size=config.pool_max_size,
            dsn=config.url,
            connect_timeout=config.connect_timeout,
            kwargs={"row_factory": dict_row},
        )

    def _get_connection(self, pool: ThreadedConnectionPool) -> psycopg.Connection:
        """Get a connection from pool with proper configuration."""
        conn = pool.getconn()
        conn.autocommit = False
        return conn

    def _determine_initial_active(self) -> None:
        """Determine which database should be active initially."""
        if self._check_database_health(self._primary_state, self._primary_pool):
            self._active_role = DatabaseRole.PRIMARY
            self._primary_state.status = DatabaseStatus.HEALTHY
            logger.info("Primary database healthy, set as active")
        elif self._standby_pool and self._check_database_health(self._standby_state, self._standby_pool):
            self._active_role = DatabaseRole.STANDBY
            self._standby_state.status = DatabaseStatus.HEALTHY
            logger.warning("Primary unhealthy, standby set as active")
        else:
            self._active_role = DatabaseRole.UNKNOWN
            logger.error("No healthy database available at startup")

    def _check_database_health(
        self, state: DatabaseState, pool: Optional[ThreadedConnectionPool]
    ) -> bool:
        """Check if a database is healthy."""
        if not pool:
            return False

        conn = None
        try:
            conn = self._get_connection(pool)
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            state.status = DatabaseStatus.HEALTHY
            state.last_check = datetime.utcnow()
            state.reset_failures()
            return True
        except Exception as e:
            state.status = DatabaseStatus.UNAVAILABLE
            state.last_check = datetime.utcnow()
            state.record_failure(str(e))
            logger.warning(f"Health check failed for {state.config.role.value}: {e}")
            return False
        finally:
            if conn:
                try:
                    pool.putconn(conn)
                except Exception:
                    pass

    @property
    def active_role(self) -> DatabaseRole:
        """Get the currently active database role."""
        return self._active_role

    @property
    def is_primary_active(self) -> bool:
        """Check if primary is currently active."""
        return self._active_role == DatabaseRole.PRIMARY

    @property
    def is_standby_active(self) -> bool:
        """Check if standby is currently active."""
        return self._active_role == DatabaseRole.STANDBY

    def get_active_pool(self) -> Optional[ThreadedConnectionPool]:
        """Get the connection pool for the active database."""
        if self._active_role == DatabaseRole.PRIMARY:
            return self._primary_pool
        elif self._active_role == DatabaseRole.STANDBY:
            return self._standby_pool
        return None

    def get_active_state(self) -> Optional[DatabaseState]:
        """Get the state for the active database."""
        if self._active_role == DatabaseRole.PRIMARY:
            return self._primary_state
        elif self._active_role == DatabaseRole.STANDBY:
            return self._standby_state
        return None

    @contextmanager
    def connection(self) -> Generator[psycopg.Connection, None, None]:
        """
        Get a connection from the active database pool.

        Yields:
            psycopg.Connection: Database connection

        Raises:
            RuntimeError: If no active database available
            psycopg.Error: If connection fails
        """
        pool = self.get_active_pool()
        state = self.get_active_state()

        if not pool or not state:
            raise RuntimeError("No active database available")

        conn = self._get_connection(pool)
        try:
            yield conn
            state.record_success()
        except psycopg.Error as e:
            state.record_failure(str(e))
            logger.error(f"Database error on {state.config.role.value}: {e}")
            self._handle_connection_failure(state, e)
            raise
        finally:
            try:
                pool.putconn(conn)
            except Exception:
                pass

    @contextmanager
    def transaction(self) -> Generator[psycopg.Connection, None, None]:
        """
        Get a connection with automatic transaction management.

        Yields:
            psycopg.Connection: Database connection with transaction

        Note:
            Commits on success, rolls back on exception.
        """
        with self.connection() as conn:
            with conn.transaction():
                yield conn

    def _handle_connection_failure(self, state: DatabaseState, error: Exception) -> None:
        """Handle connection failure and trigger failover if needed."""
        if not self._failover_enabled:
            return

        if state.consecutive_failures >= self._failure_threshold:
            logger.warning(
                f"Database {state.config.role.value} reached failure threshold "
                f"({state.consecutive_failures}/{self._failure_threshold})"
            )
            self._attempt_failover(state.config.role, str(error))

    def _attempt_failover(self, failed_role: DatabaseRole, reason: str) -> bool:
        """
        Attempt to failover to the other database.

        Args:
            failed_role: The role that failed
            reason: Reason for failover

        Returns:
            bool: True if failover succeeded
        """
        with self._failover_lock:
            if failed_role == DatabaseRole.PRIMARY:
                target_role = DatabaseRole.STANDBY
                target_state = self._standby_state
                target_pool = self._standby_pool
            else:
                target_role = DatabaseRole.PRIMARY
                target_state = self._primary_state
                target_pool = self._primary_pool

            if not target_state or not target_pool:
                logger.error(f"No {target_role.value} database available for failover")
                return False

            if not self._check_database_health(target_state, target_pool):
                logger.error(f"Target database {target_role.value} is not healthy")
                return False

            if not self._is_sync_safe_for_failover():
                logger.error("Failover aborted: replication lag too high or sync unsafe")
                self._record_failover_event(
                    FailoverEvent(
                        timestamp=datetime.utcnow(),
                        from_role=failed_role,
                        to_role=target_role,
                        reason=f"ABORTED: {reason}",
                        sync_lag_seconds=self._sync_state.replication_lag_seconds,
                        data_loss_risk=True,
                    )
                )
                return False

            old_active = self._active_role
            self._active_role = target_role
            target_state.status = DatabaseStatus.HEALTHY

            self._record_failover_event(
                FailoverEvent(
                    timestamp=datetime.utcnow(),
                    from_role=failed_role,
                    to_role=target_role,
                    reason=reason,
                    sync_lag_seconds=self._sync_state.replication_lag_seconds,
                    data_loss_risk=False,
                )
            )

            logger.warning(
                f"FAILOVER: {old_active.value} -> {target_role.value} | Reason: {reason}"
            )
            return True

    def _is_sync_safe_for_failover(self) -> bool:
        """Check if it's safe to failover based on sync state."""
        if self._sync_state.status == SyncStatus.UNKNOWN:
            return False

        if self._sync_state.status == SyncStatus.OUT_OF_SYNC:
            return False

        if self._sync_state.replication_lag_seconds is not None:
            if self._sync_state.replication_lag_seconds > self._max_replication_lag:
                return False

        return True

    def _record_failover_event(self, event: FailoverEvent) -> None:
        """Record a failover event in history."""
        self._failover_history.append(event)
        if len(self._failover_history) > 100:
            self._failover_history = self._failover_history[-100:]

    def check_and_recover_primary(self) -> bool:
        """
        Check if primary has recovered and optionally failback.

        Returns:
            bool: True if primary recovered and failback occurred (if auto)
        """
        if not self._primary_pool or not self._primary_state:
            return False

        if self._active_role == DatabaseRole.PRIMARY:
            return True

        if not self._check_database_health(self._primary_state, self._primary_pool):
            return False

        if not self._is_primary_ready_for_failback():
            logger.info("Primary healthy but not ready for failback (sync lag or conflicts)")
            return False

        if self._failback_mode == "auto":
            return self._perform_failback()
        else:
            logger.info("Primary recovered, failback mode is manual - use trigger_failback()")
            return False

    def _is_primary_ready_for_failback(self) -> bool:
        """Check if primary is ready for failback."""
        if self._sync_state.status != SyncStatus.IN_SYNC:
            return False

        if self._sync_state.replication_lag_seconds is not None:
            if self._sync_state.replication_lag_seconds > 5:
                return False

        if self._sync_state.last_error:
            return False

        return True

    def trigger_failback(self) -> bool:
        """
        Manually trigger failback to primary.

        Returns:
            bool: True if failback succeeded
        """
        if self._active_role == DatabaseRole.PRIMARY:
            logger.info("Primary already active")
            return True

        if not self._primary_pool or not self._primary_state:
            logger.error("Primary not configured")
            return False

        if not self._is_primary_ready_for_failback():
            logger.error("Primary not ready for failback - sync incomplete or conflicts exist")
            return False

        return self._perform_failback()

    def _perform_failback(self) -> bool:
        """Perform the actual failback to primary."""
        with self._failover_lock:
            if not self._check_database_health(self._primary_state, self._primary_pool):
                logger.error("Primary health check failed during failback")
                return False

            old_active = self._active_role
            self._active_role = DatabaseRole.PRIMARY
            self._primary_state.status = DatabaseStatus.HEALTHY

            self._record_failover_event(
                FailoverEvent(
                    timestamp=datetime.utcnow(),
                    from_role=old_active,
                    to_role=DatabaseRole.PRIMARY,
                    reason="Manual failback to primary",
                    sync_lag_seconds=self._sync_state.replication_lag_seconds,
                    data_loss_risk=False,
                    initiated_by="manual",
                )
            )

            logger.info(f"FAILBACK: {old_active.value} -> primary")
            return True

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive status of both databases."""
        primary_status = self._get_database_status(self._primary_state, self._primary_pool, DatabaseRole.PRIMARY)
        standby_status = self._get_database_status(self._standby_state, self._standby_pool, DatabaseRole.STANDBY)

        return {
            "active_database": self._active_role.value,
            "failover_enabled": self._failover_enabled,
            "failback_mode": self._failback_mode,
            "primary": primary_status,
            "standby": standby_status,
            "sync": {
                "status": self._sync_state.status.value,
                "last_sync": self._sync_state.last_sync.isoformat() if self._sync_state.last_sync else None,
                "last_successful_sync": self._sync_state.last_successful_sync.isoformat() if self._sync_state.last_successful_sync else None,
                "replication_lag_seconds": self._sync_state.replication_lag_seconds,
                "pending_changes": self._sync_state.pending_changes,
                "last_error": self._sync_state.last_error,
            },
            "failover_history": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "from": e.from_role.value,
                    "to": e.to_role.value,
                    "reason": e.reason,
                    "sync_lag_seconds": e.sync_lag_seconds,
                    "data_loss_risk": e.data_loss_risk,
                    "initiated_by": e.initiated_by,
                }
                for e in self._failover_history[-10:]
            ],
        }

    def _get_database_status(
        self, state: Optional[DatabaseState], pool: Optional[ThreadedConnectionPool], role: DatabaseRole
    ) -> Dict[str, Any]:
        """Get status for a single database."""
        if not state or not pool:
            return {
                "role": role.value,
                "status": "not_configured",
                "healthy": False,
            }

        return {
            "role": role.value,
            "status": state.status.value,
            "healthy": state.status == DatabaseStatus.HEALTHY,
            "last_check": state.last_check.isoformat() if state.last_check else None,
            "consecutive_failures": state.consecutive_failures,
            "last_error": state.last_error,
            "active_connections": state.active_connections,
            "total_queries": state.total_queries,
            "failed_queries": state.failed_queries,
            "is_active": self._active_role == role,
        }

    def get_health(self) -> Dict[str, Any]:
        """Get health status for load balancer / monitoring."""
        active_state = self.get_active_state()

        if not active_state:
            return {
                "healthy": False,
                "active_database": "none",
                "message": "No active database",
            }

        return {
            "healthy": active_state.status == DatabaseStatus.HEALTHY,
            "active_database": self._active_role.value,
            "sync_status": self._sync_state.status.value,
            "replication_lag_seconds": self._sync_state.replication_lag_seconds,
        }

    def get_sync_status(self) -> Dict[str, Any]:
        """Get detailed sync status."""
        return {
            "status": self._sync_state.status.value,
            "last_sync": self._sync_state.last_sync.isoformat() if self._sync_state.last_sync else None,
            "last_successful_sync": self._sync_state.last_successful_sync.isoformat() if self._sync_state.last_successful_sync else None,
            "replication_lag_seconds": self._sync_state.replication_lag_seconds,
            "pending_changes": self._sync_state.pending_changes,
            "last_error": self._sync_state.last_error,
            "max_allowed_lag_seconds": self._max_replication_lag,
            "sync_enabled": self._sync_enabled,
            "sync_interval_seconds": self._sync_interval,
        }

    def update_sync_state(
        self,
        status: Optional[SyncStatus] = None,
        lag_seconds: Optional[float] = None,
        pending_changes: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update synchronization state (called by sync worker)."""
        if status:
            self._sync_state.status = status

        if lag_seconds is not None:
            self._sync_state.update_lag(lag_seconds)

        if pending_changes is not None:
            with self._sync_state._lock:
                self._sync_state.pending_changes = pending_changes

        if error:
            self._sync_state.record_sync_failure(error)
        elif lag_seconds is not None and lag_seconds <= 1:
            self._sync_state.record_sync_success(pending_changes or 0)

    def shutdown(self) -> None:
        """Shutdown all connection pools."""
        self._shutdown = True

        if self._primary_pool:
            self._primary_pool.close()
            self._primary_pool = None

        if self._standby_pool:
            self._standby_pool.close()
            self._standby_pool = None

        logger.info("Database manager shutdown complete")


def get_database_manager() -> DatabaseManager:
    """Get the singleton DatabaseManager instance."""
    return DatabaseManager()