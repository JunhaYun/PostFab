"""
Data Agent — Claude Function Calling으로 Mock DB를 조회한다.
agentic loop: Claude가 필요한 도구를 스스로 선택하고 결과를 해석한다.
"""
import os
import json
import anthropic
from src.tools.postfab_tools import TOOL_SPECS, execute_tool

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """당신은 반도체 후공정(P&T) 데이터 조회·분석 전문가입니다.
제공된 도구로 데이터를 조회/분석하고 결과를 한국어로 간결하게 요약하세요.

[도구 선택 규칙]
- 기본 정보(제품/고객사/BOM/상태): get_lot_info
- 지나간 공정 순서·이력: get_test_result 또는 analyze_lot_yield
- 레시피/스펙: get_recipe
- 설비 실측값: get_eqp_history
- Strip 목록: get_strip_map

[수율은 '해상도'로 도구를 고른다 — 가장 중요]
- LOT·공정 단위 수율("이 LOT 수율", "어느 공정에서 빠졌어") → analyze_lot_yield
  · get_test_result는 원본 row 덤프이고, analyze_lot_yield는 최종 수율·손실 공정·최악 공정을 계산해준다. 수율 '분석' 질문은 analyze_lot_yield를 우선 쓴다.
- Strip·die 단위 수율("어느 strip이 이상해", "die 불량 위치") → analyze_strip_yield
- 이 둘은 경쟁이 아니라 해상도가 다르다. LOT 전체 수율이면 analyze_lot_yield, strip 안쪽이면 analyze_strip_yield.

[LOT 하나 vs 여러 LOT — 두 번째 갈림길]
- 위 도구들은 전부 'LOT ID를 이미 아는' 경우의 도구다. LOT을 지정하지 않고 기간으로 묻는 질문은 아래를 쓴다.
- "지난달 수율 낮은 LOT 뽑아줘", "6월에 안 좋았던 LOT" → find_low_yield_lots
- "6월 공정별/설비별 수율", "어느 설비가 제일 나빠", "월별 추이" → summarize_yield_by
- "5월 대비 6월 변화", "최근 나빠지는 설비/공정" → compare_yield_periods
- 날짜는 반드시 YYYY-MM-DD로 넘긴다. "지난달"처럼 상대 표현이면 오늘 날짜 기준으로 환산하되,
  기준이 모호하면 추측하지 말고 사용자에게 기간을 되묻는다.
- 기간 집계로 문제 LOT을 찾은 뒤 그 LOT을 더 파고들 때는 analyze_lot_yield로 이어간다.

[숫자 해석]
- 수율이 100% 미만이거나 REJECT>0이면, "어느 공정/strip에서 몇 개가 빠졌는지"를 반드시 명시한다.
- fail_location(중앙 집중/가장자리)이 있으면 그 패턴을 그대로 전달한다.

[데이터가 없을 때]
- 도구가 error/안내 메시지를 반환하면 추측하지 말고 사실대로 전한다.
  (예: 공정 미진행 LOT, strip 없는 LOT은 다른 도구를 안내한다.)

주의: 당신은 '조회·식별'만 담당한다. 원인 추정·권장 조치는 하지 않는다(원인분석 전용 경로의 몫)."""

# 최종 답변 전용 포매터 — 도구 선택(위 프롬프트)과 표현을 분리한다.
# 소형 모델이 풍부한 데이터를 보면 원인분석 리포트로 폭주하므로, 최종 답변은
# 수집된 JSON만 근거로 별도 호출에서 '짧은 사실 요약'으로 강제한다.
FORMAT_PROMPT = """너는 반도체 후공정 데이터 '조회 결과'를 사용자에게 전달하는 역할이다.
아래 조회 결과(JSON)만 근거로 사용자 질문에 2~4문장으로 답하라.

규칙:
- 조회된 사실(수율, 손실 공정/strip, 불량 위치, 기본정보 등)만 전달한다.
- 개수를 말할 때는 JSON에 이미 계산돼 있는 값(요약/전체_그룹수/조회건수 등)만 인용한다.
  목록의 항목을 직접 세지 않는다 — 목록은 상위 일부만 담겨 있어 세면 틀린다.
  JSON에 없는 개수는 아예 언급하지 않는다.
- 원인 추정·권장 조치·'요약/추정 원인/근거/조치' 같은 리포트 섹션을 절대 만들지 않는다.
- JSON에 없는 값이나 존재하지 않는 도구 이름을 지어내지 않는다.
- 데이터가 없거나 안내 메시지면 그 사실을 그대로 전한다.
- 큰 제목(#)·굵은 섹션 헤더 나열 금지. 필요하면 표 1개까지만 간단히 사용한다."""

