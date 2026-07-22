"""
data/corpus/*.jsonl(04_build_corpus.py 산출물)을 임베딩해 ChromaDB에 적재.

컬렉션 2개로 분리 적재 (검색 입도 이원화 — 보고서 4.1절):
  glossary_cards : 용어 카드. 임베딩 입력 = "용어 (별칭): 정의"
  article_chunks : 섹션 청크. 임베딩 입력 = context_header + 본문
                   (문서 맥락이 벡터에 포함되도록)

코퍼스나 임베딩 모델(src/rag/config.py)이 바뀌면 재실행 — 기존 컬렉션을
지우고 전체 재임베딩한다 (구/신 모델 벡터 혼용 방지).

실행: python -m src.rag.build_vectorstore
"""
import json
import sys

import chromadb
from chromadb.utils import embedding_functions

from src.rag.config import (CHROMA_DIR, COLLECTION_CARDS, COLLECTION_CHUNKS,
                            CORPUS_DIR, EMBED_MODEL)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_jsonl(name: str) -> list[dict]:
    path = CORPUS_DIR / name
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def clean_meta(d: dict) -> dict:
    """Chroma 메타데이터는 None을 허용하지 않음 — 빈 문자열로 치환."""
    return {k: (v if v is not None else "") for k, v in d.items()}


def build():
    print(f"[RAG] 임베딩 모델: {EMBED_MODEL}")
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # ── 용어 카드 ──
    cards = load_jsonl("glossary_cards.jsonl")
    card_texts = []
    for c in cards:
        alias = f" ({', '.join(c['aliases'])})" if c["aliases"] else ""
        card_texts.append(f"{c['term']}{alias}: {c['definition']}")
    card_metas = [clean_meta(dict(
        term=c["term"], canonical_term=c["canonical_term"],
        aliases=", ".join(c["aliases"]), source=c["source"],
        source_url=c["source_url"], language=c["language"],
        extracted_by=c["extracted_by"])) for c in cards]

    # ── 섹션 청크 ──
    chunks = load_jsonl("article_chunks.jsonl")
    chunk_texts = [f"{c['context_header']}\n{c['text']}" for c in chunks]
    chunk_metas = [clean_meta(dict(
        doc_id=c["doc_id"], title=c["context_header"], section=c["section"],
        source=c["source"], source_url=c["source_url"],
        language=c["language"], n_tokens=c["n_tokens"])) for c in chunks]

    for name, records, texts, metas in [
        (COLLECTION_CARDS, cards, card_texts, card_metas),
        (COLLECTION_CHUNKS, chunks, chunk_texts, chunk_metas),
    ]:
        try:
            client.delete_collection(name)
        except Exception:
            pass
        col = client.create_collection(
            name, embedding_function=ef,
            metadata={"hnsw:space": "cosine", "embed_model": EMBED_MODEL})
        # 배치 적재 (임베딩은 SentenceTransformer가 내부 배치 처리)
        BATCH = 64
        for i in range(0, len(records), BATCH):
            col.add(ids=[r["id"] for r in records[i:i + BATCH]],
                    documents=texts[i:i + BATCH],
                    metadatas=metas[i:i + BATCH])
            print(f"  [{name}] {min(i + BATCH, len(records))}/{len(records)}")
        print(f"[RAG] {name}: {len(records)}건 적재 완료")

    print(f"[RAG] 저장 → {CHROMA_DIR}")


if __name__ == "__main__":
    build()
