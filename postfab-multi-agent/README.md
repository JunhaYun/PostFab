# PostFab Multi-Agent

반도체 후공정 P&T 업무 자동화를 위한 **RAG + Function Calling 기반 Multi-Agent 시스템**.

현장 엔지니어가 자연어로 질문하면 6개 에이전트가 분업해 공정 지식 검색, MES 데이터 조회·집계, 수율 저하 원인 분석 리포트 생성까지 처리합니다.
Knowledge Agent는 RAG로 공정 지식을 검색하고, Data Agent는 Function Calling으로 MES/FDC/YMS Mock DB를 조회하며, Report Agent는 이를 종합해 LOT 이상 원인 분석 리포트를 생성합니다.

---

## 평가 결과

모든 개선은 **같은 시험지로 전/후를 재서** 채택 여부를 정했습니다.

| 평가 | 결과 |
|---|---|
| 검색 정확도 Accuracy@3 (임베딩 파인튜닝 전 → 후) | **0.888 → 0.970** |
| 검색 정확도 NDCG@10 (임베딩 파인튜닝 전 → 후) | **0.869 → 0.925** |
| Router intent 분류 (4개 intent, 26문항) | **26/26 (100%)** |
| Data Agent 도구 선택 | **18/18 (100%)** |
| E2E 원인분석 성공률 (36문항, 단일 실행) | **35/36 (97.2%)** |
| 멀티턴 지시어 해석 (대화 5개 / 14턴) | 12/14 → **14/14 (100%)** |

BM25 하이브리드 검색은 구현 후 측정했으나 **Accuracy@3가 0.970 → 0.925로 나빠져 채택하지 않았습니다.**

📊 **측정 방법, 유형별 분해, 채점기 버그 이력, 한계 → [docs/EVALUATION.md](docs/EVALUATION.md)**

---

## 시스템 아키텍처

LangGraph `StateGraph` + 조건부 엣지로 구성. intent에 따라 4가지 경로로 분기합니다.

```
User
 ↓
Rewriter Agent    → 이전 대화가 있을 때만 지시어("걔가", "첫번째 LOT")를 푼 독립 질문으로 재작성
 ↓
Router Agent      → intent 분류 (knowledge / data / root_cause / out_of_scope)
 ↓
 ├── knowledge    → Knowledge Agent (RAG 검색) → 답변
 ├── data         → Data Agent (Function Calling agentic loop) → 답변
 ├── out_of_scope → LLM 호출 없이 정중히 거절
 └── root_cause   → Planner → Data Agent → Knowledge Search → Report Agent → 리포트
```

**Rewriter를 Router 앞에 둔 이유**: RAG 검색은 대화 맥락을 볼 수 없어서 "그게 뭔데?"를 그대로 검색하면 엉뚱한 문서가 나옵니다. 재작성기는 이전 대화가 없으면 아예 호출되지 않으므로 단일 질문 경로에는 영향이 없습니다.

멀티턴 누적 컨텍스트는 `workflow.run()`의 `compact_history()`가 서버측 한 곳에서 압축합니다(웹/Streamlit/API에 동시 적용). 리포트는 '요약' 섹션만 남기고, **표는 그대로 둡니다** — "첫번째 LOT"이 앞 턴의 표를 가리키기 때문입니다.

---

## 지원 시나리오

### 1. 용어·개념 질문 (knowledge)
```
"FDC가 뭐야?"  /  "Incomplete Fill 불량 원인이 뭐야?"
Router → Knowledge Agent → RAG 검색 → 답변
```

### 2. LOT 데이터 조회 (data)
```
"HY260A01 수율 알려줘"
Router → Data Agent → analyze_lot_yield → 답변
```

### 3. 기간 단위 이상 탐지 (data)
```
"5월 대비 6월에 수율 떨어진 설비 있어?"  /  "6월에 수율 낮은 LOT 10개"
Router → Data Agent → compare_yield_periods / find_low_yield_lots → 답변
```
LOT ID를 모르는 상태에서 **대상을 특정하는 앞단**입니다. 기존 도구는 전부 `lot_id`가 필수라 "이미 답을 아는 사람"만 쓸 수 있었습니다.

