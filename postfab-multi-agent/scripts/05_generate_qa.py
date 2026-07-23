"""
05_generate_qa.py — 코퍼스에서 평가/파인튜닝용 QA 쌍 합성 생성 (formal/casual 페어).

GPU 불필요: 이 스크립트는 Claude API 호출만 수행하는 CPU/네트워크 작업입니다.
GPU가 필요한 단계는 07(임베딩 파인튜닝 학습)부터이며, 그건 Colab에서 실행 예정.

입력: data/corpus/glossary_cards.jsonl (378장), data/corpus/article_chunks.jsonl (308개)
출력: data/finetune/qa_pairs.jsonl
  {"question","answer","question_type","source_type","source_id","concept","phrasing"}

말투 실험 설계:
  - 소스(카드/청크) 하나당 질문을 formal/casual 두 벌 생성해 같은 source_id로 페어링.
  - formal: 말투 지시 없는 기본 프롬프트 (베이스라인 — 흔한 AI 보고서체 "~입니까?/~십시오").
  - casual: REGISTERS 팔레트 중 하나를 라운드로빈으로 강제 배정해 현장 구어체로 작성.
  - 같은 개념(concept)에 대해 formal/casual을 짝지어야 "말투 때문에" 검색 정확도가
    달라지는지를 개념 난이도와 분리해서 볼 수 있음 (무작위로 따로 뽑으면 비교가 오염됨).
  → 06에서 같은 source_id의 formal/casual 페어를 같은 split(train/valid/test)에 유지
  → 08에서 phrasing별 Accuracy@k를 나눠 측정 (파인튜닝 전/후 비교의 핵심 축)

question_type: 용어 카드는 항상 "term". 아티클 청크는 모델이 concept(개념 설명형)/
  flow(공정 순서형)/judgment(현장 판단·트러블슈팅형) 중 하나를 골라 formal 호출에서
  결정하고, 같은 페어의 casual 호출에는 그 유형을 그대로 강제해 일관성을 유지.

재실행 안전: 이미 생성된 (source_id, phrasing)은 건너뜀. 중단돼도 이어서 생성 가능.

사용법:
  python scripts/05_generate_qa.py --limit 10          # 스모크 테스트 (신규 10개 소스)
  python scripts/05_generate_qa.py                     # 전체 (~1,372 QA)
  python scripts/05_generate_qa.py --source-type card   # 용어 카드만
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import anthropic

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
CORPUS_DIR = BASE / "data" / "corpus"
OUT_PATH = BASE / "data" / "finetune" / "qa_pairs.jsonl"

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 500
REQUEST_DELAY = 0.3  # API 호출 간 지연(초)

REGISTERS = [
    dict(name="반말_의문형", desc="반말로 짧게 묻는 말투", example="트래킹이 뭐야?"),
    dict(name="축약_명령형", desc="용건만 축약해서 명령하듯 말하는 말투", example="OOO 설명좀. OOO 정리해줘"),
    dict(name="구어체_존댓말", desc="존댓말이지만 딱딱하지 않은 구어체", example="OOO가 뭔가요? OOO 좀 알려주세요"),
    dict(name="현장_은어약어", desc="정식 용어 대신 줄임말·영문 약어·구어 표현을 섞어 쓰는 말투",
         example="trackin이 뭐야?, MSL 몇이야?"),
    dict(name="트러블슈팅_다급체", desc="문제 상황에 부딪힌 다급한 현장 말투", example="OOO 터졌는데 뭐부터 봐야돼?, OOO 왜 이럼?"),
    dict(name="키워드형_단문", desc="단어 위주로 짧게 묻는 말투", example="OOO 원인?, OOO 기준?"),
]

BANNED_NOTE = (
    "주의: '~입니까?', '~하십시오', '~바랍니다' 같은 격식 보고서체는 쓰지 마세요. "
    "실제 반도체 후공정 현장 엔지니어가 사내 챗봇에 빠르게 타이핑하듯 자연스럽게 써주세요. "
    "필요하면 오탈자성 축약(정식 용어를 짧게 줄여쓰기 등)도 괜찮습니다."
)

VALID_QUESTION_TYPES = {"concept", "flow", "judgment"}


def load_env_key() -> str | None:
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    env_path = BASE / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*ANTHROPIC_API_KEY\s*=\s*(.+)\s*$", line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    return None


def call_json(client: anthropic.Anthropic, model: str, prompt: str, retries: int = 1) -> dict:
    last_err: Exception | None = None
    for _ in range(retries + 1):
        resp = client.messages.create(
            model=model, max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("모델이 응답을 거부함")
        text = next(b.text for b in resp.content if b.type == "text").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            last_err = e
            prompt = prompt + "\n\n(이전 응답이 JSON으로 파싱되지 않았습니다. 순수 JSON 객체 하나만 출력하세요.)"
    raise last_err


def build_formal_prompt(kind: str, item: dict) -> str:
    if kind == "card":
        return (
            f"다음은 반도체 후공정 분야의 용어 카드입니다.\n\n"
            f"용어: {item['term']}\n정의: {item['definition']}\n\n"
            f"이 용어의 정의를 묻는 질문 1개와 답을 한국어로 작성하세요. "
            f"답변은 정의를 바탕으로 2~3문장.\n\n"
            f'JSON 형식으로만 답하세요: {{"question": "...", "answer": "..."}}'
        )
    return (
        f"다음은 반도체 후공정 기술 문서의 일부입니다.\n\n"
        f"위치: {item['context_header']}\n내용:\n{item['text']}\n\n"
        f"이 내용을 바탕으로 질문 1개와 답을 한국어로 작성하세요 (원문이 영어여도 질문/답은 한국어로). "
        f"답변은 이 내용에 실제로 있는 근거만 바탕으로 2~4문장.\n"
        f'"question_type"은 concept(개념 설명형)/flow(공정 순서형)/judgment(현장 판단·트러블슈팅형) 중 '
        f"이 내용에 가장 자연스러운 것 하나를 고르세요.\n\n"
        f'JSON 형식으로만 답하세요: {{"question_type": "...", "question": "...", "answer": "..."}}'
    )


def build_casual_prompt(kind: str, item: dict, register: dict, question_type: str) -> str:
    if kind == "card":
        content = f"용어: {item['term']}\n정의: {item['definition']}"
    else:
        content = f"위치: {item['context_header']}\n내용:\n{item['text']}"
    return (
        f"다음은 반도체 후공정 분야 자료입니다.\n\n{content}\n\n"
        f"이 내용에 대한 질문 1개와 답을 아래 말투로 작성하세요.\n"
        f"말투: {register['desc']} (예: \"{register['example']}\")\n{BANNED_NOTE}\n"
        f'질문 성격은 "{question_type}" 유형에 맞게 작성하고, 답변 내용은 원문 근거 기준 2~4문장.\n\n'
        f'JSON 형식으로만 답하세요: {{"question": "...", "answer": "..."}}'
    )


def load_existing(path: Path) -> dict:
    """source_id -> {"question_type": str, "phrasings": set[str]}"""
    state: dict = {}
    if not path.exists():
        return state
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            st = state.setdefault(row["source_id"], {"question_type": row["question_type"], "phrasings": set()})
            st["phrasings"].add(row["phrasing"])
    return state


def load_sources(source_type: str) -> list[tuple[str, dict]]:
    sources = []
    if source_type in ("card", "both"):
        with open(CORPUS_DIR / "glossary_cards.jsonl", encoding="utf-8") as f:
            sources += [("card", json.loads(line)) for line in f if line.strip()]
    if source_type in ("chunk", "both"):
        with open(CORPUS_DIR / "article_chunks.jsonl", encoding="utf-8") as f:
            sources += [("chunk", json.loads(line)) for line in f if line.strip()]
    return sources


def main():
    ap = argparse.ArgumentParser(description="코퍼스 → 평가/파인튜닝용 QA 쌍 생성 (formal/casual 페어)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=0, help="신규로 처리할 최대 소스 개수 (0=전체, 스모크 테스트용)")
    ap.add_argument("--source-type", choices=["card", "chunk", "both"], default="both")
    args = ap.parse_args()

    print("[알림] 이 스크립트는 GPU를 쓰지 않습니다 (Claude API 호출만 수행). "
          "GPU는 07 임베딩 파인튜닝 단계부터 필요하며 Colab에서 실행할 예정입니다.")

    api_key = load_env_key()
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY가 환경변수/.env에 없습니다")
    client = anthropic.Anthropic(api_key=api_key)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = load_existing(OUT_PATH)
    sources = load_sources(args.source_type)

    done = failed = skipped = 0
    out_f = open(OUT_PATH, "a", encoding="utf-8")
    try:
        for i, (kind, item) in enumerate(sources):
            if args.limit and done >= args.limit:
                break
            source_id = item["id"]
            st = state.get(source_id)
            if st and {"formal", "casual"} <= st["phrasings"]:
                skipped += 1
                continue

            source_type = "glossary_card" if kind == "card" else "article_chunk"
            concept = item["canonical_term"] if kind == "card" else item["context_header"]
            register = REGISTERS[i % len(REGISTERS)]

            try:
                # formal 먼저 생성 (chunk의 question_type을 여기서 확정)
                if not st or "formal" not in st["phrasings"]:
                    result = call_json(client, args.model, build_formal_prompt(kind, item))
                    if kind == "card":
                        question_type = "term"
                    else:
                        question_type = result.get("question_type", "concept")
                        if question_type not in VALID_QUESTION_TYPES:
                            question_type = "concept"
                    row = dict(question=result["question"], answer=result["answer"],
                               question_type=question_type, source_type=source_type,
                               source_id=source_id, concept=concept, phrasing="formal")
                    out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    out_f.flush()
                    state.setdefault(source_id, {"question_type": question_type, "phrasings": set()})
                    state[source_id]["question_type"] = question_type
                    state[source_id]["phrasings"].add("formal")
                    time.sleep(REQUEST_DELAY)

                # casual: formal에서 정해진 question_type을 그대로 강제
                if "casual" not in state.get(source_id, {}).get("phrasings", set()):
                    question_type = state[source_id]["question_type"]
                    result = call_json(client, args.model,
                                        build_casual_prompt(kind, item, register, question_type))
                    row = dict(question=result["question"], answer=result["answer"],
                               question_type=question_type, source_type=source_type,
                               source_id=source_id, concept=concept, phrasing="casual")
                    out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    out_f.flush()
                    state[source_id]["phrasings"].add("casual")
                    time.sleep(REQUEST_DELAY)

                done += 1
                print(f"[ok {done}] {kind} {source_id} ({register['name']})")
            except Exception as e:
                failed += 1
                print(f"[FAIL] {kind} {source_id} - {e}")
    finally:
        out_f.close()

    print(f"\n완료 {done} / 실패 {failed} / 캐시 스킵 {skipped}")


if __name__ == "__main__":
    main()
