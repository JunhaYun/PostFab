"""
LangGraph 기반 Multi-Agent Workflow.

그래프 구조:
  START
    ↓
  router_node
    ↓ (intent에 따라 분기)
  ┌──────────────────────────────┐
  │ knowledge   │ data  │ root_cause
  ↓             ↓       ↓
knowledge_node  data_node  planner_node
  ↓             ↓          ↓
  END           END        data_node (root_cause용)
                              ↓
                           knowledge_search_node
                              ↓
                           report_node
                              ↓
                             END
"""
from __future__ import annotations
from typing import TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, END

from src.agents import router_agent, planner_agent, knowledge_agent, data_agent, report_agent
from src.rag.retriever import retrieve_as_context
from src import metrics


# ── State 정의 ─────────────────────────────────────────────────
class AgentState(TypedDict):
    # 입력
    user_query: str
    history: list[dict]
    # Router 결과
    intent: str
    lot_id: str | None
    query_summary: str
    # Planner 결과
    planner_steps: list[str]
    # 수집 데이터
    collected_data: dict
    knowledge_context: str
    # 출력
    answer: str
    # 메타
    log: Annotated[list[dict], operator.add]  # 각 노드가 append
    latency_ms: int


# ── 노드 정의 ──────────────────────────────────────────────────

def router_node(state: AgentState) -> dict:
    result = router_agent.route(state["user_query"], history=state.get("history"))
    return {
        "intent": result.get("intent", "knowledge"),
        "lot_id": result.get("lot_id"),
        "query_summary": result.get("query_summary", ""),
        "log": [{"step": "Router", "intent": result.get("intent"), "lot_id": result.get("lot_id")}],
    }


def planner_node(state: AgentState) -> dict:
    steps = planner_agent.plan(state["user_query"], lot_id=state.get("lot_id"))
    return {
        "planner_steps": steps,
        "log": [{"step": "Planner", "steps": steps}],
    }


def knowledge_node(state: AgentState) -> dict:
    """시나리오 1 — 용어/개념 질문."""
    log: list[dict] = []
    answer = knowledge_agent.answer(
        state["user_query"], log=log, history=state.get("history")
    )
    return {"answer": answer, "log": log}


def data_node(state: AgentState) -> dict:
    """시나리오 2 — 데이터 단순 조회 / 시나리오 3의 DB 조회 단계."""
    log: list[dict] = []
    intent = state.get("intent", "data")
    lot_id = state.get("lot_id")

    if intent == "root_cause":
        steps = [s for s in state.get("planner_steps", [])
                 if s not in ("search_knowledge", "generate_report")]
        request = f"다음 정보를 조회해줘: {', '.join(steps)}. LOT ID: {lot_id}"
    else:
        request = state["user_query"]

    answer, collected = data_agent.query(request, log=log, history=state.get("history"))
    return {
        "answer": answer,          # 시나리오 2는 이게 최종 답변
        "collected_data": collected,
        "log": log,
    }


def knowledge_search_node(state: AgentState) -> dict:
    """시나리오 3 — 공정 지식 RAG 검색."""
    keywords = f"{state['user_query']} 수율 저하 원인 FDC 알람 Recipe"
    context = retrieve_as_context(keywords, n_results=4)
    return {
        "knowledge_context": context,
        "log": [{"step": "Knowledge 검색", "query": keywords, "chars": len(context)}],
    }


def report_node(state: AgentState) -> dict:
    """시나리오 3 — 원인 분석 리포트 생성."""
    log: list[dict] = []
    report = report_agent.generate(
        user_query=state["user_query"],
        collected_data=state.get("collected_data", {}),
        knowledge_context=state.get("knowledge_context", ""),
        log=log,
    )
    return {"answer": report, "log": log}


# ── 조건부 엣지 ────────────────────────────────────────────────

def route_by_intent(state: AgentState) -> str:
    intent = state.get("intent", "knowledge")
    if intent == "data":
        return "data"
    elif intent == "root_cause":
        return "root_cause"
    return "knowledge"


def root_cause_needs_knowledge(state: AgentState) -> str:
    steps = state.get("planner_steps", [])
    return "knowledge_search" if "search_knowledge" in steps else "report"


# ── 그래프 구성 ────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    g = StateGraph(AgentState)

    g.add_node("router",           router_node)
    g.add_node("knowledge",        knowledge_node)
    g.add_node("data",             data_node)
    g.add_node("planner",          planner_node)
    g.add_node("knowledge_search", knowledge_search_node)
    g.add_node("report",           report_node)

    g.set_entry_point("router")

    # router → 분기
    g.add_conditional_edges("router", route_by_intent, {
        "knowledge":  "knowledge",
        "data":       "data",
        "root_cause": "planner",
    })

    # 시나리오 1, 2 종료
    g.add_edge("knowledge", END)
    g.add_edge("data",      END)   # 시나리오 2

    # 시나리오 3 체인
    g.add_edge("planner", "data")  # planner → data (root_cause용 DB 조회)
    g.add_conditional_edges("data", root_cause_needs_knowledge, {
        "knowledge_search": "knowledge_search",
        "report":           "report",
    })
    g.add_edge("knowledge_search", "report")
    g.add_edge("report", END)

    return g.compile()


_graph = _build_graph()


# ── 외부 진입점 (기존 API와 동일한 시그니처 유지) ─────────────

def run(user_query: str, history: list | None = None) -> dict:
    """
    Returns:
        {"router": dict, "planner": list, "log": list, "answer": str, "latency_ms": int}
    """
    with metrics.Timer() as timer:
        try:
            final_state = _graph.invoke({
                "user_query":        user_query,
                "history":           history or [],
                "intent":            "",
                "lot_id":            None,
                "query_summary":     "",
                "planner_steps":     [],
                "collected_data":    {},
                "knowledge_context": "",
                "answer":            "",
                "log":               [],
                "latency_ms":        0,
            })
        except Exception as e:
            # API 오류 등으로 그래프 실행이 실패해도 UI가 죽지 않도록 처리
            final_state = {
                "intent": "error",
                "lot_id": None,
                "query_summary": "",
                "planner_steps": [],
                "log": [{"step": "Error", "detail": str(e)}],
                "answer": f"요청 처리 중 오류가 발생했습니다: {e}\n잠시 후 다시 시도해주세요.",
            }

    latency = round(timer.elapsed_ms)
    intent = final_state.get("intent", "unknown")
    log = final_state.get("log", [])

    metrics.record(intent=intent, latency_ms=timer.elapsed_ms, log=log)

    # 기존 UI 코드와 호환되는 형태로 반환
    router_log = next((e for e in log if e.get("step") == "Router"), {})
    return {
        "router": {
            "intent":        intent,
            "lot_id":        final_state.get("lot_id"),
            "query_summary": final_state.get("query_summary", ""),
            "original_query": user_query,
        },
        "planner":    final_state.get("planner_steps", []),
        "log":        log,
        "answer":     final_state.get("answer", ""),
        "latency_ms": latency,
    }
