"""
Health Monitoring for Dual Neon PostgreSQL.

Provides comprehensive health checks for both databases,
replication lag monitoring, and alerting capabilities.
"""

import os
import threading
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

import psycopg
from psycopg_pool import ConnectionPool

from .models import DatabaseStatus, DatabaseState, SyncStatus
from .manager import get_database_manager

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    database_role: str
    healthy: bool
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    details: Dict[str, Any] = field(default_factory=dict)


class HealthMonitor:
    """
    Comprehensive health monitoring for dual database setup.

    Features:
    - Periodic health checks for both databases
    - Connection pool monitoring
    - Replication lag tracking
    - Resource usage monitoring (Neon CU-hours estimation)
    - Alerting thresholds
    """

    def __init__(self, manager: Optional["DatabaseManager"] = None) -> None:
        self._manager = manager or get_database_manager()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._check_interval = int(os.getenv("DATABASE_HEALTH_CHECK_INTERVAL", "30"))
        self._history: List[HealthCheckResult] = []
        self._max_history = 1000

        # Alert thresholds
        self._max_latency_ms = int(os.getenv("DB_ALERT_LATENCY_MS", "5000"))
        self._max_replication_lag = int(os.getenv("DATABASE_MAX_REPLICATION_LAG_SECONDS", "30"))
        self._max_consecutive_failures = int(os.getenv("DATABASE_FAILURE_THRESHOLD", "3"))

    def start(self) -> None:
        """Start background health monitoring."""
        with self._lock:
            if self._running:
                return

            self._running = True
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._thread.start()
            logger.info("Health monitoring started")

    def stop(self) -> None:
        """Stop background health monitoring."""
        with self._lock:
            self._running = False

        if self._thread:
            self._thread.join(timeout=10)
            logger.info("Health monitoring stopped")

    def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                self.check_all()
            except Exception as e:
                logger.error(f"Health check error: {e}")

            time.sleep(self._check_interval)

    def check_all(self) -> Dict[str, HealthCheckResult]:
        """Check health of both databases."""
        results = {}

        if self._manager._primary_pool and self._manager._primary_state:
            results["primary"] = self._check_database(
                self._manager._primary_state,
                self._manager._primary_pool,
                "primary"
            )

        if self._manager._standby_pool and self._manager._standby_state:
            results["standby"] = self._check_database(
                self._manager._standby_state,
                self._manager._standby_pool,
                "standby"
            )

        self._evaluate_alerts(results)
        return results

    def _get_connection(self, pool: ConnectionPool) -> psycopg.Connection:
        """Get a connection from pool."""
        conn = pool.getconn()
        conn.autocommit = False
        return conn

    def _put_connection(self, pool: ConnectionPool, conn: psycopg.Connection) -> None:
        """Return connection to pool."""
        try:
            pool.putconn(conn)
        except Exception:
            pass

    def _check_database(
        self,
        state: DatabaseState,
        pool: ConnectionPool,
        role: str
    ) -> HealthCheckResult:
        """Check health of a single database."""
        start = time.perf_counter()
        error = None
        healthy = False
        details = {}

        conn = None
        try:
            conn = self._get_connection(pool)
            with conn.cursor() as cur:
                # Basic connectivity
                cur.execute("SELECT 1")

                # Get database stats
                stats = self._get_database_stats(cur)
                details.update(stats)

                # Check pool status
                pool_stats = self._get_pool_stats(pool)
                details.update(pool_stats)

            latency_ms = (time.perf_counter() - start) * 1000
            healthy = True
            state.status = DatabaseStatus.HEALTHY
            state.last_check = datetime.utcnow()
            state.reset_failures()

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            error = str(e)
            healthy = False
            state.record_failure(error)
            state.status = DatabaseStatus.UNAVAILABLE if state.consecutive_failures >= self._max_consecutive_failures else DatabaseStatus.DEGRADED
            logger.warning(f"Health check failed for {role}: {error}")
        finally:
            if conn:
                self._put_connection(pool, conn)

        result = HealthCheckResult(
            database_role=role,
            healthy=healthy,
            latency_ms=latency_ms,
            error=error,
            details=details,
        )

        self._add_to_history(result)
        return result

    def _get_database_stats(self, cur: psycopg.Cursor) -> Dict[str, Any]:
        """Get database statistics."""
        stats = {}

        try:
            # Database size
            cur.execute(
                "SELECT pg_size_pretty(pg_database_size(current_database())) as size"
            )
            row = cur.fetchone()
            if row:
                stats["database_size"] = row["size"]

            # Active connections
            cur.execute(
                "SELECT count(*) as count FROM pg_stat_activity WHERE datname = current_database()"
            )
            row = cur.fetchone()
            if row:
                stats["active_connections"] = row["count"]

            # Transaction stats
            cur.execute(
                """
                SELECT xact_commit, xact_rollback,
                       blks_read, blks_hit,
                       tup_returned, tup_fetched,
                       tup_inserted, tup_updated, tup_deleted
                FROM pg_stat_database
                WHERE datname = current_database()
                """
            )
            row = cur.fetchone()
            if row:
                stats.update(dict(row))

        except Exception as e:
            logger.debug(f"Failed to get database stats: {e}")

        return stats

    def _get_pool_stats(self, pool: ConnectionPool) -> Dict[str, Any]:
        """Get connection pool statistics."""
        # ConnectionPool doesn't expose detailed stats
        # We track what we can in DatabaseState
        return {}

    def _evaluate_alerts(self, results: Dict[str, HealthCheckResult]) -> None:
        """Evaluate health check results against alert thresholds."""
        for role, result in results.items():
            if not result.healthy:
                logger.warning(f"ALERT: {role} database unhealthy: {result.error}")
                continue

            if result.latency_ms and result.latency_ms > self._max_latency_ms:
                logger.warning(
                    f"ALERT: {role} database high latency: {result.latency_ms:.0f}ms "
                    f"(threshold: {self._max_latency_ms}ms)"
                )

        # Check replication lag
        sync_state = self._manager._sync_state
        if sync_state.replication_lag_seconds is not None:
            if sync_state.replication_lag_seconds > self._max_replication_lag:
                logger.warning(
                    f"ALERT: Replication lag {sync_state.replication_lag_seconds:.1f}s "
                    f"exceeds threshold {self._max_replication_lag}s"
                )

    def _add_to_history(self, result: HealthCheckResult) -> None:
        """Add result to history with size limit."""
        with self._lock:
            self._history.append(result)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

    def get_recent_history(
        self,
        role: Optional[str] = None,
        limit: int = 100
    ) -> List[HealthCheckResult]:
        """Get recent health check history."""
        with self._lock:
            history = self._history
            if role:
                history = [r for r in history if r.database_role == role]
            return history[-limit:]

    def get_uptime_stats(self, role: str, window_minutes: int = 60) -> Dict[str, Any]:
        """Calculate uptime statistics for a time window."""
        cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
        history = self.get_recent_history(role, limit=1000)
        history = [r for r in history if r.timestamp >= cutoff]

        if not history:
            return {"uptime_percent": 0, "checks": 0}

        healthy_count = sum(1 for r in history if r.healthy)
        total = len(history)

        return {
            "uptime_percent": round(healthy_count / total * 100, 2),
            "healthy_checks": healthy_count,
            "total_checks": total,
            "window_minutes": window_minutes,
            "avg_latency_ms": round(
                sum(r.latency_ms or 0 for r in history if r.latency_ms) / max(1, sum(1 for r in history if r.latency_ms)),
                2
            ) if any(r.latency_ms for r in history) else None,
        }

    def estimate_cu_hours(self, role: str) -> Optional[float]:
        """
        Estimate Neon Compute Unit hours usage.

        Note: This is an approximation based on connection time and query activity.
        Actual CU-hours should be checked in Neon dashboard.
        """
        state = self._manager._primary_state if role == "primary" else self._manager._standby_state
        if not state:
            return None

        # Rough estimation: each active connection consumes ~1 CU-hour per hour
        # This is very approximate - Neon's actual calculation is more complex
        active_connections = getattr(state, 'active_connections', 0)
        uptime_hours = 24  # Assume 24h for daily estimate

        return active_connections * uptime_hours * 0.1  # Very rough factor

    def check_resource_warnings(self) -> List[Dict[str, Any]]:
        """Check for resource usage warnings."""
        warnings = []

        # Check replication lag
        sync_state = self._manager._sync_state
        if sync_state.replication_lag_seconds is not None:
            if sync_state.replication_lag_seconds > self._max_replication_lag * 0.8:
                warnings.append({
                    "type": "replication_lag",
                    "severity": "warning",
                    "message": f"Replication lag at {sync_state.replication_lag_seconds:.1f}s (80% of threshold)",
                    "value": sync_state.replication_lag_seconds,
                    "threshold": self._max_replication_lag,
                })

        # Check consecutive failures
        for state, role in [(self._manager._primary_state, "primary"),
                             (self._manager._standby_state, "standby")]:
            if state and state.consecutive_failures >= self._max_consecutive_failures * 0.5:
                warnings.append({
                    "type": "consecutive_failures",
                    "severity": "warning",
                    "message": f"{role} has {state.consecutive_failures} consecutive failures",
                    "value": state.consecutive_failures,
                    "threshold": self._max_consecutive_failures,
                })

        return warnings


def create_health_monitor(manager: Optional["DatabaseManager"] = None) -> HealthMonitor:
    """Factory function to create health monitor."""
    return HealthMonitor(manager)