"""
13_eval_e2e.py — E2E(원인 분석) 평가 러너. 로드맵 ④의 채점기.

09는 도구 선택까지, 10은 intent 분류까지만 본다. 이 스크립트는 workflow.run()을 그대로
호출해 Router→Planner→Data→Knowledge→Report 전 구간을 태우고, **최종 리포트 본문**에
정답이 들어 있는지를 잰다.

측정 항목:
  1) intent 정확도       — root_cause로 라우팅되는가
  2) 정답 키워드 재현율   — 리포트가 공정/설비/불량명(+스펙 항목)을 짚었는가
  3) 문항 성공률          — 정답 키워드를 전부 포함한 문항 비율(부분 점수 없음)
  4) 지식베이스 인용률    — 해당 사건의 대응 문서가 검색 컨텍스트에 실제로 들어왔는가

4번을 따로 재는 이유: 리포트에 그럴듯한 원인 설명이 있어도 그게 지식베이스에서 온 게
아니라 LLM 내부 지식일 수 있기 때문이다(실제로 그런 상태였던 적이 있어 이 지표를 넣었다).

사용:
  postfab/Scripts/python.exe scripts/13_eval_e2e.py
  postfab/Scripts/python.exe scripts/13_eval_e2e.py --limit 5
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src import workflow

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_PATH = os.path.join(BASE, "data", "eval", "e2e_eval.json")
OUT_PATH = os.path.join(BASE, "data", "eval", "e2e_result.json")


def knowledge_query(log: list) -> str:
    for e in log:
        if e.get("step") == "Knowledge 검색":
            return e.get("query", "")
    return ""


def doc_cited(log: list, related_doc: str) -> bool:
    """대응 문서가 검색 컨텍스트에 들어왔는지 — 검색어가 아니라 문서 제목으로 확인.

    검색 결과의 title은 섹션 헤더 전체("[후공정 불량 트러블슈팅 카드] > 몰딩 온도 …")라
    정답지의 문서명은 그 일부다. 따라서 완전 일치가 아니라 포함 여부로 본다.
    """
    section = related_doc.split("> ")[-1].strip()
    for e in log:
        if e.get("step") == "Knowledge 검색":
            return any(section in t for t in e.get("context_titles", []))
    return False


def run(limit: int | None = None):
    with open(EVAL_PATH, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    if limit:
        cases = cases[:limit]

    rows = []
    by_type = defaultdict(lambda: [0, 0])
    consecutive_api_errors = 0

    for idx, c in enumerate(cases, start=1):
        try:
            r = workflow.run(c["query"])
        except Exception as e:
            rows.append({**c, "intent_ok": False, "hits": [], "misses": c["expected_keywords"],
                          "passed": False, "cited": False, "error": str(e)})
            continue

        # workflow는 API 오류를 삼키고 intent="error"로 돌려준다. 이걸 오답으로 세면
        # 크레딧 소진·레이트리밋 같은 인프라 문제가 "성능 저하"로 기록돼 버린다.
        if r["router"]["intent"] == "error":
            detail = next((e.get("detail", "") for e in r.get("log", [])
                           if e.get("step") == "Error"), "")
            consecutive_api_errors += 1
            print(f"  [{idx}/{len(cases)}] !! {c['id']:<16} API 오류: {detail[:110]}")
            if consecutive_api_errors >= 3:
                sys.exit("\n[13] API 오류가 연속 3회 발생해 중단합니다. "
                         "점수를 남기지 않습니다(인프라 문제를 성능 수치로 기록하지 않기 위함).\n"
                         "     크레딧/키를 확인한 뒤 다시 실행하세요.")
            continue
        consecutive_api_errors = 0

        answer = r.get("answer", "")
        intent_ok = r["router"]["intent"] == c["expected_intent"]
        hits = [k for k in c["expected_keywords"] if k in answer]
        misses = [k for k in c["expected_keywords"] if k not in answer]
        passed = intent_ok and not misses
        cited = doc_cited(r.get("log", []), c["related_doc"])

        by_type[c["event_type"]][1] += 1
        by_type[c["event_type"]][0] += passed
        rows.append({**c, "intent_ok": intent_ok, "hits": hits, "misses": misses,
                      "passed": passed, "cited": cited})
        print(f"  [{idx}/{len(cases)}] {'OK ' if passed else 'XX '} {c['id']:<16} "
              f"{'인용O' if cited else '인용X'}  {c['query'][:34]}")
        if misses:
            print(f"        놓친 키워드: {misses}")

    n = len(rows)
    intent_ok = sum(r["intent_ok"] for r in rows)
    passed = sum(r["passed"] for r in rows)
    cited = sum(r["cited"] for r in rows)
    total_kw = sum(len(r["expected_keywords"]) for r in rows)
    hit_kw = sum(len(r["hits"]) for r in rows)

    print("\n" + "=" * 62)
    print(f"intent 정확도    : {intent_ok}/{n} = {intent_ok / n * 100:.1f}%")
    print(f"정답 키워드 재현 : {hit_kw}/{total_kw} = {hit_kw / total_kw * 100:.1f}%")
    print(f"문항 성공률      : {passed}/{n} = {passed / n * 100:.1f}%")
    print(f"지식베이스 인용률: {cited}/{n} = {cited / n * 100:.1f}%")
    print("\n사건 유형별 성공률:")
    for t in sorted(by_type):
        ok, tot = by_type[t]
        print(f"  {t:<24} {ok}/{tot} = {ok / tot * 100:.1f}%")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "n": n,
            "intent_accuracy": round(intent_ok / n, 4),
            "keyword_recall": round(hit_kw / total_kw, 4),
            "case_pass_rate": round(passed / n, 4),
            "doc_citation_rate": round(cited / n, 4),
            "by_event_type": {t: {"passed": v[0], "total": v[1]} for t, v in by_type.items()},
            "cases": rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n저장 → {OUT_PATH}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
