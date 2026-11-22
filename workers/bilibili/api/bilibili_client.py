"""
Bilibili API Client - Fetch channel and video metadata without downloading media.

Uses Bilibili's public web APIs to retrieve channel info, video lists,
and video metadata. No media files are downloaded.
"""

import json
import time
import urllib.request
import urllib.error
import re
from typing import Optional, Dict, List, Any

BILIBILI_API_BASE = "https://api.bilibili.com"

DESKTOP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Referer": "https://m.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

DEFAULT_HEADERS = MOBILE_HEADERS

UID_PATTERN = re.compile(r"^[0-9]{1,20}$")
BVID_PATTERN = re.compile(r"^BV[A-Za-z0-9]{10}$")


class BilibiliClient:
    """Client for Bilibili public APIs."""

    def __init__(self, timeout: int = 15, max_retries: int = 3, retry_delay: float = 1.0):
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def _request(self, url: str, headers: dict = None) -> Optional[Dict[str, Any]]:
        """Make HTTP GET request with retry logic."""
        if headers is None:
            headers = DEFAULT_HEADERS
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    if data.get("code") == 0:
                        return data.get("data")
                    return None
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                continue
        return None

    def resolve_channel_uid(self, url_or_uid: str) -> Optional[str]:
        """Resolve channel UID from URL or UID string."""
        url_or_uid = url_or_uid.strip()

        # Direct UID
        if UID_PATTERN.fullmatch(url_or_uid):
            return url_or_uid

        # Extract from URL
        patterns = [
            r"space\.bilibili\.com/(\d+)",
            r"b23\.tv/.*",
        ]

        for pattern in patterns:
            match = re.search(pattern, url_or_uid)
            if match:
                return match.group(1) if match.lastindex else None

        # Try to resolve b23.tv short URL
        if "b23.tv" in url_or_uid:
            try:
                req = urllib.request.Request(url_or_uid, headers=DEFAULT_HEADERS, method="HEAD")
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    final_url = response.geturl()
                    match = re.search(r"space\.bilibili\.com/(\d+)", final_url)
                    if match:
                        return match.group(1)
            except Exception:
                pass

        return None

    def get_channel_info(self, uid: str) -> Optional[Dict[str, Any]]:
        """Get channel information by UID."""
        if not UID_PATTERN.fullmatch(uid):
            return None

        # Try acc/info with desktop UA first
        url = f"{BILIBILI_API_BASE}/x/space/acc/info?mid={uid}"
        data = self._request(url, headers=DESKTOP_HEADERS)

        if data and data.get("name"):
            return {
                "uid": uid,
                "name": data.get("name"),
                "source_url": f"https://space.bilibili.com/{uid}",
                "face": data.get("face"),
                "sign": data.get("sign"),
                "level": data.get("level"),
            }

        # Fallback: get channel name from arc/search
        search_result = self.get_channel_videos(uid, page=1, page_size=1)
        if search_result.get("total", 0) > 0:
            # We got results, channel exists
            # Try to get name from first video's author
            # Re-fetch with full data
            url2 = f"{BILIBILI_API_BASE}/x/space/arc/search?mid={uid}&ps=1&tid=0&pn=1&keyword=&order=pubdate&platform=web"
            data2 = self._request(url2, headers=MOBILE_HEADERS)
            name = None
            if data2:
                vl = data2.get("list", {}).get("vlist", [])
                if vl:
                    name = vl[0].get("author")

            return {
                "uid": uid,
                "name": name or f"Channel {uid}",
                "source_url": f"https://space.bilibili.com/{uid}",
                "face": None,
                "sign": None,
                "level": None,
            }

        return None

    def get_channel_videos(
        self,
        uid: str,
        page: int = 1,
        page_size: int = 30,
        order: str = "pubdate",
    ) -> Dict[str, Any]:
        """Get paginated video list for a channel.

        Returns dict with 'videos' (list of bvid) and 'total' (int).
        """
        if not UID_PATTERN.fullmatch(uid):
            return {"videos": [], "total": 0}

        url = (
            f"{BILIBILI_API_BASE}/x/space/arc/search"
            f"?mid={uid}&ps={page_size}&tid=0&pn={page}"
            f"&keyword=&order={order}&platform=web"
        )

        data = self._request(url, headers=MOBILE_HEADERS)

        if not data:
            return {"videos": [], "total": 0}

        video_list = data.get("list", {}).get("vlist", [])
        total = data.get("page", {}).get("count", 0)

        videos = []
        seen = set()
        for video in video_list:
            bvid = video.get("bvid")
            if not bvid or bvid in seen:
                continue
            seen.add(bvid)

            # Parse duration from "MM:SS" or "HH:MM:SS" format
            duration_seconds = None
            length = video.get("length", "")
            if length:
                parts = length.split(":")
                try:
                    if len(parts) == 2:
                        duration_seconds = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        duration_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                except (ValueError, IndexError):
                    pass

            videos.append({
                "bvid": bvid,
                "aid": video.get("aid"),
                "title": video.get("title"),
                "thumbnail": video.get("pic"),
                "description": video.get("description"),
                "duration_seconds": duration_seconds,
                "published_at": video.get("created"),
                "views": video.get("play"),
                "channel_uid": str(video.get("mid", "")),
                "channel_name": video.get("author"),
                "url": f"https://www.bilibili.com/video/{bvid}",
            })

        return {"videos": videos, "total": total, "bvids": [v["bvid"] for v in videos]}

    def get_all_channel_videos(
        self,
        uid: str,
        page_size: int = 30,
        max_pages: int = 100,
        fetch_metadata: bool = True,
    ) -> List[Dict[str, Any]]:
        """Get all videos for a channel by paginating. Returns full metadata."""
        all_videos = []
        seen = set()
        page = 1

        while page <= max_pages:
            result = self.get_channel_videos(uid, page=page, page_size=page_size)
            videos = result.get("videos", [])

            if not videos:
                break

            for video in videos:
                bvid = video["bvid"]
                if bvid not in seen:
                    seen.add(bvid)
                    all_videos.append(video)

            total = result.get("total", 0)
            if len(all_videos) >= total:
                break

            page += 1
            time.sleep(0.5)  # Rate limiting

        return all_videos

    def _classify_status(self, data: dict, rights: dict) -> str:
        """Classify video access status using existing logic."""
        def is_enabled(value):
            return value is True or value == 1 or value == "1"

        if is_enabled(data.get("is_upower_exclusive")):
            return "POWER"
        if is_enabled(rights.get("ugc_pay")):
            return "PAID"
        if is_enabled(rights.get("pay")):
            return "PAID"
        if is_enabled(data.get("is_chargeable_season")):
            return "PAID"
        if (
            "is_upower_exclusive" in data
            and isinstance(rights, dict)
            and rights
        ):
            return "FREE_CANDIDATE"
        return "UNKNOWN"

    def get_video_info(self, bvid: str) -> Optional[Dict[str, Any]]:
        """Get detailed metadata for a single video."""
        if not BVID_PATTERN.fullmatch(bvid):
            return None

        url = f"{BILIBILI_API_BASE}/x/web-interface/view?bvid={bvid}"
        data = self._request(url, headers=DESKTOP_HEADERS)

        if not data:
            return None

        # Extract relevant fields
        owner = data.get("owner", {})
        stat = data.get("stat", {})
        pages = data.get("pages", [])
        rights = data.get("rights") or {}

        cid = None
        if pages:
            cid = pages[0].get("cid")

        # Classify using existing logic
        status = self._classify_status(data, rights)

        return {
            "bvid": bvid,
            "aid": data.get("aid"),
            "cid": cid,
            "title": data.get("title"),
            "description": data.get("desc"),
            "thumbnail": data.get("pic"),
            "duration_seconds": data.get("duration"),
            "published_at": data.get("pubdate"),
            "views": stat.get("view"),
            "channel_uid": str(owner.get("mid", "")) if owner.get("mid") else None,
            "channel_name": owner.get("name"),
            "url": f"https://www.bilibili.com/video/{bvid}",
            "status": status,
            "rights": rights,
            "is_upower_exclusive": data.get("is_upower_exclusive"),
            "is_upower_preview": data.get("is_upower_preview"),
            "is_upower_play": data.get("is_upower_play"),
        }

    def get_videos_batch(self, bvids: List[str]) -> List[Dict[str, Any]]:
        """Get metadata for multiple videos."""
        results = []
        for bvid in bvids:
            info = self.get_video_info(bvid)
            if info:
                results.append(info)
            time.sleep(0.3)  # Rate limiting
        return results