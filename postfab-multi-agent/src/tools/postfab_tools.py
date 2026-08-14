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
    """emap의 fail(0) 분포 패턴을 분류한다.

    반환값은 지식베이스 「수율 분석 가이드 > emap 패턴」 카드 4종과 대응한다:
    중앙 집중 / 가장자리 분포 / 라인·스트라이프 분포 / 산발 분포.

    판정은 불량의 최외곽 경계가 아니라 '밀도'로 한다. 실제 emap에는 어디든 배경 불량이
    몇 개씩 있는데, 경계 기준으로 보면 구석에 찍힌 불량 하나 때문에 중앙 집중 클러스터가
    전체 범위로 잡혀 오분류된다.
    """
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)
    fails = [(i, j) for i, r in enumerate(rows) for j, c in enumerate(r) if c == "0"]
    if not fails:
        return "불량 없음"
    total = len(fails)

    # ① 라인/스트라이프 — 멀티헤드 설비의 특정 헤드/사이트 이상은 일정 간격으로 나타난다.
    #    불량 열이 어떤 주기 k의 한 잔여류에 몰려 있으면 그 간격이 곧 헤드 수 단서가 된다.
    for k in (8, 4, 6, 3, 2):
        if n_cols < k * 3:
            continue
        for r in range(k):
            hits = sum(1 for _, j in fails if j % k == r)
            if hits >= total * 0.8 and len({j for _, j in fails if j % k == r}) >= 3:
                return "라인·스트라이프 분포"

    # ② 중앙 vs 가장자리 — 면적 대비 밀도 비율로 본다.
    #    가장자리 띠는 그 자체로 전체 면적의 30~40%를 차지하므로, 단순히 "불량의 절반이
    #    가장자리에 있다"는 조건은 완전 무작위 분포도 통과해버린다. 기대치(면적 비율)보다
    #    유의미하게 높을 때만 편중으로 본다.
    def _is_edge(i: int, j: int) -> bool:
        return (i < n_rows * 0.1 or i >= n_rows * 0.9
                or j < n_cols * 0.1 or j >= n_cols * 0.9)

    edge_cells = sum(1 for i in range(n_rows) for j in range(n_cols) if _is_edge(i, j))
    edge_area_ratio = edge_cells / (n_rows * n_cols) if n_rows and n_cols else 0

    in_center = sum(1 for i, j in fails
                    if n_rows * 0.2 <= i <= n_rows * 0.8 and n_cols * 0.2 <= j <= n_cols * 0.8)
    on_edge = sum(1 for i, j in fails if _is_edge(i, j))

    if in_center >= total * 0.7:
        return "중앙 집중"
    if edge_area_ratio and on_edge / total >= edge_area_ratio * 1.6:
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


# ── 기간 집계 도구 (여러 LOT을 가로질러 보는 도구) ────────────────────────────
#
# 위 도구들은 전부 LOT 하나(또는 strip 하나)를 보는 도구다. LOT 3,000개/6개월치
# 데이터가 들어오면서 "기간 내 수율 낮은 LOT", "설비별 추세" 같은 질문에 답할
# 수단이 없어 여기에 추가한다.
#
# 설계 원칙 — SQL은 여기 고정해 두고 LLM에게는 '조건'(기간/대상/개수)만 채우게 한다.
# LLM이 즉석에서 SQL을 짜면 문법은 맞아도 도메인상 틀린 숫자가 조용히 나올 수 있고
# (YIELD가 '96.2%' TEXT라 평균이 엉뚱해지는 것, 공정별 수율을 단순 산술평균 내는 것),
# 그런 오답은 에러가 안 나므로 사람이 알아채지 못한다. 조건 선택이 틀리면(6월↔7월)
# 사람이 바로 알아보지만 계산식이 틀리면 알아볼 방법이 없다.

from datetime import datetime

# group_by 라벨 → 집계 컬럼. LLM이 임의 컬럼을 넣지 못하도록 화이트리스트로 둔다.
_GROUP_COLUMNS = {
    "공정": "STEPNAME",
    "설비": "EQPID",
    "월":   "substr(TRACKOUTTIME, 1, 6)",
}

