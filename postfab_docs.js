const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, PageBreak, LevelFormat, ExternalHyperlink,
  TableOfContents,
} = require('docx');
const fs = require('fs');

// ── 색상 팔레트 ──────────────────────────────────────────────
const C = {
  primary:   '1E40AF',  // 짙은 파랑
  secondary: '0F766E',  // 청록
  accent:    'DC2626',  // 빨강
  orange:    'D97706',  // 오렌지
  light:     'DBEAFE',  // 연파랑
  lightGreen:'D1FAE5',  // 연초록
  lightRed:  'FEE2E2',  // 연빨강
  lightYellow:'FEF3C7', // 연노랑
  gray:      'F1F5F9',  // 연회색
  border:    'CBD5E1',  // 테두리
  white:     'FFFFFF',
  text:      '1E293B',
  muted:     '64748B',
};

const PAGE_W = 12240;
const PAGE_H = 15840;
const MARGIN = 1080;
const CONTENT_W = PAGE_W - MARGIN * 2;

// ── 헬퍼 ─────────────────────────────────────────────────────
const border1 = (color = C.border) => ({ style: BorderStyle.SINGLE, size: 1, color });
const borders = (color = C.border) => ({ top: border1(color), bottom: border1(color), left: border1(color), right: border1(color) });
const cellMargin = { top: 100, bottom: 100, left: 140, right: 140 };

function h(level, text, color = C.primary) {
  const sizes = { 1: 36, 2: 28, 3: 24 };
  const spacing = { 1: { before: 400, after: 200 }, 2: { before: 300, after: 150 }, 3: { before: 240, after: 120 } };
  const hl = [null, HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3][level];
  return new Paragraph({
    heading: hl,
    spacing: spacing[level],
    children: [new TextRun({ text, size: sizes[level], bold: true, color, font: 'Arial' })],
  });
}

function p(text, opts = {}) {
  return new Paragraph({
    spacing: { before: 60, after: 120 },
    children: [new TextRun({ text, size: 22, font: 'Arial', color: C.text, ...opts })],
  });
}

function bullet(text, bold_prefix = '') {
  return new Paragraph({
    numbering: { reference: 'bullets', level: 0 },
    spacing: { before: 40, after: 40 },
    children: [
      ...(bold_prefix ? [new TextRun({ text: bold_prefix, bold: true, size: 22, font: 'Arial', color: C.text })] : []),
      new TextRun({ text, size: 22, font: 'Arial', color: C.text }),
    ],
  });
}

function numbered(text, bold_prefix = '') {
  return new Paragraph({
    numbering: { reference: 'numbers', level: 0 },
    spacing: { before: 40, after: 40 },
    children: [
      ...(bold_prefix ? [new TextRun({ text: bold_prefix, bold: true, size: 22, font: 'Arial', color: C.text })] : []),
      new TextRun({ text, size: 22, font: 'Arial', color: C.text }),
    ],
  });
}

function code(text) {
  return new Paragraph({
    spacing: { before: 60, after: 60 },
    shading: { fill: 'F1F5F9', type: ShadingType.CLEAR },
    indent: { left: 360 },
    children: [new TextRun({ text, font: 'Courier New', size: 18, color: '1D4ED8' })],
  });
}

function divider() {
  return new Paragraph({
    spacing: { before: 200, after: 200 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.border } },
    children: [],
  });
}

function pageBreak() {
  return new Paragraph({ children: [new PageBreak()] });
}

function tip(emoji, title, body) {
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: [CONTENT_W],
    rows: [new TableRow({ children: [
      new TableCell({
        borders: { top: border1(C.primary), bottom: border1(C.primary), left: { style: BorderStyle.SINGLE, size: 12, color: C.primary }, right: border1(C.border) },
        width: { size: CONTENT_W, type: WidthType.DXA },
        shading: { fill: C.light, type: ShadingType.CLEAR },
        margins: cellMargin,
        children: [
          new Paragraph({ spacing: { before: 0, after: 60 }, children: [
            new TextRun({ text: `${emoji} ${title}`, bold: true, size: 22, font: 'Arial', color: C.primary }),
          ]}),
          new Paragraph({ spacing: { before: 0, after: 0 }, children: [
            new TextRun({ text: body, size: 20, font: 'Arial', color: C.text }),
          ]}),
        ],
      })
    ]})]
  });
}

