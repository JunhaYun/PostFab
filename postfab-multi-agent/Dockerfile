FROM python:3.11-slim

WORKDIR /app

# build-essential은 설치하지 않는다 — 의존성이 전부 휠로 들어와 컴파일러가 필요 없다.
# (있으면 이미지가 약 300MB 커진다. 새 의존성을 추가했다가 빌드가 깨지면 그때 되살릴 것.)

# 패키지 먼저 설치 (레이어 캐시 활용)
#
# torch를 CPU 전용 인덱스에서 먼저 받는다. 기본 PyPI 휠은 CUDA 라이브러리를
# 함께 끌어오는데(nvidia-* 2.7GB + triton 691MB) 컨테이너에 GPU가 없어 전부 낭비다.
# 이 두 줄 순서를 바꾸면 안 된다 — sentence-transformers가 먼저 설치되면
# CUDA 버전 torch가 딸려 들어온다.
COPY postfab-multi-agent/requirements-docker.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements-docker.txt

# 소스 복사
COPY postfab-multi-agent/ .

# 파인튜닝 모델 + corpus (이미지에 포함)
# 실제로 쓰는 v2 하나만 넣는다 — models/ 전체는 8.6GB이고 나머지 셋은
# 파인튜닝 전/후 비교와 ablation 실험용이라 런타임에 필요 없다.
# 경로는 src/rag/config.py의 기본값(<repo>/../models/postfab-bge-m3-v2)과 맞춰야 한다.
COPY models/postfab-bge-m3-v2/ /models/postfab-bge-m3-v2/
COPY data/corpus_raw.json /data/corpus_raw.json

# DB 생성 + 시뮬레이션 데이터 + RAG 벡터스토어 구축
#
# 11_simulate_data.py를 빼면 안 된다 — create_mock_db.py는 if_exists="replace"로
# 원본 엑셀 4개 LOT만 적재하므로, 이것만 돌리면 이미지 안의 DB가 4 LOT/13행이 되고
# 기간 집계 도구(find_low_yield_lots 등)와 데모 시나리오가 전부 빈 결과를 낸다.
# 시뮬레이터는 API 키가 필요 없고 시드 고정이라 빌드마다 같은 데이터가 나온다.
#
# build_vectorstore는 반드시 -m 으로 실행한다 — 스크립트가 `from src.rag.config import ...`로
# 패키지 절대경로 임포트를 쓰기 때문에 `python src/rag/build_vectorstore.py`는
# ModuleNotFoundError: No module named 'src' 로 죽는다.
RUN python scripts/create_mock_db.py && \
    python scripts/11_simulate_data.py && \
    python -m src.rag.build_vectorstore

EXPOSE 8000

# healthcheck를 compose가 아니라 이미지에 둔다 — 받아서 docker run으로 쓰는 쪽에도 적용되도록.
# curl은 python:3.11-slim에 없으므로 python으로 확인한다(예전 compose healthcheck가
# curl을 써서 컨테이너가 항상 unhealthy로 표시됐다).
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=60s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=5).status == 200 else 1)"

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
