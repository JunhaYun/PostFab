"""
Report Agent — Knowledge + Data 결과를 종합하여 원인 분석 리포트를 생성한다.
"""
import os
import json
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """당신은 반도체 후공정 P&T 수율 저하 원인 분석 전문가입니다.
제공된 데이터와 공정 지식을 바탕으로 아래 형식의 분석 리포트를 작성하세요.

## 리포트 형식
### 1. 요약
(2~3문장으로 핵심 내용)

### 2. 확인된 데이터
(조회된 주요 수치와 이상 항목)

### 3. 추정 원인
(가장 유력한 원인, 우선순위 순으로)

### 4. 근거
(어떤 데이터/지식이 해당 원인을 뒷받침하는지)

### 5. 권장 조치
(엔지니어가 즉시 취해야 할 행동)"""


def generate(
    user_query: str,
    collected_data: dict,
    knowledge_context: str,
    log: list | None = None,
) -> str:
    """
    Args:
        collected_data: Data Agent가 수집한 원본 데이터 딕셔너리
        knowledge_context: Knowledge Agent가 검색한 RAG 컨텍스트
    """
    data_str = json.dumps(collected_data, ensure_ascii=False, indent=2)

    user_message = f"""[사용자 요청]
{user_query}

[수집된 데이터]
{data_str}

[관련 공정 지식]
{knowledge_context}

위 내용을 바탕으로 수율 저하 원인 분석 리포트를 작성해주세요."""

    if log is not None:
        log.append({"step": "Report 생성", "data_keys": list(collected_data.keys())})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text.strip()
