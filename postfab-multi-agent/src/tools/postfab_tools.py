"""
Function Calling 도구 정의 — Data Agent가 사용하는 SQLite 조회 함수들.
"""
import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "mock", "postfab.db")


def _connect():
    return sqlite3.connect(DB_PATH)


def _rows_to_dict(cursor, rows):
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in rows]


# ── 개별 조회 함수 ─────────────────────────────────────────────────────────────

def get_lot_info(lot_id: str) -> dict:
    """LOT 기본 정보 조회."""
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM tdlotinfo WHERE LOTID = ?", (lot_id,))
        rows = cur.fetchall()
    if not rows:
        return {"error": f"LOT ID '{lot_id}'를 찾을 수 없습니다."}
    return {"lot_info": _rows_to_dict(cur, rows)}


def get_test_result(lot_id: str) -> dict:
    """LOT의 공정 진행 이력 및 수율 조회 (STEPSEQ 순서대로)."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM tdtestresult WHERE LOTID = ? ORDER BY STEPSEQ",
            (lot_id,)
        )
        rows = cur.fetchall()
    if not rows:
        return {"error": f"LOT ID '{lot_id}'의 TEST 결과를 찾을 수 없습니다."}
    return {"test_results": _rows_to_dict(cur, rows)}


def get_recipe(lot_id: str) -> dict:
    """LOT에 사용된 레시피 이름과 해당 레시피의 스펙(항목별 min/max) 조회."""
    with _connect() as conn:
        # 레시피 이름 조회
        cur = conn.execute(
            "SELECT CHECKVALUE FROM tdrecipemapping WHERE LOTID = ? AND ITEMNAME = 'RECIPE'",
            (lot_id,)
        )
        row = cur.fetchone()
        if not row:
            return {"error": f"LOT ID '{lot_id}'의 레시피 정보를 찾을 수 없습니다."}
        recipe_name = row[0]

        # 레시피 스펙 조회
        cur2 = conn.execute(
            "SELECT KEYDATA, VALDATA, VALDATA2 FROM tdrecipemaster WHERE RECEIPE = ? AND ACTIVEFLAG = 'T'",
            (recipe_name,)
        )
        specs = cur2.fetchall()

    result = {
        "recipe_name": recipe_name,
        "specs": [{"항목": r[0], "min": r[1], "max": r[2]} for r in specs]
    }
    return result


def get_eqp_history(lot_id: str, eqp_id: str) -> dict:
    """특정 LOT이 특정 설비를 거칠 때의 실측값 조회."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT KEYDATA, VALDATA, TXNTIMESTAMP FROM tdeqphistory WHERE LOTID = ? AND EQPID = ?",
            (lot_id, eqp_id)
        )
        rows = cur.fetchall()
    if not rows:
        return {"message": f"LOT '{lot_id}' / 설비 '{eqp_id}' 이력이 없습니다."}
    return {
        "eqp_id": eqp_id,
        "lot_id": lot_id,
        "measured_values": [{"항목": r[0], "실측값": r[1], "시각": r[2]} for r in rows]
    }


EMAP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "post_data", "emap")


def get_emap(strip_id: str) -> dict:
    """Strip ID로 emap 파일을 읽어 pass/fail 분포 및 불량 위치를 분석합니다."""
    path = os.path.join(EMAP_DIR, f"{strip_id}.txt")
    if not os.path.exists(path):
        return {"error": f"emap 파일을 찾을 수 없습니다: {strip_id}.txt"}

    with open(path, "r") as f:
        content = f.read()

    # 0/1로만 구성된 줄만 map 행으로 인식
    rows = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and all(c in "01" for c in stripped):
            rows.append(stripped)

    total = sum(len(r) for r in rows)
    fail = sum(r.count("0") for r in rows)
    good = total - fail
    yield_pct = round(good / total * 100, 2) if total > 0 else 0

    # 불량 행 요약
    fail_rows = []
    for i, r in enumerate(rows):
        if "0" in r:
            fail_positions = [j for j, c in enumerate(r) if c == "0"]
            fail_rows.append({"row": i, "fail_count": len(fail_positions),
                               "fail_col_range": f"{fail_positions[0]}~{fail_positions[-1]}"})

    return {
        "strip_id": strip_id,
        "total_die": total,
        "good_die": good,
        "fail_die": fail,
        "yield_pct": f"{yield_pct}%",
        "fail_row_summary": fail_rows,
    }


def get_strip_map(lot_id: str) -> dict:
    """LOT에 속한 Strip 목록 및 각 Strip의 공정 정보 조회."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM tdstripmap WHERE LOTID = ?",
            (lot_id,)
        )
        rows = cur.fetchall()
    if not rows:
        return {"error": f"LOT ID '{lot_id}'의 Strip 정보를 찾을 수 없습니다."}
    return {"strips": _rows_to_dict(cur, rows)}


# ── Function Calling 스펙 (Claude tool_use 형식) ──────────────────────────────

TOOL_SPECS = [
    {
        "name": "get_lot_info",
        "description": "LOT ID로 LOT 기본 정보(제품, BOM, 고객사, 상태 등)를 조회합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lot_id": {"type": "string", "description": "조회할 LOT ID (예: HY260A01)"}
            },
            "required": ["lot_id"]
        }
    },
    {
        "name": "get_test_result",
        "description": "LOT ID로 공정 진행 이력과 각 공정별 수율(YIELD), 사용 설비(EQPID)를 조회합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lot_id": {"type": "string", "description": "조회할 LOT ID"}
            },
            "required": ["lot_id"]
        }
    },
    {
        "name": "get_recipe",
        "description": "LOT ID로 사용된 레시피 이름과 레시피의 항목별 스펙(min/max 기준값)을 조회합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lot_id": {"type": "string", "description": "조회할 LOT ID"}
            },
            "required": ["lot_id"]
        }
    },
    {
        "name": "get_eqp_history",
        "description": "특정 LOT이 특정 설비를 거칠 때의 실측값(온도, 압력 등)을 조회합니다. 레시피 스펙과 비교하여 이상 여부를 판단할 수 있습니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lot_id":  {"type": "string", "description": "조회할 LOT ID"},
                "eqp_id": {"type": "string", "description": "조회할 설비 ID (예: ML45DS)"}
            },
            "required": ["lot_id", "eqp_id"]
        }
    },
    {
        "name": "get_emap",
        "description": "Strip ID로 emap 파일을 읽어 pass/fail 분포, 수율, 불량 위치(행/열)를 분석합니다. 특정 strip의 불량 패턴을 파악할 때 사용합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "strip_id": {"type": "string", "description": "조회할 Strip ID (예: 260628_STR340FG_0403)"}
            },
            "required": ["strip_id"]
        }
    },
    {
        "name": "get_strip_map",
        "description": "LOT ID로 해당 LOT의 Strip 목록과 각 Strip의 위치, 공정, 설비 정보를 조회합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lot_id": {"type": "string", "description": "조회할 LOT ID (예: SM260B01)"}
            },
            "required": ["lot_id"]
        }
    },
]

# 함수 이름 → 실제 함수 매핑
TOOL_FUNCTIONS = {
    "get_lot_info":    get_lot_info,
    "get_test_result": get_test_result,
    "get_recipe":      get_recipe,
    "get_eqp_history": get_eqp_history,
    "get_strip_map":   get_strip_map,
    "get_emap":        get_emap,
}


def execute_tool(name: str, inputs: dict) -> str:
    """Function Calling 결과를 JSON 문자열로 반환."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
    result = fn(**inputs)
    return json.dumps(result, ensure_ascii=False, indent=2)
