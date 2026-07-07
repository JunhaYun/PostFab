"""
KPI 수집 및 저장 모듈.
응답시간, Routing 정확도, Function Call 횟수, RAG 검색 횟수를 추적한다.
"""
import json
import os
import time
from datetime import datetime

METRICS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "metrics.json")


def _load() -> dict:
    if os.path.exists(METRICS_PATH):
        with open(METRICS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {
        "total_queries": 0,
        "intent_counts": {"knowledge": 0, "data": 0, "root_cause": 0, "unknown": 0},
        "latency_ms": [],          # 전체 응답시간 목록
        "function_call_count": 0,  # Function Calling 총 호출 횟수
        "rag_search_count": 0,     # RAG 검색 총 횟수
        "history": [],             # 최근 20건 상세 기록
    }


def _save(data: dict):
    os.makedirs(os.path.dirname(METRICS_PATH), exist_ok=True)
    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record(intent: str, latency_ms: float, log: list):
    """workflow 실행 결과를 KPI로 기록."""
    data = _load()

    data["total_queries"] += 1
    data["intent_counts"][intent] = data["intent_counts"].get(intent, 0) + 1
    data["latency_ms"].append(round(latency_ms))

    # 최근 100건만 유지
    if len(data["latency_ms"]) > 100:
        data["latency_ms"] = data["latency_ms"][-100:]

    # 로그에서 Function Call / RAG 횟수 집계
    fn_calls = sum(1 for e in log if e.get("step") == "Function Call")
    rag_calls = sum(1 for e in log if e.get("step") in ("RAG 검색", "Knowledge 검색"))
    data["function_call_count"] += fn_calls
    data["rag_search_count"] += rag_calls

    # 상세 기록 (최근 20건)
    data["history"].append({
        "ts": datetime.now().strftime("%H:%M:%S"),
        "intent": intent,
        "latency_ms": round(latency_ms),
        "fn_calls": fn_calls,
        "rag_calls": rag_calls,
    })
    data["history"] = data["history"][-20:]

    _save(data)


def get_summary() -> dict:
    """KPI 요약 반환."""
    data = _load()
    ms = data["latency_ms"]
    return {
        "total_queries": data["total_queries"],
        "intent_counts": data["intent_counts"],
        "avg_latency_ms": round(sum(ms) / len(ms)) if ms else 0,
        "p95_latency_ms": round(sorted(ms)[int(len(ms) * 0.95)]) if len(ms) >= 2 else 0,
        "function_call_count": data["function_call_count"],
        "rag_search_count": data["rag_search_count"],
        "history": data["history"],
    }


def reset():
    if os.path.exists(METRICS_PATH):
        os.remove(METRICS_PATH)


class Timer:
    """with 블록으로 사용하는 간단한 타이머."""
    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000
