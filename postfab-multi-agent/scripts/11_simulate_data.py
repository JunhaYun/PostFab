"""
11_simulate_data.py — 후공정 MES 트랜잭션 시뮬레이터.

목적: LOT 4개짜리 스냅샷이던 mock DB를 "시간이 흐르는" 대용량 데이터로 확장한다.
     시계열 질의(수율 추이/설비 이상 추적)와 로드맵 ⑤ Text-to-SQL이 의미를 가지려면
     집계·필터링이 필요한 양의 데이터가 있어야 하기 때문.

설계 원칙:
  1) 스키마 불변 — 기존 6개 테이블의 컬럼을 그대로 쓰고 행만 추가한다. 에이전트/도구 코드 무수정.
  2) 기존 4개 LOT 보존 — 시나리오 1/2/3 데모와 09 평가셋이 HY260A01/SM260B01에 의존하므로
     시뮬레이션 LOT은 그 옆에 추가만 하고 원본은 건드리지 않는다.
  3) 공정 순서는 지어내지 않는다 — 아래 PROCESS_PLAN 참조. 실제 MES에 있던 5개 스텝을
     유지하고, 빠진 단계만 공개 문서(SK하이닉스 뉴스룸) 근거로 채웠다.
  4) 재실행 안전 — 이전 시뮬레이션 행을 _sim_lots 레지스트리로 추적해 지우고 다시 만든다.
     시드 고정(--seed)이므로 같은 옵션이면 같은 데이터가 재현된다.

실행 순서:
  postfab/Scripts/python.exe scripts/create_mock_db.py     # 원본 4개 LOT 적재 (DB 초기화)
  postfab/Scripts/python.exe scripts/11_simulate_data.py   # 시뮬레이션 LOT 추가
  (create_mock_db.py는 DB 파일을 삭제하고 다시 만들므로 반드시 이 순서로 실행할 것)

옵션:
  --lots 3000 --months 6 --seed 42
  --dry-run   : DB에 쓰지 않고 생성 통계만 출력
"""
import argparse
import json
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "mock", "postfab.db")
EVENTS_PATH = os.path.join(BASE, "data", "eval", "simulated_events.json")

# ── 공정 순서 (PROCESS_PLAN) ───────────────────────────────────────────────
# origin="mes"    : 기존 mock DB(tdtestresult)에 실제로 존재하던 스텝 — 이름/의미 그대로 유지
# origin="corpus" : mock DB엔 없지만 공개 문서에 기재된 단계. 근거 URL을 함께 남긴다.
#
# 근거 (코퍼스 article_chunks, SK하이닉스 뉴스룸 공개 자료):
#   - chunk-0237/0238 「반도체 후공정 6편: 컨벤셔널 패키지 공정」
#     https://news.skhynix.co.kr/seominsuk-column-types-of-packages-6/
#     서브스트레이트 타입: Back Grinding → Wafer Sawing → Die Attach → Wire Bonding
#                        → Molding → Solder Ball Mounting → Singulation
#   - chunk-0167 「반도체 후공정 9편: 패키지의 역할과 재료(1)」
#     https://news.skhynix.co.kr/seominsuk-column-package-role-material-1/
#     세부 공통 공정: Wafer Thinning → Wafer Sawing → UV → Die Attach → Adhesive Cure
#                    → Wire Bonding → Plasma Cleaning → Mold → Post Mold Cure → Marking
#
# 기존 MES 스텝(AS_Plasma=Plasma Cleaning, AS_Mold=Mold, AS_DieBuild=Die Attach)이
# 위 문서 흐름의 부분집합이라, 문서에만 있고 DB에 없던 단계를 제자리에 끼워 넣었다.
# tdrecipemaster의 SPECNAME이 "MOLD_PCB"(=PCB/서브스트레이트 타입)이므로
# 리드프레임 타입(Trimming/Forming)이 아니라 서브스트레이트 타입 분기를 따른다.
# 수율 모델은 "대부분 100%, 일부 LOT에서만 손실"이다 — 기존 실데이터 패턴을 그대로 따랐다.
# (HY260A01: 100/100/100/100/96.2%, SM260B01: 100/98.5% — 공정마다 조금씩 깎이는 게 아니라
#  평소엔 무손실이고 특정 LOT·공정에서만 빠진다.) 매 스텝 99.x%로 두면 13단계 누적에서
# 최종 수율이 92%대까지 내려가 실제와 어긋나므로 손실 발생 확률 × 손실 크기로 모델링한다.
PROCESS_PLAN = [
    # (STEPSEQ, STEPNAME, 설비 prefix, 손실발생 확률, 평균 손실%, 손실 변동폭, origin)
    (10,  "AS_BackGrind",    "BG",  0.05, 0.3, 0.15, "corpus"),
    (20,  "AS_WaferSaw",     "WS",  0.10, 0.5, 0.25, "corpus"),
    (30,  "AS_DieBuild",     "DA",  0.08, 0.4, 0.20, "mes"),
    (40,  "AS_Inspection",   "IS",  0.15, 0.8, 0.40, "mes"),
    (50,  "AS_WireBond",     "WB",  0.20, 1.0, 0.50, "corpus"),
    (60,  "AS_MoldPreBake",  "PB",  0.02, 0.2, 0.10, "mes"),
    (70,  "AS_Plasma",       "PL",  0.03, 0.2, 0.10, "mes"),
    (80,  "AS_Mold",         "ML",  0.18, 1.2, 0.60, "mes"),
    (90,  "AS_PostMoldCure", "PMC", 0.03, 0.2, 0.10, "corpus"),
    (100, "AS_Marking",      "MK",  0.05, 0.3, 0.15, "corpus"),
    (110, "AS_BallMount",    "BM",  0.12, 0.7, 0.35, "corpus"),
    (120, "AS_Singulation",  "SG",  0.12, 0.8, 0.40, "corpus"),
    (130, "FT_FinalTest",    "FT",  0.60, 2.5, 1.20, "corpus"),
]

