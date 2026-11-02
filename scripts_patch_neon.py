from pathlib import Path
import shutil

path = Path("workers/bilibili/api/main.py")
source = path.read_text(encoding="utf-8")

if "from workers.bilibili.api import neon_store" in source:
    print("SKIP: Neon integration already exists")
    raise SystemExit(0)


def replace_once(old: str, new: str):
    global source

    if source.count(old) != 1:
        raise RuntimeError(
            f"Expected one occurrence: {old[:70]}"
        )

    source = source.replace(old, new, 1)


replace_once(
    "from fastapi import Depends, FastAPI, Header, HTTPException, Query",
    """from fastapi import Depends, FastAPI, Header, HTTPException, Query
from workers.bilibili.api import neon_store""",
)

replace_once(
    """    folder = DATA_ROOT / uid

    source = read_jsonl(folder / "all_videos.jsonl")""",
    """    if neon_store.enabled():
        return neon_store.get_channel_videos(
            uid, format_duration
        )

    folder = DATA_ROOT / uid

    source = read_jsonl(folder / "all_videos.jsonl")""",
)

replace_once(
    """def list_channels():
    if not DATA_ROOT.is_dir():""",
    """def list_channels():
    if neon_store.enabled():
        return {
            "success": True,
            "channels": neon_store.list_channels(),
        }

    if not DATA_ROOT.is_dir():""",
)

replace_once(
    """    if not DATA_ROOT.is_dir():
        raise HTTPException(404, "Video not found")""",
    """    if neon_store.enabled():
        video = neon_store.get_video(bvid, format_duration)

        if video is None:
            raise HTTPException(404, "Video not found")

        return {"success": True, "video": video}

    if not DATA_ROOT.is_dir():
        raise HTTPException(404, "Video not found")""",
)

replace_once(
    '@app.get("/v1/channels", dependencies=[Depends(require_api_key)])',
    '''@app.get("/ready")
def ready():
    if not neon_store.enabled():
        raise HTTPException(503, "Database not configured")

    neon_store.check_ready()
    return {"success": True, "status": "ready"}


@app.get("/v1/channels", dependencies=[Depends(require_api_key)])''',
)

shutil.copy2(path, "data/backup/main_pre_neon.py")
path.write_text(source, encoding="utf-8")

print("NEON API INTEGRATION COMPLETE")