function twoColTable(rows, headers = null, colWidths = null) {
  const cw = colWidths || [3600, CONTENT_W - 3600];
  const tableRows = [];
  if (headers) {
    tableRows.push(new TableRow({ children: headers.map((h, i) => new TableCell({
      borders: borders(C.primary),
      width: { size: cw[i], type: WidthType.DXA },
      shading: { fill: C.primary, type: ShadingType.CLEAR },
      margins: cellMargin,
      children: [new Paragraph({ children: [new TextRun({ text: h, bold: true, color: C.white, size: 20, font: 'Arial' })] })],
    }))}));
  }
  rows.forEach((row, ri) => {
    const fill = ri % 2 === 0 ? C.white : C.gray;
    tableRows.push(new TableRow({ children: row.map((cell, ci) => new TableCell({
      borders: borders(C.border),
      width: { size: cw[ci], type: WidthType.DXA },
      shading: { fill, type: ShadingType.CLEAR },
      margins: cellMargin,
      children: [new Paragraph({ children: [new TextRun({ text: cell, size: 20, font: 'Arial', color: C.text })] })],
    }))}));
  });
  return new Table({ width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: cw, rows: tableRows });
}

function sp(n = 1) {
  return new Paragraph({ spacing: { before: 0, after: n * 80 }, children: [] });
}

