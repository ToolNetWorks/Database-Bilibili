import json
import re
import time
from collections import Counter
from pathlib import Path

from yt_dlp import YoutubeDL
from check_power_ytdlp import inspect_video

ROOT = Path("/root/tool-phim")
CHANNEL = "98240066"
FOLDER = ROOT / "data/channels" / CHANNEL
SOURCE = FOLDER / "all_videos.jsonl"
OLD_RESULTS = FOLDER / "videos_classified.jsonl"
RESULTS = FOLDER / "videos_classified_all.jsonl"
COOKIES = ROOT / "secrets/bilibili/cookies_bilibili.txt"
BATCH_SIZE = 20
DELAY_SECONDS = 3


def read_jsonl(path):
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def save_results(records):
    temporary = RESULTS.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    temporary.replace(RESULTS)


def main():
    if not SOURCE.is_file():
        print("ERROR: Chưa có all_videos.jsonl")
        return 1
    if not COOKIES.is_file():
        print("ERROR: Thiếu cookie Bilibili")
        return 1
    source_items = read_jsonl(SOURCE)
    video_ids = []
    seen = set()
    for item in source_items:
        bvid = item.get("id", "")
        if not isinstance(bvid, str):
            continue
        if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
            continue
        if bvid in seen:
            continue
        seen.add(bvid)
        video_ids.append(bvid)
    existing = read_jsonl(RESULTS)
    if not existing:
        existing = read_jsonl(OLD_RESULTS)
    records = {row["bvid"]: row for row in existing if row.get("bvid")}
    # UNKNOWN sẽ được kiểm tra lại ở lần sau.
    pending = [bvid for bvid in video_ids if bvid not in records or records[bvid].get("status") == "UNKNOWN"]
    batch = pending[:BATCH_SIZE]
    print(f"CHANNEL: {CHANNEL}")
    print(f"TOTAL: {len(video_ids)}")
    print(f"ALREADY STORED: {len(records)}")
    print(f"THIS BATCH: {len(batch)}")
    if not batch:
        print("ALL VIDEOS SCANNED")
        return 0
    options = {
        "cookiefile": str(COOKIES),
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 25,
    }
    with YoutubeDL(options) as ydl:
        extractor = ydl.get_info_extractor("BiliBili")
        for index, bvid in enumerate(batch, 1):
            try:
                result = inspect_video(extractor, bvid)
            except Exception as error:
                result = {"bvid": bvid, "status": "UNKNOWN", "error_type": type(error).__name__}
            records[bvid] = result
            save_results(list(records.values()))
            print(f"[{index}/{len(batch)}] {bvid} -> {result.get('status')}", flush=True)
            if index < len(batch):
                time.sleep(DELAY_SECONDS)
    counts = Counter(row.get("status", "UNKNOWN") for row in records.values())
    print("\n===== SCAN SUMMARY =====")
    print(f"TOTAL CHANNEL: {len(video_ids)}")
    print(f"STORED: {len(records)}")
    for status in ("FREE_CANDIDATE", "POWER", "POWER_PREVIEW", "PAID", "UNKNOWN"):
        print(f"{status}: {counts[status]}")
    print(f"SAVED: {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
