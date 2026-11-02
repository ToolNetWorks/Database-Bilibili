"""Safe, idempotent patch of the Database-Bilibili FastAPI entrypoint."""
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / 'workers/bilibili/api/main.py'
EXTENSION = ROOT / 'workers/bilibili/api/docs_api.py'
BACKUP = ROOT / 'data/backup/main_before_api_docs.py'

if not MAIN.is_file() or not EXTENSION.is_file():
    raise SystemExit('ERROR: Run from Database-Bilibili project root, with docs_api.py present')

source = MAIN.read_text(encoding='utf-8')
if 'install_docs_api(app, require_api_key, neon_store, format_duration)' in source:
    print('ALREADY INSTALLED: no changes needed')
    raise SystemExit(0)

old_import = 'from fastapi import Depends, FastAPI, Header, HTTPException, Query'
old_auth = 'def require_api_key(x_api_key: str | None = Header(default=None)):'
if source.count(old_import) != 1 or source.count(old_auth) != 1:
    raise SystemExit('ERROR: Unknown main.py version; refusing unsafe modification')

source = source.replace(old_import, old_import + ', Security\nfrom fastapi.security import APIKeyHeader', 1)
source = source.replace(
    old_auth,
    'api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)\n\n\n'
    'def require_api_key(x_api_key: str | None = Security(api_key_header)):',
    1,
)
source += (
    '\n\n# Public docs landing page and authenticated read-only POST endpoint.\n'
    'from workers.bilibili.api.docs_api import install_docs_api\n'
    'install_docs_api(app, require_api_key, neon_store, format_duration)\n'
)
BACKUP.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(MAIN, BACKUP)
MAIN.write_text(source, encoding='utf-8')
print('API DOCS INSTALLED: /api-docs, /docs, POST /v1/videos/batch')