// ── 문서 내용 ─────────────────────────────────────────────────
const children = [

  // ══════════════════════════════════════════════════════════
  //  COVER
  // ══════════════════════════════════════════════════════════
  new Paragraph({ spacing: { before: 2000, after: 400 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: 'PostFab Multi-Agent', size: 56, bold: true, color: C.primary, font: 'Arial' })] }),
  new Paragraph({ spacing: { before: 0, after: 200 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '반도체 후공정 P&T 업무 자동화 시스템', size: 32, color: C.secondary, font: 'Arial' })] }),
  new Paragraph({ spacing: { before: 0, after: 600 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: 'RAG  +  Function Calling  +  LangGraph  +  Fine-tuning', size: 24, color: C.muted, font: 'Arial' })] }),
  divider(),
  new Paragraph({ spacing: { before: 200, after: 100 }, alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '포트폴리오 기술 문서', size: 24, bold: true, color: C.text, font: 'Arial' })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 0, after: 1200 },
    children: [new TextRun({ text: '작성일: 2025년 6월', size: 22, color: C.muted, font: 'Arial' })] }),

  pageBreak(),

  // ══════════════════════════════════════════════════════════
  //  PART 1: 포트폴리오 설명
  // ══════════════════════════════════════════════════════════
  new Paragraph({ spacing: { before: 0, after: 100 }, children: [
    new TextRun({ text: 'PART 1', size: 18, color: C.muted, font: 'Arial', bold: true }),
  ]}),
  h(1, '포트폴리오 설명'),
  divider(),
  sp(),

  h(2, '1-1. 프로젝트 개요'),
  p('본 프로젝트는 반도체 후공정 P&T(Packaging & Test) 업무 자동화를 위한 Multi-Agent 시스템입니다. 현장 엔지니어가 자연어로 질문하면, 여러 AI 에이전트가 협업하여 공정 지식 검색, DB 조회, 원인 분석 리포트 생성까지 자동으로 처리합니다.'),
  sp(),

  h(2, '1-2. 핵심 기술 스택'),
  twoColTable([
    ['LangGraph', 'Multi-Agent 워크플로우 설계 (StateGraph + 조건부 엣지)'],
    ['Anthropic Claude API', 'Router / Planner / Knowledge / Data / Report 5개 Agent'],
    ['RAG (ChromaDB)', '후공정 용어집 벡터 임베딩 + 유사 문서 검색'],
    ['Function Calling', 'Claude tool_use로 SQLite Mock DB 자동 조회'],
    ['ICL (In-Context Learning)', 'Few-shot 예시로 Router 분류 정확도 향상'],
    ['FastAPI', '운영용 REST API 백엔드'],
    ['Streamlit', '프로토타입 챗봇 UI'],
    ['Docker', '컨테이너 기반 배포'],
    ['QLoRA Fine-tuning', '도메인 특화 LLM 학습 실험 (Llama-3 8B)'],
  ], ['기술', '적용 내용']),
  sp(2),

  h(2, '1-3. 시스템 아키텍처'),
  p('사용자 질문은 단일 진입점(workflow.run())을 통해 LangGraph StateGraph로 처리됩니다. intent에 따라 3가지 경로로 분기합니다.'),
  sp(),
  twoColTable([
    ['knowledge',   'Router → Knowledge Agent (RAG 검색) → 답변'],
    ['data',        'Router → Data Agent (Function Calling) → 답변'],
    ['root_cause',  'Router → Planner → Data Agent → Knowledge Search → Report Agent → 리포트'],
  ], ['Intent', '실행 경로'], [2400, CONTENT_W - 2400]),
  sp(2),

  h(2, '1-4. 지원 시나리오 3가지'),
  h(3, '시나리오 1 — 용어/개념 질문'),
  p('예시: "FDC가 뭐야?" / "tRCD Timing Fail 원인은?"'),
  p('postfab_terms.md에 정의된 후공정 용어 12개를 ChromaDB로 벡터화하여 유사도 검색 후 Claude Haiku가 답변 생성.'),
  sp(),
  h(3, '시나리오 2 — 데이터 단순 조회'),
  p('예시: "LOT002 수율 알려줘" / "TEST02 FDC 알람 있어?"'),
  p('Claude의 Function Calling(tool_use) 기능으로 SQLite Mock DB를 자동 조회. Claude가 어떤 함수를 호출할지 스스로 판단.'),
  sp(),
  h(3, '시나리오 3 — 수율 저하 원인 분석'),
  p('예시: "LOT002 수율 저하 원인 분석해줘"'),
  p('Planner가 get_lot_info, get_test_result, get_fdc_alarm, get_yield_trend, get_recipe_history 5개 함수를 계획. Data Agent가 순서대로 조회 후 Report Agent(Sonnet)가 구조화된 리포트 생성.'),
  sp(2),

  h(2, '1-5. 포트폴리오 핵심 어필 포인트'),
  bullet('LangGraph로 Multi-Agent 파이프라인 직접 설계 — StateGraph, 조건부 엣지, 공유 State 활용'),
  bullet('ICL(In-Context Learning) 기반 Router — Few-shot 예시로 맥락 이어받기("아니 mes.") 처리'),
  bullet('Function Calling Agentic Loop — Claude가 필요한 함수를 스스로 선택하고 결과를 재해석'),
  bullet('RAG 파이프라인 직접 구축 — 문서 파싱, 임베딩, ChromaDB 저장, 유사도 검색 전체 구현'),
  bullet('KPI 대시보드 — 응답시간(avg/P95), Intent 분포, Function Call 횟수 실시간 추적'),
  bullet('FastAPI + Docker — 프로토타입(Streamlit)과 운영용 API 서버 병행 구현'),
  bullet('대화 히스토리 유지 — 이전 대화 맥락을 Claude API history로 전달, 자연스러운 챗봇 경험'),
  sp(),
  tip('💡', '면접 포인트', '"LangGraph를 왜 썼나요?" → 에이전트 간 공유 상태(State)를 명시적으로 관리하고, 조건부 엣지로 분기 로직을 그래프 구조로 표현하여 디버깅과 확장이 용이하기 때문입니다.'),
  sp(2),

  pageBreak(),

  // ══════════════════════════════════════════════════════════
  //  PART 2: 코드 구조 (인수인계)
  // ══════════════════════════════════════════════════════════
  new Paragraph({ spacing: { before: 0, after: 100 }, children: [
    new TextRun({ text: 'PART 2', size: 18, color: C.muted, font: 'Arial', bold: true }),
  ]}),
  h(1, '코드 구조 설명 (인수인계)'),
  divider(),
  p('이 섹션은 처음 이 프로젝트를 받는 팀원이 구조를 빠르게 파악할 수 있도록 작성되었습니다. "어디서부터 읽어야 하나?" 라는 질문에 답합니다.'),
  sp(),

  h(2, '2-1. 폴더 구조 한눈에 보기'),
  twoColTable([
    ['postfab-multi-agent/', '프로젝트 루트'],
    ['  app/api/main.py', '★ FastAPI 서버 — 외부에서 들어오는 모든 요청을 받는 곳'],
    ['  app/frontend/index.html', '웹 UI — 순수 HTML/JS 챗봇 화면'],
    ['  app/streamlit_app.py', 'Streamlit UI — 프로토타입용 (개발/데모)'],
    ['  src/workflow.py', '★★ LangGraph 그래프 정의 — 전체 흐름의 핵심'],
    ['  src/agents/', '5개 Agent 파일 (각 역할 분리)'],
    ['  src/rag/', 'RAG 벡터스토어 구축 + 검색'],
    ['  src/tools/postfab_tools.py', 'Function Calling 도구 정의 + SQLite 조회'],
    ['  src/metrics.py', 'KPI 수집/저장'],
    ['  data/docs/postfab_terms.md', 'RAG 지식 소스 — 용어 추가 시 이 파일만 수정'],
    ['  data/mock/postfab.db', 'SQLite Mock DB (MES/FDC/YMS 데이터)'],
    ['  data/finetune/postfab_qa.jsonl', 'Fine-tuning Q&A 데이터셋'],
    ['  scripts/create_mock_db.py', 'DB 초기화 스크립트'],
    ['  Dockerfile / docker-compose.yml', '컨테이너 배포 설정'],
  ], ['경로', '역할/설명'], [3800, CONTENT_W - 3800]),
  sp(2),

  h(2, '2-2. 요청이 처리되는 순서 (코드 읽기 순서)'),
  p('사용자가 "LOT002 수율 저하 원인 분석해줘"를 입력했을 때 코드가 실행되는 순서입니다.'),
  sp(),
  numbered('app/api/main.py → POST /api/chat 엔드포인트가 요청을 받음', '1단계: 진입 '),
  numbered('src/workflow.py → run() 함수 호출, LangGraph 그래프 invoke', '2단계: 그래프 시작 '),
  numbered('router_node → router_agent.route() 호출, intent="root_cause" 판별', '3단계: Router '),
  numbered('planner_node → planner_agent.plan() 호출, 실행 단계 목록 계획', '4단계: Planner '),
  numbered('data_node → data_agent.query() 호출, Claude tool_use로 5개 함수 자동 호출', '5단계: Data Agent '),
  numbered('knowledge_search_node → retriever.retrieve_as_context() 로 RAG 검색', '6단계: Knowledge '),
  numbered('report_node → report_agent.generate() 호출, Sonnet으로 리포트 작성', '7단계: Report '),
  numbered('workflow.run()이 결과 dict 반환 → API 응답으로 전달', '8단계: 반환 '),
  sp(2),

  h(2, '2-3. 각 Agent 파일 설명'),
  twoColTable([
    ['router_agent.py', 'Claude Haiku에게 intent 분류 요청. ICL(few-shot 예시 12개) 포함. 반환값: {"intent": ..., "lot_id": ...}'],
    ['planner_agent.py', 'root_cause일 때만 호출. 실행 단계 목록을 JSON 배열로 반환. 예: ["get_lot_info", "get_test_result", ...]'],
    ['knowledge_agent.py', 'RAG 검색 결과를 컨텍스트로 받아 Claude Haiku가 답변 생성. 시나리오 1 전용.'],
    ['data_agent.py', 'Claude tool_use agentic loop 구현. Claude가 스스로 함수 선택 → 실행 → 결과 해석을 반복.'],
    ['report_agent.py', 'Data + Knowledge 결과를 종합해 구조화된 리포트 생성. 품질을 위해 Claude Sonnet 사용.'],
  ], ['파일', '설명'], [2800, CONTENT_W - 2800]),
  sp(2),

  h(2, '2-4. 데이터 흐름 (State)'),
  p('LangGraph의 AgentState가 노드 간 데이터를 공유합니다. 각 노드는 State의 일부만 업데이트하고 나머지는 유지됩니다.'),
  sp(),
  twoColTable([
    ['user_query', '사용자 원본 질문 (변경 없음)'],
    ['history', '이전 대화 목록 (변경 없음)'],
    ['intent / lot_id', 'router_node가 채움'],
    ['planner_steps', 'planner_node가 채움'],
    ['collected_data', 'data_node가 채움 (DB 조회 결과 원본)'],
    ['knowledge_context', 'knowledge_search_node가 채움 (RAG 검색 결과)'],
    ['answer', '최종 답변 (knowledge_node / data_node / report_node 중 하나가 채움)'],
    ['log', '각 노드가 실행 기록을 append (operator.add로 자동 합산)'],
  ], ['State 키', '설명'], [2400, CONTENT_W - 2400]),
  sp(2),

  h(2, '2-5. DB 구조 (Mock)'),
  p('data/mock/postfab.db — SQLite. scripts/create_mock_db.py로 초기화.'),
  sp(),
  twoColTable([
    ['lot_info', 'LOT 기본 정보. lot_id, product, process, equipment, status 컬럼.'],
    ['test_result', 'FT 수율 데이터. yield_pct, fail_reason 포함. LOT002가 78%로 이상 LOT.'],
    ['fdc_alarm', 'FDC 알람 이력. TEST02에 TEMP_HIGH, CONTACT_FAIL, POWER_NOISE 3건.'],
    ['yield_trend', '일별 수율 추이. TEST02가 96.5%에서 78%로 하락하는 데이터.'],
    ['recipe_history', 'Recipe 변경 이력. VCCQ 범위 조정, tRCD 마진 축소 2건.'],
  ], ['테이블', '내용'], [2400, CONTENT_W - 2400]),
  sp(2),

  h(2, '2-6. 자주 수정하는 파일'),
  tip('📝', '용어 추가', 'data/docs/postfab_terms.md에 ## 헤더로 새 용어 추가 후 python src/rag/build_vectorstore.py 재실행'),
  sp(),
  tip('🗄️', 'DB 데이터 변경', 'scripts/create_mock_db.py에서 executemany 데이터 수정 후 python scripts/create_mock_db.py 재실행'),
  sp(),
  tip('🔧', '새 함수 추가', 'src/tools/postfab_tools.py에 함수 추가 + TOOL_SPECS에 JSON 스펙 추가 + TOOL_FUNCTIONS에 매핑'),
  sp(2),

  pageBreak(),

  // ══════════════════════════════════════════════════════════
  //  PART 3: 실행 방법 & 남은 작업
  // ══════════════════════════════════════════════════════════
  new Paragraph({ spacing: { before: 0, after: 100 }, children: [
    new TextRun({ text: 'PART 3', size: 18, color: C.muted, font: 'Arial', bold: true }),
  ]}),
  h(1, '실행 방법 & 남은 작업'),
  divider(),
  sp(),

  h(2, '3-1. 최초 환경 설정 (1회만)'),
  h(3, 'Step 1 — 패키지 설치'),
  code('cd postfab-multi-agent'),
  code('pip install -r requirements.txt'),
  sp(),
  h(3, 'Step 2 — API Key 설정'),
  code('copy .env.example .env'),
  p('→ .env 파일을 열어 ANTHROPIC_API_KEY=sk-ant-XXX 입력'),
  sp(),
  h(3, 'Step 3 — Mock DB 생성'),
  code('python scripts/create_mock_db.py'),
  sp(),
  h(3, 'Step 4 — RAG 벡터스토어 구축 (약 1~3분, 모델 다운로드 포함)'),
  code('python src/rag/build_vectorstore.py'),
  sp(2),

  h(2, '3-2. 실행 방법 3가지'),
  h(3, '방법 A — FastAPI 서버 (운영/데모 권장)'),
  code('uvicorn app.api.main:app --reload --port 8000'),
  p('→ 브라우저에서 http://localhost:8000 접속'),
  p('→ API 문서: http://localhost:8000/docs'),
  sp(),
  h(3, '방법 B — Streamlit (개발/빠른 테스트)'),
  code('streamlit run app/streamlit_app.py'),
  p('→ 브라우저에서 http://localhost:8501 접속'),
  sp(),
  h(3, '방법 C — Docker (배포/이식성)'),
  code('docker compose up --build'),
  p('→ http://localhost:8000 접속 (.env 파일 필요)'),
  p('→ 첫 빌드 시 RAG 구축까지 약 5분 소요'),
  sp(2),

  h(2, '3-3. 테스트 시나리오 (이 순서로 해야 함)'),
  twoColTable([
    ['1번', '"FDC가 뭐야?"', 'RAG 검색만 동작. 가장 빠름. 기본 작동 확인용.'],
    ['2번', '"LOT002 수율 알려줘"', 'Function Calling 1회 동작. DB 연결 확인용.'],
    ['3번', '"LOT002 수율 저하 원인 분석해줘"', '전체 파이프라인. Function Call 5개 + RAG + 리포트.'],
    ['맥락 테스트', '"아니 mes."', '직전 대화 이후 입력. 맥락 이어받기 확인용.'],
  ], ['순서', '질문', '확인 목적'], [800, 3200, CONTENT_W - 4000]),
  sp(),
  tip('✅', '성공 기준', '3번 시나리오에서 Function Call 로그 5개가 표시되고 구조화된 리포트(요약/원인/조치)가 출력되면 전체 정상 작동.'),
  sp(2),

  h(2, '3-4. 내가 아직 해야 할 것들'),
  sp(),

  h(3, '① Fine-tuning 실행 (가장 중요)'),
  twoColTable([
    ['현재 상태', '코드와 데이터셋은 완성. 실제 학습만 안 돌림.'],
    ['필요한 것', 'Google Colab (무료 T4 GPU) 또는 로컬 GPU'],
    ['파일 위치', 'notebooks/finetune_qlora.ipynb'],
    ['데이터셋', 'data/finetune/postfab_qa.jsonl (12개 Q&A)'],
    ['실행 방법', 'Colab에서 notebooks/finetune_qlora.ipynb 열고 셀 순서대로 실행'],
    ['소요 시간', 'Colab T4 기준 약 20~40분'],
    ['결과물', 'LoRA 어댑터 파일 → Knowledge Agent에 연결 가능'],
  ], ['항목', '내용'], [2400, CONTENT_W - 2400]),
  sp(),

  h(3, '② 실제 데이터로 DB 교체 (실무 연결 시)'),
  bullet('현재: Mock 데이터 (LOT001~003, 가상 알람)'),
  bullet('목표: 실제 MES/FDC 시스템 API 연결'),
  bullet('방법: src/tools/postfab_tools.py의 함수를 실제 DB 쿼리로 교체'),
  sp(),

  h(3, '③ 용어집 확장 (지식 품질 향상)'),
  bullet('현재: postfab_terms.md에 12개 용어'),
  bullet('추가 권장: Wire Bonding, Solder Ball, EMI, 공정 불량 유형 등'),
  bullet('방법: postfab_terms.md에 ## 헤더로 추가 → build_vectorstore.py 재실행'),
  sp(),

  h(3, '④ MCP (Model Context Protocol) 연동 — 선택'),
  bullet('현재: Function Calling으로 직접 함수 호출'),
  bullet('MCP 적용 시: 도구를 별도 MCP 서버로 분리 → 다른 클라이언트에서도 공유 가능'),
  bullet('참고: Anthropic 공식 MCP 문서 (https://modelcontextprotocol.io)'),
  sp(),

  h(3, '⑤ README 스크린샷 추가 (포트폴리오 완성도)'),
  bullet('실행 후 3번 시나리오 결과 화면 스크린샷 캡처'),
  bullet('특히: Function Call 로그 5개 + 리포트 출력 화면이 핵심'),
  bullet('README.md의 "Streamlit UI 주요 화면" 섹션에 이미지 삽입'),
  sp(2),

  h(2, '3-5. 트러블슈팅 빠른 참조'),
  twoColTable([
    ['AuthenticationError', 'ANTHROPIC_API_KEY 없거나 잘못됨', '.env 파일 확인'],
    ['no such table', 'DB 미생성', 'python scripts/create_mock_db.py 실행'],
    ['Collection not found', 'ChromaDB 미구축', 'python src/rag/build_vectorstore.py 실행'],
    ['ModuleNotFoundError', '패키지 미설치', 'pip install -r requirements.txt 실행'],
    ['lot_id: null', 'Router가 LOT ID 인식 못함', '질문에 "LOT002" 명시'],
    ['응답 없음 (5초 이상)', 'API 호출 중 타임아웃', 'ANTHROPIC_API_KEY 유효성 확인'],
  ], ['오류', '원인', '해결'], [2400, 3200, CONTENT_W - 5600]),
  sp(2),

  divider(),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 200, after: 0 },
    children: [new TextRun({ text: 'PostFab Multi-Agent — 포트폴리오 기술 문서', size: 18, color: C.muted, font: 'Arial' })] }),
];

