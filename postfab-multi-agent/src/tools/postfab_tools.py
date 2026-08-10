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


def check_spec_violation(lot_id: str) -> dict:
    """레시피 스펙(min/max)과 설비 실측값을 자동 비교하여 스펙 이탈 항목을 찾는다."""
    with _connect() as conn:
        cur = conn.execute(
            "SELECT CHECKVALUE FROM tdrecipemapping WHERE LOTID = ? AND ITEMNAME = 'RECIPE'",
            (lot_id,)
        )
        row = cur.fetchone()
        if not row:
            return {"error": f"LOT ID '{lot_id}'의 레시피 정보를 찾을 수 없습니다."}
        recipe_name = row[0]

        specs = conn.execute(
            "SELECT KEYDATA, VALDATA, VALDATA2 FROM tdrecipemaster WHERE RECEIPE = ? AND ACTIVEFLAG = 'T'",
            (recipe_name,)
        ).fetchall()
        measured = conn.execute(
            "SELECT EQPID, KEYDATA, VALDATA, TXNTIMESTAMP FROM tdeqphistory WHERE LOTID = ?",
            (lot_id,)
        ).fetchall()

    if not measured:
        return {"recipe_name": recipe_name,
                "message": f"LOT '{lot_id}'의 설비 실측 이력이 없어 스펙 비교를 할 수 없습니다."}

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    spec_map = {k: (mn, mx) for k, mn, mx in specs}
    violations = []
    in_spec = []
    for eqp, key, val, ts in measured:
        if key not in spec_map:
            continue
        mn, mx = spec_map[key]
        v, lo, hi = _num(val), _num(mn), _num(mx)
        if v is None or lo is None or hi is None:
            continue
        item = {"항목": key, "실측값": val, "min": mn, "max": mx, "설비": eqp, "시각": ts}
        if v < lo or v > hi:
            item["이탈유형"] = "MIN 미달" if v < lo else "MAX 초과"
            violations.append(item)
        else:
            in_spec.append(item)

    return {
        "lot_id": lot_id,
        "recipe_name": recipe_name,
        "checked_count": len(in_spec) + len(violations),
        "violation_count": len(violations),
        "violations": violations if violations else "스펙 이탈 항목 없음",
        "in_spec_items": [i["항목"] for i in in_spec],
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


# ── 분석 도구 (단순 조회를 넘어 "해석"까지) ─────────────────────────────────

def analyze_lot_yield(lot_id: str) -> dict:
    """LOT의 공정별 수율을 분석해 최종 수율과 수율이 빠진 공정을 자동으로 짚어준다.

    get_test_result가 원본 row를 덤프하는 것과 달리, 이 도구는
    - LOT 최종 수율(첫 공정 투입 대비 마지막 공정 산출)
    - 수율이 빠진 공정(REJECT>0)을 STEPSEQ 순으로 식별
    - 가장 수율이 낮은 공정(worst step)
    을 계산해서 "어디서 문제가 났는지"를 바로 답할 수 있게 한다.
    """
    with _connect() as conn:
        cur = conn.execute(
            "SELECT STEPSEQ, STEPNAME, EQPID, INQTY, REJECT, OUTQTY, YIELD, YIELD_NUM "
            "FROM tdtestresult WHERE LOTID = ? ORDER BY STEPSEQ",
            (lot_id,),
        )
        rows = _rows_to_dict(cur, cur.fetchall())

    if not rows:
        return {"error": f"LOT ID '{lot_id}'의 공정 수율 데이터가 없습니다. "
                         f"(tdlotinfo에는 있어도 공정이 진행되지 않았을 수 있습니다.)"}

    first, last = rows[0], rows[-1]
    in_qty = first.get("INQTY") or 0
    out_qty = last.get("OUTQTY") or 0
    lot_yield = round(out_qty / in_qty * 100, 2) if in_qty else None

    loss_steps = [
        {"STEPSEQ": r["STEPSEQ"], "공정": r["STEPNAME"], "설비": r["EQPID"],
         "REJECT": r["REJECT"], "수율": r["YIELD"]}
        for r in rows if (r.get("REJECT") or 0) > 0
    ]

    # 가장 수율이 낮은 공정 (YIELD_NUM 기준, None은 제외)
    numbered = [r for r in rows if r.get("YIELD_NUM") is not None]
    worst = min(numbered, key=lambda r: r["YIELD_NUM"]) if numbered else None

    # 검사에서 분류된 불량 유형(tdeqphistory의 DEFECT_TYPE 키). 수율 숫자만으로는
    # 원인 지식을 검색할 단서가 없으므로, 불량명을 여기서 함께 돌려준다.
    defect_type = None
    if worst is not None:
        with _connect() as conn:
            hit = conn.execute(
                "SELECT VALDATA FROM tdeqphistory "
                "WHERE LOTID = ? AND EQPID = ? AND KEYDATA = 'DEFECT_TYPE' LIMIT 1",
                (lot_id, worst["EQPID"]),
            ).fetchone()
        if hit:
            defect_type = hit[0]

    return {
        "lot_id": lot_id,
        "step_count": len(rows),
        "progression": [r["STEPNAME"] for r in rows],   # 지나간 공정 순서
        "lot_final_yield": f"{lot_yield}%" if lot_yield is not None else "N/A",
        "loss_steps": loss_steps if loss_steps else "수율 손실 공정 없음 (전 공정 정상)",
        "defect_type": defect_type,   # 없으면 None (미분류 또는 정상 LOT)
        "worst_step": None if worst is None else {
            "공정": worst["STEPNAME"], "설비": worst["EQPID"],
            "수율": worst["YIELD"], "REJECT": worst["REJECT"],
        },
    }


def _classify_fail_location(rows: list[str]) -> str:
    """emap의 fail(0) 위치가 중앙에 몰렸는지 가장자리인지 분류한다."""
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)
    fails = [(i, j) for i, r in enumerate(rows) for j, c in enumerate(r) if c == "0"]
    if not fails:
        return "불량 없음"

    rmin = min(i for i, _ in fails); rmax = max(i for i, _ in fails)
    cmin = min(j for _, j in fails); cmax = max(j for _, j in fails)
    touches_edge = (rmin == 0 or cmin == 0 or rmax == n_rows - 1 or cmax == n_cols - 1)

    # 불량 bounding box가 그리드 가운데 60% 안에 들어오면 '중앙 집중'
    in_mid = (rmin >= n_rows * 0.2 and rmax <= n_rows * 0.8
              and cmin >= n_cols * 0.2 and cmax <= n_cols * 0.8)
    if in_mid and not touches_edge:
        return "중앙 집중"
    if touches_edge:
        return "가장자리 분포"
    return "산발 분포"


