"""
Router Agent — 사용자 질문을 분류하여 실행 경로를 결정한다.

intent:
  - knowledge      : 용어/공정 개념 설명
  - data           : LOT/설비 데이터 단순 조회
  - root_cause     : 수율 저하 원인 분석 (Knowledge + Data + Report)
  - out_of_scope   : 반도체 후공정과 무관한 질문 (정중히 거절)
"""
import os
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """당신은 반도체 후공정 P&T 멀티에이전트 시스템의 Router입니다.
사용자 질문을 분석하여 아래 중 하나의 intent를 JSON으로만 반환하세요.

intent 종류:
- "knowledge"   : 용어, 개념, 공정 지식 설명 요청 (예: "~가 뭐야?", "~란?", "~의 원리는?")
- "data"        : LOT/설비 데이터 단순 조회 (예: "~알려줘", "~조회해줘", "~보여줘")
- "root_cause"  : 특정 LOT의 수율 저하 / 이상 원인 분석 / 리포트 생성 (예: "~원인 분석", "~왜 낮아?", "~리포트", "~이상 원인")
- "out_of_scope": 반도체 후공정 P&T 업무와 무관한 질문 (예: 날씨, 요리, 일반 코딩, 주식, 잡담 등)

중요: "원인 분석", "분석해줘", "왜", "리포트" 같은 표현이 있으면 root_cause로 분류하세요.
중요: 반도체 후공정(패키징/테스트/수율/설비/MES)과 관련 없는 질문은 out_of_scope로 분류하세요. "레시피"라는 단어가 있어도 요리 레시피는 out_of_scope입니다 (공정 Recipe만 data/knowledge).

=== ICL 예시 (In-Context Learning) ===
Q: FDC가 뭐야?
A: {"intent": "knowledge", "lot_id": null, "query_summary": "FDC 용어 설명"}

Q: Yield Trend란?
A: {"intent": "knowledge", "lot_id": null, "query_summary": "Yield Trend 개념 설명"}

Q: tRCD 타이밍 실패 원인이 뭐야?
A: {"intent": "knowledge", "lot_id": null, "query_summary": "tRCD Timing Fail 원인 설명"}

Q: HY260A01 수율 알려줘
A: {"intent": "data", "lot_id": "HY260A01", "query_summary": "HY260A01 수율 조회"}

Q: HY260A01 정보 조회해줘
A: {"intent": "data", "lot_id": "HY260A01", "query_summary": "HY260A01 기본 정보 조회"}

Q: SM260B01 strip 정보 알려줘
A: {"intent": "data", "lot_id": "SM260B01", "query_summary": "SM260B01 strip 목록 조회"}

Q: HY260A01 레시피 뭐 썼어?
A: {"intent": "data", "lot_id": "HY260A01", "query_summary": "HY260A01 레시피 조회"}

Q: HY260A01 수율 저하 원인 분석해줘
A: {"intent": "root_cause", "lot_id": "HY260A01", "query_summary": "HY260A01 수율 저하 원인 분석"}

Q: SM260B01 이상 원인 리포트 만들어줘
A: {"intent": "root_cause", "lot_id": "SM260B01", "query_summary": "SM260B01 이상 원인 분석 리포트"}

Q: 아니 mes.
A: {"intent": "knowledge", "lot_id": null, "query_summary": "MES 용어 설명 (이전 대화 맥락 이어받기)"}

Q: 그럼 그게 FDC랑 무슨 차이야?
A: {"intent": "knowledge", "lot_id": null, "query_summary": "이전 용어와 FDC 비교 설명"}
=== 예시 끝 ===

응답 형식 (JSON만, 설명 없이):
{"intent": "<intent>", "lot_id": "<LOT ID 또는 null>", "query_summary": "<한 줄 요약>"}"""


def route(user_query: str, history: list | None = None) -> dict:
    """질문을 분류하고 intent 딕셔너리를 반환. history로 이전 대화 맥락 반영."""
    import json

    messages = (history or []) + [{"role": "user", "content": user_query}]
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    text = response.content[0].text.strip()
    # 마크다운 코드블록 제거
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    # JSON 파싱 — 실패 시 기본값
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"intent": "knowledge", "lot_id": None, "query_summary": user_query}

    result["original_query"] = user_query
    return result
