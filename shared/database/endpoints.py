"""
Internal Database Monitoring Endpoints.

Provides authenticated endpoints for:
- Database status
- Health checks
- Sync status
- Failover history
- Manual failback trigger
"""

import os
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Security
from fastapi.security import APIKeyHeader

from shared.database.manager import get_database_manager
from shared.database.health import create_health_monitor
from shared.database.sync import OutboxSync

router = APIRouter(prefix="/internal/database", tags=["database-internal"])

# Authentication for internal endpoints
internal_api_key_header = APIKeyHeader(name="X-Internal-API-Key", auto_error=False)


def require_internal_key(x_internal_key: str | None = Security(internal_api_key_header)) -> None:
    """Verify internal API key for sensitive endpoints."""
    configured_key = os.getenv("INTERNAL_API_KEY", "")

    if not configured_key:
        raise HTTPException(503, "Internal API authentication not configured")

    if not x_internal_key:
        raise HTTPException(401, "Missing internal API key")

    if not secrets.compare_digest(x_internal_key, configured_key):
        raise HTTPException(401, "Invalid internal API key")


@router.get("/status", dependencies=[Depends(require_internal_key)])
def get_database_status():
    """Get comprehensive database status."""
    manager = get_database_manager()
    return manager.get_status()


@router.get("/health", dependencies=[Depends(require_internal_key)])
def get_database_health():
    """Get health status for load balancer monitoring."""
    manager = get_database_manager()
    return manager.get_health()


@router.get("/sync-status", dependencies=[Depends(require_internal_key)])
def get_sync_status():
    """Get detailed synchronization status."""
    manager = get_database_manager()
    sync_status = manager.get_sync_status()

    # Add outbox-specific details
    outbox_sync = OutboxSync(manager)
    outbox_status = outbox_sync.get_sync_status()

    return {
        **sync_status,
        "outbox": outbox_status,
    }


@router.get("/failover-history", dependencies=[Depends(require_internal_key)])
def get_failover_history(limit: int = 20):
    """Get recent failover events."""
    manager = get_database_manager()
    status = manager.get_status()
    return {
        "failover_history": status["failover_history"][:limit],
    }


@router.post("/failback", dependencies=[Depends(require_internal_key)])
def trigger_failback():
    """
    Manually trigger failback to primary database.

    Only succeeds if:
    - Primary is healthy
    - Replication lag is minimal
    - No unresolved conflicts
    """
    manager = get_database_manager()

    if manager.active_role == "primary":
        return {"success": True, "message": "Primary already active"}

    success = manager.trigger_failback()

    if success:
        return {"success": True, "message": "Failback to primary completed"}
    else:
        raise HTTPException(409, "Failback failed - primary not ready or sync incomplete")


@router.post("/failover", dependencies=[Depends(require_internal_key)])
def trigger_failover(target: str = "standby", reason: str = "Manual failover"):
    """
    Manually trigger failover to standby database.

    Args:
        target: Target database role (standby)
        reason: Reason for manual failover
    """
    manager = get_database_manager()

    if target not in ("standby", "primary"):
        raise HTTPException(400, "Invalid target. Must be 'standby' or 'primary'")

    target_role = "standby" if target == "standby" else "primary"

    if manager.active_role == target_role:
        return {"success": True, "message": f"{target_role} already active"}

    # For manual failover, we bypass some safety checks but still require target health
    from shared.database.models import DatabaseRole

    if target_role == "standby":
        success = manager._attempt_failover(DatabaseRole.PRIMARY, f"MANUAL: {reason}")
    else:
        success = manager._attempt_failover(DatabaseRole.STANDBY, f"MANUAL: {reason}")

    if success:
        return {"success": True, "message": f"Failover to {target_role} completed"}
    else:
        raise HTTPException(409, f"Failover to {target_role} failed - target not healthy or sync unsafe")


@router.get("/pool-stats", dependencies=[Depends(require_internal_key)])
def get_pool_stats():
    """Get connection pool statistics."""
    manager = get_database_manager()

    def get_pool_info(pool, state, role):
        if not pool or not state:
            return {"role": role, "configured": False}
        return {
            "role": role,
            "configured": True,
            "min_size": state.config.pool_min_size,
            "max_size": state.config.pool_max_size,
            "active_connections": state.active_connections,
            "total_queries": state.total_queries,
            "failed_queries": state.failed_queries,
        }

    return {
        "primary": get_pool_info(manager._primary_pool, manager._primary_state, "primary"),
        "standby": get_pool_info(manager._standby_pool, manager._standby_state, "standby"),
    }


@router.get("/resource-warnings", dependencies=[Depends(require_internal_key)])
def get_resource_warnings():
    """Get current resource usage warnings."""
    monitor = create_health_monitor(get_database_manager())
    warnings = monitor.check_resource_warnings()
    return {"warnings": warnings}


@router.get("/uptime", dependencies=[Depends(require_internal_key)])
def get_uptime_stats(window_minutes: int = 60):
    """Get uptime statistics for both databases."""
    monitor = create_health_monitor(get_database_manager())

    return {
        "primary": monitor.get_uptime_stats("primary", window_minutes),
        "standby": monitor.get_uptime_stats("standby", window_minutes),
    }


@router.post("/sync/trigger", dependencies=[Depends(require_internal_key)])
def trigger_sync():
    """Manually trigger a synchronization cycle."""
    manager = get_database_manager()
    outbox_sync = OutboxSync(manager)

    if manager.active_role != "primary":
        raise HTTPException(409, "Primary not active, cannot sync")

    result = outbox_sync.sync_once()
    return result


@router.post("/sync/reset-checkpoint", dependencies=[Depends(require_internal_key)])
def reset_sync_checkpoint():
    """Reset synchronization checkpoint (use with caution)."""
    manager = get_database_manager()

    if not manager._standby_pool:
        raise HTTPException(409, "No standby database configured")

    try:
        conn = manager._standby_pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sync_checkpoint SET last_processed_id = 0, last_processed_at = NULL, lag_seconds = NULL WHERE id = 1"
                )
            return {"success": True, "message": "Checkpoint reset"}
        finally:
            manager._standby_pool.putconn(conn)
    except Exception as e:
        raise HTTPException(500, f"Failed to reset checkpoint: {e}")