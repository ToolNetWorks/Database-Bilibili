import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

COOKIE_FILE = Path("/root/tool-phim/secrets/bilibili/cookies_bilibili.txt")

def main():
    if not COOKIE_FILE.is_file():
        print("ERROR: Không tìm thấy cookie")
        return 1

    jar = http.cookiejar.MozillaCookieJar(str(COOKIE_FILE))
    jar.load(ignore_discard=True)

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar)
    )

    video_ids = ["BV1bn4P6bE59", "BV1GVxC6CEp3"]

    for bvid in video_ids:
        print(f"\n===== {bvid} =====")

        url = f"https://www.bilibili.com/video/{bvid}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.bilibili.com/",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )

        try:
            with opener.open(request, timeout=25) as response:
                html = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            print("STATUS: UNKNOWN")
            print("ERROR:", exc)
            continue

        match = re.search(
            r"window\.__INITIAL_STATE__\s*=\s*",
            html,
        )

        if not match:
            print("STATUS: UNKNOWN")
            print("Không tìm thấy dữ liệu video")
            continue

        try:
            data, _ = json.JSONDecoder().raw_decode(
                html[match.end():].lstrip()
            )
        except (ValueError, json.JSONDecodeError):
            print("STATUS: UNKNOWN")
            print("Không đọc được JSON video")
            continue

        video = data.get("videoData") or {}
        rights = video.get("rights") or {}
        power = video.get("is_upower_exclusive")

        if power is True:
            status = "POWER"
        elif rights.get("ugc_pay") == 1 or rights.get("pay") == 1:
            status = "PAID"
        elif power is False:
            status = "FREE_CANDIDATE"
        else:
            status = "UNKNOWN"

        print(json.dumps({
            "bvid": bvid,
            "title": video.get("title"),
            "duration_seconds": video.get("duration"),
            "status": status,
            "is_upower_exclusive": power,
            "ugc_pay": rights.get("ugc_pay"),
            "pay": rights.get("pay"),
        }, ensure_ascii=False, indent=2))

    return 0

if __name__ == "__main__":
    sys.exit(main())