# 고객사/작업자/설비 식별자는 전부 합성값이다. 원본 데이터의 실제 값(고객사명, 사번,
# 설비 호기명, 사업장 코드)을 코드에 남기지 않기 위해 중립적인 코드 체계로 대체했다.
# 시뮬레이션 LOT은 이 값들을 쓰고, 원본 4개 LOT은 엑셀에 있던 값을 그대로 유지한다
# (post_data/는 gitignore이므로 저장소에는 실제 값이 올라가지 않는다).
CUSTOMERS = [
    # (prefix, CUSTOMERNAME, PRODUCTNAME, PLANNAME, 비중)
    ("CA", "CUSTOMER_A", "PRODUCT_A01", "AS_PRODUCTA-STD", 0.45),
    ("CB", "CUSTOMER_B", "PRODUCT_B01", "AS_PRODUCTB-STD", 0.40),
    ("CC", "CUSTOMER_C", "PRODUCT_C01", "AS_PRODUCTC-STD", 0.15),
]

ASSEMBLY_SITE = "SITE01"

# tdrecipemaster에 이미 정의된 몰드 레시피 항목 (스펙 min/max)
# 기존 행을 그대로 읽어 쓰므로 여기선 "사건을 심을 수 있는 수치형 항목"만 나열한다.
NUMERIC_RECIPE_ITEMS = [
    "MOLD_TEMP_3", "CURE_TIME", "CLAMP_FORCE", "TRANSFER_TIME", "TRANSFER_PRESSURE",
]

OPERATORS = [f"OP{i:03d}" for i in range(1, 6)]


# ── 시간 포맷 헬퍼 (기존 데이터 관례: "YYYYMMDD HHMMSSmmm") ─────────────
def ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%d %H%M%S") + "000"


def ts_short(dt: datetime) -> str:
    return dt.strftime("%Y%m%d %H%M%S")


def datecode(dt: datetime) -> str:
    """기존 데이터의 DATECODE는 '2601' 형태 — 연도 뒤 2자리 + 주차 2자리."""
    return f"{dt.strftime('%y')}{dt.isocalendar().week:02d}"


