"""
FastAPI 백엔드 — PostFab Multi-Agent.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from src.workflow import run as agent_run
from src import metrics

app = FastAPI(title="PostFab Multi-Agent API", version="1.0.0")

# ── 정적 파일 (프론트엔드) ─────────────────────────────────────
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ── 요청/응답 스키마 ───────────────────────────────────────────
class ChatRequest(BaseModel):
    query: str
    history: list[dict] = []   # [{"role": "user"|"assistant", "content": "..."}]


class ChatResponse(BaseModel):
    answer: str
    intent: str
    lot_id: str | None
    query_summary: str
    planner: list[str]
    log: list[dict]
    latency_ms: int


# ── 엔드포인트 ────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query가 비어 있습니다.")

    result = agent_run(req.query.strip(), history=req.history or None)

    router = result["router"]
    return ChatResponse(
        answer=result["answer"],
        intent=router.get("intent", "unknown"),
        lot_id=router.get("lot_id"),
        query_summary=router.get("query_summary", ""),
        planner=result["planner"],
        log=result["log"],
        latency_ms=result.get("latency_ms", 0),
    )


@app.get("/api/metrics")
def get_metrics():
    return metrics.get_summary()


@app.delete("/api/metrics")
def reset_metrics():
    metrics.reset()
    return {"message": "KPI 초기화 완료"}


@app.get("/api/health")
def health():
    return {"status": "ok"}
