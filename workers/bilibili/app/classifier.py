import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


def extract_bvid(value: str) -> str | None:
    match = re.search(r"BV[0-9A-Za-z]{10}", value)
    return match.group(0) if match else None


def is_enabled(value) -> bool:
    return value is True or value == 1 or value == "1"


def fetch_video(bvid: str) -> dict:
    url = (
        "https://api.bilibili.com/x/web-interface/view?"
        + urllib.parse.urlencode({"bvid": bvid})
    )

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
        raise RuntimeError(
            f"Bilibili API: {payload.get('code')} "
            f"{payload.get('message', '')}"
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("Video metadata is missing")

    return data


def classify_video(data: dict) -> dict:
    rights = data.get("rights") or {}

    if is_enabled(data.get("is_upower_exclusive")):
        status = "POWER"
    elif is_enabled(rights.get("ugc_pay")):
        status = "UGC_PAID"
    elif is_enabled(rights.get("pay")):
        status = "PGC_PAID"
    elif is_enabled(data.get("is_chargeable_season")):
        status = "PAID_SEASON"
    elif (
        "is_upower_exclusive" in data
        and isinstance(rights, dict)
        and rights
    ):
        status = "FREE_CANDIDATE"
    else:
        status = "UNKNOWN"

    return {
        "bvid": data.get("bvid"),
        "title": data.get("title"),
        "duration_seconds": data.get("duration"),
        "uploader": (data.get("owner") or {}).get("name"),
        "status": status,
        "power_exclusive": data.get("is_upower_exclusive"),
        "power_preview": data.get("is_upower_preview"),
        "power_play": data.get("is_upower_play"),
        "ugc_pay": rights.get("ugc_pay"),
        "pgc_pay": rights.get("pay"),
        "chargeable_season": data.get("is_chargeable_season"),
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python classifier.py <Bilibili URL or BVID>")
        return 2

    bvid = extract_bvid(sys.argv[1])
    if not bvid:
        print("ERROR: Invalid Bilibili BV ID")
        return 2

    try:
        video = fetch_video(bvid)
        result = classify_video(video)
    except (
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        RuntimeError,
    ) as error:
        print(f"ERROR: {error}")
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
