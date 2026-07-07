FROM python:3.11-slim

WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 패키지 먼저 설치 (레이어 캐시 활용)
COPY postfab-multi-agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY postfab-multi-agent/ .

# 파인튜닝 모델 + corpus (이미지에 포함)
COPY models/ /models/
COPY data/corpus_raw.json /data/corpus_raw.json

# DB 생성 + RAG 벡터스토어 구축
RUN python scripts/create_mock_db.py && \
    python src/rag/build_vectorstore.py

EXPOSE 8000

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
