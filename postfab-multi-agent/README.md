# PostFab Multi-Agent

반도체 후공정 P&T 업무 자동화를 위한 **RAG + Function Calling 기반 Multi-Agent 시스템**.

본 프로젝트는 Router, Planner, Knowledge, Data, Report Agent로 역할을 분리한 Multi-Agent 시스템입니다.  
Knowledge Agent는 RAG를 통해 공정 지식을 검색하고, Data Agent는 Function Calling으로 MES/FDC/YMS Mock DB를 조회하며, Report Agent는 이를 종합해 Lot 이상 원인 분석 리포트를 생성합니다.

---

## 시스템 아키텍처

```
User
 ↓
Router Agent      → 질문 유형 분류 (knowledge / data / root_cause)
 ↓
Planner Agent     → 실행 단계 계획 수립 (root_cause 시에만 동작)
 ↓
Knowledge Agent ── RAG (ChromaDB + SentenceTransformer 다국어 임베딩)
Data Agent ─────── Function Calling (SQLite Mock DB: MES/FDC/YMS)
 ↓
Report Agent      → 데이터 + 지식 종합 → 원인 분석 리포트
 ↓
Final Answer (Streamlit UI)
```

---

## 지원 시나리오

### 시나리오 1 — 용어/개념 질문
```
"FDC가 뭐야?"
흐름: Router → Knowledge Agent → RAG 검색 → 답변
```

### 시나리오 2 — LOT 데이터 조회
```
"LOT002 수율 알려줘"
흐름: Router → Data Agent → Function Calling (get_test_result) → 답변
```

### 시나리오 3 — 수율 저하 원인 분석 리포트
```
"LOT002 수율 저하 원인 분석해줘"
흐름: Router → Planner → Data Agent (5개 함수 호출) + Knowledge Agent → Report Agent → 리포트
```

---

## Agent 역할

| Agent | 모델 | 역할 |
|-------|------|------|
| **Router** | claude-haiku-4-5 | 질문 유형 분류 (knowledge / data / root_cause) |
| **Planner** | claude-haiku-4-5 | 분석에 필요한 실행 단계 계획 |
| **Knowledge** | claude-haiku-4-5 | RAG 기반 공정 지식 검색 및 답변 생성 |
| **Data** | claude-haiku-4-5 | Function Calling으로 Mock DB 조회 |
| **Report** | claude-sonnet-4-6 | 수집 데이터 종합 → 구조화된 리포트 생성 |

> Router/Planner/Knowledge/Data는 속도·비용 최적화를 위해 Haiku 사용.  
> 최종 리포트 품질을 위해 Report Agent만 Sonnet 사용.

---

## Function Calling 도구

Data Agent가 Claude tool_use로 호출하는 함수 목록:

| 함수 | 설명 |
|------|------|
| `get_lot_info(lot_id)` | LOT 기본 정보 (제품, 공정, 설비, 상태) |
| `get_test_result(lot_id)` | Final Test 수율, 불량 유형 |
| `get_fdc_alarm(equipment_id)` | FDC 알람 이력 (코드, 심각도, 시각) |
| `get_yield_trend(equipment_id)` | 일별 수율 트렌드 |
| `get_recipe_history(equipment_id)` | Recipe 변경 이력 |

---

## Mock DB 구성

`data/mock/postfab.db` (SQLite)

| 테이블 | 내용 |
|--------|------|
| `lot_info` | LOT 기본 정보 (LOT001~003) |
| `test_result` | FT1 수율 데이터 (LOT002: 78% — 이상 LOT) |
| `fdc_alarm` | TEST02 설비 알람 3건 (TEMP_HIGH, CONTACT_FAIL, POWER_NOISE) |
| `yield_trend` | TEST02 최근 5일 수율 추이 (96.5% → 78% 하락) |
| `recipe_history` | TEST02 Recipe 변경 이력 2건 (VCCQ 범위, tRCD 마진 조정) |

---

## RAG 구성

- **임베딩 모델**: `paraphrase-multilingual-MiniLM-L12-v2` (한국어 지원)
- **벡터 DB**: ChromaDB (로컬 Persistent)
- **지식 소스**: `data/docs/postfab_terms.md` (FDC, Yield, tRCD, VCCQ 등 12개 용어)
- **청크 단위**: `##` 헤더 기준 섹션 분할

