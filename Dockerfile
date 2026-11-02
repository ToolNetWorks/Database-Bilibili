FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

WORKDIR /app

COPY requirements-api.txt .

RUN pip install --no-cache-dir \
    -r requirements-api.txt

COPY workers/bilibili/api/main.py workers/bilibili/api/neon_store.py workers/bilibili/api/docs_api.py ./workers/bilibili/api/

RUN python -m compileall -q workers/bilibili/api

USER 10001:10001

EXPOSE 10000

CMD ["sh", "-c", "exec uvicorn workers.bilibili.api.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --loop uvloop --http httptools"]
