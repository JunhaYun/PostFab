"""
Router intent 분류 정확도 평가 러너.

측정 항목:
  1) 전체 intent 분류 정확도 (knowledge/data/root_cause/out_of_scope)
  2) intent별 정확도 breakdown — out_of_scope가 유독 새는지 등을 구분해서 봄
  3) history가 있는 케이스(멀티턴 맥락 이어받기)도 같은 표에 포함

09_eval_data_agent.py는 data 경로의 도구 선택까지 재지만, 이 스크립트는
Router 한 단계(route_by_intent 이전, 순수 텍스트 분류)만 떼어서 본다.

사용:
  postfab/Scripts/python.exe scripts/10_eval_router.py
  (ANTHROPIC_API_KEY 필요. --limit N 으로 앞 N개만 평가 가능)
"""
import os
import sys
import json
import argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.agents import router_agent

EVAL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "eval", "router_eval.json")


def run(limit: int | None = None):
    with open(EVAL_PATH, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    if limit:
        cases = cases[:limit]

    rows = []
    by_intent = defaultdict(lambda: [0, 0])  # expected_intent -> [ok, total]

    for c in cases:
        route = router_agent.route(c["query"], history=c.get("history"))
        got = route.get("intent")
        expected = c["expected_intent"]
        hit = (got == expected)

        by_intent[expected][1] += 1
        by_intent[expected][0] += hit
        rows.append((c["id"], expected, got, hit, bool(c.get("history"))))

    print(f"\n{'ID':<22} {'expected':<14} {'got':<14} {'':<4} ctx")
    print("-" * 66)
    for cid, expected, got, hit, has_ctx in rows:
        mark = "OK " if hit else "XX "
        ctx = "O" if has_ctx else ""
        print(f"{cid:<22} {expected:<14} {got:<14} {mark:<4} {ctx}")

    n = len(cases)
    total_ok = sum(hit for *_, hit, _ in rows)
    print("-" * 66)
    print(f"전체 정확도: {total_ok}/{n} = {total_ok/n*100:.1f}%\n")

    print("intent별 breakdown:")
    for intent in sorted(by_intent):
        ok, total = by_intent[intent]
        print(f"  {intent:<14} {ok}/{total} = {ok/total*100:.1f}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