### 4. 수율 저하 원인 분석 (root_cause)
```
"HY260A01 수율 저하 원인 분석해줘"
Router → Planner → Data Agent(다중 도구 호출) → Knowledge Search → Report Agent → 리포트
```

### 5. 멀티턴 드릴다운
```
"5월 대비 6월에 수율 떨어진 설비 있어?"      → ML002 특정
"그럼 걔가 6월에 처리한 LOT 중 수율 낮은 거 5개"  → 지시어 "걔" 해석 + eqp_id 자동 전달
"첫번째 LOT 원인 분석해줘"                    → 앞 턴 표의 첫 행을 lot_id로 해석 → 리포트
```

### 6. 범위 밖 질문 (out_of_scope)
```
"오늘 점심 뭐 먹지?" → LLM 호출 없이 정중히 거절
```

---

## Agent 역할

| Agent | 모델 | 역할 |
|-------|------|------|
| **Rewriter** | claude-haiku-4-5 | 이전 대화가 있을 때 지시어를 푼 독립 질문으로 재작성 |
| **Router** | claude-haiku-4-5 | intent 분류 (knowledge / data / root_cause / out_of_scope), ICL few-shot |
| **Planner** | claude-haiku-4-5 | 원인 분석에 필요한 실행 단계 계획 (root_cause 시에만) |
| **Knowledge** | claude-haiku-4-5 | RAG 기반 공정 지식 검색 및 답변 생성 |
| **Data** | claude-haiku-4-5 | Function Calling agentic loop로 Mock DB 조회·집계 |
| **Report** | claude-sonnet-4-6 | 수집 데이터 + 검색 지식 종합 → 구조화된 리포트 |

> 속도·비용 최적화를 위해 Report를 제외한 전 에이전트에 Haiku 사용. 최종 리포트 품질을 위해 Report Agent만 Sonnet.
> **전 에이전트 `temperature=0`** — 미설정 시 기본값 1.0으로 돌아 같은 질문에 답이 매번 달라졌고, 이것이 E2E 평가 실행 편차의 주원인이었습니다.

---

## Function Calling 도구 (12개)

Data Agent가 Claude `tool_use`로 호출합니다.

### 단일 LOT 조회 — `lot_id`를 이미 아는 경우

| 함수 | 설명 |
|------|------|
| `get_lot_info(lot_id)` | LOT 기본 정보 (제품, BOM, 고객사, 상태) |
| `get_test_result(lot_id)` | 공정 진행 이력, 공정별 수율(YIELD), 사용 설비 |
| `get_recipe(lot_id)` | 사용 레시피 이름 및 항목별 스펙(min/max) |
| `get_eqp_history(lot_id, eqp_id)` | 설비 통과 시 실측값 (온도, 압력, 시간 등) |
| `check_spec_violation(lot_id)` | 레시피 스펙 vs 실측값 자동 비교 → 스펙 이탈 항목 탐지 |
| `get_strip_map(lot_id)` | LOT 소속 Strip 목록 및 위치/공정 정보 |
| `get_emap(strip_id)` | Strip emap 파싱 → pass/fail 분포, 불량 위치 패턴 |

### 분석 — 원본 덤프 대신 결론을 만들어 주는 도구

| 함수 | 설명 |
|------|------|
| `analyze_lot_yield(lot_id)` | 최종 수율, 손실 발생 공정(REJECT>0), 최저 수율 공정을 자동으로 짚음 |
| `analyze_strip_yield(lot_id)` | LOT 내 strip emap을 모아 die 단위 집계 + 불량 위치 패턴(중앙 집중/가장자리) 판정 |

### 기간 집계 — `lot_id`를 **모르는** 상태에서 대상을 찾는 도구

