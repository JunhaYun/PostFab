"""
04_build_corpus.py — 구조화 문서 → 최종 검색 코퍼스 (레이어 4).

입력:  data/structured/*.json (03_build_docs.py 산출물)
출력:  data/corpus/glossary_cards.jsonl — 용어 카드 (1줄 = 1용어)
       data/corpus/article_chunks.jsonl — 섹션 청크 (1줄 = 1청크)
       data/corpus/_stats.json          — 소스별 집계 (구축 근거 기록)

설계 (보고서 3.3절 — 검색 입도 이원화):
  용어 카드  : 짧은 용어 질문("track out이 뭐야?") 담당. 1용어 = 1레코드.
               출처: ① 용어집 문서(advantest/jedec/internal 사전류) 파싱
                     ② 아티클 각주("※ 용어: 정의" — 저자가 단 정의) 파싱
               동일 용어가 복수 소스에 있어도 병합하지 않고 canonical_term으로 그룹핑.
  섹션 청크  : 설명·비교·공정 흐름 질문 담당. 1소제목 섹션 = 1청크,
               MAX_CHUNK_TOKENS 초과 시 문단 경계에서 분할.
               임베딩 입력은 context_header + text (문서 맥락 부여).

토큰 계산: 로컬 bge-m3 토크나이저(실제 임베딩 모델과 동일 기준) 사용,
           없으면 문자수 기반 근사치로 폴백.

사용법:
  python scripts/04_build_corpus.py
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
STRUCTURED_DIR = BASE / "data" / "structured"
CORPUS_DIR = BASE / "data" / "corpus"
TOKENIZER_PATH = BASE.parent / "models" / "postfab-bge-m3-final"

MAX_CHUNK_TOKENS = 500   # 섹션이 이보다 크면 문단 경계 분할
TARGET_SUB_TOKENS = 450  # 분할 시 서브청크 목표 크기

# ── 토큰 카운터 (bge-m3 기준, 폴백: 근사치) ─────────────────────────────
try:
    from transformers import AutoTokenizer
    _tok = AutoTokenizer.from_pretrained(str(TOKENIZER_PATH))
    def n_tokens(text: str) -> int:
        return len(_tok.encode(text, add_special_tokens=False))
    TOKENIZER_NAME = "bge-m3 (local)"
except Exception:
    def n_tokens(text: str) -> int:
        return len(text) // 2   # 한국어 대략 2자/토큰 근사
    TOKENIZER_NAME = "char-approx (fallback)"


# ── 용어 카드 ───────────────────────────────────────────────────────────

def parse_term_aliases(raw_term: str) -> tuple[str, list[str]]:
    """'Yield (수율)' → ('Yield', ['수율']) / 'FDC (Fault Detection...)' → 분리."""
    m = re.match(r"^(.*?)\s*[（(]([^）)]+)[）)]\s*$", raw_term)
    if not m:
        return raw_term.strip(), []
    base = m.group(1).strip()
    inner = [a.strip() for a in re.split(r"[,/]", m.group(2)) if a.strip()]
    return (base, inner) if base else (raw_term.strip(), [])


def canonicalize(term: str) -> str:
    return re.sub(r"\s+", " ", term).strip().casefold()


SEE_PAT = re.compile(r'^See\s+[“"\'‘]?(.+?)[”"\'’]?\.?$', re.IGNORECASE)

def resolve_see_references(cards: list[dict]) -> tuple[list[dict], int]:
    """정의가 'See "X"'뿐인 참조 카드를 처리 — 대상 카드에 별칭으로 흡수하고 제거.

    예: 'BGA → See "ball grid array package"' → ball grid array package 카드의
    aliases에 BGA 추가 후 BGA 카드 삭제. 대상을 못 찾으면 카드를 그대로 둔다.
    """
    by_canon: dict[str, list[dict]] = {}
    for c in cards:
        by_canon.setdefault(c["canonical_term"], []).append(c)

    kept, absorbed, dropped = [], 0, 0
    for c in cards:
        m = SEE_PAT.match(c["definition"].strip())
        if m:
            target = canonicalize(re.sub(r'[”"\'’.]+$', "", m.group(1).strip()))
            targets = by_canon.get(target)
            if targets and target != c["canonical_term"]:
                for tc in targets:
                    if c["term"] != tc["term"] and c["term"] not in tc["aliases"]:
                        tc["aliases"].append(c["term"])
                absorbed += 1
                continue   # 대상 카드에 별칭으로 흡수 → 참조 카드 제외
            # 대상을 코퍼스에서 못 찾음 — 정의 없는 빈 참조 카드는 검색 노이즈라 제거
            dropped += 1
            continue
        kept.append(c)
    return kept, absorbed, dropped


# JEDEC PDF 파싱(02_clean.py parse_jedec)이 다의어 항목("(2) (in a package):" 류)을
# 부모 정의에 이어붙이는 과정에서, 서로 다른 뜻(같은 용어) 뿐 아니라 간혹 완전히 다른
# 용어("scratch", "foreign material" 등)까지 딸려 들어오는 경우가 있다. 아래 두 정규식으로
# ① 같은 용어의 추가 의미(2)(3)... 는 canonical_term을 공유하는 별도 카드로 분리하고
# ② 번호가 (1)로 재시작하며 새 용어로 보이는 지점부터는 다른 용어가 섞여든 것으로 보고 절단한다.
# (번호 없이 끼어드는 극소수 사례는 이 규칙으로 못 잡아 소량의 잔여 문장이 남을 수 있음 — 감수)
JEDEC_RESTART_PAT = re.compile(r"\.\s+([A-Za-z][A-Za-z\-\s,;/]{1,45}?)\s+\(1\)")
JEDEC_SENSE_PAT = re.compile(r"\.\s+\((\d+)\)\s*(\([^)]{0,60}\))?:?\s*")


def split_jedec_senses(term: str, definition: str) -> list[str]:
    """JEDEC 다의어 정의를 같은 용어의 의미별 정의 목록으로 분리 (부작용: 다른 용어 혼입 절단)."""
    canon = canonicalize(term)
    m = JEDEC_RESTART_PAT.search(definition)
    if m and not canonicalize(m.group(1)).startswith(canon):
        definition = definition[:m.start()].rstrip(" .") + "."   # 다른 용어 혼입 지점에서 절단

    parts = JEDEC_SENSE_PAT.split(definition)
    # re.split with capturing groups → [머리글, 번호1, 한정어1, 본문1, 번호2, 한정어2, 본문2, ...]
    if len(parts) == 1:
        return [definition.strip()]
    head = re.sub(r"^\(\d+\)\s*", "", parts[0].strip())   # 선두 '(1)' 잔여 표시 제거
    senses = [head] if head else []
    for i in range(1, len(parts), 3):
        qualifier = (parts[i + 1] or "").strip()
        body = parts[i + 2].strip() if i + 2 < len(parts) else ""
        if body:
            senses.append(f"{qualifier} {body}".strip() if qualifier else body)
    return senses or [definition.strip()]


def cards_from_glossary_doc(doc: dict) -> list[dict]:
    """용어집 문서(1섹션 = 1용어)를 카드로 변환. JEDEC은 다의어를 의미별 카드로 분리."""
    cards = []
    for sec in doc["sections"]:
        if not sec["heading"]:
            continue
        term, aliases = parse_term_aliases(sec["heading"])
        definitions = (split_jedec_senses(term, sec["text"].strip())
                      if doc["source"] == "jedec" else [sec["text"].strip()])
        for definition in definitions:
            cards.append(dict(
                canonical_term=canonicalize(term), term=term, aliases=list(aliases),
                definition=definition,
                source=doc["source"], source_doc=doc["doc_id"], source_url=doc["url"],
                language=doc["language"], extracted_by="parsed"))
    return cards


FOOTNOTE_PAT = re.compile(r"^※\s*(.+?)\s*[:：]\s*(.+)$", re.MULTILINE)

def cards_from_footnotes(doc: dict) -> list[dict]:
    """아티클 각주('※ 용어: 정의' — 저자가 직접 단 정의)를 카드로 추출."""
    cards = []
    for sec in doc["sections"]:
        for m in FOOTNOTE_PAT.finditer(sec["text"]):
            term, aliases = parse_term_aliases(m.group(1))
            cards.append(dict(
                canonical_term=canonicalize(term), term=term, aliases=aliases,
                definition=m.group(2).strip(),
                source=doc["source"], source_doc=doc["doc_id"], source_url=doc["url"],
                language=doc["language"], extracted_by="footnote"))
    return cards


# ── 섹션 청크 ───────────────────────────────────────────────────────────

def split_paragraphs(text: str, max_tokens: int) -> list[str]:
    """문단 경계(빈 줄)에서 목표 토큰 이하로 누적 분할.

    그림 설명([그림 설명: ...])은 독립 조각을 시작하지 않고 항상 직전 본문에
    붙는다 — 캡션만으로 이뤄진 청크(원문 없이 AI 설명뿐)와 과도 분할을 방지.
    """
    paras = [p for p in re.split(r"\n\n+", text) if p.strip()]
    subs, buf, buf_tokens = [], [], 0
    for p in paras:
        pt = n_tokens(p)
        is_caption = p.lstrip().startswith("[그림 설명:")
        # 본문 문단만 새 조각을 열 수 있다 (캡션은 항상 현재 조각에 이어붙임)
        if buf and not is_caption and buf_tokens + pt > max_tokens:
            subs.append("\n\n".join(buf))
            buf, buf_tokens = [], 0
        buf.append(p)
        buf_tokens += pt
    if buf:
        subs.append("\n\n".join(buf))
    return subs


def chunks_from_article(doc: dict) -> list[dict]:
    chunks = []
    for sec in doc["sections"]:
        heading = sec["heading"] or "(서두)"
        header = f"[{doc['title']}] > {heading}"
        parts = ([sec["text"]] if n_tokens(sec["text"]) <= MAX_CHUNK_TOKENS
                 else split_paragraphs(sec["text"], TARGET_SUB_TOKENS))
        for i, text in enumerate(parts):
            chunks.append(dict(
                doc_id=doc["doc_id"],
                context_header=header + (f" ({i + 1}/{len(parts)})" if len(parts) > 1 else ""),
                text=text.strip(),
                source=doc["source"], source_url=doc["url"],
                series=doc["series"], part=doc["part"], section=heading,
                language=doc["language"], n_tokens=n_tokens(text)))
    return chunks


# ── 메인 ────────────────────────────────────────────────────────────────

def main():
    print(f"토크나이저: {TOKENIZER_NAME}")
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    cards, chunks = [], []
    for path in sorted(STRUCTURED_DIR.glob("*.json")):
        if path.name == "_index.json":
            continue
        doc = json.load(open(path, encoding="utf-8"))
        if doc["doc_type"] == "glossary":
            cards.extend(cards_from_glossary_doc(doc))
        else:
            chunks.extend(chunks_from_article(doc))
            cards.extend(cards_from_footnotes(doc))   # 각주는 카드로 이중 활용

    cards, n_absorbed, n_dropped = resolve_see_references(cards)
    print(f"'See X' 참조 카드: {n_absorbed}건 별칭 흡수 / {n_dropped}건 제거(대상 없음)")

    for i, c in enumerate(cards, 1):
        c["id"] = f"card-{i:04d}"
    for i, c in enumerate(chunks, 1):
        c["id"] = f"chunk-{i:04d}"

    with open(CORPUS_DIR / "glossary_cards.jsonl", "w", encoding="utf-8") as f:
        for c in cards:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with open(CORPUS_DIR / "article_chunks.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # 집계 (구축 근거 기록)
    card_src = Counter(c["source"] for c in cards)
    card_by = Counter(c["extracted_by"] for c in cards)
    chunk_src = Counter(c["source"] for c in chunks)
    dup_terms = {t: n for t, n in Counter(c["canonical_term"] for c in cards).items() if n > 1}
    token_dist = [c["n_tokens"] for c in chunks]
    # 품질 지표: 원문 프로즈 없이 그림 설명만으로 이뤄진 청크 (0에 가까워야 함)
    def prose_len(t: str) -> int:
        t = re.sub(r"\[그림 설명:.*?\]", "", t, flags=re.DOTALL)
        t = re.sub(r"▲[^\n]*", "", t)
        return len(t.strip())
    caption_only = sum(1 for c in chunks if prose_len(c["text"]) < 15)
    stats = dict(
        tokenizer=TOKENIZER_NAME,
        n_cards=len(cards), cards_by_source=dict(card_src), cards_by_extractor=dict(card_by),
        n_chunks=len(chunks), chunks_by_source=dict(chunk_src),
        chunk_tokens=dict(min=min(token_dist), max=max(token_dist),
                          mean=round(sum(token_dist) / len(token_dist), 1)),
        n_duplicate_canonical_terms=len(dup_terms),
        n_caption_only_chunks=caption_only,
    )
    with open(CORPUS_DIR / "_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print(f"\n용어 카드 {len(cards)}장  (소스별: {dict(card_src)})")
    print(f"  추출 방식: {dict(card_by)}")
    print(f"  복수 소스 중복 용어(canonical 그룹): {len(dup_terms)}개")
    print(f"섹션 청크 {len(chunks)}개  (소스별: {dict(chunk_src)})")
    print(f"  청크 토큰: 최소 {stats['chunk_tokens']['min']} / 평균 {stats['chunk_tokens']['mean']} / 최대 {stats['chunk_tokens']['max']}")
    print(f"  캡션만 있는 청크(원문 없음): {caption_only}개")
    print(f"\n저장: {CORPUS_DIR}")


if __name__ == "__main__":
    main()
