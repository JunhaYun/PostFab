"""
06_split_qa.py — QA 쌍을 train/valid/test로 분할.

입력: data/finetune/qa_pairs.jsonl (1,372개, 05 산출물)
출력: data/finetune/train.jsonl, valid.jsonl, test.jsonl

분할 설계:
  - 분할 단위 = source_id (카드/청크 하나). 같은 source_id의 formal/casual 페어는
    항상 같은 split에 남긴다 — 둘로 쪼개면 08 평가에서 answer가 이미 노출된 채로
    검색 정확도를 재는 꼴이라 데이터 누수가 된다.
  - 05 품질점검에서 발견한 "동일 질문 텍스트가 서로 다른 source_id(정답)를 가리키는"
    완전 중복 그룹은 eval(valid/test)에서 정답이 모호해지므로, 그 그룹에 속한
    source_id 전부를 강제로 train에 배치한다(무작위 배정 대상에서 제외).
  - 나머지 source_id를 고정 시드로 섞어 80/10/10 비율로 배정. 카드/청크 비율이
    유지되도록 source_type별로 각각 섞은 뒤 같은 비율로 잘라 합친다(계층 분할).

사용법:
  python scripts/06_split_qa.py                 # 기본 80/10/10, seed=42
  python scripts/06_split_qa.py --seed 7
"""
import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
IN_PATH = BASE / "data" / "finetune" / "qa_pairs.jsonl"
OUT_DIR = BASE / "data" / "finetune"


def load_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def find_forced_train_ids(rows: list[dict]) -> dict[str, list[str]]:
    """동일 question 텍스트가 여러 source_id를 가리키는 그룹 -> {question: [source_id, ...]}"""
    by_question: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        by_question[r["question"]].add(r["source_id"])
    return {q: sorted(ids) for q, ids in by_question.items() if len(ids) > 1}


def stratified_split(source_ids: list[str], id_to_type: dict[str, str],
                      ratios: tuple[float, float, float], seed: int) -> dict[str, str]:
    """source_type별로 나눠 섞은 뒤 비율대로 잘라 합친다. 반환: source_id -> split명"""
    by_type: dict[str, list[str]] = defaultdict(list)
    for sid in source_ids:
        by_type[id_to_type[sid]].append(sid)

    rng = random.Random(seed)
    assignment: dict[str, str] = {}
    train_r, valid_r, _test_r = ratios
    for stype, ids in by_type.items():
        ids = sorted(ids)  # 정렬 후 셔플 -> 입력 순서 무관하게 재현 가능
        rng.shuffle(ids)
        n = len(ids)
        n_train = round(n * train_r)
        n_valid = round(n * valid_r)
        n_valid = min(n_valid, n - n_train)  # 반올림 오차로 초과하지 않도록
        for sid in ids[:n_train]:
            assignment[sid] = "train"
        for sid in ids[n_train:n_train + n_valid]:
            assignment[sid] = "valid"
        for sid in ids[n_train + n_valid:]:
            assignment[sid] = "test"
    return assignment


def main():
    ap = argparse.ArgumentParser(description="QA 쌍을 train/valid/test로 분할 (source_id 단위, 중복질문 누수 방지)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--train-ratio", type=float, default=0.8)
    ap.add_argument("--valid-ratio", type=float, default=0.1)
    ap.add_argument("--test-ratio", type=float, default=0.1)
    args = ap.parse_args()

    ratios = (args.train_ratio, args.valid_ratio, args.test_ratio)
    if abs(sum(ratios) - 1.0) > 1e-6:
        sys.exit(f"비율 합이 1이 아닙니다: {ratios}")

    if not IN_PATH.exists():
        sys.exit(f"입력 파일이 없습니다: {IN_PATH} (먼저 05_generate_qa.py 실행)")

    rows = load_rows(IN_PATH)
    print(f"총 QA 행: {len(rows)}")

    id_to_type = {r["source_id"]: r["source_type"] for r in rows}
    all_source_ids = sorted(id_to_type)
    print(f"총 source(카드+청크): {len(all_source_ids)}")

    dup_groups = find_forced_train_ids(rows)
    forced_train_ids = sorted({sid for ids in dup_groups.values() for sid in ids})
    print(f"\n완전 중복 질문 그룹: {len(dup_groups)}개 (source {len(forced_train_ids)}개, train 고정)")
    for q, ids in dup_groups.items():
        print(f"  {q!r} -> {ids}")

    free_ids = [sid for sid in all_source_ids if sid not in forced_train_ids]
    assignment = stratified_split(free_ids, id_to_type, ratios, args.seed)
    for sid in forced_train_ids:
        assignment[sid] = "train"

    split_rows: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        split_rows[assignment[r["source_id"]]].append(r)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for split in ("train", "valid", "test"):
        out_path = OUT_DIR / f"{split}.jsonl"
        with open(out_path, "w", encoding="utf-8") as f:
            for r in split_rows[split]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\n{split}: {out_path.name} — {len(split_rows[split])}행 "
              f"({len({r['source_id'] for r in split_rows[split]})} sources)")
        by_stype = defaultdict(int)
        by_qtype = defaultdict(int)
        by_phrasing = defaultdict(int)
        for r in split_rows[split]:
            by_stype[r["source_type"]] += 1
            by_qtype[r["question_type"]] += 1
            by_phrasing[r["phrasing"]] += 1
        print(f"  source_type: {dict(by_stype)}")
        print(f"  question_type: {dict(by_qtype)}")
        print(f"  phrasing: {dict(by_phrasing)}")

    total = sum(len(v) for v in split_rows.values())
    assert total == len(rows), f"행 수 불일치: {total} != {len(rows)}"
    print(f"\n검증 통과: 총 {total}행, 분할 합 일치")


if __name__ == "__main__":
    main()
