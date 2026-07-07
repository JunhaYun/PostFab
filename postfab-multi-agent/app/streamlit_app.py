"""
Streamlit UI — PostFab Multi-Agent 챗봇 데모.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="PostFab Multi-Agent",
    page_icon="🏭",
    layout="wide",
)

st.markdown("""
<style>
.agent-box {
    border-left: 4px solid #4f8ef7;
    padding: 8px 16px;
    margin: 6px 0;
    border-radius: 0 8px 8px 0;
    background: #f0f4ff;
}
.step-badge {
    display: inline-block;
    background: #4f8ef7;
    color: white;
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 0.78em;
    font-weight: 600;
    margin-right: 8px;
}
.fn-call {
    border-left: 4px solid #f7a84f;
    padding: 6px 12px;
    margin: 4px 0;
    background: #fff8f0;
    border-radius: 0 6px 6px 0;
    font-family: monospace;
    font-size: 0.85em;
}
.report-section {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 10px;
    padding: 20px 24px;
}
</style>
""", unsafe_allow_html=True)


# ── 헬퍼 ───────────────────────────────────────────────────────
def render_log(log: list):
    for entry in log:
        step = entry.get("step", "")
        if step in ("Router", "Planner"):
            continue
        elif step == "Function Call":
            tool, inp = entry["tool"], entry["input"]
            args_str = ", ".join(f'"{v}"' for v in inp.values())
            st.markdown(f'<div class="fn-call">🔧 <b>{tool}({args_str})</b></div>',
                        unsafe_allow_html=True)
            with st.expander(f"결과 — {tool}"):
                st.code(entry["result_preview"], language="json")
        elif step == "RAG 검색":
            st.markdown(f'<div class="agent-box"><span class="step-badge">Knowledge</span>'
                        f'🔍 RAG 검색 | {entry["retrieved_chars"]}자</div>', unsafe_allow_html=True)
        elif step == "Knowledge 검색":
            st.markdown(f'<div class="agent-box"><span class="step-badge">Knowledge</span>'
                        f'🔍 지식 검색 | {entry["chars"]}자</div>', unsafe_allow_html=True)
        elif step == "Report 생성":
            st.markdown(f'<div class="agent-box"><span class="step-badge">Report</span>'
                        f'📝 리포트 생성 | {entry["data_keys"]}</div>', unsafe_allow_html=True)


def render_kpi():
    """KPI 대시보드 렌더링."""
    from src import metrics as m
    kpi = m.get_summary()

    total = kpi["total_queries"]
    if total == 0:
        st.info("아직 실행 기록이 없습니다. 질문을 해보세요!")
        return

    # ── 핵심 지표 ─────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 질문 수", total)
    c2.metric("평균 응답시간", f"{kpi['avg_latency_ms']}ms")
    c3.metric("Function Calls", kpi["function_call_count"])
    c4.metric("RAG 검색", kpi["rag_search_count"])

    if kpi["p95_latency_ms"]:
        st.caption(f"P95 응답시간: {kpi['p95_latency_ms']}ms")

    st.divider()

    # ── Intent 분포 ───────────────────────────────────────────
    st.markdown("#### Intent 분포")
    ic = kpi["intent_counts"]
    cols = st.columns(3)
    intent_map = [("📚 knowledge", "knowledge", "#3b82f6"),
                  ("🗄️ data", "data", "#10b981"),
                  ("📊 root_cause", "root_cause", "#ef4444")]
    for col, (label, key, _) in zip(cols, intent_map):
        cnt = ic.get(key, 0)
        pct = round(cnt / total * 100) if total else 0
        col.metric(label, f"{cnt}건", f"{pct}%")

    # 막대 시각화
    import streamlit as st2
    for label, key, color in intent_map:
        cnt = ic.get(key, 0)
        pct = cnt / total if total else 0
        st.progress(pct, text=f"{label}: {cnt}건 ({round(pct*100)}%)")

    st.divider()

    # ── 최근 실행 기록 ────────────────────────────────────────
    st.markdown("#### 최근 실행 기록 (최대 20건)")
    history = list(reversed(kpi["history"]))
    if history:
        import pandas as pd
        df = pd.DataFrame(history)
        df.columns = ["시각", "Intent", "응답시간(ms)", "FnCall수", "RAG수"]
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    if st.button("KPI 초기화", type="secondary"):
        m.reset()
        st.rerun()


# ── 사이드바 ───────────────────────────────────────────────────
with st.sidebar:
    st.title("🏭 PostFab\nMulti-Agent")
    st.caption("반도체 후공정 P&T 자동화")
    st.divider()

    st.markdown("### 📚 용어 질문")
    for ex in ["FDC가 뭐야?", "Yield Trend란?", "tRCD Timing Fail 원인은?",
               "Contact Resistance가 높아지면?", "Burn-in 공정은 왜 필요해?"]:
        if st.button(ex, key=f"k_{ex}", use_container_width=True):
            st.session_state["pending_query"] = ex

    st.markdown("### 🗄️ 데이터 조회")
    for ex in ["LOT002 수율 알려줘", "LOT001 정보 조회해줘",
               "TEST02 FDC 알람 있어?", "TEST02 수율 트렌드 보여줘"]:
        if st.button(ex, key=f"d_{ex}", use_container_width=True):
            st.session_state["pending_query"] = ex

    st.markdown("### 📊 원인 분석")
    if st.button("LOT002 수율 저하 원인 분석해줘", key="r_main", use_container_width=True):
        st.session_state["pending_query"] = "LOT002 수율 저하 원인 분석해줘"

    st.divider()
    if st.button("대화 초기화", use_container_width=True):
        st.session_state["messages"] = []
        st.session_state["chat_log"] = []
        st.rerun()

    st.divider()
    st.markdown("### DB 현황")
    try:
        import sqlite3
        _db = os.path.join(os.path.dirname(__file__), "..", "data", "mock", "postfab.db")
        _c = sqlite3.connect(_db)
        col_a, col_b = st.columns(2)
        col_a.metric("LOT", _c.execute("SELECT COUNT(*) FROM lot_info").fetchone()[0])
        col_b.metric("알람", _c.execute("SELECT COUNT(*) FROM fdc_alarm").fetchone()[0])
        _c.close()
    except Exception:
        st.warning("DB 미생성")

# ── 메인 ───────────────────────────────────────────────────────
st.title("🏭 PostFab Multi-Agent")
st.caption("RAG + Function Calling | ICL Router | 대화 맥락 유지")

if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "chat_log" not in st.session_state:
    st.session_state["chat_log"] = []

# 탭 구성 — 챗봇 탭 + KPI 탭
tab_chat, tab_kpi = st.tabs(["💬 챗봇", "📊 KPI 대시보드"])

with tab_kpi:
    render_kpi()

with tab_chat:
    default_query = st.session_state.pop("pending_query", "")

    # ── 대화 히스토리 표시 ─────────────────────────────────────
    for msg in st.session_state["chat_log"]:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                meta = msg.get("meta", {})
                router = meta.get("router", {})
                intent = router.get("intent", "")
                latency = meta.get("latency_ms", 0)

                intent_color = {"knowledge": "🟢", "data": "🔵", "root_cause": "🔴"}.get(intent, "⚪")
                st.caption(f"{intent_color} intent: **{intent}** | LOT: {router.get('lot_id') or '-'} | ⏱ {latency}ms")

                if meta.get("planner"):
                    st.caption("📋 " + " → ".join(f"`{s}`" for s in meta["planner"]))

                if meta.get("log"):
                    with st.expander("🔍 실행 로그"):
                        render_log(meta["log"])

                if intent == "root_cause":
                    st.markdown(f'<div class="report-section">\n\n{msg["content"]}\n\n</div>',
                                unsafe_allow_html=True)
                else:
                    st.markdown(msg["content"])

    # ── 입력 ───────────────────────────────────────────────────
    if default_query:
        st.session_state["_prefill"] = default_query

    prefill = st.session_state.pop("_prefill", "")
    query = st.chat_input("질문을 입력하세요")
    if prefill and not query:
        query = prefill

    if query:
        from src.workflow import run as agent_run

        st.session_state["chat_log"].append({"role": "user", "content": query})
        with st.chat_message("user"):
            st.markdown(query)

        api_history = st.session_state["messages"].copy()

        with st.chat_message("assistant"):
            with st.spinner("에이전트 실행 중..."):
                result = agent_run(query, history=api_history)

            router = result["router"]
            intent = router.get("intent", "")
            intent_color = {"knowledge": "🟢", "data": "🔵", "root_cause": "🔴"}.get(intent, "⚪")
            latency = result.get("latency_ms", 0)

            st.caption(f"{intent_color} intent: **{intent}** | LOT: {router.get('lot_id') or '-'} | ⏱ {latency}ms")

            if result["planner"]:
                st.caption("📋 " + " → ".join(f"`{s}`" for s in result["planner"]))

            if result["log"]:
                with st.expander("🔍 실행 로그"):
                    render_log(result["log"])

            if intent == "root_cause":
                st.markdown(f'<div class="report-section">\n\n{result["answer"]}\n\n</div>',
                            unsafe_allow_html=True)
            else:
                box = st.empty()
                displayed = ""
                for char in result["answer"]:
                    displayed += char
                    box.markdown(displayed + "▌")
                    time.sleep(0.004)
                box.markdown(result["answer"])

        st.session_state["messages"].append({"role": "user", "content": query})
        st.session_state["messages"].append({"role": "assistant", "content": result["answer"]})
        st.session_state["chat_log"].append({
            "role": "assistant",
            "content": result["answer"],
            "meta": {
                "router": result["router"],
                "planner": result["planner"],
                "log": result["log"],
                "latency_ms": result.get("latency_ms", 0),
            },
        })
