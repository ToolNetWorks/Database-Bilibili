"""
Database Manager for Dual Neon PostgreSQL with Automatic Failover & Safe Failback.

This module provides:
- Connection pooling for primary and standby databases
- Automatic failover with safety checks
- Data synchronization via outbox pattern
- Health monitoring
- Manual failback support
"""

from .manager import DatabaseManager, get_database_manager
from .models import DatabaseRole, DatabaseStatus, SyncStatus, FailoverEvent
from .sync import OutboxSync, ChangeLogSync
from .health import HealthMonitor

__all__ = [
    "DatabaseManager",
    "get_database_manager",
    "DatabaseRole",
    "DatabaseStatus",
    "SyncStatus",
    "FailoverEvent",
    "OutboxSync",
    "ChangeLogSync",
    "HealthMonitor",
]