"""
LangGraph 기반 Multi-Agent Workflow.

그래프 구조:
  START
    ↓
  router_node
    ↓ (intent에 따라 분기)
  ┌───────────────────────────────────────────┐
  │ knowledge   │ data  │ root_cause │ out_of_scope
  ↓             ↓       ↓            ↓
knowledge_node  data_node  planner_node  out_of_scope_node
  ↓             ↓          ↓               ↓
  END           END        data_node       END
                            (root_cause용)
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
from src.rag.retriever import format_context, retrieve, retrieve_as_context
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

    # root_cause는 answer를 Report Agent가 다시 쓰므로 요약 호출을 생략(collected_data만 필요).
    answer, collected = data_agent.query(
        request, log=log, history=state.get("history"),
        summarize=(intent != "root_cause"),
    )
    return {
        "answer": answer,          # 시나리오 2는 이게 최종 답변
        "collected_data": collected,
        "log": log,
    }


def out_of_scope_node(state: AgentState) -> dict:
    """반도체 후공정 P&T와 무관한 질문을 정중히 거절."""
    answer = (
        "죄송합니다, 저는 반도체 후공정(P&T) 관련 질문만 답변할 수 있습니다. "
        "공정/설비 용어 설명, LOT·설비 데이터 조회, 수율 저하 원인 분석 등을 문의해 주세요."
    )
    return {
        "answer": answer,
        "log": [{"step": "OutOfScope", "query": state["user_query"]}],
    }


# analyze_strip_yield의 판정 용어와 지식베이스 emap 패턴 카드의 제목 용어가 다르다
# (판정 "가장자리 분포" vs 카드 "에지 집중형"). 검색이 카드에 닿도록 카드 쪽 표현을 덧붙인다.
_EMAP_CARD_TERMS = {
    "중앙 집중":          ["중앙 집중형", "Center Cluster"],
    "가장자리 분포":      ["에지 집중형", "Edge Concentration"],
    "라인·스트라이프 분포": ["라인/스트라이프형", "Line Stripe", "멀티헤드"],
    "산발 분포":          ["랜덤 산발형", "Random Distribution"],
}


def _search_terms_from_data(collected: dict) -> list[str]:
    """수집 데이터에서 지식 검색에 쓸 단서(공정명/스펙 이탈 항목/불량명)를 뽑는다.

    고정 키워드("수율 저하 원인 FDC 알람 Recipe")만으로 검색하면 'FDC'/'Recipe' 용어 카드와
    일반적인 수율 분석 가이드만 걸리고, 정작 원인을 설명하는 트러블슈팅/알람 카드는 못 찾는다.
    Data Agent가 실제로 찾아낸 값을 검색어에 넣어야 해당 카드가 상위로 올라온다.
    """
    terms: list[str] = []
    for result in collected.values():
        if not isinstance(result, dict):
            continue
        # 스펙 이탈 항목 — 원인 카드/알람 코드를 직접 가리키는 가장 강한 단서
        for v in result.get("violations", []) or []:
            if isinstance(v, dict):
                terms += [str(v.get("항목", "")), str(v.get("이탈유형", ""))]
        # 수율이 가장 많이 빠진 공정 + 관측된 불량 유형
        worst = result.get("worst_step")
        if isinstance(worst, dict):
            terms.append(str(worst.get("공정", "")))
        for key in ("defect_type", "fail_pattern"):
            if result.get(key):
                terms.append(str(result[key]))
        # get_eqp_history는 항목/실측값 리스트로 오므로 DEFECT_TYPE 항목을 따로 집는다
        for m in result.get("measured_values", []) or []:
            if isinstance(m, dict) and m.get("항목") == "DEFECT_TYPE":
                terms.append(str(m.get("실측값", "")))
        # strip 분석의 불량 위치 패턴 — emap 패턴 카드를 가리키는 단서
        worst_strip = result.get("worst_strip")
        if isinstance(worst_strip, dict) and worst_strip.get("fail_location"):
            loc = str(worst_strip["fail_location"])
            terms += ["emap 패턴", loc, *_EMAP_CARD_TERMS.get(loc, [])]
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for t in terms:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def knowledge_search_node(state: AgentState) -> dict:
    """시나리오 3 — 공정 지식 RAG 검색."""
    terms = _search_terms_from_data(state.get("collected_data", {}))
    if terms:
        keywords = f"{' '.join(terms)} 수율 저하 원인 조치"
    else:
        keywords = f"{state['user_query']} 수율 저하 원인 FDC 알람 Recipe"
    docs = retrieve(keywords, n_results=4)
    context = format_context(docs)
    return {
        "knowledge_context": context,
        # context_titles는 평가(13번)에서 "리포트의 원인 설명이 지식베이스에서 온 것인지,
        # LLM 내부 지식인지"를 구분하는 데 쓴다.
        "log": [{"step": "Knowledge 검색", "query": keywords, "chars": len(context),
                 "context_titles": [d["title"] for d in docs]}],
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
    elif intent == "out_of_scope":
        return "out_of_scope"
    return "knowledge"


def after_data(state: AgentState) -> str:
    """data 노드 이후 분기.

    - 시나리오 2(intent=data): 여기서 종료(answer가 최종 답변).
    - 시나리오 3(intent=root_cause, planner 경유): 지식검색/리포트로 계속.
    이 둘을 하나의 조건부 엣지로 합쳐야 한다. data 노드에 무조건 엣지(→END)와
    조건부 엣지를 동시에 걸면 LangGraph가 양쪽으로 fan-out 하여, 단순 조회에도
    report 노드가 실행돼 answer를 리포트로 덮어쓴다.
    """
    if state.get("intent") == "root_cause":
        # 지식 검색은 root_cause 파이프라인의 고정 단계다(상단 그래프 구조 참조).
        # 예전엔 planner가 "search_knowledge"를 계획에 넣었을 때만 실행했는데,
        # planner가 이를 누락하면 리포트가 지식베이스 근거 없이 LLM 내부 지식만으로
        # 작성됐다. 검색 자체는 LLM 호출이 없어 비용도 사실상 없으므로 항상 태운다.
        return "knowledge_search"
    return "end"


# ── 그래프 구성 ────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    g = StateGraph(AgentState)

    g.add_node("router",           router_node)
    g.add_node("knowledge",        knowledge_node)
    g.add_node("data",             data_node)
    g.add_node("planner",          planner_node)
    g.add_node("knowledge_search", knowledge_search_node)
    g.add_node("report",           report_node)
    g.add_node("out_of_scope",     out_of_scope_node)

    g.set_entry_point("router")

    # router → 분기
    g.add_conditional_edges("router", route_by_intent, {
        "knowledge":    "knowledge",
        "data":         "data",
        "root_cause":   "planner",
        "out_of_scope": "out_of_scope",
    })

    # 시나리오 1 종료
    g.add_edge("knowledge", END)
    g.add_edge("out_of_scope", END)

    # data 노드는 intent에 따라 단 한 방향으로만 분기한다(무조건 엣지 금지).
    #   - 시나리오 2(data)      → END
    #   - 시나리오 3(root_cause) → knowledge_search 또는 report
    g.add_edge("planner", "data")  # planner → data (root_cause용 DB 조회)
    g.add_conditional_edges("data", after_data, {
        "knowledge_search": "knowledge_search",
        "report":           "report",
        "end":              END,
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
