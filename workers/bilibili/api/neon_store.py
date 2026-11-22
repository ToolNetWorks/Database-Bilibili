"""
Neon Database Store - Updated to use Database Manager.

Provides database access through the centralized Database Manager
with automatic failover and connection pooling.
"""

import os
from typing import List, Dict, Optional

from fastapi import HTTPException
from shared.database.manager import get_database_manager


def enabled() -> bool:
    """Check if database is configured."""
    primary_url = os.getenv("DATABASE_PRIMARY_URL", "").strip()
    fallback_url = os.getenv("DATABASE_URL", "").strip()
    return bool(primary_url or fallback_url)


def _execute_query(sql: str, params: tuple = ()) -> List[Dict]:
    """Execute a query using the Database Manager."""
    manager = get_database_manager()

    try:
        with manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
    except Exception as e:
        raise HTTPException(503, f"Database temporarily unavailable: {e}") from None


def check_ready() -> None:
    """Check if database is ready for queries."""
    _execute_query("SELECT 1 FROM bilibili_channels LIMIT 1")
    _execute_query("SELECT 1 FROM bilibili_videos LIMIT 1")


def list_channels() -> List[Dict]:
    """List all channels with video counts."""
    return _execute_query("""
        SELECT
            c.uid,
            c.name,
            COUNT(v.bvid)::int AS total_videos,
            COUNT(v.bvid) FILTER (
                WHERE v.status <> 'UNKNOWN'
            )::int AS classified
        FROM bilibili_channels c
        LEFT JOIN bilibili_videos v
            ON v.channel_uid = c.uid
        GROUP BY c.uid, c.name
        ORDER BY c.uid
    """)


def normalize_video(row: Dict, format_duration) -> Dict:
    """Normalize video row to API format."""
    seconds = row.get("duration_seconds")

    return {
        "bvid": row["bvid"],
        "channel_uid": row["channel_uid"],
        "url": row["url"],
        "title": row["title"],
        "thumbnail": row["thumbnail"],
        "aid": row["aid"],
        "cid": row["cid"],
        "duration_seconds": seconds,
        "duration_display": format_duration(seconds),
        "status": row["status"],
    }


VIDEO_FIELDS = """
    bvid, channel_uid, url, title, thumbnail,
    aid, cid, duration_seconds, status
"""


def get_channel_videos(uid: str, format_duration) -> List[Dict]:
    """Get all videos for a channel."""
    rows = _execute_query(
        f"""
        SELECT {VIDEO_FIELDS}
        FROM bilibili_videos
        WHERE channel_uid = %s
        ORDER BY created_at, bvid
        """,
        (uid,),
    )

    return [
        normalize_video(row, format_duration)
        for row in rows
    ]


def get_video(bvid: str, format_duration) -> Optional[Dict]:
    """Get a single video by BVID."""
    rows = _execute_query(
        f"""
        SELECT {VIDEO_FIELDS}
        FROM bilibili_videos
        WHERE bvid = %s
        LIMIT 1
        """,
        (bvid,),
    )

    if not rows:
        return None

    return normalize_video(rows[0], format_duration)