| 함수 | 설명 |
|------|------|
| `find_low_yield_lots(start_date, end_date, [limit, step_name, eqp_id])` | 기간 내 저수율 LOT 순위 |
| `summarize_yield_by(group_by, start_date, end_date, [limit])` | 공정별 / 설비별 / 월별 수율 집계 |
| `compare_yield_periods(group_by, period1, period2, [limit])` | 두 기간 비교, 하락폭 순 정렬 |

> **자유 SQL(Text-to-SQL)을 의도적으로 배제했습니다.** `SELECT`만 허용해도 막을 수 있는 건 쓰기/삭제뿐이고, "에러 없이 실행되는 틀린 숫자"는 막지 못합니다 — `YIELD`가 `'96.2%'` TEXT라 `AVG()`가 엉뚱해지거나, 공정별 수율을 단순 산술평균 내는 경우입니다. 제조 데이터에서 그런 오답은 사람이 알아채기 어렵습니다.
> 그래서 **SQL은 코드에 고정하고 LLM은 조건(기간/대상/개수)만 채우게** 했습니다. 수율은 전부 **가중 수율(산출합/투입합)**로 계산하며 산술평균은 쓰지 않습니다. 오타 입력 시에는 빈 결과 대신 에러 + 사용 가능 목록을 반환합니다 — 0건이 "그 기간엔 문제 없었다"로 오독되는 것을 막기 위해서입니다.

---

## Mock DB 구성

`data/mock/postfab.db` (SQLite). `scripts/create_mock_db.py`로 `post_data/` 엑셀을 적재하고, `scripts/11_simulate_data.py`로 6개월치 트랜잭션을 시뮬레이션합니다.

| 테이블 | 행 수 | 내용 |
|--------|------|------|
| `tdlotinfo` | 3,004 | LOT 기본 정보 |
| `tdtestresult` | 39,013 | 공정 진행 이력 및 공정별 수율 (STEPSEQ 순) |
| `tdeqphistory` | 36,445 | 설비 실측값 이력 (스펙 이탈 판단용) |
| `tdrecipemapping` | 3,001 | LOT ↔ 레시피 매핑 |
| `tdrecipemaster` | 13 | 레시피 항목별 스펙 (min/max) |
| `tdstripmap` | 510 | Strip 목록 및 위치 |

emap 원본: `post_data/emap/*.txt` 510개 (Strip별 die pass/fail 맵)

**시뮬레이션 규모**: LOT 3,000개 / 6개월(2026-01-05 ~ 07-04) / 13공정. LOT의 5%에 strip 3매 + emap을 생성합니다.

**공정 순서는 지어내지 않았습니다.** 기존 MES 데이터의 5개 스텝을 그대로 두고, 빠진 단계만 공개 자료(SK하이닉스 뉴스룸 등)를 근거로 채워 13단계를 구성했으며, 각 단계에 `origin`(mes/corpus)과 근거 URL을 주석으로 남겼습니다.

**정답 있는 이상 사건 12건**을 심어 두었습니다 — 스펙 위반 4 / 설비 열화 2 / 급락 2 / strip 패턴 4. 정답지는 `data/eval/simulated_events.json`이며, E2E 평가 문항이 여기서 자동 생성됩니다.

---

## RAG 구성

- **임베딩 모델**: `models/postfab-bge-m3-v2` — `BAAI/bge-m3`를 후공정 도메인으로 파인튜닝
- **벡터 DB**: ChromaDB (로컬 Persistent), 컬렉션 2개 — `glossary_cards` / `article_chunks`
- **코퍼스**: 검색 단위 **686개** (용어 카드 378 + 본문 청크 308)
- **설정 단일화**: `src/rag/config.py` — 적재(`build_vectorstore`)와 검색(`retriever`)이 **반드시 같은 모델**을 써야 하므로 한 곳에서 관리합니다. 모델을 바꾸면 코퍼스 전체 재임베딩이 필요합니다.
- **모델 전환**: `POSTFAB_EMBED_MODEL` 환경변수 (파인튜닝 전/후 비교 실험용)

### 지식 소스 출처

