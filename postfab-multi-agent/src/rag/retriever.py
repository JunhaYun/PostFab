"""
신규 코퍼스(용어 카드 + 섹션 청크) 이중 검색 Retriever.

검색 전략 (보고서 4.1절):
  ① 용어 정확 일치 우선 — 질문에 용어 카드의 term/alias가 그대로 들어있으면
     해당 카드를 최상위로 (사전 검색은 lexical이 강함, 짧은 질문 대응)
  ② 벡터 검색 — 카드/청크 두 컬렉션을 모두 조회해 거리순 병합

기존 인터페이스 유지: retrieve(), retrieve_as_context() 시그니처 불변
→ knowledge_agent 등 상위 로직은 수정 불필요.
"""
import json
import re

import chromadb
from chromadb.utils import embedding_functions

from src.rag.config import (CHROMA_DIR, COLLECTION_CARDS, COLLECTION_CHUNKS,
                            CORPUS_DIR, EMBED_MODEL)

_collections = None
_term_index = None   # {정규화된 용어/별칭: 카드 dict} — 정확 일치 검색용


def _get_collections():
    global _collections
    if _collections is None:
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collections = {
            "cards": client.get_collection(COLLECTION_CARDS, embedding_function=ef),
            "chunks": client.get_collection(COLLECTION_CHUNKS, embedding_function=ef),
        }
    return _collections


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _get_term_index() -> dict:
    global _term_index
    if _term_index is None:
        _term_index = {}
        with open(CORPUS_DIR / "glossary_cards.jsonl", encoding="utf-8") as f:
            for line in f:
                card = json.loads(line)
                for key in [card["term"], *card["aliases"]]:
                    key = _normalize(key)
                    if len(key) >= 2:                     # 한 글자 용어는 오탐 위험
                        _term_index.setdefault(key, []).append(card)
    return _term_index


def _exact_term_matches(query: str, limit: int = 2) -> list[dict]:
    """질문 안에 용어가 그대로 포함된 카드를 긴 용어 우선으로 반환."""
    q = _normalize(query)
    hits = []
    for key, cards in _get_term_index().items():
        if key in q:
            hits.extend((len(key), c) for c in cards)
    hits.sort(key=lambda x: -x[0])                        # 긴 용어(구체적) 우선
    seen, result = set(), []
    for _, card in hits:
        if card["id"] not in seen:
            seen.add(card["id"])
            result.append(card)
        if len(result) >= limit:
            break
    return result


def retrieve(query: str, n_results: int = 3) -> list[dict]:
    """용어 정확 일치 + 이중 벡터 검색 병합 결과를 반환.

    Returns: [{"title", "content", "distance", "source_url", "kind"}, ...]
    """
    results = []
    seen_ids = set()

    # ① 용어 정확 일치 (distance 0.0으로 최상위 배치)
    for card in _exact_term_matches(query):
        results.append({
            "title": card["term"], "content": card["definition"],
            "distance": 0.0, "source_url": card["source_url"] or "",
            "kind": "glossary_exact",
        })
        seen_ids.add(card["id"])

    # ② 벡터 검색 — 두 컬렉션 조회 후 거리순 병합
    cols = _get_collections()
    candidates = []
    for kind, col in [("glossary", cols["cards"]), ("article", cols["chunks"])]:
        res = col.query(query_texts=[query], n_results=n_results)
        for rid, doc, meta, dist in zip(res["ids"][0], res["documents"][0],
                                        res["metadatas"][0], res["distances"][0]):
            if rid in seen_ids:
                continue
            candidates.append({
                "title": meta.get("term") or meta.get("title", ""),
                "content": doc, "distance": round(dist, 4),
                "source_url": meta.get("source_url", ""), "kind": kind,
            })
    candidates.sort(key=lambda r: r["distance"])
    results.extend(candidates)
    return results[:n_results + len([r for r in results if r["kind"] == "glossary_exact"])]


def retrieve_as_context(query: str, n_results: int = 3) -> str:
    """검색 결과를 LLM 컨텍스트용 문자열로 반환 (기존 인터페이스 유지)."""
    docs = retrieve(query, n_results)
    if not docs:
        return "관련 지식을 찾을 수 없습니다."
    parts = []
    for d in docs:
        src = f" (출처: {d['source_url']})" if d["source_url"] else ""
        parts.append(f"[{d['title']}]{src}\n{d['content']}")
    return "\n\n---\n\n".join(parts)
