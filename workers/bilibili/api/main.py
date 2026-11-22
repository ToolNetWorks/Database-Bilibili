import json
import os
import re
import secrets
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from workers.bilibili.api import neon_store
from workers.bilibili.api.bilibili_client import BilibiliClient
import os
RECOVERY_SINGLE_DB = os.getenv("RECOVERY_SINGLE_DB", "false").lower() == "true"

app = FastAPI(
    title="Database-Bilibili API",
    version="0.1.0",
)

if not RECOVERY_SINGLE_DB:
    from shared.database.endpoints import router as database_internal_router
    app.include_router(database_internal_router)

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

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

bilibili_client = BilibiliClient()


def require_api_key(x_api_key: str | None = Security(api_key_header)):
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
        return neon_store.get_channel_videos(uid, format_duration)

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
            "channel_uid": uid,
            "url": f"https://www.bilibili.com/video/{bvid}",
            "title": details.get("title") or item.get("title"),
            "thumbnail": details.get("thumbnail"),
            "description": details.get("description"),
            "duration_seconds": details.get("duration_seconds"),
            "duration_display": format_duration(
                details.get("duration_seconds")
            ),
            "published_at": details.get("published_at"),
            "views": details.get("views"),
            "aid": details.get("aid"),
            "cid": details.get("cid"),
            "status": status,
        })

    return videos


@app.get("/health")
async def health():
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


# --- Channel Resolution ---

@app.get("/v1/channels/resolve", dependencies=[Depends(require_api_key)])
def resolve_channel(url: str = Query(..., description="Channel URL or UID")):
    """Resolve channel UID from URL or UID."""
    uid = bilibili_client.resolve_channel_uid(url)

    if not uid:
        raise HTTPException(400, "Could not resolve channel UID from input")

    # Verify channel exists
    info = bilibili_client.get_channel_info(uid)
    if not info or not info.get("name"):
        raise HTTPException(404, "Channel not found or inaccessible")

    return {
        "success": True,
        "channel_uid": uid,
        "channel_url": f"https://space.bilibili.com/{uid}",
        "name": info.get("name"),
    }


# --- Channel List ---

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
            "name": folder.name,
            "source_url": f"https://space.bilibili.com/{folder.name}",
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


# --- Channel Videos ---

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
    if not UID_PATTERN.fullmatch(uid):
        raise HTTPException(400, "Invalid channel UID")

    if access not in ("all", "free", "power", "paid"):
        raise HTTPException(400, "Invalid access filter")

    if status and status not in ALLOWED_STATUSES:
        raise HTTPException(400, "Invalid status")

    if status and access != "all":
        raise HTTPException(
            400,
            "Use either status or access filter",
        )

    offset = (page - 1) * limit

    if neon_store.enabled():
        all_videos = neon_store.get_channel_videos(uid, format_duration, limit=1000, offset=0)
        total_stored = len(all_videos)

        # Apply filters
        filtered = all_videos
        if status:
            filtered = [v for v in filtered if v["status"] == status]
        if access != "all":
            filters = {
                "free": {"FREE_CANDIDATE"},
                "power": {"POWER", "POWER_PREVIEW"},
                "paid": {"PAID"},
            }
            allowed = filters[access]
            filtered = [v for v in filtered if v["status"] in allowed]

        paginated = filtered[offset:offset + limit]

        return {
            "success": True,
            "uid": uid,
            "access": access,
            "status_filter": status,
            "total": len(filtered),
            "page": page,
            "limit": limit,
            "summary": {
                "total": total_stored,
                "free_candidate": sum(v["status"] == "FREE_CANDIDATE" for v in all_videos),
                "power": sum(v["status"] in ("POWER", "POWER_PREVIEW") for v in all_videos),
                "paid": sum(v["status"] == "PAID" for v in all_videos),
                "unknown": sum(v["status"] == "UNKNOWN" for v in all_videos),
            },
            "videos": paginated,
        }

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
    total = len(videos)

    return {
        "success": True,
        "uid": uid,
        "access": access,
        "status_filter": status,
        "total": total,
        "page": page,
        "limit": limit,
        "summary": summary,
        "videos": videos[start:start + limit],
    }


