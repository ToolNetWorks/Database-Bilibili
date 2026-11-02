import json
import re
import time
from collections import Counter
from pathlib import Path

from yt_dlp import YoutubeDL

from check_power_ytdlp import inspect_video

ROOT = Path("/root/tool-phim")
CHANNEL_UID = "98240066"
SOURCE = ROOT / "data/channels" / CHANNEL_UID / "videos.jsonl"
OUTPUT = ROOT / "data/channels" / CHANNEL_UID / "videos_classified.jsonl"
COOKIES = ROOT / "secrets/bilibili/cookies_bilibili.txt"
MAX_VIDEOS = 20
REQUEST_DELAY = 2


def load_video_ids():
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Missing input file: {SOURCE}")
    ids = []
    seen = set()
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        bvid = record.get("id", "")
        if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
            continue
        if bvid in seen:
            continue
        seen.add(bvid)
        ids.append(bvid)
        if len(ids) >= MAX_VIDEOS:
            break
    return ids


def scan_video(extractor, bvid):
    try:
        return inspect_video(extractor, bvid)
    except Exception as exc:
        return {"bvid": bvid, "status": "UNKNOWN", "error_type": type(exc).__name__}


def main():
    if not COOKIES.is_file():
        print("ERROR: Bilibili session cookie not found")
        return 1
    video_ids = load_video_ids()
    if not video_ids:
        print("ERROR: No valid videos found")
        return 1
    options = {
        "cookiefile": str(COOKIES),
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 25,
    }
    results = []
    with YoutubeDL(options) as ydl:
        extractor = ydl.get_info_extractor("BiliBili")
        for index, bvid in enumerate(video_ids, 1):
            result = scan_video(extractor, bvid)
            results.append(result)
            status = result.get("status", "UNKNOWN")
            duration = result.get("duration_seconds", 0)
            print(f"[{index:02d}/{len(video_ids):02d}] {bvid} | {status} | {duration}s", flush=True)
            if index < len(video_ids):
                time.sleep(REQUEST_DELAY)
    temp_file = OUTPUT.with_suffix(".jsonl.tmp")
    with temp_file.open("w", encoding="utf-8") as file:
        for result in results:
            file.write(json.dumps(result, ensure_ascii=False) + "\n")
    temp_file.replace(OUTPUT)
    counts = Counter(item.get("status", "UNKNOWN") for item in results)
    print("\n===== KET QUA PHAN LOAI =====")
    print("Tong video:", len(results))
    for status in ("FREE_CANDIDATE", "POWER", "POWER_PREVIEW", "PAID", "UNKNOWN"):
        print(f"{status}: {counts[status]}")
    print(f"\nSaved: {OUTPUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
