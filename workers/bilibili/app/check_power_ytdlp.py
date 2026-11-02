import json
import sys
from pathlib import Path

from yt_dlp import YoutubeDL


PROJECT = Path("/root/tool-phim")
COOKIE_FILE = PROJECT / "secrets/bilibili/cookies_bilibili.txt"

VIDEO_IDS = [
    "BV1bn4P6bE59",
    "BV1GVxC6CEp3",
]


def classify_video(data: dict) -> str:
    rights = data.get("rights") or {}

    if data.get("is_upower_exclusive") in (True, 1):
        if data.get("is_upower_preview") in (True, 1):
            return "POWER_PREVIEW"
        return "POWER"

    if rights.get("ugc_pay") in (True, 1):
        return "PAID"

    if rights.get("pay") in (True, 1):
        return "PAID"

    if data.get("is_upower_exclusive") is False:
        return "FREE_CANDIDATE"

    return "UNKNOWN"


def inspect_video(extractor, bvid: str) -> dict:
    url = f"https://www.bilibili.com/video/{bvid}"

    headers = extractor.geo_verification_headers()

    html, response = extractor._download_webpage_handle(
        url,
        bvid,
        headers=headers,
    )

    # Detect redirects to unsupported regional pages.
    if not extractor._match_valid_url(response.url):
        return {
            "bvid": bvid,
            "status": "UNKNOWN",
            "reason": "Video redirected to another page",
        }

    state = extractor._search_json(
        r"window\.__INITIAL_STATE__\s*=",
        html,
        "initial state",
        bvid,
        default=None,
    )

    if not isinstance(state, dict):
        return {
            "bvid": bvid,
            "status": "UNKNOWN",
            "reason": "INITIAL_STATE unavailable",
        }

    video = state.get("videoData") or state.get("videoInfo")

    if not isinstance(video, dict):
        return {
            "bvid": bvid,
            "status": "UNKNOWN",
            "reason": "Video data unavailable",
        }

    return {
        "bvid": bvid,
        "title": video.get("title"),
        "duration_seconds": video.get("duration"),
        "status": classify_video(video),
        "is_upower_exclusive": video.get(
            "is_upower_exclusive"
        ),
        "is_upower_preview": video.get(
            "is_upower_preview"
        ),
        "is_upower_play": video.get(
            "is_upower_play"
        ),
        "rights": {
            "ugc_pay": (video.get("rights") or {}).get(
                "ugc_pay"
            ),
            "pay": (video.get("rights") or {}).get(
                "pay"
            ),
        },
    }


def main() -> int:
    if not COOKIE_FILE.is_file():
        print("ERROR: Missing Bilibili cookies")
        return 1

    options = {
        "cookiefile": str(COOKIE_FILE),
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 25,
    }

    with YoutubeDL(options) as ydl:
        extractor = ydl.get_info_extractor("BiliBili")

        for bvid in VIDEO_IDS:
            print(f"\n===== {bvid} =====")

            try:
                result = inspect_video(extractor, bvid)
            except Exception as exc:
                print(json.dumps({
                    "bvid": bvid,
                    "status": "UNKNOWN",
                    "error_type": type(exc).__name__,
                }, ensure_ascii=False, indent=2))
                continue

            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ))

    return 0


if __name__ == "__main__":
    sys.exit(main())