// ── 문서 생성 ─────────────────────────────────────────────────
const doc = new Document({
  numbering: {
    config: [
      { reference: 'bullets', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 640, hanging: 320 } } } }] },
      { reference: 'numbers', levels: [{ level: 0, format: LevelFormat.DECIMAL, text: '%1.', alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 640, hanging: 320 } } } }] },
    ],
  },
  styles: {
    default: { document: { run: { font: 'Arial', size: 22, color: C.text } } },
    paragraphStyles: [
      { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 36, bold: true, color: C.primary, font: 'Arial' },
        paragraph: { spacing: { before: 400, after: 200 }, outlineLevel: 0 } },
      { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 28, bold: true, color: C.primary, font: 'Arial' },
        paragraph: { spacing: { before: 300, after: 150 }, outlineLevel: 1 } },
      { id: 'Heading3', name: 'Heading 3', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 24, bold: true, color: C.secondary, font: 'Arial' },
        paragraph: { spacing: { before: 240, after: 100 }, outlineLevel: 2 } },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: PAGE_W, height: PAGE_H },
        margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN },
      },
    },
    headers: {
      default: new Header({ children: [new Paragraph({
        border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.border } },
        children: [new TextRun({ text: 'PostFab Multi-Agent  |  포트폴리오 기술 문서', size: 18, color: C.muted, font: 'Arial' })],
      })]}),
    },
    footers: {
      default: new Footer({ children: [new Paragraph({
        alignment: AlignmentType.RIGHT,
        children: [
          new TextRun({ text: 'Page ', size: 18, color: C.muted, font: 'Arial' }),
          new TextRun({ children: [PageNumber.CURRENT], size: 18, color: C.muted, font: 'Arial' }),
          new TextRun({ text: ' / ', size: 18, color: C.muted, font: 'Arial' }),
          new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 18, color: C.muted, font: 'Arial' }),
        ],
      })]}),
    },
    children,
  }],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync('PostFab_Portfolio_Document.docx', buf);
  console.log('OK: PostFab_Portfolio_Document.docx');
});