| 소스 | 카드 | 청크 |
|---|---|---|
| JEDEC 표준 | 227 | — |
| SK하이닉스 공개 자료 (뉴스룸/강의/후공정) | 69 | 232 |
| Advantest 공개 자료 | 45 | — |
| ASE 공개 자료 | — | 27 |
| 자체 작성 문서 (`data/docs/*.md`) | 37 | 49 |

> **회사 내부 문서는 사용하지 않았습니다.** 트러블슈팅 카드·알람 코드·MES 운영·수율 분석 가이드(`data/docs/*.md`)는 공개 자료를 바탕으로 LLM이 초안을 쓰고 도메인 지식으로 검수한 **현실적인 가상 문서**입니다.

---

## 설치 및 실행

### 방법 A — Docker (권장)

모델과 코퍼스를 포함한 단일 이미지입니다. 빌드 시 Mock DB 생성과 벡터스토어 구축까지 끝납니다.

```bash
cd postfab-multi-agent
cp .env.example .env   # ANTHROPIC_API_KEY 입력
docker compose up --build
```

→ http://localhost:8000 (웹 UI + FastAPI)

### 방법 B — 로컬 실행

```bash
cd postfab-multi-agent
pip install -r requirements.txt

cp .env.example .env   # ANTHROPIC_API_KEY 입력

python scripts/create_mock_db.py      # Mock DB 생성
python src/rag/build_vectorstore.py   # RAG 벡터스토어 구축
```

**FastAPI 서버 + 웹 UI**
```bash
uvicorn app.api.main:app --reload --port 8000
```
→ http://localhost:8000

**Streamlit 프로토타입 UI**
```bash
streamlit run app/streamlit_app.py
```

> 임베딩 모델(`models/`)과 데이터(`data/`)는 용량 문제로 저장소에서 제외되어 있습니다. 로컬 실행 시 `POSTFAB_EMBED_MODEL=BAAI/bge-m3`로 베이스 모델을 쓸 수 있습니다(첫 실행 시 약 2.3GB 다운로드). 단, 파인튜닝 모델 대비 검색 성능은 낮습니다.

### API 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/chat` | 질문 → 에이전트 실행 → 답변 + 실행 로그 |
| `GET` | `/api/metrics` | 누적 호출/토큰/비용 메트릭 |
| `DELETE` | `/api/metrics` | 메트릭 초기화 |
| `GET` | `/api/health` | 헬스체크 |

---

## 데이터 파이프라인 & 평가 스크립트

```
scripts/
├── 01_crawl.py            # 공개 자료 수집
├── 02_clean.py            # 정제 (+ 02b/02c 이미지 캡션·검수)
├── 03_build_docs.py       # 문서화
├── 04_build_corpus.py     # 용어 카드 / 본문 청크 분리
├── 05_generate_qa.py      # 파인튜닝용 QA 생성
├── 06_split_qa.py         # train / valid / test 분할
├── 07_train_embedding.py  # bge-m3 파인튜닝 (GPU, Colab)
│
├── 08_eval_retrieval.py   # 검색 평가 (--retrieval vector|hybrid)
├── 09_eval_data_agent.py  # Data Agent 도구 선택 평가
├── 10_eval_router.py      # Router intent 분류 평가
│
├── 11_simulate_data.py    # 6개월 MES 트랜잭션 시뮬레이션 (+ 정답 사건 심기)
├── 12_build_e2e_eval.py   # 정답지 → E2E 문항 자동 생성
├── 13_eval_e2e.py         # E2E 원인분석 채점 (지표 4종 + LLM 의미 채점)
└── 14_eval_multiturn.py   # 멀티턴 지시어 해석 평가
```

**11번을 다시 돌리면 LOT ID가 바뀌므로 12번도 반드시 다시 돌려야 합니다.** E2E 문항을 손으로 쓰지 않고 자동 생성하는 이유입니다.

---

## 폴더 구조

