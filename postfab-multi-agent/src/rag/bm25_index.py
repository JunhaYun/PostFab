"""
BM25(rank_bm25) 키워드 검색 인덱스 — 벡터 검색과 결합할 하이브리드 검색의 키워드 쪽 절반.

벡터 검색은 "뜻"으로 찾고, BM25는 "질문에 있는 단어가 문서에 그대로 있나"를 세서 찾는다.
정확한 코드명/용어처럼 흔치 않은 단어에 점수를 더 주기 때문에 벡터 검색이 놓치는 케이스를
보완할 수 있다.

카드(glossary_cards)와 청크(article_chunks)를 하나의 코퍼스로 합쳐 단일 인덱스를 만든다
(두 컬렉션을 따로 인덱싱하면 IDF 통계가 갈라져서 점수를 직접 비교할 수 없기 때문 —
벡터 쪽도 retriever.py에서 두 컬렉션을 풀(pool)로 합쳐서 거리순 정렬하므로 대칭을 맞춘다).

임베딩 텍스트와 동일한 입력(corpus_text.py)을 써서 벡터/BM25가 "같은 문서 표현"을 보게 한다.
"""
import json
import re
from collections import defaultdict

from rank_bm25 import BM25Okapi

from src.rag.config import CORPUS_DIR
from src.rag.corpus_text import build_card_text, build_chunk_text

_index = None  # (BM25Okapi, ids: list[str]) — 지연 로딩 싱글턴


def _tokenize(text: str) -> list[str]:
    """형태소 분석 없이 한글/영숫자 덩어리 단위로 쪼갠다 (BM25는 토큰 집합만 필요)."""
    return re.findall(r"[0-9A-Za-z_]+|[가-힣]+", text.lower())


def _get_index() -> tuple[BM25Okapi, list[str]]:
    global _index
    if _index is None:
        ids: list[str] = []
        corpus: list[list[str]] = []
        with open(CORPUS_DIR / "glossary_cards.jsonl", encoding="utf-8") as f:
            for line in f:
                card = json.loads(line)
                ids.append(card["id"])
                corpus.append(_tokenize(build_card_text(card)))
        with open(CORPUS_DIR / "article_chunks.jsonl", encoding="utf-8") as f:
            for line in f:
                chunk = json.loads(line)
                ids.append(chunk["id"])
                corpus.append(_tokenize(build_chunk_text(chunk)))
        _index = (BM25Okapi(corpus), ids)
    return _index


def bm25_ranked_ids(query: str, n_results: int) -> list[str]:
    """카드+청크 통합 코퍼스에서 BM25 점수 상위 n_results개 id를 점수 내림차순으로 반환."""
    bm25, ids = _get_index()
    scores = bm25.get_scores(_tokenize(query))
    order = sorted(range(len(ids)), key=lambda i: scores[i], reverse=True)[:n_results]
    return [ids[i] for i in order]


def rrf_merge(rank_lists: list[list[str]], k: int = 60) -> list[str]:
    """여러 순위 리스트(등수 순으로 정렬된 id 목록)를 Reciprocal Rank Fusion으로 합친다.

    벡터 거리(작을수록 좋음)와 BM25 점수(클수록 좋음, 스케일 무제한)는 값을 직접 더할 수
    없어서 등수만 쓴다 — 각 리스트에서 i번째(0-index)면 1/(k+i+1)점을 주고 합산.
    k=60은 정보검색에서 흔히 쓰는 값(등수 차이를 완만하게 반영).
    """
    scores: dict[str, float] = defaultdict(float)
    for ranks in rank_lists:
        for i, doc_id in enumerate(ranks):
            scores[doc_id] += 1.0 / (k + i + 1)
    return sorted(scores, key=lambda d: -scores[d])