class Simulator:
    def __init__(self, n_lots: int, months: int, seed: int):
        self.rng = random.Random(seed)
        self.n_lots = n_lots
        self.months = months
        self.seed = seed
        self.start = datetime(2026, 1, 5, 6, 0, 0)
        self.end = self.start + timedelta(days=30 * months)

        self.lots, self.testresults, self.eqphistory, self.recipemapping = [], [], [], []
        self.events = []
        self.eqp_pool = self._build_eqp_pool()
        self.recipe_spec = {}   # KEYDATA -> (min, max)  — DB의 tdrecipemaster에서 읽음

    def _build_eqp_pool(self) -> dict:
        """스텝별 설비 목록. 같은 스텝에 설비 3대를 두어 설비별 비교 질의가 가능하게 한다.

        설비 ID는 공정 코드 + 일련번호로 생성한 합성값이다(원본 데이터의 실제 호기명은
        쓰지 않는다). 따라서 시뮬레이션 LOT과 원본 4개 LOT은 설비 ID를 공유하지 않으며,
        설비 단위 질의도 두 집합에 각각 따로 걸린다.
        """
        return {step: [f"{prefix}{i:03d}" for i in range(1, 4)]
                for _seq, step, prefix, *_ in PROCESS_PLAN}

    # ── 사건(정답 있는 이상) 정의 ──────────────────────────────────────
    def _plan_events(self):
        """정답을 아는 이상 사건을 시간축 위에 배치한다.

        각 사건은 (기간 × 설비 × 공정)에 걸리는 LOT들의 수율을 떨어뜨리고,
        원인이 레시피 스펙 위반인 경우 tdeqphistory 실측값도 스펙 밖으로 쓴다.
        정답은 data/eval/simulated_events.json에 저장돼 로드맵 ④ 평가셋의 근거가 된다.
        """
        span = (self.end - self.start).days

        def window(start_frac, days):
            s = self.start + timedelta(days=int(span * start_frac))
            return s, s + timedelta(days=days)

        # defect_name은 ⓪단계 지식베이스(troubleshooting_cards.md, 코퍼스에 internal_docs로 존재)의
        # 카드 제목과 1:1로 맞췄다. 이게 없으면 원인 분석 시 RAG가 해당 카드를 못 찾아
        # "수율이 떨어졌다"까지만 답하고 원인·조치를 설명하지 못한다(related_doc 참조).
        specs = [
            # (사건 유형, 공정, 설비, 기간, 하락폭, 레시피 항목, 위반 방향, 불량명, 대응 카드)
            ("recipe_spec_violation", "AS_Mold", "ML001", window(0.12, 9), 4.0, "MOLD_TEMP_3", "high",
             "몰딩 온도 스펙 이탈", "몰딩 온도 스펙 이탈 트러블슈팅"),
            ("recipe_spec_violation", "AS_Mold", "ML002", window(0.38, 7), 3.2, "CURE_TIME", "low",
             "박리 (Delamination)", "박리 (Delamination) 트러블슈팅"),
            ("recipe_spec_violation", "AS_Mold", "ML003", window(0.62, 6), 2.6, "CLAMP_FORCE", "low",
             "몰드 플래시 (Mold Flash)", "몰드 플래시 (Mold Flash) 트러블슈팅"),
            ("recipe_spec_violation", "AS_Mold", "ML002", window(0.82, 8), 3.5, "TRANSFER_TIME", "high",
             "인컴플리트 필 (Incomplete Fill)", "인컴플리트 필 (Incomplete Fill) 트러블슈팅"),
            # 레시피와 무관한 설비 열화 — 원인이 스펙 위반이 아닌 케이스도 섞는다
            ("eqp_degradation", "AS_WireBond", "WB002", window(0.25, 21), 5.0, None, None,
             "볼 리프트 (Ball Lift)", "볼 리프트 (Ball Lift) 트러블슈팅"),
            ("eqp_degradation", "AS_BallMount", "BM003", window(0.55, 14), 3.8, None, None,
             "솔더볼 미싱 (Solder Ball Missing)", "솔더볼 미싱 (Solder Ball Missing) 트러블슈팅"),
            ("sudden_drop", "AS_Singulation", "SG002", window(0.70, 3), 6.5, None, None,
             "쏘잉 치핑 (Sawing Chipping)", "쏘잉 치핑 (Sawing Chipping) 트러블슈팅"),
            ("sudden_drop", "FT_FinalTest", "FT001", window(0.90, 4), 7.0, None, None,
             "Contact Fail 급증", "Contact Fail 급증 트러블슈팅"),
        ]

        for idx, (etype, step, eqp, (s, e), drop, item, direction, defect, doc) in enumerate(specs, start=1):
            self.events.append({
                "event_id": f"SIM-EVT-{idx:02d}",
                "type": etype,
                "step": step,
                "eqp_id": eqp,
                "start": s.strftime("%Y-%m-%d"),
                "end": e.strftime("%Y-%m-%d"),
                "yield_drop_pct": drop,
                "root_cause_item": item,
                "violation_direction": direction,
                "defect_name": defect,
                "related_doc": f"[후공정 불량 트러블슈팅 카드] > {doc}",
                "_start_dt": s, "_end_dt": e,
                "affected_lots": [],
            })

    def _event_for(self, step: str, eqp: str, when: datetime):
        for ev in self.events:
            if ev["step"] == step and ev["eqp_id"] == eqp and ev["_start_dt"] <= when <= ev["_end_dt"]:
                return ev
        return None

    # ── LOT 생성 ────────────────────────────────────────────────────────
    def _pick_customer(self):
        r = self.rng.random()
        acc = 0.0
        for c in CUSTOMERS:
            acc += c[4]
            if r <= acc:
                return c
        return CUSTOMERS[-1]

    def generate(self):
        self._plan_events()
        span_seconds = int((self.end - self.start).total_seconds())

        for i in range(1, self.n_lots + 1):
            prefix, customer, product, plan = self._pick_customer()[:4]
            lot_id = f"{prefix}{260 + (i // 900):03d}S{i:04d}"
            start_dt = self.start + timedelta(seconds=self.rng.randint(0, span_seconds))

            self._make_lot_row(i, lot_id, customer, start_dt)
            self._make_route(i, lot_id, product, plan, start_dt)
            self._make_recipe_mapping(i, lot_id, start_dt)

    def _make_lot_row(self, i: int, lot_id: str, customer: str, start_dt: datetime):
        family = "Test" if self.rng.random() < 0.08 else "Assembly"
        self.lots.append((
            f"{start_dt.strftime('%y%m%d')}.S{i:04d}",   # SYSID
            lot_id, lot_id, customer, family, datecode(start_dt),
            f"BOM{self.rng.randint(1, 9):02d}",
            f"VISIONPROGRAM{self.rng.randint(1, 9):02d}",
            lot_id, lot_id,
            ts_short(start_dt + timedelta(days=self.rng.randint(20, 45))),  # SHIPDUEDATE
            ASSEMBLY_SITE, "F", "F", f"{start_dt.strftime('%y%m%d')}.S{i:04d}.0001", "F", "F", None,
        ))

    def _make_route(self, i: int, lot_id: str, product: str, plan: str, start_dt: datetime):
        """LOT 하나가 PROCESS_PLAN을 시간순으로 통과하며 tdtestresult 행을 남긴다."""
        qty = self.rng.choice([1000, 1000, 1000, 2000, 500])
        cur = start_dt
        in_qty = qty

        for seq, step, _prefix, loss_prob, loss_mean, loss_spread, _origin in PROCESS_PLAN:
            eqp = self.rng.choice(self.eqp_pool[step])
            cur += timedelta(hours=self.rng.uniform(1.5, 14.0))     # 대기 + 이동
            track_in = cur
            cur += timedelta(minutes=self.rng.uniform(20, 240))     # 처리 시간
            track_out = cur

            # 평소엔 손실 0(수율 100%), 확률적으로만 손실 발생
            loss = 0.0
            if self.rng.random() < loss_prob:
                loss = max(0.1, self.rng.gauss(loss_mean, loss_spread))

            # 사건 구간에 걸린 LOT은 손실이 확정적으로 추가된다
            ev = self._event_for(step, eqp, track_in)
            if ev:
                loss += max(0.1, self.rng.gauss(ev["yield_drop_pct"], ev["yield_drop_pct"] * 0.2))
                if lot_id not in ev["affected_lots"]:
                    ev["affected_lots"].append(lot_id)

            yield_pct = max(60.0, min(100.0, 100.0 - loss))

            out_qty = int(round(in_qty * yield_pct / 100.0))
            reject = in_qty - out_qty
            actual_yield = round(out_qty / in_qty * 100, 1) if in_qty else 0.0

            self.testresults.append((
                f"{i:06d}s.sim.{seq:04d}", lot_id, product, plan, step, seq,
                "Assembly" if not step.startswith("FT_") else "Test",
                ts(track_in), ts(track_out), self.rng.choice(OPERATORS), eqp,
                in_qty, reject, out_qty, f"{actual_yield}%", ts(track_out), actual_yield,
            ))

            # 몰드 공정은 설비 실측값(FDC)을 tdeqphistory에 남긴다 — 원인 분석의 근거 데이터
            if step == "AS_Mold":
                self._make_eqp_history(i, lot_id, eqp, track_out, ev)

            # 사건에 걸린 LOT은 검사에서 분류된 불량 유형을 남긴다.
            # tdeqphistory가 (KEYDATA, VALDATA) 키-값 이력 테이블이므로 스키마를 바꾸지 않고
            # DEFECT_TYPE 키로 기록한다. 이 값이 있어야 원인 분석 시 RAG가 해당 트러블슈팅
            # 카드를 찾아낸다 — 수치만 있고 불량명이 없으면 일반적인 수율 가이드만 검색된다.
            if ev:
                self.eqphistory.append((
                    f"{i:06d}s.sim.def", eqp, lot_id, self.rng.choice(OPERATORS),
                    ts(track_out), "DEFECT_TYPE", ev["defect_name"],
                ))

            in_qty = out_qty

    def _make_eqp_history(self, i: int, lot_id: str, eqp: str, when: datetime, ev):
        """레시피 항목별 실측값. 사건 대상 LOT은 원인 항목만 스펙 밖으로 쓴다."""
        for key, (lo, hi) in self.recipe_spec.items():
            if lo is None:
                continue
            if ev and ev.get("root_cause_item") == key:
                # 위반 폭은 기존 실데이터를 기준으로 잡았다 —
                # HY260A01의 MOLD_TEMP_3 스펙 350~360에 실측 370(=스펙폭의 1배 초과).
                margin = max((hi - lo) * 0.8, 1.0)
                val = hi + margin if ev["violation_direction"] == "high" else lo - margin
            elif lo == hi:
                val = lo
            else:
                val = self.rng.uniform(lo, hi)
            self.eqphistory.append((
                f"{i:06d}s.sim.eqp", eqp, lot_id, self.rng.choice(OPERATORS), ts(when),
                key, round(val, 2) if isinstance(val, float) else val,
            ))

    def _make_recipe_mapping(self, i: int, lot_id: str, start_dt: datetime):
        self.recipemapping.append((
            f"{i:06d}rc.sim", lot_id, self.rng.choice(OPERATORS),
            ts(start_dt), 1, "RECIPE", "MOLD83LS",
        ))


