"""
Knowledge Agent — RAG로 후공정 지식을 검색하고 LLM으로 답변을 생성한다.
"""
import os
import anthropic
from src.rag.retriever import retrieve_as_context

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """당신은 반도체 후공정 P&T 전문가입니다.
아래 [참고 지식]을 바탕으로 사용자 질문에 정확하고 간결하게 답변하세요.
참고 지식에 없는 내용은 추측하지 말고 "해당 내용은 지식 베이스에 없습니다"라고 하세요."""


def answer(query: str, log: list | None = None, history: list | None = None) -> str:
    """RAG 검색 후 LLM으로 답변 생성. history로 이전 대화 맥락 반영."""
    context = retrieve_as_context(query, n_results=3)

    if log is not None:
        log.append({"step": "RAG 검색", "query": query, "retrieved_chars": len(context)})

    # 이전 대화 + 현재 질문 (RAG 컨텍스트는 현재 질문에만 첨부)
    user_message = f"[참고 지식]\n{context}\n\n[질문]\n{query}"
    messages = (history or []) + [{"role": "user", "content": user_message}]

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    return response.content[0].text.strip()