```
postfab-multi-agent/
├── app/
│   ├── api/main.py               # FastAPI 서버 (운영용)
│   ├── frontend/index.html       # 웹 UI (HTML/JS 챗봇)
│   └── streamlit_app.py          # Streamlit 프로토타입 UI
├── src/
│   ├── agents/
│   │   ├── rewriter_agent.py     # 멀티턴 질문 재작성
│   │   ├── router_agent.py       # intent 분류 (ICL few-shot)
│   │   ├── planner_agent.py      # 실행 단계 계획
│   │   ├── knowledge_agent.py    # RAG 기반 지식 검색 + 답변
│   │   ├── data_agent.py         # Function Calling agentic loop
│   │   └── report_agent.py       # 원인 분석 리포트 생성
│   ├── rag/
│   │   ├── config.py             # 임베딩 모델·경로 단일 관리
│   │   ├── corpus_text.py        # 카드/청크 → 임베딩 대상 텍스트
│   │   ├── build_vectorstore.py  # ChromaDB 구축
│   │   ├── retriever.py          # 벡터 검색 + 컨텍스트 포매팅
│   │   └── bm25_index.py         # BM25 하이브리드 (측정 후 미채택, 기록용)
│   ├── tools/postfab_tools.py    # 도구 12개 정의 + tool_use 스펙 + 실행기
│   ├── workflow.py               # LangGraph 그래프 정의 + history 압축 + 로그 수집
│   └── metrics.py                # 호출/토큰/비용 집계
├── scripts/                      # 데이터 파이프라인 + 평가 (위 참고)
├── data/                         # 코퍼스·벡터스토어·평가셋 (일부 gitignore)
├── post_data/                    # MES 원본 엑셀 + emap (gitignore)
├── docs/EVALUATION.md            # 평가 결과 상세
├── notebooks/finetune_qlora.ipynb
├── Dockerfile / docker-compose.yml / entrypoint.sh
├── .env.example
└── requirements.txt
```

---

## 파인튜닝

### 임베딩 파인튜닝 (실제 적용)

`scripts/07_train_embedding.py` — `BAAI/bge-m3`를 후공정 도메인 코퍼스로 파인튜닝. GPU 필요(Colab 실행).

**핵심 설계 — positive는 생성된 QA answer가 아니라 원본 소스 텍스트입니다.** 실제 검색이 임베딩하는 대상은 카드 정의문과 청크 본문이지 QA answer가 아니기 때문입니다. 학습 타깃과 실제 검색 대상이 어긋나면 파인튜닝 효과가 검색 성능으로 이어지지 않습니다.

결과: Accuracy@3 0.888 → 0.970, NDCG@10 0.869 → 0.925 ([상세](docs/EVALUATION.md#1-검색--임베딩-파인튜닝-전후))

### QLoRA 실험 (별도, 미적용)

`notebooks/finetune_qlora.ipynb` — 답변 생성 LLM 자체를 도메인 특화하려던 초기 실험(`beomi/Llama-3-Open-Ko-8B`, 4-bit NF4 + LoRA rank=16). 최종 시스템은 Claude API + RAG 구조를 택했으므로 적용하지 않았고, 기록으로 남겨 둡니다.

---

## 실행 로그

Function Calling 로그는 멀티에이전트가 실제로 분업하며 동작한다는 핵심 증거입니다. 웹 UI와 Streamlit 모두 에이전트별 실행 과정을 펼쳐 볼 수 있습니다.

```
[Rewriter]  "첫번째 LOT 원인 분석해줘" → "CB262S2401 수율 저하 원인 분석해줘"
[Router]    intent: root_cause | LOT ID: CB262S2401
[Planner]   get_lot_info → analyze_lot_yield → check_spec_violation → get_eqp_history
[Data]      🔧 get_lot_info("CB262S2401")         → [결과 보기]
            🔧 analyze_lot_yield("CB262S2401")    → [결과 보기]
            🔧 check_spec_violation("CB262S2401") → [결과 보기]
[Knowledge] 🔍 수집 데이터로 검색어 조립 → 트러블슈팅 카드 검색
[Report]    📝 리포트 생성 (원인: TRANSFER_TIME 64.38초 > MAX 스펙 60.3초)
```
