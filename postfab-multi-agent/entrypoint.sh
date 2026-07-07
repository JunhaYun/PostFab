#!/bin/sh
set -e

echo "[entrypoint] RAG 벡터스토어 구축 중..."
python src/rag/build_vectorstore.py

echo "[entrypoint] FastAPI 서버 시작..."
exec uvicorn app.api.main:app --host 0.0.0.0 --port 8000