def analyze_strip_yield(lot_id: str) -> dict:
    """LOT에 속한 strip들의 emap을 모아 die 단위 수율을 집계하고
    가장 불량이 심한 strip과 그 불량 위치 패턴(중앙/가장자리)을 찾는다.

    LOT 단위 die 수율 분석의 진입점. tdstripmap으로 strip 목록을 얻고
    각 strip의 emap을 읽어 strip별 수율을 순위화한다.
    """
    with _connect() as conn:
        cur = conn.execute(
            "SELECT STRIPID, POSITION FROM tdstripmap WHERE LOTID = ? ORDER BY POSITION",
            (lot_id,),
        )
        strips = cur.fetchall()

    if not strips:
        return {"error": f"LOT ID '{lot_id}'에 연결된 strip이 없습니다. "
                         f"strip 단위가 아닌 LOT/공정 단위 수율은 analyze_lot_yield를 사용하세요."}

    results = []
    for strip_id, position in strips:
        emap = get_emap(strip_id)
        if "error" in emap:
            results.append({"strip_id": strip_id, "position": position, "error": emap["error"]})
            continue
        path = os.path.join(EMAP_DIR, f"{strip_id}.txt")
        with open(path, "r") as f:
            map_rows = [ln.strip() for ln in f.read().splitlines()
                        if ln.strip() and all(c in "01" for c in ln.strip())]
        results.append({
            "strip_id": strip_id,
            "position": position,
            "yield_pct": emap["yield_pct"],
            "good_die": emap["good_die"],
            "fail_die": emap["fail_die"],
            "fail_location": _classify_fail_location(map_rows),
        })

    ranked = [r for r in results if "yield_pct" in r]
    worst = min(ranked, key=lambda r: float(r["yield_pct"].rstrip("%"))) if ranked else None
    total_die = sum(r["good_die"] + r["fail_die"] for r in ranked)
    total_fail = sum(r["fail_die"] for r in ranked)
    lot_die_yield = round((total_die - total_fail) / total_die * 100, 2) if total_die else None

    return {
        "lot_id": lot_id,
        "strip_count": len(strips),
        "lot_die_yield": f"{lot_die_yield}%" if lot_die_yield is not None else "N/A",
        "strips": results,
        "worst_strip": None if worst is None else {
            "strip_id": worst["strip_id"], "position": worst["position"],
            "yield_pct": worst["yield_pct"], "fail_location": worst["fail_location"],
        },
    }


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
        "name": "check_spec_violation",
        "description": "LOT ID로 레시피 스펙(min/max)과 설비 실측값을 자동 비교하여 스펙 이탈 항목을 찾습니다. 수율 저하 원인 분석 시 get_recipe/get_eqp_history를 개별 조회하는 대신 이 도구 하나로 이상 여부를 바로 확인할 수 있습니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lot_id": {"type": "string", "description": "검사할 LOT ID (예: HY260A01)"}
            },
            "required": ["lot_id"]
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
    {
        "name": "analyze_lot_yield",
        "description": "LOT의 공정별 수율을 '분석'합니다. 최종 수율, 수율이 빠진 공정(REJECT>0), 가장 수율 낮은 공정을 자동으로 짚어줍니다. '수율 알려줘/분석해줘/어디서 빠졌어' 같은 LOT·공정 단위 수율 질문은 get_test_result 원본 덤프 대신 이 도구를 우선 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lot_id": {"type": "string", "description": "분석할 LOT ID (예: HY260A01)"}
            },
            "required": ["lot_id"]
        }
    },
    {
        "name": "analyze_strip_yield",
        "description": "LOT에 속한 strip들의 emap을 모아 die 단위 수율을 집계하고, 가장 불량이 심한 strip과 불량 위치 패턴(중앙 집중/가장자리)을 찾습니다. 'strip 수율/die 불량/어느 strip이 이상해' 같은 strip·die 단위 질문에 사용하세요. (공정 단위 수율은 analyze_lot_yield)",
        "input_schema": {
            "type": "object",
            "properties": {
                "lot_id": {"type": "string", "description": "분석할 LOT ID (예: SM260B01)"}
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
    "check_spec_violation": check_spec_violation,
    "analyze_lot_yield":   analyze_lot_yield,
    "analyze_strip_yield": analyze_strip_yield,
}


def execute_tool(name: str, inputs: dict) -> str:
    """Function Calling 결과를 JSON 문자열로 반환."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
    try:
        result = fn(**inputs)
    except Exception as e:
        result = {"error": f"{name} 실행 중 오류: {e}"}
    return json.dumps(result, ensure_ascii=False, indent=2)
