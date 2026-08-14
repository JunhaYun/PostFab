"""
Rewriter Agent — 멀티턴 질문을 '자기완결적'으로 바꾼다 (로드맵 ⑥).

왜 필요한가:
  Router와 Data Agent는 대화 전체를 보므로 지시어를 알아서 푼다(측정: 지시어 해석 9/9).
  그런데 **RAG 검색은 대화를 못 본다** — `knowledge_agent.answer()`가 질문 원문을
  그대로 `retrieve()`에 넘긴다. 그래서 "그게 뭔데?"로 검색하면 신뢰성·홀드코드 같은
  무관한 문서가 나오고, Knowledge Agent는 (지어내지 말라는 지시를 지켜) 되묻는다.

  같은 문제를 원인분석 경로에서는 이미 고쳤다 — `knowledge_search_node`는 질문이 아니라
  수집 데이터에서 검색어를 만든다. 이 노드는 그 해법을 입구로 옮겨, 경로마다 따로
  풀지 않고 **한 번 풀어서 모두가 같은 질문을 보게** 한다.

  덤으로 Router도 고쳐진 질문을 보게 되어, "그럼 어떻게 대응해?"가 root_cause로 잘못
  분류돼 리포트를 통째로 다시 만들던 것(55초, 리포트 3천 자)이 knowledge로 간다.

안전장치: history가 없으면(첫 턴) 호출하지 않는다. 결과가 수상하면 원문을 쓴다.
"""
import os
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """당신은 멀티턴 대화의 질문을 '자기완결적'으로 바꾸는 재작성기입니다.
이전 대화를 참고해, 현재 질문의 지시어와 생략된 대상을 구체적인 이름으로 바꾸세요.

바꿔야 할 것: 그게/그거/걔/이거, 첫번째/두번째, 아까 그 ~, 이 LOT, 그럼 ~는?
구체적인 이름: LOT ID, 설비 ID, 공정명, 불량명, 기간 등 이전 대화에 실제로 나온 표기

규칙:
- 재작성된 질문 한 줄만 출력합니다. 설명·따옴표·접두어·번호를 붙이지 마세요.
- 이미 자기완결적인 질문이면 원문을 그대로 출력합니다.
- 의미를 바꾸거나 정보를 추가하지 마세요. 지시어를 이름으로 바꾸는 것만 합니다.
- 지시어가 가리키는 대상이 이전 대화에 없으면 원문을 그대로 출력합니다.
- 이전 대화에 나온 표기를 그대로 씁니다(예: 볼 리프트, ML002, CB262S2401).
- 반도체 후공정과 무관한 질문(날씨/점심 등)도 원문 그대로 출력합니다.

=== 예시 ===
[이전] ...볼 리프트(Ball Lift) 불량에 기인합니다. WB002 설비...
[질문] 그게 뭔데?
[출력] 볼 리프트가 뭔데?

[이전] ...볼 리프트(Ball Lift) 불량입니다...
[질문] 그럼 어떻게 대응해?
[출력] 볼 리프트는 어떻게 대응해?

[이전] ...ML002가 99.78%에서 98.76%로 하락했습니다...
[질문] 그럼 걔가 6월에 처리한 LOT 중 수율 낮은 거 5개 알려줘
[출력] ML002가 6월에 처리한 LOT 중 수율 낮은 거 5개 알려줘

[이전] | CB262S2401 | AS_Mold | ... | CA260S0014 | ...
[질문] 첫번째 LOT 원인 분석해줘
[출력] CB262S2401 원인 분석해줘

[이전] 6월 설비별 수율은 ... 입니다.
[질문] 5월은?
[출력] 5월 설비별 수율 알려줘

[이전] CA260S0056의 최종 수율은 95.5%입니다.
[질문] CA260S0450 수율 알려줘
[출력] CA260S0450 수율 알려줘

[이전] (아무 대화)
[질문] 오늘 점심 뭐 먹지?
[출력] 오늘 점심 뭐 먹지?
=== 예시 끝 ==="""

# 재작성 결과가 이 배수를 넘으면 설명을 덧붙인 것으로 보고 원문을 쓴다
MAX_EXPANSION = 4
MAX_CHARS = 300


def rewrite(user_query: str, history: list | None = None) -> tuple[str, bool]:
    """(사용할 질문, 재작성 여부)를 반환. 실패하면 원문을 그대로 돌려준다."""
    if not history:
        return user_query, False          # 첫 턴은 풀 지시어가 없다 — 호출 자체를 아낀다

    # 대화를 문자열로 눌러 넣는다. messages로 넘기면 모델이 대화를 '이어가려' 한다.
    convo = "\n".join(
        f"[{'사용자' if m.get('role') == 'user' else '시스템'}] {str(m.get('content', ''))[:800]}"
        for m in history[-6:]
    )
    prompt = f"[이전 대화]\n{convo}\n\n[질문] {user_query}\n[출력]"

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        out = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    except Exception:
        return user_query, False          # 재작성 실패가 대화를 끊으면 안 된다

    out = out.strip().strip('"').strip("'").splitlines()[0].strip() if out else ""

    # 수상하면 원문 — 재작성기가 설명을 붙이거나 질문을 통째로 바꾸는 걸 막는다
    if (not out
            or len(out) > MAX_CHARS
            or len(out) > max(len(user_query) * MAX_EXPANSION, 60)):
        return user_query, False

    return out, out != user_query
