"""
Planner Agent — root_cause 분석 시 실행 계획을 수립한다.
"""
import os
import json
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """당신은 반도체 후공정 수율 저하 원인 분석 플래너입니다.
사용자 요청을 분석하여 분석에 필요한 단계를 JSON 배열로만 반환하세요.

사용 가능한 단계:
- get_lot_info    : LOT 기본 정보 조회
- get_test_result : 공정별 수율 및 사용 설비 조회
- get_recipe      : 사용 레시피 및 스펙(항목별 min/max) 조회
- get_eqp_history : 설비 실측값 조회 (레시피 스펙 이탈 여부 확인)
- get_strip_map   : Strip 목록 및 위치 조회 (Strip 이상 시)
- get_emap        : Strip별 emap 분석 (불량 위치 패턴 확인)
- search_knowledge : 공정 지식 검색
- generate_report  : 원인 분석 리포트 생성

응답 형식 (JSON 배열만, 설명 없이):
["get_lot_info", "get_test_result", ...]"""


def plan(user_query: str, lot_id: str | None = None) -> list[str]:
    """분석 단계 목록을 반환."""
    context = f"질문: {user_query}"
    if lot_id:
        context += f"\nLOT ID: {lot_id}"

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": context}],
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        steps = json.loads(text)
        if isinstance(steps, list):
            return steps
    except json.JSONDecodeError:
        pass

    # 파싱 실패 시 기본 전체 계획
    return [
        "get_lot_info",
        "get_test_result",
        "get_recipe",
        "get_eqp_history",
        "search_knowledge",
        "generate_report",
    ]
