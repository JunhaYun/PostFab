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


def kw_label(kw) -> str:
    """키워드 표기 — any-of 목록이면 대표값(첫 항목)으로 보여준다."""
    return kw[0] if isinstance(kw, list) else kw


def kw_hit(kw, answer: str) -> bool:
    """문자열이면 정확 포함, 목록이면 하나라도 포함되면 정답(any-of).

    식별자(ML001 등)는 문자열로 남겨 정확 비교하고, 사람이 붙인 라벨만 목록으로 온다.
    """
    if isinstance(kw, list):
        return any(v in answer for v in kw)
    return kw in answer


# ── LLM 채점 ────────────────────────────────────────────────────────────────
# 문자열 채점은 표기가 흔들리면 오판하고, LLM 채점은 실행마다 흔들린다. 둘을 겸한다:
# 사실 채점(결정론적)은 회귀 비교의 기준선으로 두고, 의미 채점(LLM)은 별도 지표로 본다.
# 채점기가 후하게 주는 것을 막으려고 정답지를 함께 넘기고 판정 근거를 쓰게 한다.
JUDGE_MODEL = "claude-sonnet-4-6"

# 사건 유형마다 정답지에 있는 항목이 다르다. 불량 사건은 공정/설비/불량명을 갖지만
# strip 사건의 정답은 strip ID와 불량 위치 패턴뿐이다. 유형에 상관없이 같은 항목을 물으면
# 채점기가 정답지에 없는 것을 판정하게 되어 결과가 오락가락한다(실제로 그랬다).
JUDGE_CRITERIA = {
    "strip_pattern": [
        ("strip", "정답의 최악 strip을 맞게 지목했는가"),
        ("pattern", "불량 위치 패턴을 맞게 특정했는가 (표현이 달라도 같은 패턴이면 통과)"),
        ("cause_sound", "원인 설명이 그 패턴의 알려진 원인과 부합하는가"),
    ],
    "_default": [
        ("step", "정답의 공정을 맞게 특정했는가"),
        ("equipment", "정답의 설비를 맞게 특정했는가"),
        ("defect", "정답의 불량 유형을 맞게 특정했는가 (표현이 달라도 같은 불량이면 통과)"),
        ("cause_sound", "원인 설명이 그 불량 유형의 알려진 원인과 부합하는가"),
    ],
}

JUDGE_SYSTEM = """당신은 반도체 후공정 원인 분석 리포트를 채점하는 평가자입니다.
[정답]은 데이터에 실제로 심어둔 사실입니다. [리포트]가 이를 짚었는지 [판정 항목]별로 보세요.

판정 기준:
- 표현이 달라도 같은 내용을 가리키면 통과로 봅니다 (예: "가장자리 집중" = "에지 집중형").
- 정답과 다른 대상을 지목했으면 불통과입니다 (예: 다른 설비 ID).
- 언급조차 없으면 불통과입니다. 후하게 주지 마세요.
- [판정 항목]에 없는 것은 판정하지 마세요.

JSON만 반환하세요. 각 항목 키에 true/false, 그리고 "reason"에 판정 근거 한 줄."""


def criteria_for(case: dict) -> list[tuple[str, str]]:
    return JUDGE_CRITERIA.get(case["event_type"], JUDGE_CRITERIA["_default"])


