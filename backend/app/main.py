from fastapi import FastAPI

app = FastAPI(
    title="TOOL PHIM API",
    version="0.1.0",
    description="Movie processing orchestration API",
)

SERVICES = [
    {"name": "bilibili", "stage": 1},
    {"name": "download", "stage": 2},
    {"name": "audio", "stage": 3},
    {"name": "subtitle", "stage": 4},
    {"name": "translate", "stage": 5},
    {"name": "dubbing", "stage": 6},
    {"name": "render", "stage": 7},
    {"name": "export", "stage": 8},
]


@app.get("/health")
def health():
    return {
        "success": True,
        "service": "toolphim-api",
        "status": "ready",
        "version": "0.1.0",
    }


@app.get("/api/v1/system/services")
def list_services():
    return {
        "success": True,
        "total": len(SERVICES),
        "services": SERVICES,
    }
