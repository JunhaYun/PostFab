"""
12_build_e2e_eval.py — E2E(원인 분석) 평가셋 생성기.

09/10이 Router·Data Agent를 단계별로 재는 반면, 이 평가셋은 파이프라인 전체를 한 번에
돌려 "최종 리포트가 심어둔 정답을 짚어냈는가"를 잰다.

문항은 손으로 쓰지 않고 `data/eval/simulated_events.json`(11번이 심은 정답지)에서 뽑는다.
시뮬레이션 데이터를 재생성하면 LOT ID가 바뀌므로, 문항을 고정해두면 곧 어긋나기 때문.
대신 **정답이 실제 데이터에서 확인되는 LOT만** 채택한다 — 사건 구간에 걸렸어도 다른 공정의
손실이 더 커서 worst_step이 뒤바뀌거나, 무작위 분포가 우연히 가장자리로 뭉치는 LOT이
있는데, 그런 문항은 "시스템이 틀렸다"가 아니라 "정답이 애매하다"에 해당하므로 걸러낸다.

채점은 정답 키워드 포함 여부로 한다(부분 점수 없음):
  - 불량 사건: 공정명 + 설비ID + 불량명 (스펙 위반 사건은 레시피 항목까지)
  - strip 사건: 최악 strip ID + 불량 위치 패턴

사용:
  postfab/Scripts/python.exe scripts/12_build_e2e_eval.py            # 생성
  postfab/Scripts/python.exe scripts/12_build_e2e_eval.py --per-event 3
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

from src.tools import postfab_tools as T

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS_PATH = os.path.join(BASE, "data", "eval", "simulated_events.json")
OUT_PATH = os.path.join(BASE, "data", "eval", "e2e_eval.json")

# 질문 말투를 섞는다 — 05번 QA 생성에서 확인했듯 현장 질문은 격식체만 오지 않는다.
PHRASINGS = [
    "{lot} 수율 저하 원인 분석해줘",
    "{lot} 왜 수율이 낮아?",
    "{lot} 이상 원인 리포트 만들어줘",
]
STRIP_PHRASINGS = [
    "{lot} 수율 이상 원인 분석해줘",
    "{lot} 어느 strip이 문제인지 원인까지 분석해줘",
    "{lot} strip 불량 원인 분석 리포트 만들어줘",
]


# 채점 키워드는 두 성격이 섞여 있다.
#   - 기계 식별자(AS_Mold, ML001, MOLD_TEMP_3, strip ID): 리포트가 그대로 옮겨 적으므로 정확 일치가 맞다.
#     오히려 ML001을 ML002로 쓰면 진짜 오답이라 느슨하게 보면 안 된다.
#   - 사람이 붙인 라벨(불량명, 불량 위치 패턴): 리포트가 자연스럽게 바꿔 쓴다.
#     예) 판정 라벨 "가장자리 분포" ↔ 리포트 "가장자리 집중" / 지식카드 "에지 집중형".
# 후자만 동의어를 허용한다. 허용 범위는 "같은 개념의 다른 표기"까지이며,
# 상위 개념(예: "몰드 온도")으로 넓히지 않는다 — 그러면 다른 키워드만 맞아도 통과해 점수가 부풀려진다.
LOCATION_SYNONYMS = {
    "중앙 집중": ["중앙 집중", "중앙 집중형", "Center Cluster"],
    "가장자리 분포": ["가장자리 분포", "가장자리 집중", "에지 집중", "엣지 집중", "Edge Concentration"],
    "라인·스트라이프 분포": ["라인·스트라이프", "라인/스트라이프", "스트라이프형", "Line/Stripe"],
    "산발 분포": ["산발 분포", "랜덤 산발", "산발 분포형", "Random Distribution"],
}

# 괄호 없는 불량명은 표기 흔들림만 허용한다(몰딩/몰드).
DEFECT_SYNONYMS = {
    "몰딩 온도 스펙 이탈": ["몰딩 온도 스펙 이탈", "몰드 온도 스펙 이탈", "몰딩 온도 이탈"],
}


def defect_variants(name: str) -> list[str]:
    """'박리 (Delamination)' → ['박리 (Delamination)', '박리', 'Delamination'].

    괄호 표기는 리포트마다 'Delamination(박리)', '박리' 등으로 갈리므로
    한글부/영문부 어느 쪽이든 인정한다(같은 개념의 다른 표기).
    """
    if name in DEFECT_SYNONYMS:
        return DEFECT_SYNONYMS[name]
    variants = [name]
    if "(" in name and name.endswith(")"):
        ko = name.split("(")[0].strip()
        en = name[name.rindex("(") + 1:-1].strip()
        variants += [v for v in (ko, en) if v]
    return variants


def verify_defect_case(lot: str, ev: dict) -> dict | None:
    """사건의 정답이 이 LOT의 데이터에서 실제로 재현되는지 확인하고 기대값을 만든다."""
    y = T.analyze_lot_yield(lot)
    if "error" in y or not y.get("worst_step"):
        return None
    worst = y["worst_step"]
    # 사건 공정이 최악 공정이어야 "원인을 짚었다"를 채점할 수 있다
    if worst["공정"] != ev["step"] or worst["설비"] != ev["eqp_id"]:
        return None
    if y.get("defect_type") != ev["defect_name"]:
        return None

    # 식별자는 문자열 그대로, 불량명은 표기 변형 목록(any-of)으로 넣는다
    expected = [ev["step"], ev["eqp_id"], defect_variants(ev["defect_name"])]
    if ev.get("root_cause_item"):
        v = T.check_spec_violation(lot)
        items = [x["항목"] for x in v.get("violations", [])]
        if ev["root_cause_item"] not in items:
            return None
        expected.append(ev["root_cause_item"])
    return {"expected_keywords": expected}


def verify_strip_case(lot: str, ev: dict, want_location: str) -> dict | None:
    s = T.analyze_strip_yield(lot)
    if "error" in s or not s.get("worst_strip"):
        return None
    worst = s["worst_strip"]
    if worst["fail_location"] != want_location:
        return None
    return {"expected_keywords": [
        worst["strip_id"],                                          # 식별자 — 정확 일치
        LOCATION_SYNONYMS.get(want_location, [want_location]),      # 패턴명 — 표기 변형 허용
    ]}


# analyze_strip_yield의 판정 용어 ← 11번이 심은 패턴명
LOCATION_OF_PATTERN = {
    "중앙 집중형 (Center Cluster)": "중앙 집중",
    "에지 집중형 (Edge Concentration)": "가장자리 분포",
    "라인/스트라이프형 (Line/Stripe Pattern)": "라인·스트라이프 분포",
    "랜덤 산발형 (Random Distribution)": "산발 분포",
}


def main():
    ap = argparse.ArgumentParser(description="심어둔 사건에서 E2E 평가 문항을 생성")
    ap.add_argument("--per-event", type=int, default=3, help="사건당 문항 수")
    args = ap.parse_args()

    if not os.path.exists(EVENTS_PATH):
        sys.exit(f"정답지가 없습니다: {EVENTS_PATH}\n먼저 scripts/11_simulate_data.py 를 실행하세요.")
    with open(EVENTS_PATH, encoding="utf-8") as f:
        data = json.load(f)

    cases, skipped = [], 0

    for ev in data["events"]:
        picked = 0
        for lot in ev["affected_lots"]:
            if picked >= args.per_event:
                break
            v = verify_defect_case(lot, ev)
            if v is None:
                skipped += 1
                continue
            cases.append({
                "id": f"{ev['event_id']}-{picked + 1}",
                "query": PHRASINGS[picked % len(PHRASINGS)].format(lot=lot),
                "lot_id": lot,
                "expected_intent": "root_cause",
                "event_id": ev["event_id"],
                "event_type": ev["type"],
                "related_doc": ev["related_doc"],
                **v,
            })
            picked += 1

    for ev in data.get("strip_events", []):
        want = LOCATION_OF_PATTERN[ev["emap_pattern"]]
        picked = 0
        for lot in ev["affected_lots"]:
            if picked >= args.per_event:
                break
            v = verify_strip_case(lot, ev, want)
            if v is None:
                skipped += 1
                continue
            cases.append({
                "id": f"{ev['event_id']}-{picked + 1}",
                "query": STRIP_PHRASINGS[picked % len(STRIP_PHRASINGS)].format(lot=lot),
                "lot_id": lot,
                "expected_intent": "root_cause",
                "event_id": ev["event_id"],
                "event_type": ev["type"],
                "related_doc": ev["related_doc"],
                **v,
            })
            picked += 1

    out = {
        "description": "E2E 원인 분석 평가셋. 11_simulate_data.py가 심은 사건에서 자동 생성되며, "
                       "정답이 실제 데이터에서 재현되는 LOT만 채택한다. "
                       "시뮬레이션 데이터를 재생성하면 이 스크립트도 다시 실행할 것.",
        "generated_from": {"seed": data.get("seed"), "n_lots": data.get("n_lots")},
        "n_cases": len(cases),
        "cases": cases,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"[12] 문항 {len(cases)}개 생성 (정답 불일치로 건너뜀 {skipped}개)")
    by_type: dict[str, int] = {}
    for c in cases:
        by_type[c["event_type"]] = by_type.get(c["event_type"], 0) + 1
    for k, v in sorted(by_type.items()):
        print(f"     {k:<24} {v}개")
    print(f"[12] 저장 → {OUT_PATH}")


if __name__ == "__main__":
    main()
