"""
RAG 레이어 공통 설정.

임베딩 모델은 반드시 적재(build_vectorstore)와 검색(retriever)이 동일해야 한다 —
서로 다른 모델의 벡터를 비교하면 검색이 무의미해지므로 여기서 단일 관리한다.
모델을 바꾸면 build_vectorstore.py를 재실행해 코퍼스 전체를 재임베딩해야 한다.

모델 전환 (파인튜닝 전/후 비교 실험용):
  기본값     : ../models/postfab-bge-m3-v2 (신 코퍼스 기준 파인튜닝 모델, 2026-07-23.
              test.jsonl 기준 NDCG@10 0.8689→0.9252. [[project-rag-rebuild]] 참고)
  원본 모델   : set POSTFAB_EMBED_MODEL=BAAI/bge-m3  (첫 실행 시 약 2.3GB 다운로드)
  구 모델     : set POSTFAB_EMBED_MODEL=<repo>/../models/postfab-bge-m3-final (구 코퍼스 기준, 더 이상 안 맞음)
"""
import os
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]

EMBED_MODEL = os.environ.get(
    "POSTFAB_EMBED_MODEL",
    str(_BASE.parent / "models" / "postfab-bge-m3-v2"),
)

CORPUS_DIR = _BASE / "data" / "corpus"
CHROMA_DIR = _BASE / "data" / "chroma"

COLLECTION_CARDS = "glossary_cards"
COLLECTION_CHUNKS = "article_chunks"
