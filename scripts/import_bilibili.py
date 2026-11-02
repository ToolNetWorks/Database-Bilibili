import json
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path("/root/Database-Bilibili")
UID = "98240066"
NAME = "嘻嘻漫场"
DATA = ROOT / "data/channels" / UID
ENV = ROOT / "secrets/neon.env"

VALID = {
    "FREE_CANDIDATE",
    "POWER",
    "POWER_PREVIEW",
    "PAID",
    "UNKNOWN",
}


def read_jsonl(path):
    if not path.is_file():
        return []

    records = []

    with path.open(encoding="utf-8") as file:
        for line in file:
            if line.strip():
                record = json.loads(line)
                if isinstance(record, dict):
                    records.append(record)

    return records


def get_database_url():
    if not ENV.is_file():
        raise RuntimeError("Missing Neon configuration")

    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.partition("=")[2].strip()

    raise RuntimeError("DATABASE_URL not found")


def to_int(value):
    if isinstance(value, bool):
        return None

    try:
        number = int(value)
        return number if number >= 0 else None
    except (ValueError, TypeError):
        return None


def build_records():
    source = read_jsonl(DATA / "all_videos.jsonl")

    if not source:
        raise RuntimeError("No channel videos found")

    metadata = {}

    for filename in (
        "videos_classified.jsonl",
        "videos_classified_all.jsonl",
    ):
        for item in read_jsonl(DATA / filename):
            bvid = item.get("bvid")

            if not isinstance(bvid, str):
                continue

            if item.get("error") or item.get("error_type"):
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

        if status not in VALID:
            status = "UNKNOWN"

        title = info.get("title") or item.get("title")

        if title in ("NA", "Không có tiêu đề", ""):
            title = None

        records[bvid] = (
            bvid,
            UID,
            f"https://www.bilibili.com/video/{bvid}",
            title,
            to_int(info.get("duration_seconds")),
            info.get("thumbnail") or info.get("pic"),
            to_int(info.get("aid")),
            to_int(info.get("cid")),
            status,
            info.get("is_upower_exclusive"),
            info.get("is_upower_preview"),
            info.get("is_upower_play"),
            Jsonb(info.get("rights") or {}),
        )

    return list(records.values())


def main():
    records = build_records()

    print("===== IMPORT DATABASE-BILIBILI =====", flush=True)
    print("CHANNEL:", UID, flush=True)
    print("VIDEO RECORDS:", len(records), flush=True)

    with psycopg.connect(
        get_database_url(),
        connect_timeout=15,
    ) as conn:

        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO bilibili_channels
                    (uid, name, source_url, total_videos)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (uid) DO UPDATE SET
                    total_videos = EXCLUDED.total_videos,
                    updated_at = NOW()
                """,
                (
                    UID,
                    NAME,
                    f"https://space.bilibili.com/{UID}",
                    len(records),
                ),
            )

            cursor.executemany(
                """
                INSERT INTO bilibili_videos (
                    bvid, channel_uid, url, title,
                    duration_seconds, thumbnail,
                    aid, cid, status,
                    is_upower_exclusive,
                    is_upower_preview,
                    is_upower_play, rights
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
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
                """,
                records,
            )

    print("===== VERIFY NEON =====", flush=True)

    with psycopg.connect(
        get_database_url(),
        connect_timeout=15,
    ) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT status, COUNT(*)
                FROM bilibili_videos
                WHERE channel_uid = %s
                GROUP BY status
                """,
                (UID,),
            )
            counts = Counter(dict(cursor.fetchall()))

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM bilibili_videos
                WHERE channel_uid = %s
                  AND title IS NOT NULL
                  AND duration_seconds IS NOT NULL
                """,
                (UID,),
            )
            complete = cursor.fetchone()[0]

    print("TOTAL:", sum(counts.values()))
    print("FREE:", counts["FREE_CANDIDATE"])
    print(
        "POWER:",
        counts["POWER"] + counts["POWER_PREVIEW"]
    )
    print("PAID:", counts["PAID"])
    print("UNKNOWN:", counts["UNKNOWN"])
    print("METADATA COMPLETE:", complete)
    print("DATABASE IMPORT COMPLETED", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        RuntimeError,
        psycopg.Error,
    ) as error:
        print(
            "IMPORT FAILED:",
            type(error).__name__,
            flush=True,
        )
        sys.exit(1)
