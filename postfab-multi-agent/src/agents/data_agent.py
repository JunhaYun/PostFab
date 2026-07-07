"""
Data Agent — Claude Function Calling으로 Mock DB를 조회한다.
agentic loop: Claude가 필요한 도구를 스스로 선택하고 결과를 해석한다.
"""
import os
import json
import anthropic
from src.tools.postfab_tools import TOOL_SPECS, execute_tool

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """당신은 반도체 후공정 데이터 조회 전문가입니다.
사용자 요청에 따라 제공된 도구를 사용하여 데이터를 조회하고, 결과를 한국어로 간결하게 요약하세요."""


def query(user_request: str, log: list | None = None, history: list | None = None) -> tuple[str, dict]:
    """
    Function Calling agentic loop 실행.
    Returns:
        answer (str): LLM 최종 답변
        collected_data (dict): 수집된 원본 데이터 (Report Agent용)
    """
    messages = (history or []) + [{"role": "user", "content": user_request}]
    collected_data = {}

    while True:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOL_SPECS,
            messages=messages,
        )

        # 도구 호출이 없으면 최종 답변
        if response.stop_reason != "tool_use":
            answer = ""
            for block in response.content:
                if hasattr(block, "text"):
                    answer = block.text.strip()
            return answer, collected_data

        # 도구 호출 처리
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input
            result_str = execute_tool(tool_name, tool_input)
            result_json = json.loads(result_str)

            # 수집 데이터 저장
            collected_data[tool_name] = result_json

            if log is not None:
                log.append({
                    "step": "Function Call",
                    "tool": tool_name,
                    "input": tool_input,
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
