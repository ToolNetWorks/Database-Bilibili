import json
import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

ROOT = Path("/root/Database-Bilibili")
UID = "98240066"
DATA_DIR = ROOT / "data/channels" / UID

VALID_STATUSES = {
    "FREE_CANDIDATE",
    "POWER",
    "POWER_PREVIEW",
    "PAID",
    "UNKNOWN",
}


def read_jsonl(path):
    if not path.is_file():
        return []

    with path.open(encoding="utf-8") as file:
        return [
            json.loads(line)
            for line in file
            if line.strip()
        ]


def to_integer(value):
    if isinstance(value, bool) or value is None:
        return None

    try:
        number = int(value)
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def to_boolean(value):
    if isinstance(value, bool):
        return value

    if value in (0, 1):
        return bool(value)

    return None


def build_records():
    source = read_jsonl(DATA_DIR / "all_videos.jsonl")

    if not source:
        raise RuntimeError("Missing Bilibili video list")

    metadata = {}

    for filename in (
        "videos_classified.jsonl",
        "videos_classified_all.jsonl",
    ):
        for item in read_jsonl(DATA_DIR / filename):
            bvid = item.get("bvid")

            if not bvid or item.get("error"):
                continue

            if item.get("error_type"):
                continue

            metadata[bvid] = item

    records = {}

    for item in source:
        bvid = item.get("id") or item.get("bvid")

        if not isinstance(bvid, str):
            continue

        if not re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid):
            continue

        info = metadata.get(bvid, {})

        status = info.get("status", "UNKNOWN")

        if status not in VALID_STATUSES:
            status = "UNKNOWN"

        title = info.get("title") or item.get("title")

        if title in ("NA", "Không có tiêu đề", ""):
            title = None

        rights = info.get("rights")
        if not isinstance(rights, dict):
            rights = {}

        records[bvid] = {
            "bvid": bvid,
            "channel_uid": UID,
            "url": f"https://www.bilibili.com/video/{bvid}",
            "title": title,
            "duration_seconds": to_integer(
                info.get("duration_seconds")
            ),
            "thumbnail": info.get("thumbnail") or info.get("pic"),
            "aid": to_integer(info.get("aid")),
            "cid": to_integer(info.get("cid")),
            "status": status,
            "is_upower_exclusive": to_boolean(
                info.get("is_upower_exclusive")
            ),
            "is_upower_preview": to_boolean(
                info.get("is_upower_preview")
            ),
            "is_upower_play": to_boolean(
                info.get("is_upower_play")
            ),
            "rights": rights,
        }

    return list(records.values())


def main():
    load_dotenv(ROOT / ".env", override=True)

    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        raise RuntimeError("DATABASE_URL missing from .env")

    records = build_records()

    print("===== DATABASE-BILIBILI =====", flush=True)
    print("CHANNEL:", UID, flush=True)
    print("SOURCE VIDEOS:", len(records), flush=True)

    if not records:
        raise RuntimeError("No video records found")

    with psycopg.connect(
        database_url,
        connect_timeout=15,
    ) as conn:
        with conn.cursor() as cursor:

            cursor.execute("""
                INSERT INTO bilibili_channels (
                    uid, name, source_url, total_videos
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (uid) DO UPDATE SET
                    name = EXCLUDED.name,
                    total_videos = EXCLUDED.total_videos,
                    updated_at = NOW()
            """, (
                UID,
                "嘻嘻漫场",
                f"https://space.bilibili.com/{UID}",
                len(records),
            ))

            cursor.execute("""
                INSERT INTO bilibili_videos (
                    bvid, channel_uid, url, title,
                    duration_seconds, thumbnail,
                    aid, cid, status,
                    is_upower_exclusive,
                    is_upower_preview,
                    is_upower_play, rights
                )
                SELECT
                    x.bvid,
                    x.channel_uid,
                    x.url,
                    x.title,
                    x.duration_seconds,
                    x.thumbnail,
                    x.aid,
                    x.cid,
                    x.status,
                    x.is_upower_exclusive,
                    x.is_upower_preview,
                    x.is_upower_play,
                    x.rights
                FROM jsonb_to_recordset(%s::jsonb) AS x(
                    bvid text,
                    channel_uid text,
                    url text,
                    title text,
                    duration_seconds integer,
                    thumbnail text,
                    aid bigint,
                    cid bigint,
                    status text,
                    is_upower_exclusive boolean,
                    is_upower_preview boolean,
                    is_upower_play boolean,
                    rights jsonb
                )
                ON CONFLICT (bvid) DO UPDATE SET
                    title = COALESCE(
                        EXCLUDED.title,
                        bilibili_videos.title
                    ),
                    duration_seconds = COALESCE(
                        EXCLUDED.duration_seconds,
                        bilibili_videos.duration_seconds
                    ),
                    thumbnail = COALESCE(
                        EXCLUDED.thumbnail,
                        bilibili_videos.thumbnail
                    ),
                    status = CASE
                        WHEN EXCLUDED.status = 'UNKNOWN'
                        THEN bilibili_videos.status
                        ELSE EXCLUDED.status
                    END,
                    updated_at = NOW()
            """, (Jsonb(records),))

        conn.commit()

        print("DATA COMMITTED", flush=True)

        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (
                        WHERE status = 'FREE_CANDIDATE'
                    ),
                    COUNT(*) FILTER (
                        WHERE status IN ('POWER', 'POWER_PREVIEW')
                    ),
                    COUNT(*) FILTER (
                        WHERE status = 'UNKNOWN'
                    ),
                    COUNT(*) FILTER (
                        WHERE title IS NOT NULL
                        AND duration_seconds IS NOT NULL
                    )
                FROM bilibili_videos
                WHERE channel_uid = %s
            """, (UID,))

            total, free, power, unknown, complete = (
                cursor.fetchone()
            )

    print("===== NEON SINGAPORE RESULT =====")
    print("TOTAL:", total)
    print("FREE:", free)
    print("POWER:", power)
    print("UNKNOWN:", unknown)
    print("METADATA COMPLETE:", complete)

    if total != len(records):
        raise RuntimeError("Database count mismatch")

    print("NEON SINGAPORE IMPORT COMPLETED")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "IMPORT FAILED:",
            type(exc).__name__,
            str(exc).splitlines()[0][:250],
            flush=True,
        )
        sys.exit(1)