# agentic loop 반복 상한 — LLM이 도구 호출을 멈추지 않을 때 무한 루프/비용 폭주 방지
MAX_TURNS = 10


def _summarize_answer(user_request: str, collected_data: dict) -> str:
    """수집된 데이터만 근거로 간결한 최종 답변을 생성한다(리포트 폭주 방지)."""
    payload = json.dumps(collected_data, ensure_ascii=False)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        temperature=0,   # 요약 표현 고정 (router_agent 주석 참고)
        system=FORMAT_PROMPT,
        messages=[{"role": "user",
                   "content": f"사용자 질문: {user_request}\n\n조회 결과(JSON):\n{payload}"}],
    )
    return "".join(b.text for b in resp.content if hasattr(b, "text")).strip()


def query(user_request: str, log: list | None = None, history: list | None = None,
          summarize: bool = True) -> tuple[str, dict]:
    """
    Function Calling agentic loop 실행.

    Args:
        summarize: True면 수집 데이터로 간결한 최종 답변을 별도 생성(시나리오2 단독 조회).
                   False면 도구 루프의 원본 답변을 그대로 반환(root_cause처럼 answer를
                   버리고 collected_data만 쓰는 경우 불필요한 호출 절약).
    Returns:
        answer (str): 최종 답변
        collected_data (dict): 수집된 원본 데이터 (Report Agent용)
    """
    messages = (history or []) + [{"role": "user", "content": user_request}]
    collected_data = {}

    for _ in range(MAX_TURNS):
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            temperature=0,   # 도구 선택이 실행마다 달라지지 않도록 고정
            system=SYSTEM_PROMPT,
            tools=TOOL_SPECS,
            messages=messages,
        )

        # 도구 호출이 없으면 최종 답변 단계
        if response.stop_reason != "tool_use":
            raw = ""
            for block in response.content:
                if hasattr(block, "text"):
                    raw = block.text.strip()
            # 데이터를 실제로 조회했으면 포매터로 간결화(리포트 폭주 방지),
            # 조회가 없었으면(되묻기 등) 원본 답변을 그대로 사용.
            if summarize and collected_data:
                return _summarize_answer(user_request, collected_data), collected_data
            return raw, collected_data

        # 도구 호출 처리
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input
            result_str = execute_tool(tool_name, tool_input)
            result_json = json.loads(result_str)

            # 수집 데이터 저장 — 같은 도구를 다른 인자로 여러 번 호출해도
            # (예: get_emap을 strip 3개에 각각 호출) 덮어쓰지 않도록 인자를 키에 포함
            args_key = ", ".join(str(v) for v in tool_input.values())
            collected_data[f"{tool_name}({args_key})"] = result_json

            if log is not None:
                # 조회 대상이 없는 것(예: strip 없는 LOT에 analyze_strip_yield)은 실패가 아니라
                # '해당 없음'이다. 시뮬 LOT의 94%는 strip이 없어 이 경우가 흔한데, UI에 원본
                # error JSON을 그대로 띄우면 정상 동작이 오류처럼 보인다 → 표시용으로 구분한다.
                no_data = isinstance(result_json, dict) and (
                    "error" in result_json or set(result_json) == {"message"}
                )
                log.append({
                    "step": "Function Call",
                    "tool": tool_name,
                    "input": tool_input,
                    "status": "no_data" if no_data else "ok",
                    "result_preview": result_str[:200],
                })

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })

        # messages 업데이트
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    # MAX_TURNS 초과 — 수집된 데이터만이라도 반환
    return "도구 호출 한도를 초과했습니다. 지금까지 수집된 데이터로 답변을 구성합니다.", collected_data
