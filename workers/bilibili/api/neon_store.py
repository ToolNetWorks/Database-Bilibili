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


def _execute_write(sql: str, params: tuple = ()) -> None:
    """Execute a write query using the Database Manager."""
    manager = get_database_manager()

    try:
        with manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
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
            c.source_url,
            c.total_videos,
            c.last_scan_at,
            COUNT(v.bvid)::int AS stored_videos
        FROM bilibili_channels c
        LEFT JOIN bilibili_videos v
            ON v.channel_uid = c.uid
        GROUP BY c.uid, c.name, c.source_url, c.total_videos, c.last_scan_at
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
        "thumbnail": row.get("thumbnail"),
        "description": row.get("description"),
        "duration_seconds": seconds,
        "duration_display": format_duration(seconds),
        "published_at": row.get("published_at"),
        "views": row.get("views"),
        "aid": row.get("aid"),
        "cid": row.get("cid"),
        "status": row["status"],
    }


VIDEO_FIELDS = """
    bvid, channel_uid, url, title, thumbnail,
    description, aid, cid, duration_seconds, status,
    published_at, views
"""


def get_channel_videos(uid: str, format_duration, limit: int = 100, offset: int = 0) -> List[Dict]:
    """Get videos for a channel with pagination."""
    rows = _execute_query(
        f"""
        SELECT {VIDEO_FIELDS}
        FROM bilibili_videos
        WHERE channel_uid = %s
        ORDER BY published_at DESC NULLS LAST, created_at DESC, bvid
        LIMIT %s OFFSET %s
        """,
        (uid, limit, offset),
    )

    return [
        normalize_video(row, format_duration)
        for row in rows
    ]


def get_channel_video_count(uid: str) -> int:
    """Get total video count for a channel."""
    rows = _execute_query(
        "SELECT COUNT(*) as cnt FROM bilibili_videos WHERE channel_uid = %s",
        (uid,),
    )
    return rows[0]["cnt"] if rows else 0


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


def get_videos_batch(bvids: List[str], format_duration) -> List[Dict]:
    """Get multiple videos by BVID list."""
    if not bvids:
        return []

    placeholders = ", ".join(["%s"] * len(bvids))
    rows = _execute_query(
        f"""
        SELECT {VIDEO_FIELDS}
        FROM bilibili_videos
        WHERE bvid IN ({placeholders})
        """,
        tuple(bvids),
    )

    return [
        normalize_video(row, format_duration)
        for row in rows
    ]


def upsert_channel(uid: str, name: Optional[str], source_url: str, total_videos: int) -> None:
    """Insert or update a channel record."""
    _execute_write("""
        INSERT INTO bilibili_channels (uid, name, source_url, total_videos, last_scan_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (uid) DO UPDATE SET
            name = COALESCE(EXCLUDED.name, bilibili_channels.name),
            source_url = EXCLUDED.source_url,
            total_videos = EXCLUDED.total_videos,
            last_scan_at = NOW(),
            updated_at = NOW()
    """, (uid, name, source_url, total_videos))


def upsert_video(video):
    """Insert or update a video record without overwriting good data with nulls."""
    import json
    rights = video.get("rights", {})
    if not isinstance(rights, dict):
        rights = {}

    _execute_write(
        "INSERT INTO bilibili_videos ("
        "bvid, channel_uid, url, title, thumbnail, "
        "description, duration_seconds, published_at, "
        "views, aid, cid, status, "
        "is_upower_exclusive, is_upower_preview, is_upower_play, "
        "rights) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (bvid) DO UPDATE SET "
        "title = COALESCE(EXCLUDED.title, bilibili_videos.title), "
        "thumbnail = COALESCE(EXCLUDED.thumbnail, bilibili_videos.thumbnail), "
        "description = COALESCE(EXCLUDED.description, bilibili_videos.description), "
        "duration_seconds = COALESCE(EXCLUDED.duration_seconds, bilibili_videos.duration_seconds), "
        "published_at = COALESCE(EXCLUDED.published_at, bilibili_videos.published_at), "
        "views = COALESCE(EXCLUDED.views, bilibili_videos.views), "
        "aid = COALESCE(EXCLUDED.aid, bilibili_videos.aid), "
        "cid = COALESCE(EXCLUDED.cid, bilibili_videos.cid), "
        "status = CASE WHEN EXCLUDED.status = 'UNKNOWN' THEN bilibili_videos.status ELSE EXCLUDED.status END, "
        "is_upower_exclusive = COALESCE(EXCLUDED.is_upower_exclusive, bilibili_videos.is_upower_exclusive), "
        "is_upower_preview = COALESCE(EXCLUDED.is_upower_preview, bilibili_videos.is_upower_preview), "
        "is_upower_play = COALESCE(EXCLUDED.is_upower_play, bilibili_videos.is_upower_play), "
        "rights = CASE WHEN EXCLUDED.rights = '{}'::jsonb THEN bilibili_videos.rights ELSE EXCLUDED.rights END, "
        "updated_at = NOW()",
        (
            video["bvid"],
            video["channel_uid"],
            video["url"],
            video.get("title"),
            video.get("thumbnail"),
            video.get("description"),
            video.get("duration_seconds"),
            video.get("published_at"),
            video.get("views"),
            video.get("aid"),
            video.get("cid"),
            video.get("status", "UNKNOWN"),
            video.get("is_upower_exclusive"),
            video.get("is_upower_preview"),
            video.get("is_upower_play"),
            json.dumps(rights),
        ),
    )


