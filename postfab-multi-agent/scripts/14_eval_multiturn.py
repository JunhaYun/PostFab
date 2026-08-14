"""
14_eval_multiturn.py — 멀티턴 평가 러너 (로드맵 ⑥).

10번은 단일 질문의 intent만, 13번은 원인분석 리포트 내용만 본다. 이 스크립트는
**대화**를 본다: 앞 턴의 답을 history로 넣고 다음 턴을 던져서, 지시어('걔', '첫번째',
'그게', '아까 그 LOT')가 앞 턴의 값으로 풀리는지를 잰다.

intent만 재면 부족하다 — "걔가 처리한 LOT"이 data로 분류돼도 eqp_id를 안 채우면
엉뚱한 답이 나온다. 그래서 **도구 인자와 lot_id까지** 채점한다.

측정 항목:
  1) intent 정확도        — 턴마다 올바른 경로로 가는가
  2) 지시어 해석 정확도    — lot_id / 도구 인자가 앞 턴의 값으로 채워지는가  ← 핵심
  3) 대화 성공률          — 한 대화의 모든 턴이 통과해야 성공(부분 점수 없음)
  4) history 토큰         — 압축이 실제로 듣는지 확인용(턴별 누적 입력 토큰)

사용:
  postfab/Scripts/python.exe scripts/14_eval_multiturn.py
  postfab/Scripts/python.exe scripts/14_eval_multiturn.py --ids MT-01-drilldown
  postfab/Scripts/python.exe scripts/14_eval_multiturn.py --no-compact   # 압축 끄고 비교
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src import workflow

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_PATH = os.path.join(BASE, "data", "eval", "multiturn_eval.json")
OUT_PATH = os.path.join(BASE, "data", "eval", "multiturn_result.json")


def check_turn(turn: dict, result: dict) -> tuple[list[str], list[str]]:
    """한 턴을 채점한다. (통과 항목, 실패 사유) 반환."""
    ok, bad = [], []
    intent = result["router"]["intent"]
    lot_id = result["router"]["lot_id"]
    answer = result["answer"]
    calls = [e for e in result["log"] if e.get("step") == "Function Call"]

    if intent == turn["expect_intent"]:
        ok.append("intent")
    else:
        bad.append(f"intent {intent} (기대 {turn['expect_intent']})")

    # 지시어 해석 — 앞 턴의 값이 이번 턴 인자로 들어왔는가
    if "expect_lot_id" in turn:
        if lot_id == turn["expect_lot_id"]:
            ok.append("lot_id")
        else:
            bad.append(f"lot_id {lot_id} (기대 {turn['expect_lot_id']})")

    if "expect_tool" in turn:
        used = [c["tool"] for c in calls]
        if turn["expect_tool"] in used:
            ok.append("tool")
        else:
            bad.append(f"도구 {used or '없음'} (기대 {turn['expect_tool']})")

    if "expect_tool_input" in turn:
        merged = {}
        for c in calls:
            merged.update(c["input"])
        for k, v in turn["expect_tool_input"].items():
            if merged.get(k) == v:
                ok.append(f"인자:{k}")
            else:
                bad.append(f"인자 {k}={merged.get(k)} (기대 {v})")

    for kw in turn.get("expect_answer_contains", []):
        if kw in answer:
            ok.append(f"키워드:{kw}")
        else:
            bad.append(f"답변에 '{kw}' 없음")

    return ok, bad


def run(ids=None, compact=True):
    data = json.load(open(EVAL_PATH, encoding="utf-8"))
    convs = data["conversations"]
    if ids:
        convs = [c for c in convs if c["id"] in ids]

    rows = []
    n_turn = n_turn_ok = 0
    n_intent = n_intent_ok = 0
    n_ref = n_ref_ok = 0          # 지시어 해석(lot_id/도구 인자)만 따로 집계

    for conv in convs:
        print(f"\n{'=' * 78}\n{conv['id']} — {conv.get('설명', '')}\n{'-' * 78}")
        history, turn_rows, conv_ok = [], [], True

        for i, turn in enumerate(conv["turns"], 1):
            q = turn["query"]
            # --no-compact면 압축을 우회해 전/후 비교가 가능하게 한다
            hist_in = history if compact else list(history)
            if not compact:
                # workflow.run이 내부에서 압축하므로, 비교 모드에서는 원본을 그대로
                # 쓰도록 임시로 압축을 무력화한다
                saved = workflow.compact_history
                workflow.compact_history = lambda h: list(h or [])
            try:
                r = workflow.run(q, history=hist_in)
            finally:
                if not compact:
                    workflow.compact_history = saved

            ok, bad = check_turn(turn, r)
            n_turn += 1
            n_intent += 1
            if r["router"]["intent"] == turn["expect_intent"]:
                n_intent_ok += 1
            # 지시어 해석 지표 — 앞 턴의 값을 이번 턴 인자로 옮겼는지만 본다
            n_ref += (1 if "expect_lot_id" in turn else 0) + len(turn.get("expect_tool_input", {}))
            n_ref_ok += sum(1 for o in ok if o == "lot_id" or o.startswith("인자:"))
            if not bad:
                n_turn_ok += 1
            else:
                conv_ok = False

            mark = "OK" if not bad else "XX"
            print(f"  [턴 {i}] {mark}  {q}")
            print(f"          intent={r['router']['intent']} lot={r['router']['lot_id']} "
                  f"({r['latency_ms']}ms)")
            for c in [e for e in r["log"] if e.get("step") == "Function Call"]:
                print(f"          🔧 {c['tool']}({json.dumps(c['input'], ensure_ascii=False)})")
            for b in bad:
                print(f"          ↳ {b}")

            turn_rows.append({"query": q, "ok": ok, "bad": bad,
                              "intent": r["router"]["intent"], "lot_id": r["router"]["lot_id"],
                              "answer": r["answer"]})
            history = history + [{"role": "user", "content": q},
                                 {"role": "assistant", "content": r["answer"]}]

        rows.append({"id": conv["id"], "passed": conv_ok, "turns": turn_rows})
        print(f"  → 대화 {'통과' if conv_ok else '실패'}")

    n_conv_ok = sum(1 for r in rows if r["passed"])
    print("\n" + "=" * 78)
    print(f"intent 정확도    : {n_intent_ok}/{n_intent} = {n_intent_ok / n_intent * 100:.1f}%")
    print(f"지시어 해석 정확도: {n_ref_ok}/{n_ref} = "
          f"{n_ref_ok / n_ref * 100:.1f}%" if n_ref else "지시어 해석    : 대상 없음")
    print(f"턴 성공률        : {n_turn_ok}/{n_turn} = {n_turn_ok / n_turn * 100:.1f}%")
    print(f"대화 성공률      : {n_conv_ok}/{len(rows)} = {n_conv_ok / len(rows) * 100:.1f}%")
    print(f"압축             : {'ON' if compact else 'OFF (비교 모드)'}")

    payload = {"n_conversations": len(rows), "n_turns": n_turn,
               "intent_accuracy": round(n_intent_ok / n_intent, 4),
               "reference_resolution": round(n_ref_ok / n_ref, 4) if n_ref else None,
               "turn_pass_rate": round(n_turn_ok / n_turn, 4),
               "conversation_pass_rate": round(n_conv_ok / len(rows), 4),
               "compact": compact, "conversations": rows}
    try:
        os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
        with open(OUT_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n저장 → {OUT_PATH}")
    except OSError as e:
        print(f"\n[저장 실패] {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default=None, help="쉼표로 구분한 대화 ID만 실행")
    ap.add_argument("--no-compact", action="store_true",
                     help="history 압축을 끄고 실행 (압축 전/후 비교용)")
    ap.add_argument("--out", default=None, help="결과 저장 경로")
    args = ap.parse_args()
    if args.out:
        OUT_PATH = (os.path.abspath(args.out)
                    if os.path.isabs(args.out) or os.path.dirname(args.out)
                    else os.path.join(BASE, "data", "eval", args.out))
    run(ids=[s.strip() for s in args.ids.split(",")] if args.ids else None,
        compact=not args.no_compact)
