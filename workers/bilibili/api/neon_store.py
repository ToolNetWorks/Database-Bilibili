import os
import json
from typing import List, Dict, Optional, Any

from fastapi import HTTPException
import psycopg
from psycopg.rows import dict_row

RECOVERY_SINGLE_DB = os.getenv("RECOVERY_SINGLE_DB", "false").lower() == "true"


def enabled() -> bool:
    if RECOVERY_SINGLE_DB:
        return bool(os.getenv("DATABASE_URL", "").strip())
    primary_url = os.getenv("DATABASE_PRIMARY_URL", "").strip()
    fallback_url = os.getenv("DATABASE_URL", "").strip()
    return bool(primary_url or fallback_url)


def _execute_query(sql: str, params: tuple = ()) -> List[Dict]:
    if RECOVERY_SINGLE_DB:
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            raise HTTPException(503, "Database not configured")
        try:
            with psycopg.connect(url, connect_timeout=10, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()
        except psycopg.Error as e:
            raise HTTPException(503, f"Database temporarily unavailable: {e}") from None
    else:
        from shared.database.manager import get_database_manager
        manager = get_database_manager()
        try:
            with manager.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()
        except Exception as e:
            raise HTTPException(503, f"Database temporarily unavailable: {e}") from None


def _execute_write(sql: str, params: tuple = ()) -> None:
    if RECOVERY_SINGLE_DB:
        url = os.getenv("DATABASE_URL", "").strip()
        if not url:
            raise HTTPException(503, "Database not configured")
        try:
            with psycopg.connect(url, connect_timeout=10, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                conn.commit()
        except psycopg.Error as e:
            raise HTTPException(503, f"Database temporarily unavailable: {e}") from None
    else:
        from shared.database.manager import get_database_manager
        manager = get_database_manager()
        try:
            with manager.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
        except Exception as e:
            raise HTTPException(503, f"Database temporarily unavailable: {e}") from None


def check_ready() -> None:
    _execute_query("SELECT 1 FROM bilibili_channels LIMIT 1")
    _execute_query("SELECT 1 FROM bilibili_videos LIMIT 1")


def list_channels() -> List[Dict]:
    return _execute_query("""
        SELECT c.uid, c.name, c.source_url, c.total_videos, c.last_scan_at,
               COUNT(v.bvid)::int AS stored_videos
        FROM bilibili_channels c
        LEFT JOIN bilibili_videos v ON v.channel_uid = c.uid
        GROUP BY c.uid, c.name, c.source_url, c.total_videos, c.last_scan_at
        ORDER BY c.uid
    """)


def normalize_video(row: Dict, format_duration) -> Dict:
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
    rows = _execute_query(
        "SELECT " + VIDEO_FIELDS + " FROM bilibili_videos WHERE channel_uid = %s ORDER BY published_at DESC NULLS LAST, created_at DESC, bvid LIMIT %s OFFSET %s",
        (uid, limit, offset),
    ) if False else _execute_query(
        "SELECT " + VIDEO_FIELDS + " FROM bilibili_videos WHERE channel_uid = %s ORDER BY published_at DESC NULLS LAST, created_at DESC, bvid LIMIT %s OFFSET %s".replace("bilibili", "bilibili"),
        (uid, limit, offset),
    )
    return [normalize_video(row, format_duration) for row in rows]


def get_channel_video_count(uid: str) -> int:
    rows = _execute_query("SELECT COUNT(*) as cnt FROM bilibili_videos WHERE channel_uid = %s".replace("bilibili", "bilibili"), (uid,))
    return rows[0]["cnt"] if rows else 0


def get_video(bvid: str, format_duration) -> Optional[Dict]:
    rows = _execute_query("SELECT " + VIDEO_FIELDS + " FROM bilibili_videos WHERE bvid = %s LIMIT 1".replace("bilibili", "bilibili"), (bvid,))
    if not rows:
        return None
    return normalize_video(rows[0], format_duration)


def get_videos_batch(bvids: List[str], format_duration) -> List[Dict]:
    if not bvids:
        return []
    placeholders = ", ".join(["%s"] * len(bvids))
    rows = _execute_query("SELECT " + VIDEO_FIELDS + " FROM bilibili_videos WHERE bvid IN (" + placeholders + ")".replace("bilibili", "bilibili"), tuple(bvids))
    return [normalize_video(row, format_duration) for row in rows]


def upsert_channel(uid: str, name, source_url: str, total_videos: int) -> None:
    _execute_write("""
        INSERT INTO bilibili_channels (uid, name, source_url, total_videos, last_scan_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (uid) DO UPDATE SET
            name = COALESCE(EXCLUDED.name, bilibili_channels.name),
            source_url = EXCLUDED.source_url,
            total_videos = EXCLUDED.total_videos,
            last_scan_at = NOW(),
            updated_at = NOW()
    """.replace("bilibili", "bilibili"), (uid, name, source_url, total_videos))


def upsert_video(video: Dict[str, Any]) -> None:
    import json
    rights = video.get("rights", {})
    if not isinstance(rights, dict):
        rights = {}
    _execute_write("""
        INSERT INTO bilibili_videos (
            bvid, channel_uid, url, title, thumbnail,
            description, duration_seconds, published_at,
            views, aid, cid, status,
            is_upower_exclusive, is_upower_preview, is_upower_play,
            rights
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (bvid) DO UPDATE SET
            title = COALESCE(EXCLUDED.title, bilibili_videos.title),
            thumbnail = COALESCE(EXCLUDED.thumbnail, bilibili_videos.thumbnail),
            description = COALESCE(EXCLUDED.description, bilibili_videos.description),
            duration_seconds = COALESCE(EXCLUDED.duration_seconds, bilibili_videos.duration_seconds),
            published_at = COALESCE(EXCLUDED.published_at, bilibili_videos.published_at),
            views = COALESCE(EXCLUDED.views, bilibili_videos.views),
            aid = COALESCE(EXCLUDED.aid, bilibili_videos.aid),
            cid = COALESCE(EXCLUDED.cid, bilibili_videos.cid),
            status = CASE WHEN EXCLUDED.status = 'UNKNOWN' THEN bilibili_videos.status ELSE EXCLUDED.status END,
            is_upower_exclusive = COALESCE(EXCLUDED.is_upower_exclusive, bilibili_videos.is_upower_exclusive),
            is_upower_preview = COALESCE(EXCLUDED.is_upower_preview, bilibili_videos.is_upower_preview),
            is_upower_play = COALESCE(EXCLUDED.is_upower_play, bilibili_videos.is_upower_play),
            rights = CASE WHEN EXCLUDED.rights = '{}'::jsonb THEN bilibili_videos.rights ELSE EXCLUDED.rights END,
            updated_at = NOW()
    """.replace("bilibili", "bilibili"), (
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
    ))
