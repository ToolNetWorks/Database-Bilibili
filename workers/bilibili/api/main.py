import json
import os
import re
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from workers.bilibili.api import neon_store

app = FastAPI(
    title="Database-Bilibili API",
    version="0.1.0",
)

DATA_ROOT = Path(
    os.getenv(
        "BILIBILI_DATA_DIR",
        "/root/Database-Bilibili/data/channels",
    )
)

BVID_PATTERN = re.compile(r"BV[A-Za-z0-9]{10}")
UID_PATTERN = re.compile(r"[0-9]{1,20}")

ALLOWED_STATUSES = {
    "FREE_CANDIDATE",
    "POWER",
    "POWER_PREVIEW",
    "PAID",
    "UNKNOWN",
}


def require_api_key(x_api_key: str | None = Header(default=None)):
    configured_key = os.getenv("BILIBILI_API_KEY", "")

    if not configured_key:
        raise HTTPException(503, "API authentication not configured")

    if not x_api_key:
        raise HTTPException(401, "Missing API key")

    if not secrets.compare_digest(x_api_key, configured_key):
        raise HTTPException(401, "Invalid API key")


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []

    records = []

    with path.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            if isinstance(item, dict):
                records.append(item)

    return records



def format_duration(seconds):
    if isinstance(seconds, bool):
        return None

    if not isinstance(seconds, (int, float)) or seconds < 0:
        return None

    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)

    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def get_channel_videos(uid: str) -> list[dict]:
    if not UID_PATTERN.fullmatch(uid):
        raise HTTPException(400, "Invalid channel UID")

    if neon_store.enabled():
        return neon_store.get_channel_videos(
            uid, format_duration
        )

    folder = DATA_ROOT / uid

    source = read_jsonl(folder / "all_videos.jsonl")

    classified = read_jsonl(
        folder / "videos_classified_all.jsonl"
    )

    if not classified:
        classified = read_jsonl(
            folder / "videos_classified.jsonl"
        )

    metadata_by_id = {
        item["bvid"]: item
        for item in classified
        if isinstance(item.get("bvid"), str)
    }

    videos = []
    seen = set()

    for item in source:
        bvid = item.get("id") or item.get("bvid")

        if not isinstance(bvid, str):
            continue

        if not BVID_PATTERN.fullmatch(bvid):
            continue

        if bvid in seen:
            continue

        seen.add(bvid)

        details = metadata_by_id.get(bvid, {})

        status = details.get("status", "UNKNOWN")

        if status not in ALLOWED_STATUSES:
            status = "UNKNOWN"

        videos.append({
            "bvid": bvid,
            "url": f"https://www.bilibili.com/video/{bvid}",
            "title": details.get("title") or item.get("title"),
            "duration_seconds": details.get("duration_seconds"),
            "duration_display": format_duration(
                details.get("duration_seconds")
            ),
            "status": status,
        })

    return videos


@app.get("/health")
def health():
    return {
        "success": True,
        "service": "database-bilibili",
        "version": "0.1.0",
        "status": "ready",
    }


@app.get("/ready")
def ready():
    if not neon_store.enabled():
        raise HTTPException(503, "Database not configured")

    neon_store.check_ready()
    return {"success": True, "status": "ready"}


@app.get("/v1/channels", dependencies=[Depends(require_api_key)])
def list_channels():
    if neon_store.enabled():
        return {
            "success": True,
            "channels": neon_store.list_channels(),
        }

    if not DATA_ROOT.is_dir():
        return {"success": True, "channels": []}

    channels = []

    for folder in DATA_ROOT.iterdir():
        if not folder.is_dir():
            continue

        if not UID_PATTERN.fullmatch(folder.name):
            continue

        videos = get_channel_videos(folder.name)

        channels.append({
            "uid": folder.name,
            "total_videos": len(videos),
            "classified": sum(
                video["status"] != "UNKNOWN"
                for video in videos
            ),
        })

    return {
        "success": True,
        "channels": channels,
    }


@app.get(
    "/v1/channels/{uid}/videos",
    dependencies=[Depends(require_api_key)],
)
def list_videos(
    uid: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = None,
    access: str = "all",
):
    if access not in ("all", "free", "power", "paid"):
        raise HTTPException(400, "Invalid access filter")

    if status and status not in ALLOWED_STATUSES:
        raise HTTPException(400, "Invalid status")

    if status and access != "all":
        raise HTTPException(
            400,
            "Use either status or access filter",
        )

    all_videos = get_channel_videos(uid)

    summary = {
        "total": len(all_videos),
        "free_candidate": sum(
            v["status"] == "FREE_CANDIDATE"
            for v in all_videos
        ),
        "power": sum(
            v["status"] in ("POWER", "POWER_PREVIEW")
            for v in all_videos
        ),
        "paid": sum(
            v["status"] == "PAID"
            for v in all_videos
        ),
        "unknown": sum(
            v["status"] == "UNKNOWN"
            for v in all_videos
        ),
    }

    filters = {
        "free": {"FREE_CANDIDATE"},
        "power": {"POWER", "POWER_PREVIEW"},
        "paid": {"PAID"},
    }

    videos = all_videos

    if status:
        videos = [
            v for v in videos
            if v["status"] == status
        ]

    if access != "all":
        allowed = filters[access]
        videos = [
            v for v in videos
            if v["status"] in allowed
        ]

    start = (page - 1) * limit

    return {
        "success": True,
        "uid": uid,
        "access": access,
        "status_filter": status,
        "total": len(videos),
        "page": page,
        "limit": limit,
        "summary": summary,
        "videos": videos[start:start + limit],
    }


@app.get(
    "/v1/videos/{bvid}",
    dependencies=[Depends(require_api_key)],
)
def get_video(bvid: str):
    if not BVID_PATTERN.fullmatch(bvid):
        raise HTTPException(400, "Invalid BVID")

    if neon_store.enabled():
        video = neon_store.get_video(bvid, format_duration)

        if video is None:
            raise HTTPException(404, "Video not found")

        return {"success": True, "video": video}

    if not DATA_ROOT.is_dir():
        raise HTTPException(404, "Video not found")

    for folder in DATA_ROOT.iterdir():
        if not folder.is_dir():
            continue

        if not UID_PATTERN.fullmatch(folder.name):
            continue

        for video in get_channel_videos(folder.name):
            if video["bvid"] == bvid:
                return {"success": True, "video": video}

    raise HTTPException(404, "Video not found")
