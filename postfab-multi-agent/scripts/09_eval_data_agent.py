"""
Data Agent(쿼리 조회/수율 분석) 경로 평가 러너.

측정 항목:
  1) Router intent 분류 정확도   (질문 → 올바른 intent)
  2) Data Agent 도구 선택 정확도 (data 질문 → 올바른 도구 호출; expected_tools any-of)

RAG 쪽(08_eval_retrieval)은 NDCG로 검색 품질을 재지만, 이 스크립트는
Function Calling 경로가 "질문에 맞는 도구를 고르는가"를 정량화한다.

사용:
  postfab/Scripts/python.exe scripts/09_eval_data_agent.py
  (ANTHROPIC_API_KEY 필요. --limit N 으로 앞 N개만 평가 가능)
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.agents import router_agent, data_agent

EVAL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "eval", "data_agent_eval.json")


def _tools_called(log: list) -> list[str]:
    return [e["tool"] for e in log if e.get("step") == "Function Call"]


def run(limit: int | None = None):
    with open(EVAL_PATH, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    if limit:
        cases = cases[:limit]

    intent_ok = 0
    tool_ok = 0
    tool_total = 0  # 도구 평가 대상(=expected_tools가 있는 케이스)
    rows = []

    for c in cases:
        route = router_agent.route(c["query"])
        intent = route.get("intent")
        intent_hit = (intent == c["expected_intent"])
        intent_ok += intent_hit

        called, tool_hit = [], None
        expected = c.get("expected_tools", [])
        # data 경로이고 기대 도구가 있는 케이스만 Data Agent를 실제로 돌린다
        if expected and intent == "data":
            tool_total += 1
            log: list = []
            try:
                _, _ = data_agent.query(c["query"], log=log)
            except Exception as e:
                log.append({"step": "Error", "detail": str(e)})
            called = _tools_called(log)
            tool_hit = any(t in called for t in expected)
            tool_ok += bool(tool_hit)

        rows.append((c["id"], intent_hit, intent, tool_hit, called))

    # 리포트
    print(f"\n{'ID':<18} {'INTENT':<8} {'got':<12} {'TOOL':<6} called")
    print("-" * 78)
    for cid, ih, got, th, called in rows:
        ic = "OK " if ih else "XX "
        tc = "" if th is None else ("OK " if th else "XX ")
        print(f"{cid:<18} {ic:<8} {got:<12} {tc:<6} {called}")

    n = len(cases)
    print("-" * 78)
    print(f"Intent 정확도 : {intent_ok}/{n} = {intent_ok/n*100:.1f}%")
    if tool_total:
        print(f"도구 선택 정확도: {tool_ok}/{tool_total} = {tool_ok/tool_total*100:.1f}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