def judge(case: dict, answer: str) -> dict | None:
    """LLM 채점. 실패 시 None(집계에서 제외)."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    crit = criteria_for(case)
    truth = {k: case.get(k) for k in ("event_type", "related_doc")}
    truth["정답 키워드"] = [kw_label(k) for k in case["expected_keywords"]]
    payload = (f"[질문]\n{case['query']}\n\n"
               f"[정답]\n{json.dumps(truth, ensure_ascii=False)}\n\n"
               f"[판정 항목]\n" + "\n".join(f"- {k}: {desc}" for k, desc in crit) + "\n\n"
               f"[리포트]\n{answer[:6000]}")
    try:
        resp = client.messages.create(
            model=JUDGE_MODEL, max_tokens=400,
            # 채점기는 자[尺]다 — 같은 리포트에 같은 판정이 나와야 한다.
            # 미지정 시 기본값 1.0이라 재실행 때마다 판정이 바뀔 수 있었다.
            temperature=0,
            system=JUDGE_SYSTEM, messages=[{"role": "user", "content": payload}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        if text.startswith("```"):
            text = text.split("```")[1].removeprefix("json").strip()
        return json.loads(text)
    except Exception as e:
        print(f"        [판정 실패] {str(e)[:80]}")
        return None


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


def run(limit: int | None = None, ids: list[str] | None = None, use_judge: bool = True):
    with open(EVAL_PATH, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    if ids:
        cases = [c for c in cases if c["id"] in set(ids)]
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
        hits = [kw_label(k) for k in c["expected_keywords"] if kw_hit(k, answer)]
        misses = [kw_label(k) for k in c["expected_keywords"] if not kw_hit(k, answer)]
        passed = intent_ok and not misses
        cited = doc_cited(r.get("log", []), c["related_doc"])
        verdict = judge(c, answer) if use_judge else None

        by_type[c["event_type"]][1] += 1
        by_type[c["event_type"]][0] += passed
        rows.append({**c, "intent_ok": intent_ok, "hits": hits, "misses": misses,
                      "passed": passed, "cited": cited, "judge": verdict,
                      # 답변 원문과 검색된 문서 제목을 남긴다 —
                      # 없으면 실패 원인을 보려고 매번 재실행해야 한다(실제로 그랬다)
                      "answer": answer,
                      "context_titles": next((e.get("context_titles", []) for e in r.get("log", [])
                                              if e.get("step") == "Knowledge 검색"), [])})
        keys = [k for k, _ in criteria_for(c)]
        j = ""
        if verdict:
            j = " 판정 " + "".join("O" if verdict.get(k) else "X" for k in keys)
        print(f"  [{idx}/{len(cases)}] {'OK ' if passed else 'XX '} {c['id']:<16} "
              f"{'인용O' if cited else '인용X'}{j}  {c['query'][:30]}")
        if misses:
            print(f"        놓친 키워드: {misses}")
        if verdict and not all(verdict.get(k) for k in keys):
            print(f"        판정 근거: {verdict.get('reason', '')[:110]}")

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

    judged = [r for r in rows if r.get("judge")]
    if judged:
        # 항목이 사건 유형마다 달라서 분모도 유형별로 다르다 — 항목별로 따로 센다
        print(f"\nLLM 의미 채점 (n={len(judged)}):")
        tally: dict[str, list[int]] = {}
        for r in judged:
            for key, _ in criteria_for(r):
                slot = tally.setdefault(key, [0, 0])
                slot[1] += 1
                slot[0] += bool(r["judge"].get(key))
        for key, (ok, tot) in tally.items():
            print(f"  {key:<14} {ok}/{tot} = {ok / tot * 100:.1f}%")
        allok = sum(1 for r in judged if all(r["judge"].get(k) for k, _ in criteria_for(r)))
        print(f"  {'전 항목 통과':<14} {allok}/{len(judged)} = {allok / len(judged) * 100:.1f}%")
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
    ap.add_argument("--ids", default=None,
                     help="쉼표로 구분한 문항 ID만 실행 (예: 재실행 비용 절감용)")
    ap.add_argument("--no-judge", action="store_true", help="LLM 의미 채점 생략")
    ap.add_argument("--out", default=None, help="결과 저장 경로 (기본 e2e_result.json)")
    args = ap.parse_args()
    if args.out:
        OUT_PATH = os.path.join(BASE, "data", "eval", args.out)
    run(limit=args.limit,
        ids=[s.strip() for s in args.ids.split(",")] if args.ids else None,
        use_judge=not args.no_judge)