MAX_ROWS = 50   # 결과 폭주 방지 상한


def _norm_date(value: str, field: str) -> tuple[str | None, str | None]:
    """'2026-06-01' / '20260601' → '20260601'. 반환 (정규화값, 에러메시지)."""
    if not value:
        return None, f"{field}가 비어 있습니다."
    raw = str(value).strip().replace("-", "").replace("/", "")
    try:
        datetime.strptime(raw, "%Y%m%d")
    except ValueError:
        return None, f"{field} '{value}'는 날짜 형식이 아닙니다. YYYY-MM-DD로 입력하세요."
    return raw, None


def _fmt_date(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def _data_period() -> tuple[str, str]:
    with _connect() as conn:
        lo, hi = conn.execute(
            "SELECT MIN(substr(TRACKOUTTIME,1,8)), MAX(substr(TRACKOUTTIME,1,8)) FROM tdtestresult"
        ).fetchone()
    return lo, hi


def _validate_period(start_date: str, end_date: str) -> tuple[tuple[str, str] | None, dict | None]:
    """기간 인자를 검증한다. 실패 시 (None, 에러dict)."""
    start, err = _norm_date(start_date, "start_date")
    if err:
        return None, {"error": err}
    end, err = _norm_date(end_date, "end_date")
    if err:
        return None, {"error": err}
    if start > end:
        return None, {"error": f"start_date({_fmt_date(start)})가 end_date({_fmt_date(end)})보다 뒤입니다."}

    lo, hi = _data_period()
    if end < lo or start > hi:
        return None, {"error": f"요청 기간({_fmt_date(start)}~{_fmt_date(end)})에 데이터가 없습니다. "
                               f"보유 기간은 {_fmt_date(lo)}~{_fmt_date(hi)}입니다."}
    return (start, end), None


def _yield_pct(in_qty: int, out_qty: int) -> float | None:
    """가중 수율 = 산출합 / 투입합. 공정별 수율의 단순 산술평균은 투입량이 다른
    공정을 같은 무게로 취급해 실제와 어긋나므로 쓰지 않는다."""
    return round(out_qty / in_qty * 100, 2) if in_qty else None


def _yield_by_group(conn, column: str, start: str, end: str) -> dict[str, dict]:
    """기간 내 그룹별 투입/산출/불량 집계."""
    rows = conn.execute(
        f"SELECT {column} AS g, COUNT(DISTINCT LOTID), SUM(INQTY), SUM(REJECT), SUM(OUTQTY) "
        f"FROM tdtestresult WHERE substr(TRACKOUTTIME,1,8) BETWEEN ? AND ? GROUP BY g",
        (start, end),
    ).fetchall()
    return {
        g: {"lot_count": lots, "in_qty": in_q, "reject": rej,
            "out_qty": out_q, "yield_pct": _yield_pct(in_q, out_q)}
        for g, lots, in_q, rej, out_q in rows
    }


def find_low_yield_lots(start_date: str, end_date: str, limit: int = 10,
                        step_name: str | None = None,
                        eqp_id: str | None = None) -> dict:
    """기간 내 수율이 낮은 LOT을 순위로 뽑는다.

    step_name/eqp_id가 없으면 LOT 최종 수율(첫 공정 투입 대비 마지막 공정 산출) 기준,
    있으면 해당 공정/설비를 지난 LOT을 그 공정의 수율 기준으로 순위화한다.
    기간 판정은 LOT의 마지막 공정 완료일(TRACKOUTTIME) 기준이다.
    """
    period, err = _validate_period(start_date, end_date)
    if err:
        return err
    start, end = period

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, MAX_ROWS))

    with _connect() as conn:
        # 오타로 조건이 아무것도 안 걸려 빈 결과가 나오는 것을 막는다.
        # 조용히 0건을 돌려주면 "그 기간엔 문제가 없었다"로 오독된다.
        for label, col, val in (("공정", "STEPNAME", step_name), ("설비", "EQPID", eqp_id)):
            if not val:
                continue
            exists = conn.execute(
                f"SELECT 1 FROM tdtestresult WHERE {col} = ? LIMIT 1", (val,)
            ).fetchone()
            if not exists:
                names = [r[0] for r in conn.execute(
                    f"SELECT DISTINCT {col} FROM tdtestresult ORDER BY {col}")]
                return {"error": f"{label} '{val}'을(를) 찾을 수 없습니다.",
                        f"사용 가능한_{label}": names}

        if step_name or eqp_id:
            sql = ("SELECT LOTID, STEPNAME, EQPID, INQTY, REJECT, OUTQTY, YIELD, "
                   "substr(TRACKOUTTIME,1,8) FROM tdtestresult "
                   "WHERE substr(TRACKOUTTIME,1,8) BETWEEN ? AND ?")
            params: list = [start, end]
            if step_name:
                sql += " AND STEPNAME = ?"
                params.append(step_name)
            if eqp_id:
                sql += " AND EQPID = ?"
                params.append(eqp_id)
            sql += " ORDER BY YIELD_NUM ASC, LOTID LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()

            target = " / ".join(x for x in (step_name, eqp_id) if x)
            return {
                "기준": f"'{target}' 공정·설비의 수율 오름차순",
                "기간": f"{_fmt_date(start)} ~ {_fmt_date(end)} (공정 완료일 기준)",
                "조회건수": len(rows),
                "lots": [
                    {"lot_id": r[0], "공정": r[1], "설비": r[2], "투입": r[3],
                     "REJECT": r[4], "산출": r[5], "수율": r[6], "완료일": _fmt_date(r[7])}
                    for r in rows
                ] or "해당 조건의 데이터가 없습니다.",
            }

        # LOT 최종 수율 기준 — 기간 내 '완료된' LOT만 대상
        rows = conn.execute(
            "SELECT t.LOTID, t.STEPSEQ, t.STEPNAME, t.EQPID, t.INQTY, t.REJECT, "
            "       t.OUTQTY, t.YIELD, t.YIELD_NUM, substr(t.TRACKOUTTIME,1,8) "
            "FROM tdtestresult t "
            "JOIN (SELECT LOTID, MAX(substr(TRACKOUTTIME,1,8)) AS d "
            "      FROM tdtestresult GROUP BY LOTID) c ON t.LOTID = c.LOTID "
            "WHERE c.d BETWEEN ? AND ? "
            "ORDER BY t.LOTID, t.STEPSEQ",
            (start, end),
        ).fetchall()

    by_lot: dict[str, list] = {}
    for r in rows:
        by_lot.setdefault(r[0], []).append(r)

    summaries = []
    for lot_id, steps in by_lot.items():
        first, last = steps[0], steps[-1]
        final = _yield_pct(first[4] or 0, last[6] or 0)
        if final is None:
            continue
        worst = min(steps, key=lambda s: s[8])
        summaries.append({
            "lot_id": lot_id,
            "최종수율": f"{final}%",
            "_sort": final,
            "최악공정": worst[2],
            "최악공정_설비": worst[3],
            "최악공정_수율": worst[7],
            "완료일": _fmt_date(last[9]),
        })

    summaries.sort(key=lambda s: s["_sort"])
    top = summaries[:limit]
    for s in top:
        s.pop("_sort")

    return {
        "기준": "LOT 최종 수율(첫 공정 투입 대비 마지막 공정 산출) 오름차순",
        "기간": f"{_fmt_date(start)} ~ {_fmt_date(end)} (LOT 완료일 기준)",
        "기간내_완료_LOT수": len(summaries),
        "lots": top or "해당 기간에 완료된 LOT이 없습니다.",
    }


