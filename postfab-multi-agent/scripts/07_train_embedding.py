"""
07_train_embedding.py — bge-m3 임베딩 파인튜닝 (GPU 필요, Google Colab에서 실행 예정).

입력:
  data/corpus/glossary_cards.jsonl, article_chunks.jsonl  — id -> 원문 텍스트
  data/finetune/train.jsonl                                — 학습 (question, source_id) 쌍
  data/finetune/valid.jsonl                                — 학습 중 모니터링용 IR 평가

출력: <output-dir>/ (기본 ../models/postfab-bge-m3-v2) — SentenceTransformer 모델

핵심 설계 — positive는 QA answer가 아니라 원본 소스 텍스트:
  05에서 생성한 qa_pairs.jsonl에는 question과 answer(생성된 답변 문단)가 같이 있지만,
  파인튜닝의 positive로 answer를 쓰면 안 된다. 실제 검색(build_vectorstore.py)이
  임베딩하는 대상은 카드 정의문/청크 본문(src/rag/corpus_text.py의
  build_card_text/build_chunk_text)이지 QA answer가 아니기 때문 — 학습 타깃과 실제
  검색 대상이 어긋나면 파인튜닝 효과가 실제 검색 성능으로 이어지지 않는다.
  그래서 여기서는 source_id로 코퍼스를 다시 찾아 그 원문을 positive로 쓴다.

학습 중 evaluator는 valid.jsonl로 모니터링만 한다 — 파인튜닝 전/후 공식 비교는
08_eval_retrieval.py를 test.jsonl로 두 번(전/후) 돌려서 낸다(단일 측정 로직으로
일관성 유지, 여기서 따로 낸 숫자와 08 숫자가 어긋나는 걸 방지).

Colab 실행 절차 (기존 파인튜닝 경험 재사용):
  1. !pip install -q sentence-transformers==3.4.1 transformers==4.49.0 datasets
  2. data/corpus/*.jsonl, data/finetune/{train,valid}.jsonl 4개 파일을 리포와 같은
     상대 경로 구조로 업로드/마운트 (data/는 gitignore라 git clone만으로는 안 옴)
  3. python scripts/07_train_embedding.py
  4. 로컬로 <output-dir> 다운로드 후, POSTFAB_EMBED_MODEL=<output-dir 경로>로 전환 +
     build_vectorstore.py 재실행 + 08_eval_retrieval.py --split test 로 after 측정

사용법:
  python scripts/07_train_embedding.py                     # 기본값
  python scripts/07_train_embedding.py --epochs 3 --batch-size 8   # GPU 메모리 부족 시
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.rag.corpus_text import build_card_text, build_chunk_text  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
CORPUS_DIR = BASE / "data" / "corpus"
FINETUNE_DIR = BASE / "data" / "finetune"
DEFAULT_OUTPUT_DIR = BASE.parent / "models" / "postfab-bge-m3-v2"
DEFAULT_BASE_MODEL = "BAAI/bge-m3"


def load_corpus_texts() -> dict[str, str]:
    id_to_text: dict[str, str] = {}
    with open(CORPUS_DIR / "glossary_cards.jsonl", encoding="utf-8") as f:
        for line in f:
            card = json.loads(line)
            id_to_text[card["id"]] = build_card_text(card)
    with open(CORPUS_DIR / "article_chunks.jsonl", encoding="utf-8") as f:
        for line in f:
            chunk = json.loads(line)
            id_to_text[chunk["id"]] = build_chunk_text(chunk)
    return id_to_text


def load_qa_rows(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"입력 파일이 없습니다: {path} (먼저 06_split_qa.py 실행)")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def build_train_dataset(rows: list[dict], id_to_text: dict[str, str]):
    from datasets import Dataset
    pairs = [{"query": r["question"], "positive": id_to_text[r["source_id"]]} for r in rows]
    return Dataset.from_list(pairs)


def build_evaluator(rows: list[dict], id_to_text: dict[str, str], name: str):
    from sentence_transformers.evaluation import InformationRetrievalEvaluator
    queries = {str(i): r["question"] for i, r in enumerate(rows)}
    relevant_docs = {str(i): {r["source_id"]} for i, r in enumerate(rows)}
    return InformationRetrievalEvaluator(
        queries=queries, corpus=id_to_text, relevant_docs=relevant_docs,
        name=name, accuracy_at_k=[1, 3, 5], ndcg_at_k=[10], show_progress_bar=True,
    )


def main():
    ap = argparse.ArgumentParser(description="bge-m3 임베딩 파인튜닝 (MultipleNegativesRankingLoss)")
    ap.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--warmup-ratio", type=float, default=0.1)
    args = ap.parse_args()

    import torch
    from sentence_transformers import (SentenceTransformer, SentenceTransformerTrainer,
                                        SentenceTransformerTrainingArguments)
    from sentence_transformers.losses import MultipleNegativesRankingLoss

    has_cuda = torch.cuda.is_available()
    print(f"[07] CUDA: {has_cuda}"
          + (f" ({torch.cuda.get_device_name(0)})" if has_cuda else " — GPU 없이 CPU로 진행 시 매우 느립니다"))

    id_to_text = load_corpus_texts()
    print(f"[07] 코퍼스: {len(id_to_text)}개 (카드+청크)")

    train_rows = load_qa_rows(FINETUNE_DIR / "train.jsonl")
    valid_rows = load_qa_rows(FINETUNE_DIR / "valid.jsonl")
    train_dataset = build_train_dataset(train_rows, id_to_text)
    evaluator = build_evaluator(valid_rows, id_to_text, name="postfab-valid")
    print(f"[07] train: {len(train_rows)}쌍 / valid(모니터링용): {len(valid_rows)}쌍")

    model = SentenceTransformer(args.base_model)

    print("[07] 파인튜닝 시작 전 valid 성능:")
    before = evaluator(model)
    for k in (1, 3, 5):
        print(f"  Accuracy@{k}: {before[f'postfab-valid_cosine_accuracy@{k}']:.4f}")
    print(f"  NDCG@10:    {before['postfab-valid_cosine_ndcg@10']:.4f}")

    loss = MultipleNegativesRankingLoss(model)
    training_args = SentenceTransformerTrainingArguments(
        output_dir=str(Path(args.output_dir).with_name(Path(args.output_dir).name + "-checkpoints")),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        warmup_ratio=args.warmup_ratio,
        learning_rate=args.lr,
        fp16=has_cuda,
        save_strategy="epoch",
        save_total_limit=1,  # 체크포인트를 에폭마다 통째로 남기면 디스크가 금방 참 — 최신 1개만 유지
        logging_steps=10,
        load_best_model_at_end=False,
        report_to="none",
    )
    trainer = SentenceTransformerTrainer(
        model=model, args=training_args, train_dataset=train_dataset,
        loss=loss, evaluator=evaluator,
    )
    trainer.train()

    print("\n[07] 파인튜닝 후 valid 성능:")
    after = evaluator(model)
    for k in (1, 3, 5):
        b, a = before[f"postfab-valid_cosine_accuracy@{k}"], after[f"postfab-valid_cosine_accuracy@{k}"]
        print(f"  Accuracy@{k}: {b:.4f} -> {a:.4f}  ({'+' if a >= b else ''}{a - b:.4f})")
    b, a = before["postfab-valid_cosine_ndcg@10"], after["postfab-valid_cosine_ndcg@10"]
    print(f"  NDCG@10:    {b:.4f} -> {a:.4f}  ({'+' if a >= b else ''}{a - b:.4f})")

    model.save(args.output_dir)
    print(f"\n[07] 모델 저장 완료 -> {args.output_dir}")
    print("[07] 공식 전/후 비교는 test.jsonl로 08_eval_retrieval.py를 다시 돌려서 낼 것 "
          "(POSTFAB_EMBED_MODEL 전환 + build_vectorstore 재실행 먼저 필요)")


if __name__ == "__main__":
    main()
