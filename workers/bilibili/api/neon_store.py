import os

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row


def enabled() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        raise HTTPException(503, "Database not configured")

    try:
        with psycopg.connect(
            database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
    except psycopg.Error:
        raise HTTPException(
            503, "Database temporarily unavailable"
        ) from None


def check_ready() -> None:
    fetch_all("SELECT 1 FROM bilibili_channels LIMIT 1")
    fetch_all("SELECT 1 FROM bilibili_videos LIMIT 1")


def list_channels() -> list[dict]:
    return fetch_all("""
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


def normalize_video(row: dict, format_duration) -> dict:
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


def get_channel_videos(uid: str, format_duration) -> list[dict]:
    rows = fetch_all(
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


def get_video(bvid: str, format_duration) -> dict | None:
    rows = fetch_all(
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