def summarize_yield_by(group_by: str, start_date: str, end_date: str,
                       limit: int = 20) -> dict:
    """기간 내 수율을 공정별/설비별/월별로 집계한다 (수율 낮은 순).

    각 그룹의 수율은 산출합/투입합(가중 수율)이며 공정별 수율의 산술평균이 아니다.
    """
    column = _GROUP_COLUMNS.get(group_by)
    if column is None:
        return {"error": f"group_by '{group_by}'는 지원하지 않습니다.",
                "사용 가능한 값": list(_GROUP_COLUMNS)}

    period, err = _validate_period(start_date, end_date)
    if err:
        return err
    start, end = period

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(limit, MAX_ROWS))

    with _connect() as conn:
        grouped = _yield_by_group(conn, column, start, end)

    items = []
    for key, v in grouped.items():
        if v["yield_pct"] is None:
            continue
        label = f"{key[:4]}-{key[4:6]}" if group_by == "월" else key
        items.append({group_by: label, "LOT수": v["lot_count"], "투입": v["in_qty"],
                      "REJECT": v["reject"], "산출": v["out_qty"],
                      "수율": f"{v['yield_pct']}%", "_sort": v["yield_pct"]})

    # 월은 시간 순, 나머지는 수율 낮은 순(=문제부터)
    items.sort(key=lambda x: x["월"] if group_by == "월" else x["_sort"])
    total_groups = len(items)          # limit로 자르기 전 전체 개수
    items = items[:limit]
    for x in items:
        x.pop("_sort")

    return {
        "집계기준": f"{group_by}별 가중 수율(산출합/투입합)",
        "기간": f"{_fmt_date(start)} ~ {_fmt_date(end)} (공정 완료일 기준)",
        "전체_그룹수": total_groups,
        "표시_건수": len(items),
        "결과": items or "해당 기간에 데이터가 없습니다.",
    }