---

## 설치 및 실행

### 1. 환경 설정

```bash
cd postfab-multi-agent
pip install -r requirements.txt

cp .env.example .env
# .env 파일에 ANTHROPIC_API_KEY 입력
```

### 2. Mock DB 생성

```bash
python scripts/create_mock_db.py
```

### 3. RAG 벡터스토어 구축

```bash
python src/rag/build_vectorstore.py
```

### 4. Streamlit 앱 실행

```bash
streamlit run app/streamlit_app.py
```

---

## 폴더 구조

```
postfab-multi-agent/
├── app/
│   └── streamlit_app.py          # Streamlit UI (사이드바, 탭, 실행 로그 시각화)
├── data/
│   ├── docs/
│   │   └── postfab_terms.md      # 후공정 용어집 (RAG 소스, 12개 용어)
│   ├── mock/
│   │   └── postfab.db            # SQLite Mock DB (MES/FDC/YMS)
│   ├── finetune/
│   │   └── postfab_qa.jsonl      # Fine-tuning용 Q&A 데이터셋 (12쌍)
│   └── chroma/                   # ChromaDB 벡터 저장소 (자동 생성)
├── scripts/
│   └── create_mock_db.py         # Mock DB 초기화
├── src/
│   ├── agents/
│   │   ├── router_agent.py       # 질문 분류 (intent: knowledge/data/root_cause)
│   │   ├── planner_agent.py      # 실행 단계 계획 수립
│   │   ├── knowledge_agent.py    # RAG 기반 지식 검색 + 답변 생성
│   │   ├── data_agent.py         # Function Calling agentic loop
│   │   └── report_agent.py       # 원인 분석 리포트 생성
│   ├── rag/
│   │   ├── build_vectorstore.py  # ChromaDB 구축 스크립트
│   │   └── retriever.py          # 유사 문서 검색
│   ├── tools/
│   │   └── postfab_tools.py      # Function 정의, Claude tool_use 스펙, 실행기
│   └── workflow.py               # 전체 에이전트 흐름 조율 + 로그 수집
├── notebooks/
│   └── finetune_qlora.ipynb      # QLoRA 파인튜닝 실험 (Colab 실행 권장)
├── .env.example
├── requirements.txt
└── README.md
```

---

## Fine-tuning 실험

`notebooks/finetune_qlora.ipynb` — QLoRA 기반 도메인 파인튜닝 실험.

- **베이스 모델**: `beomi/Llama-3-Open-Ko-8B` (한국어 특화 Llama-3 8B)
- **학습 기법**: QLoRA (4-bit NF4 양자화 + LoRA rank=16)
- **데이터셋**: `data/finetune/postfab_qa.jsonl` (12개 Q&A 쌍)
- **실행 환경**: Google Colab T4 GPU 권장

파인튜닝 목표: 일반 LLM이 후공정 도메인 용어(FDC, tRCD, VCCQ 등)에 정확하게 답변하도록 특화.

---

## Streamlit UI 주요 화면

```
[질문 입력창]  [실행 ▶]

┌─ 🔍 에이전트 실행 과정 ─┬─ 💬 최종 답변 ─┬─ 📜 이전 기록 ─┐

[Router Agent]  intent: root_cause | LOT ID: LOT002

[Planner Agent] get_lot_info → get_test_result → get_fdc_alarm
                → get_yield_trend → get_recipe_history
                → search_knowledge → generate_report

[실행 로그]
🔧 get_lot_info("LOT002")        → [결과 보기]
🔧 get_test_result("LOT002")     → [결과 보기]
🔧 get_fdc_alarm("TEST02")       → [결과 보기]
🔧 get_yield_trend("TEST02")     → [결과 보기]
🔧 get_recipe_history("TEST02")  → [결과 보기]
🔍 Knowledge Agent: 공정 지식 검색 완료
📝 Report Agent: 리포트 생성

└──────────────────────────────────────────────────────────┘
```

Function Calling 로그가 멀티에이전트가 실제로 분업하며 작동한다는 핵심 증거다.
