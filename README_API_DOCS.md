# Database-Bilibili docs + POST batch lookup patch

Works with current `main` branch and Neon adapter. No database migration or new dependencies are required.

Contents:
- `workers/bilibili/api/docs_api.py`: public documentation page and authenticated POST `/v1/videos/batch`.
- `install_docs.py`: idempotent update to `workers/bilibili/api/main.py`, preserving a backup in `data/backup/`.
- `test_docs.py`: optional local mock tests (requires `httpx` for FastAPI TestClient).

## Installation on VPS

1. Upload and extract archive into `/root/Database-Bilibili` (merge files, do not delete existing files).
2. `cd /root/Database-Bilibili`
3. `.venv/bin/python install_docs.py`
4. `.venv/bin/python -m py_compile workers/bilibili/api/main.py workers/bilibili/api/docs_api.py`
5. `git add workers/bilibili/api/main.py workers/bilibili/api/docs_api.py`
6. `git commit -m 'Add interactive API docs and authenticated POST batch lookup'`
7. `git push origin main`

Your Render Web Service should redeploy automatically if auto-deploy is enabled.

## URLs after deployment

- `https://database-bilibili.onrender.com/api-docs`: custom docs page, including interactive POST tester.
- `https://database-bilibili.onrender.com/docs`: Swagger UI with X-API-Key Authorize.
- `https://database-bilibili.onrender.com/v1/videos/batch`: authenticated POST.

Example:

```bash
curl -X POST https://database-bilibili.onrender.com/v1/videos/batch \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $BILIBILI_API_KEY" \
  -d '{"bvids":["BV1bn4P6bE59","BV12s4P6dEgq"]}'
```

No secrets should be committed. This POST does not write to Neon.