def compare_yield_periods(group_by: str, period1_start: str, period1_end: str,
                          period2_start: str, period2_end: str,
                          limit: int = 10) -> dict:
    """두 기간의 수율을 공정별/설비별로 비교해 변화량(2기간 - 1기간)을 낸다.
    수율이 많이 떨어진 순으로 정렬하므로 설비 열화·추세 확인에 쓴다.
    """
    column = _GROUP_COLUMNS.get(group_by)
    if column is None or group_by == "월":
        return {"error": f"group_by '{group_by}'는 기간 비교에 쓸 수 없습니다.",
                "사용 가능한 값": ["공정", "설비"]}

    p1, err = _validate_period(period1_start, period1_end)
    if err:
        return {"error": f"[기간1] {err['error']}"}
    p2, err = _validate_period(period2_start, period2_end)
    if err:
        return {"error": f"[기간2] {err['error']}"}

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, MAX_ROWS))

    with _connect() as conn:
        g1 = _yield_by_group(conn, column, *p1)
        g2 = _yield_by_group(conn, column, *p2)

    both, only1, only2 = [], [], []
    for key in sorted(set(g1) | set(g2)):
        a, b = g1.get(key), g2.get(key)
        if a is None or a["yield_pct"] is None:
            only2.append(key)
            continue
        if b is None or b["yield_pct"] is None:
            only1.append(key)
            continue
        delta = round(b["yield_pct"] - a["yield_pct"], 2)
        both.append({group_by: key,
                     "기간1_수율": f"{a['yield_pct']}%", "기간1_LOT수": a["lot_count"],
                     "기간2_수율": f"{b['yield_pct']}%", "기간2_LOT수": b["lot_count"],
                     "변화": f"{delta:+}%p", "_sort": delta})

    both.sort(key=lambda x: x["_sort"])

    # 개수는 여기서 세서 넘긴다. limit 때문에 '결과'에는 상위 N건만 담기는데,
    # 그 목록만 보고 LLM이 "몇 개가 하락했다"를 세면 틀린다(실제로 15/16, 6/11로 틀렸다).
    n_down = sum(1 for x in both if x["_sort"] < 0)
    n_flat = sum(1 for x in both if x["_sort"] == 0)
    n_up = len(both) - n_down - n_flat

    top = both[:limit]
    for x in top:
        x.pop("_sort")

    result = {
        "비교기준": f"{group_by}별 가중 수율, 변화 = 기간2 - 기간1 (하락 순)",
        "기간1": f"{_fmt_date(p1[0])} ~ {_fmt_date(p1[1])}",
        "기간2": f"{_fmt_date(p2[0])} ~ {_fmt_date(p2[1])}",
        "비교된_그룹수": len(both),
        "요약": {"하락": n_down, "유지": n_flat, "상승": n_up},
        "표시_건수": f"하락 상위 {len(top)}건만 표시 (전체 {len(both)}건)",
        "결과": top or "두 기간 모두에 존재하는 그룹이 없습니다.",
    }
    # 한쪽 기간에만 있는 그룹은 비교 대상에서 빠졌다는 사실을 명시한다
    # (조용히 빠지면 "변화 없음"으로 오독된다).
    if only1:
        result["기간1에만_있음"] = only1
    if only2:
        result["기간2에만_있음"] = only2
    return result


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
    {
        "name": "find_low_yield_lots",
        "description": (
            "기간을 지정해 수율이 낮은 LOT을 순위로 뽑습니다. LOT ID를 모르는 상태에서 "
            "'지난달 수율 낮은 LOT 10개', '6월에 수율 안 좋았던 LOT' 처럼 여러 LOT을 "
            "가로질러 찾을 때 사용합니다. step_name/eqp_id를 주면 그 공정·설비를 지난 "
            "LOT을 해당 공정 수율 기준으로 순위화합니다. "
            "(LOT ID를 이미 아는 단일 LOT 분석은 analyze_lot_yield를 쓰세요.)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "조회 시작일 YYYY-MM-DD (예: 2026-06-01)"},
                "end_date":   {"type": "string", "description": "조회 종료일 YYYY-MM-DD (예: 2026-06-30)"},
                "limit":      {"type": "integer", "description": "가져올 LOT 개수 (기본 10, 최대 50)"},
                "step_name":  {"type": "string", "description": "특정 공정만 볼 때 공정명 (예: AS_Mold). 생략 가능"},
                "eqp_id":     {"type": "string", "description": "특정 설비만 볼 때 설비 ID (예: ML45DS). 생략 가능"}
            },
            "required": ["start_date", "end_date"]
        }
    },
    {
        "name": "summarize_yield_by",
        "description": (
            "기간 내 수율을 공정별/설비별/월별로 집계합니다. '6월 공정별 평균 수율', "
            "'설비별로 어디가 제일 나빠?', '월별 수율 추이' 같은 전체 현황 질문에 사용합니다. "
            "수율은 산출합/투입합(가중 수율)으로 계산합니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "group_by":   {"type": "string", "enum": ["공정", "설비", "월"],
                               "description": "집계 단위"},
                "start_date": {"type": "string", "description": "조회 시작일 YYYY-MM-DD"},
                "end_date":   {"type": "string", "description": "조회 종료일 YYYY-MM-DD"},
                "limit":      {"type": "integer", "description": "가져올 그룹 개수 (기본 20, 최대 50)"}
            },
            "required": ["group_by", "start_date", "end_date"]
        }
    },
    {
        "name": "compare_yield_periods",
        "description": (
            "두 기간의 수율을 공정별/설비별로 비교해 변화량을 냅니다. "
            "'5월 대비 6월에 수율 떨어진 설비', '최근 나빠지고 있는 공정' 처럼 "
            "추세·열화를 확인할 때 사용합니다. 수율이 많이 떨어진 순으로 정렬됩니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "group_by":      {"type": "string", "enum": ["공정", "설비"],
                                  "description": "비교 단위"},
                "period1_start": {"type": "string", "description": "기준 기간 시작일 YYYY-MM-DD (예: 이전 달)"},
                "period1_end":   {"type": "string", "description": "기준 기간 종료일 YYYY-MM-DD"},
                "period2_start": {"type": "string", "description": "비교 기간 시작일 YYYY-MM-DD (예: 최근 달)"},
                "period2_end":   {"type": "string", "description": "비교 기간 종료일 YYYY-MM-DD"},
                "limit":         {"type": "integer", "description": "가져올 그룹 개수 (기본 10, 최대 50)"}
            },
            "required": ["group_by", "period1_start", "period1_end", "period2_start", "period2_end"]
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
    "find_low_yield_lots":   find_low_yield_lots,
    "summarize_yield_by":    summarize_yield_by,
    "compare_yield_periods": compare_yield_periods,
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
