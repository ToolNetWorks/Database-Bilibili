"""Interactive documentation and read-only batch BVID lookup."""

import re

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field


class BatchLookupRequest(BaseModel):
    bvids: list[str] = Field(
        min_length=1,
        max_length=100,
        description="1–100 Bilibili video IDs (BV + 10 alphanumeric characters).",
        examples=[["BV1bn4P6bE59", "BV12s4P6dEgq"]],
    )


HTML = r"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Database-Bilibili | API Docs</title>
<style>
:root{color-scheme:dark;--bg:#0c1017;--card:#151b25;--line:#303b4d;--text:#eef3fa;--muted:#a9b4c3;--accent:#8ab7ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{border-bottom:1px solid var(--line);padding:15px max(20px,calc((100vw - 980px)/2));display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
header strong{font-size:17px}main{max-width:980px;margin:auto;padding:44px 20px 70px}h1{font-size:clamp(28px,5vw,42px);margin:0 0 10px;line-height:1.2}h2{margin:40px 0 14px;font-size:22px}h3{margin:22px 0 9px}
p{color:var(--muted)}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.pill{color:#83e8b6;font-size:13px;font-weight:700}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin:16px 0}
.path{display:flex;gap:10px;align-items:center;border-bottom:1px solid var(--line);padding:12px 0;flex-wrap:wrap}
.path:last-child{border:0}.method{background:#1d4638;color:#8bdfb7;border-radius:4px;padding:3px 8px;font-size:12px;font-weight:800}.post{background:#263c65;color:#a2cbff}
code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}code{overflow-wrap:anywhere}pre{background:#0b1019;border:1px solid var(--line);border-radius:8px;padding:15px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
label{display:block;margin-top:15px;margin-bottom:5px;font-weight:650}input,textarea{width:100%;padding:12px;color:var(--text);background:#0c111a;border:1px solid #4c5b71;border-radius:7px;font:14px ui-monospace,monospace}
textarea{min-height:90px;resize:vertical}button{margin-top:15px;cursor:pointer;background:var(--accent);color:#0b162a;padding:12px 20px;border:0;border-radius:7px;font-weight:750}button:disabled{opacity:.6;cursor:wait}
.note{font-size:13px}.links{display:flex;gap:14px;flex-wrap:wrap}.result{min-height:54px}
</style></head><body>
<header><strong>Database-Bilibili <span class="pill">API v1</span></strong><nav class="links"><a href="/docs">Swagger</a><a href="/redoc">ReDoc</a><a href="/openapi.json">OpenAPI JSON</a></nav></header>
<main><div class="pill">REST API · Neon Singapore</div><h1>API Documentation</h1>
<p>Tra cứu metadata video Bilibili, danh sách kênh và phân loại FREE / POWER. API không cung cấp nội dung video.</p>
<div class="card"><strong>Base URL</strong><pre>https://database-bilibili.onrender.com</pre>
<strong>Authentication</strong><pre>X-API-Key: YOUR_API_KEY</pre><p class="note">Các endpoint /v1/* yêu cầu API Key. Không gửi API Key qua URL hoặc chia sẻ công khai.</p></div>
<h2>Endpoints</h2><div class="card">
<div class="path"><span class="method">GET</span><code>/health</code> Service health</div>
<div class="path"><span class="method">GET</span><code>/ready</code> Database readiness</div>
<div class="path"><span class="method">GET</span><code>/v1/channels</code> Danh sách kênh</div>
<div class="path"><span class="method">GET</span><code>/v1/channels/{uid}/videos?access=free&amp;page=1&amp;limit=20</code></div>
<div class="path"><span class="method">GET</span><code>/v1/videos/{bvid}</code> Video details</div>
<div class="path"><span class="method post">POST</span><code>/v1/videos/batch</code> Tra cứu 1–100 BVID</div>
</div>
<h2>POST /v1/videos/batch</h2><p>Truy vấn nhiều BVID trong một request. Chỉ đọc Neon; không thêm, sửa hoặc xóa dữ liệu.</p>
<h3>JSON Request</h3><pre>{
  "bvids": ["BV1bn4P6bE59", "BV12s4P6dEgq"]
}</pre>
<h3>cURL</h3><pre>curl -X POST 'https://database-bilibili.onrender.com/v1/videos/batch' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -d '{"bvids":["BV1bn4P6bE59","BV12s4P6dEgq"]}'</pre>
<h2>Try POST Request</h2><div class="card">
<label for="key">X-API-Key</label><input type="password" id="key" autocomplete="off" placeholder="Nhập API Key">
<label for="ids">BVID (mỗi dòng một mã)</label><textarea id="ids" spellcheck="false">BV1bn4P6bE59
BV12s4P6dEgq</textarea>
<button type="button" id="send">Send POST</button><p class="note">Key chỉ được sử dụng cho request HTTPS đến cùng domain; trang không lưu Key.</p>
<h3>Response</h3><pre class="result" id="out" aria-live="polite">Chưa gửi request.</pre></div>
<p>HTTP: 200 thành công · 401 thiếu/sai API Key · 422 dữ liệu không hợp lệ · 503 kết nối Neon lỗi.</p>
<p>Xem schema chi tiết và thử tất cả GET/POST tại <a href="/docs">Swagger UI</a>.</p>
</main><script>
(function(){
  const button=document.getElementById('send');const out=document.getElementById('out');
  button.addEventListener('click',async()=>{
    const key=document.getElementById('key').value.trim();
    const bvids=document.getElementById('ids').value.split(/[\s,]+/).filter(Boolean);
    if(!key){out.textContent='Thiếu X-API-Key.';return;}
    if(bvids.length<1||bvids.length>100){out.textContent='Nhập từ 1 đến 100 BVID.';return;}
    button.disabled=true;out.textContent='Sending...';
    try{const response=await fetch('/v1/videos/batch',{method:'POST',headers:{'Content-Type':'application/json','X-API-Key':key},body:JSON.stringify({bvids}),cache:'no-store'});
      const result=await response.json();out.textContent='HTTP '+response.status+'\n'+JSON.stringify(result,null,2);
    }catch(e){out.textContent='Network request failed.';}finally{button.disabled=false;}
  });
})();
</script></body></html>"""


def install_docs_api(app: FastAPI, require_api_key, neon_store, format_duration) -> None:
    """Attach documentation and a parameterized read-only POST endpoint."""

    @app.get("/", include_in_schema=False)
    def docs_redirect():
        return RedirectResponse(url="/api-docs", status_code=307)

    @app.get("/api-docs", include_in_schema=False, response_class=HTMLResponse)
    def docs_landing():
        return HTMLResponse(HTML)

    @app.post(
        "/v1/videos/batch",
        dependencies=[Depends(require_api_key)],
        tags=["Videos"],
        summary="Batch lookup Bilibili videos by BVID",
        description="Read metadata of 1–100 BVIDs from Neon. No database writes.",
    )
    def batch_lookup(body: BatchLookupRequest):
        if not neon_store.enabled():
            raise HTTPException(503, "Database not configured")

        unique_ids = list(dict.fromkeys(body.bvids))
        if any(re.fullmatch(r"BV[A-Za-z0-9]{10}", value) is None for value in unique_ids):
            raise HTTPException(422, "Invalid BVID format")

        records = neon_store.fetch_all(
            f"SELECT {neon_store.VIDEO_FIELDS} "
            "FROM bilibili_videos WHERE bvid = ANY(%s)",
            (unique_ids,),
        )
        by_id = {record["bvid"]: record for record in records}
        videos = [
            neon_store.normalize_video(by_id[bvid], format_duration)
            for bvid in unique_ids
            if bvid in by_id
        ]
        return {
            "success": True,
            "requested": len(unique_ids),
            "found": len(videos),
            "missing": [bvid for bvid in unique_ids if bvid not in by_id],
            "videos": videos,
        }
