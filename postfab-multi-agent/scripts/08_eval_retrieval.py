"""
08_eval_retrieval.py — 검색(Retrieval) 정확도 측정. 파인튜닝 전/후 비교의 시험지 역할.

측정 방식: retriever.py의 용어 정확일치(lexical) 지름길을 거치지 않고, 두 ChromaDB
컬렉션(glossary_cards/article_chunks)에 순수 벡터 검색만 던져 정답 source_id가
몇 순위에 나오는지를 본다 — 임베딩 모델 자체의 성능만 분리해서 재기 위함
(retriever.py의 정확일치는 검색어에 용어가 그대로 들어있을 때만 작동하는 별도 로직이라
 여기 섞으면 "모델이 잘한 것"과 "사전 매칭이 잘한 것"이 뒤섞임).

임베딩 모델은 src/rag/config.py의 EMBED_MODEL(=POSTFAB_EMBED_MODEL 환경변수)을 그대로
쓴다 — 반드시 data/chroma가 같은 모델로 build_vectorstore.py를 통해 적재된 상태여야 함.

베이스라인(파인튜닝 전) 측정 절차:
  set POSTFAB_EMBED_MODEL=BAAI/bge-m3          (원본 모델, 첫 실행 시 약 2.3GB 다운로드)
  python -m src.rag.build_vectorstore           (원본 모델로 재적재)
  python scripts/08_eval_retrieval.py            (test.jsonl로 측정)

파인튜닝 후 재측정 시 EMBED_MODEL을 파인튜닝 모델 경로로 바꾸고 위 두 단계 반복.

지표: Accuracy@1/3/5, NDCG@10 (정답이 1개뿐이므로 NDCG@10 = 1/log2(rank+1), rank>10이면 0).
전체 + phrasing(formal/casual)별 + source_type별 + question_type별로 나눠 출력·저장.

사용법:
  python scripts/08_eval_retrieval.py                  # test.jsonl 전체
  python scripts/08_eval_retrieval.py --split valid
"""
import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.rag.config import CHROMA_DIR, COLLECTION_CARDS, COLLECTION_CHUNKS, EMBED_MODEL  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
FINETUNE_DIR = BASE / "data" / "finetune"
OUT_DIR = BASE / "data" / "eval"

K_LIST = (1, 3, 5)
NDCG_K = 10
MAX_K = max(*K_LIST, NDCG_K)


def load_rows(split: str) -> list[dict]:
    path = FINETUNE_DIR / f"{split}.jsonl"
    if not path.exists():
        sys.exit(f"입력 파일이 없습니다: {path} (먼저 06_split_qa.py 실행)")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def batch_query(col, questions: list[str], n_results: int, batch_size: int = 64):
    """collection.query를 배치로 호출해 [{ids, distances}, ...] (질문 순서 유지)를 반환."""
    out = []
    for i in range(0, len(questions), batch_size):
        chunk = questions[i:i + batch_size]
        res = col.query(query_texts=chunk, n_results=n_results)
        for ids, dists in zip(res["ids"], res["distances"]):
            out.append(list(zip(ids, dists)))
    return out


def rank_of(source_id: str, cards_hits: list[tuple], chunks_hits: list[tuple]) -> int | None:
    """카드+청크 top-K를 거리순으로 합쳐 정답의 1-indexed 순위를 반환 (없으면 None)."""
    merged = sorted(cards_hits + chunks_hits, key=lambda x: x[1])
    for rank, (rid, _dist) in enumerate(merged, start=1):
        if rid == source_id:
            return rank
    return None


def ndcg_at(rank: int | None, k: int) -> float:
    if rank is None or rank > k:
        return 0.0
    return 1.0 / math.log2(rank + 1)  # IDCG=1 (정답 1개, 이상적 순위 1위)


def safe_model_tag(model_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model_name).strip("_")


def summarize(rows: list[dict], ranks: list[int | None]) -> dict:
    n = len(rows)
    acc = {k: sum(1 for r in ranks if r is not None and r <= k) / n for k in K_LIST}
    ndcg = sum(ndcg_at(r, NDCG_K) for r in ranks) / n
    return {"n": n, **{f"accuracy@{k}": round(v, 4) for k, v in acc.items()},
            f"ndcg@{NDCG_K}": round(ndcg, 4)}


def main():
    ap = argparse.ArgumentParser(description="ChromaDB 순수 벡터 검색으로 Accuracy@k / NDCG@10 측정")
    ap.add_argument("--split", choices=["train", "valid", "test"], default="test")
    ap.add_argument("--batch-size", type=int, default=64)
    args = ap.parse_args()

    rows = load_rows(args.split)
    print(f"[08] split={args.split} 행={len(rows)} 임베딩 모델={EMBED_MODEL}")

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    cards_col = client.get_collection(COLLECTION_CARDS, embedding_function=ef)
    chunks_col = client.get_collection(COLLECTION_CHUNKS, embedding_function=ef)

    questions = [r["question"] for r in rows]
    n_results = min(MAX_K, cards_col.count(), chunks_col.count())
    cards_hits_all = batch_query(cards_col, questions, n_results, args.batch_size)
    chunks_hits_all = batch_query(chunks_col, questions, n_results, args.batch_size)

    ranks = [rank_of(r["source_id"], ch, kh)
             for r, ch, kh in zip(rows, cards_hits_all, chunks_hits_all)]

    result = {
        "embed_model": EMBED_MODEL, "split": args.split, "n": len(rows),
        "overall": summarize(rows, ranks),
        "by_phrasing": {}, "by_source_type": {}, "by_question_type": {},
    }

    for group_key in ("phrasing", "source_type", "question_type"):
        groups: dict[str, list[int]] = defaultdict(list)
        for idx, r in enumerate(rows):
            groups[r[group_key]].append(idx)
        out_key = f"by_{group_key}"
        for gval, idxs in sorted(groups.items()):
            g_rows = [rows[i] for i in idxs]
            g_ranks = [ranks[i] for i in idxs]
            result[out_key][gval] = summarize(g_rows, g_ranks)

    print(f"\n전체 (n={result['overall']['n']}): {result['overall']}")
    for group_key in ("by_phrasing", "by_source_type", "by_question_type"):
        print(f"\n{group_key}:")
        for gval, stats in result[group_key].items():
            print(f"  {gval:16s} (n={stats['n']:4d}): {stats}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = safe_model_tag(Path(EMBED_MODEL).name or EMBED_MODEL)
    out_path = OUT_DIR / f"retrieval_{tag}_{args.split}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n저장 → {out_path}")


if __name__ == "__main__":
    main()
