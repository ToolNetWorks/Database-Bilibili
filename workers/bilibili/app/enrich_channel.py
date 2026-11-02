import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CHANNEL_UID = "98240066"
BASE_DIR = Path("data/channels") / CHANNEL_UID
INPUT_FILE = BASE_DIR / "videos.jsonl"
OUTPUT_FILE = BASE_DIR / "videos_metadata.jsonl"


def fetch_metadata(bvid: str) -> dict:
    query = urllib.parse.urlencode({"bvid": bvid})
    url = f"https://api.bilibili.com/x/web-interface/view?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)

    if payload.get("code") != 0:
        raise ValueError(
            f"API code={payload.get('code')}: "
            f"{payload.get('message', 'Unknown error')}"
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("Missing metadata")

    return data


def classify(data: dict) -> str:
    rights = data.get("rights") or {}

    if data.get("is_upower_exclusive") is True:
        return "POWER"

    if rights.get("ugc_pay") == 1:
        return "UGC_PAID"

    if rights.get("pay") == 1:
        return "PGC_PAID"

    if data.get("is_chargeable_season") is True:
        return "PAID_SEASON"

    return "UNKNOWN"


def main() -> int:
    if not INPUT_FILE.is_file():
        print(f"ERROR: Missing {INPUT_FILE}")
        return 1

    BASE_DIR.mkdir(parents=True, exist_ok=True)

    records = []
    seen = set()

    for line in INPUT_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        item = json.loads(line)
        bvid = item.get("id")

        if not isinstance(bvid, str) or not bvid.startswith("BV"):
            continue

        if bvid in seen:
            continue

        seen.add(bvid)

        try:
            data = fetch_metadata(bvid)

            result = {
                "bvid": bvid,
                "url": f"https://www.bilibili.com/video/{bvid}",
                "title": data.get("title", ""),
                "duration_seconds": data.get("duration"),
                "owner": (data.get("owner") or {}).get("name"),
                "status": classify(data),
                "rights": data.get("rights"),
                "is_upower_exclusive": data.get("is_upower_exclusive"),
                "cid": data.get("cid"),
                "error": None,
            }

        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
        ) as exc:
            result = {
                "bvid": bvid,
                "status": "UNKNOWN",
                "error": str(exc),
            }

        records.append(result)

        print(
            f"[{len(records):02d}] "
            f"{result['status']:<12} "
            f"{bvid} | "
            f"{result.get('title', '')[:55]}"
        )

        time.sleep(1)

    with OUTPUT_FILE.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )

    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Total: {len(records)}")
    print("UNKNOWN requires further verification.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