# --- Single Video ---

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


# --- Batch Video Query ---

@app.post("/v1/videos/batch", dependencies=[Depends(require_api_key)])
def get_videos_batch(bvids: list[str]):
    """Get metadata for multiple videos."""
    if not bvids:
        raise HTTPException(400, "No BVIDs provided")

    if len(bvids) > 50:
        raise HTTPException(400, "Maximum 50 BVIDs per request")

    for bvid in bvids:
        if not BVID_PATTERN.fullmatch(bvid):
            raise HTTPException(400, f"Invalid BVID: {bvid}")

    if neon_store.enabled():
        videos = neon_store.get_videos_batch(bvids, format_duration)
        return {"success": True, "videos": videos}

    # Fallback to file-based
    all_videos = {}
    if DATA_ROOT.is_dir():
        for folder in DATA_ROOT.iterdir():
            if not folder.is_dir() or not UID_PATTERN.fullmatch(folder.name):
                continue
            for video in get_channel_videos(folder.name):
                all_videos[video["bvid"]] = video

    videos = [all_videos[bvid] for bvid in bvids if bvid in all_videos]
    return {"success": True, "videos": videos}


# --- Channel Scan (fetch from Bilibili directly) ---

@app.post("/v1/channels/scan", dependencies=[Depends(require_api_key)])
def scan_channel(
    uid: str = Query(..., description="Channel UID"),
    max_pages: int = Query(5, ge=1, le=20, description="Max pages to fetch"),
    fetch_metadata: bool = Query(True, description="Fetch detailed metadata for each video"),
):
    """Scan a channel: fetch video list and metadata directly from Bilibili."""
    if not UID_PATTERN.fullmatch(uid):
        raise HTTPException(400, "Invalid channel UID")

    # Get channel info
    channel_info = bilibili_client.get_channel_info(uid)
    if not channel_info or not channel_info.get("name"):
        raise HTTPException(404, "Channel not found on Bilibili")

    # Get all videos with metadata from arc/search
    all_videos = bilibili_client.get_all_channel_videos(uid, max_pages=max_pages)

    if not all_videos:
        return {
            "success": True,
            "uid": uid,
            "name": channel_info.get("name"),
            "total_videos": 0,
            "videos": [],
            "message": "No videos found",
        }

    videos = []
    seen = set()

    for info in all_videos:
        bvid = info["bvid"]
        if bvid in seen:
            continue
        seen.add(bvid)

        # Classify status using rights
        status = "UNKNOWN"
        # arc/search doesn't include rights, so we'll classify based on available data
        # is_pay field indicates paid content
        if info.get("is_pay"):
            status = "PAID"

        video = {
            "bvid": bvid,
            "channel_uid": uid,
            "url": f"https://www.bilibili.com/video/{bvid}",
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "description": info.get("description"),
            "duration_seconds": info.get("duration_seconds"),
            "duration_display": format_duration(info.get("duration_seconds")),
            "published_at": info.get("published_at"),
            "views": info.get("views"),
            "aid": info.get("aid"),
            "cid": None,
            "status": status,
        }
        videos.append(video)

        # Store in database if available
        if neon_store.enabled():
            try:
                neon_store.upsert_channel(uid, channel_info.get("name"), f"https://space.bilibili.com/{uid}", len(all_videos))
                neon_store.upsert_video(video)
            except Exception:
                pass  # Don't fail scan if DB write fails

    # Update channel in DB
    if neon_store.enabled():
        try:
            neon_store.upsert_channel(uid, channel_info.get("name"), f"https://space.bilibili.com/{uid}", len(all_videos))
        except Exception:
            pass

    return {
        "success": True,
        "uid": uid,
        "name": channel_info.get("name"),
        "total_videos": len(videos),
        "videos": videos,
    }


# Public docs landing page and authenticated read-only POST endpoint.
from workers.bilibili.api.docs_api import install_docs_api
install_docs_api(app, require_api_key, neon_store, format_duration)