# ── DB 적재 ────────────────────────────────────────────────────────────
def load_recipe_spec(conn) -> dict:
    """tdrecipemaster에서 KEYDATA별 스펙(min,max)을 읽는다 — 스펙을 새로 지어내지 않기 위함."""
    spec = {}
    for key, lo, hi in conn.execute(
        "SELECT KEYDATA, VALDATA, VALDATA2 FROM tdrecipemaster WHERE ACTIVEFLAG='T'"
    ):
        try:
            spec[key] = (float(lo), float(hi))
        except (TypeError, ValueError):
            spec[key] = (None, None)          # VISUAL_INSPECTION 같은 비수치 항목
    return spec


def purge_previous(conn):
    """이전 시뮬레이션 행 제거 — 원본 4개 LOT은 레지스트리에 없으므로 보존된다."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_sim_lots'")
    if not cur.fetchone():
        conn.execute("CREATE TABLE _sim_lots (LOTID TEXT PRIMARY KEY, SEED INTEGER, CREATED TEXT)")
        return 0
    lot_ids = [r[0] for r in conn.execute("SELECT LOTID FROM _sim_lots")]
    if not lot_ids:
        return 0
    for table in ("tdlotinfo", "tdtestresult", "tdeqphistory", "tdrecipemapping"):
        conn.execute(
            f"DELETE FROM {table} WHERE LOTID IN (SELECT LOTID FROM _sim_lots)"
        )
    conn.execute("DELETE FROM _sim_lots")
    return len(lot_ids)


def main():
    ap = argparse.ArgumentParser(description="후공정 MES 트랜잭션 시뮬레이터")
    ap.add_argument("--lots", type=int, default=3000)
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        sys.exit(f"DB가 없습니다: {DB_PATH}\n먼저 scripts/create_mock_db.py 를 실행하세요.")

    conn = sqlite3.connect(DB_PATH)

    sim = Simulator(args.lots, args.months, args.seed)
    sim.recipe_spec = load_recipe_spec(conn)
    print(f"[11] 레시피 스펙 {len(sim.recipe_spec)}개 로드 (tdrecipemaster 기준)")

    sim.generate()
    print(f"[11] 생성: LOT {len(sim.lots):,} / 공정이력 {len(sim.testresults):,} "
          f"/ 설비실측 {len(sim.eqphistory):,} / 레시피매핑 {len(sim.recipemapping):,}")
    print(f"[11] 기간: {sim.start:%Y-%m-%d} ~ {sim.end:%Y-%m-%d} ({args.months}개월), "
          f"공정 {len(PROCESS_PLAN)}단계")

    print("\n[11] 심어둔 사건:")
    for ev in sim.events:
        cause = ev["root_cause_item"] or "-"
        print(f"  {ev['event_id']} {ev['type']:<24} {ev['step']:<16} {ev['eqp_id']:<8} "
              f"{ev['start']}~{ev['end']} 하락 {ev['yield_drop_pct']}%p "
              f"원인={cause} LOT {len(ev['affected_lots'])}개")

    if args.dry_run:
        print("\n[11] --dry-run: DB에 쓰지 않고 종료")
        conn.close()
        return

    removed = purge_previous(conn)
    if removed:
        print(f"\n[11] 이전 시뮬레이션 LOT {removed:,}개 제거 후 재생성")

    conn.executemany(f"INSERT INTO tdlotinfo VALUES ({','.join('?' * 18)})", sim.lots)
    conn.executemany(f"INSERT INTO tdtestresult VALUES ({','.join('?' * 17)})", sim.testresults)
    conn.executemany(f"INSERT INTO tdeqphistory VALUES ({','.join('?' * 7)})", sim.eqphistory)
    conn.executemany(f"INSERT INTO tdrecipemapping VALUES ({','.join('?' * 7)})", sim.recipemapping)
    conn.executemany(
        "INSERT INTO _sim_lots VALUES (?,?,?)",
        [(row[1], args.seed, datetime.now().isoformat(timespec="seconds")) for row in sim.lots],
    )
    conn.commit()

    # 정답지 저장 — 로드맵 ④ 전체 평가셋이 이 파일을 근거로 문항을 만든다
    os.makedirs(os.path.dirname(EVENTS_PATH), exist_ok=True)
    with open(EVENTS_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "description": "11_simulate_data.py가 심어둔 정답 있는 이상 사건. "
                           "로드맵 ④ E2E 평가셋의 근거로 사용한다.",
            "seed": args.seed, "n_lots": args.lots, "months": args.months,
            "process_plan": [
                {"seq": row[0], "step": row[1], "origin": row[-1]} for row in PROCESS_PLAN
            ],
            "events": [
                {k: v for k, v in ev.items() if not k.startswith("_")} for ev in sim.events
            ],
        }, f, ensure_ascii=False, indent=2)

    total = conn.execute("SELECT COUNT(*) FROM tdtestresult").fetchone()[0]
    lots_total = conn.execute("SELECT COUNT(*) FROM tdlotinfo").fetchone()[0]
    print(f"\n[11] 적재 완료 — tdlotinfo {lots_total:,}행 / tdtestresult {total:,}행")
    print(f"[11] 정답지 저장 → {EVENTS_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
