"""
Data models for Database Manager.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import threading


class DatabaseRole(Enum):
    """Role of a database in the replication setup."""
    PRIMARY = "primary"
    STANDBY = "standby"
    UNKNOWN = "unknown"


class DatabaseStatus(Enum):
    """Health status of a database connection."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class SyncStatus(Enum):
    """Synchronization status between primary and standby."""
    IN_SYNC = "in_sync"
    SYNCING = "syncing"
    LAGGING = "lagging"
    OUT_OF_SYNC = "out_of_sync"
    UNKNOWN = "unknown"


@dataclass
class DatabaseConfig:
    """Configuration for a single database connection."""
    url: str
    role: DatabaseRole
    pool_min_size: int = 1
    pool_max_size: int = 5
    connect_timeout: int = 10
    command_timeout: int = 30
    ssl_mode: str = "require"
    retry_max_attempts: int = 3
    retry_base_delay: float = 0.5
    retry_max_delay: float = 30.0


@dataclass
class DatabaseState:
    """Runtime state of a database."""
    config: DatabaseConfig
    status: DatabaseStatus = DatabaseStatus.UNKNOWN
    last_check: Optional[datetime] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    active_connections: int = 0
    total_queries: int = 0
    failed_queries: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_success(self) -> None:
        with self._lock:
            self.consecutive_failures = 0
            self.last_error = None
            self.total_queries += 1

    def record_failure(self, error: str) -> None:
        with self._lock:
            self.consecutive_failures += 1
            self.last_error = error
            self.total_queries += 1
            self.failed_queries += 1

    def reset_failures(self) -> None:
        with self._lock:
            self.consecutive_failures = 0
            self.last_error = None


@dataclass
class FailoverEvent:
    """Record of a failover event."""
    timestamp: datetime
    from_role: DatabaseRole
    to_role: DatabaseRole
    reason: str
    sync_lag_seconds: Optional[float] = None
    data_loss_risk: bool = False
    initiated_by: str = "automatic"


@dataclass
class SyncState:
    """State of data synchronization."""
    status: SyncStatus = SyncStatus.UNKNOWN
    last_sync: Optional[datetime] = None
    last_successful_sync: Optional[datetime] = None
    replication_lag_seconds: Optional[float] = None
    pending_changes: int = 0
    last_error: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update_lag(self, lag_seconds: float) -> None:
        with self._lock:
            self.replication_lag_seconds = lag_seconds
            if lag_seconds <= 0:
                self.status = SyncStatus.IN_SYNC
            elif lag_seconds <= 30:
                self.status = SyncStatus.LAGGING
            else:
                self.status = SyncStatus.OUT_OF_SYNC

    def record_sync_start(self) -> None:
        with self._lock:
            self.status = SyncStatus.SYNCING
            self.last_sync = datetime.utcnow()

    def record_sync_success(self, pending: int = 0) -> None:
        with self._lock:
            self.status = SyncStatus.IN_SYNC
            self.last_successful_sync = datetime.utcnow()
            self.pending_changes = pending
            self.last_error = None

    def record_sync_failure(self, error: str) -> None:
        with self._lock:
            self.status = SyncStatus.OUT_OF_SYNC
            self.last_error = error