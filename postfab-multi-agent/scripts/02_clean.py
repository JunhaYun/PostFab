"""
02_clean.py — raw 원본에서 본문만 추출해 markdown으로 정제.

입력:  data/raw/  (01_crawl.py 산출물 + manifest.json)
출력:  data/clean/<source>/<slug>.md          — 본문 markdown (사람 검수용)
       data/clean/<source>/<slug>.images.json — 본문 내 이미지 목록 (02b VLM 캡셔닝 입력)
       data/clean/clean_report.json           — 품질 검사 결과 (편입/제외 판정 근거)

소스별 파서 (보고서 3.2절의 결정론적 규칙):
  - skhynix   : div.post-contents 안만 사용. h3.sub-title→'##', p.footnote→'※'(용어 정의 보존),
                p.caption→ⓒ/출처 제거 후 유지, img 제거(목록만 별도 저장), div.post-intro 제거
  - ase       : <main> 안의 h1~h4/p/li만 사용 (버튼·폼·네비 제외)
  - advantest : h3.m-heading-type3(용어) + div.m-text(정의) 쌍 → '## 용어' 형식
  - jedec     : PDF에서 볼드+콜론 스팬을 용어 경계로 파싱, 후공정 키워드 필터 적용

품질 검사:
  ① 분량 — 아티클 본문이 MIN_ARTICLE_CHARS 미만이면 코퍼스 제외 (include=false)
  ② 구조 — 뉴스룸 아티클에서 섹션(##)이 0개면 파서 오류 의심 경고
  판정과 사유는 clean_report.json에 기록되어 03단계가 이를 기준으로 문서를 선별한다.

사용법:
  python scripts/02_clean.py            # 전체 정제
  python scripts/02_clean.py --source skhynix
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import fitz  # pymupdf
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
RAW_DIR = BASE / "data" / "raw"
CLEAN_DIR = BASE / "data" / "clean"
MANIFEST_PATH = RAW_DIR / "manifest.json"
REPORT_PATH = CLEAN_DIR / "clean_report.json"

MIN_ARTICLE_CHARS = 500   # 아티클 본문 최소 분량 — 미만이면 코퍼스 제외

# JEDEC 전체 용어 중 후공정(패키지·조립·테스트) 관련 용어만 남기는 필터.
# 용어명에 아래 단어가 (단어 경계 기준) 포함되면 편입. 조정 시 결과는 report에 집계됨.
JEDEC_TERM_KEYWORDS = [
    "package", "packaging", "assembly", "bond", "bonding", "bump", "solder",
    "mold", "molding", "encapsulant", "encapsulation", "underfill", "die",
    "wafer", "substrate", "leadframe", "lead", "flip chip", "ball grid",
    "chip scale", "interposer", "singulation", "dicing", "grinding",
    "burn-in", "test", "probe", "socket", "handler", "delamination",
    "warpage", "coplanarity", "moisture", "reliability", "thermal",
    "BGA", "CSP", "WLP", "TSV", "PoP", "SiP", "stack", "via",
]


# ─────────────────────────────── 공통 유틸 ───────────────────────────────

def normalize(text: str) -> str:
    """유니코드 NFC + 공백 정리 + 깨진 마크업 잔해 제거."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"/p>", "", text)          # 원문에 섞인 깨진 태그 잔해
    text = re.sub(r"\s*\n\s*", " ", text)    # 문단 내부 줄바꿈 → 공백 (원본 HTML 개행 잔재)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def load_manifest_index() -> dict:
    """raw 파일 경로 → manifest 레코드 매핑."""
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return {r["file"]: r for r in json.load(f) if r.get("file")}


def frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v if v is not None else ''}")
    lines.append("---\n")
    return "\n".join(lines)


def write_output(source: str, slug: str, meta: dict, body: str, images: list) -> Path:
    out_dir = CLEAN_DIR / source
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{slug}.md"
    md_path.write_text(frontmatter(meta) + body + "\n", encoding="utf-8")
    if images:
        (out_dir / f"{slug}.images.json").write_text(
            json.dumps(images, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path


# ─────────────────────────────── 뉴스룸 파서 ───────────────────────────────

def parse_skhynix(html: str) -> tuple[str, str, list]:
    """뉴스룸 아티클 → (제목, 본문 markdown, 이미지 목록)."""
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h2.post-title")
    title = normalize(title_el.get_text()) if title_el else ""

    body_el = soup.select_one("div.post-contents")
    if body_el is None:
        return title, "", []

    # 시리즈 인트로(반복 보일러플레이트)와 각주 마커(빨간 별표) 제거
    for el in body_el.select("div.post-intro"):
        el.decompose()
    for el in body_el.select('span[style*="color: red"]'):
        el.decompose()

    lines: list[str] = []
    images: list[dict] = []
    section_idx = 0
    for el in body_el.find_all(["h3", "p", "table"], recursive=True):
        classes = el.get("class") or []
        if el.name == "h3" and "sub-title" in classes:
            section_idx += 1
            lines.append(f"\n## {normalize(el.get_text())}\n")
        elif el.name == "p" and "footnote" in classes:
            # 저자가 단 용어 정의 — <br> 단위로 나눠 '※'로 보존 (04에서 용어 카드가 됨)
            for part in el.get_text("\n").split("\n"):
                part = normalize(part)
                if part:
                    lines.append(f"※ {part}")
            lines.append("")
        elif el.name == "p" and "caption" in classes:
            cap = el.get_text("\n")
            cap = re.sub(r"\(ⓒ[^)]*\)", "", cap)                      # 저작권 표기 제거
            cap_lines = [normalize(x) for x in cap.split("\n")]
            cap_lines = [x for x in cap_lines if x and not x.startswith("출처")]  # 참고문헌 줄 제거
            if cap_lines:
                lines.append(" ".join(cap_lines))
                lines.append("")
        elif el.name == "p":
            if el.find("img"):
                for img in el.find_all("img"):
                    images.append({"section": section_idx,
                                   "src": img.get("src", ""),
                                   "alt": img.get("alt", "")})
                continue
            text = normalize(el.get_text())
            if text:
                lines.append(text)
                lines.append("")
        elif el.name == "table":
            for tr in el.find_all("tr"):
                cells = [normalize(td.get_text()) for td in tr.find_all(["td", "th"])]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return title, body, images


# ─────────────────────────────── ASE 파서 ───────────────────────────────

def parse_ase(html: str) -> tuple[str, str, list]:
    """ASE 기술 페이지 → (제목, 본문 markdown, 이미지 목록)."""
    soup = BeautifulSoup(html, "html.parser")
    title = normalize(soup.title.get_text()) if soup.title else ""
    main = soup.find("main")
    if main is None:
        return title, "", []

    # 본문이 아닌 인터랙션 요소 제거
    for el in main.find_all(["nav", "form", "button", "script", "style", "noscript"]):
        el.decompose()

    lines: list[str] = []
    images: list[dict] = []
    section_idx = 0
    seen = set()  # 반응형 레이아웃의 중복 블록 제거용
    for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "img"], recursive=True):
        if el.name == "img":
            src = el.get("src", "")
            if src and not src.startswith("data:"):
                images.append({"section": section_idx, "src": src,
                               "alt": el.get("alt", "")})
            continue
        if el.find(["p", "li"]):   # 자식에 같은 대상이 있으면 중복 방지를 위해 건너뜀
            continue
        text = normalize(el.get_text())
        if not text or text in seen:
            continue
        seen.add(text)
        if el.name in ("h1", "h2"):
            section_idx += 1
            lines.append(f"\n## {text}\n")
        elif el.name in ("h3", "h4"):
            lines.append(f"\n### {text}\n")
        elif el.name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)
            lines.append("")
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return title, body, images


# ─────────────────────────────── Advantest 파서 ───────────────────────────────

def parse_advantest(html: str) -> tuple[str, str, int]:
    """용어집 → ('Advantest Glossary', '## 용어\\n정의' markdown, 용어 수)."""
    soup = BeautifulSoup(html, "html.parser")
    lines: list[str] = []
    n_terms = 0
    for h in soup.select("h3.m-heading-type3"):
        term = normalize(h.get_text())
        def_el = h.find_next_sibling("div", class_="m-text")
        if not term or def_el is None:
            continue
        definition = normalize(def_el.get_text(" "))
        if not definition:
            continue
        lines.append(f"## {term}\n")
        lines.append(definition)
        lines.append("")
        n_terms += 1
    return "Advantest IR Glossary", "\n".join(lines).strip(), n_terms


# ─────────────────────────────── JEDEC PDF 파서 ───────────────────────────────

def matches_jedec_filter(term: str) -> bool:
    lowered = term.lower()
    for kw in JEDEC_TERM_KEYWORDS:
        if re.search(rf"(?<![a-z]){re.escape(kw.lower())}(?![a-z])", lowered):
            return True
    return False


def parse_jedec(pdf_path: Path) -> tuple[str, str, dict]:
    """JESD88E PDF → 볼드+콜론 스팬을 용어 경계로 (용어, 정의) 추출 후 키워드 필터.

    조판 특성(사전 검증됨): 단단 조판, 용어는 10pt 볼드로 시작해 ':'로 끝남,
    페이지 헤더는 11pt, 출처 표기는 8pt.
    """
    doc = fitz.open(pdf_path)
    entries: list[tuple[str, list[str]]] = []
    cur_term, cur_def = None, []
    bold_buf: list[str] = []   # 용어가 여러 볼드 스팬으로 쪼개지는 경우 누적
                               # 예: "chip-out (1)" + "(in a package):"

    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    text = span["text"]
                    size, bold = span["size"], bool(span["flags"] & 16)
                    if size < 9 or size > 10.5:   # 출처(8pt)·페이지 헤더(11pt) 제외
                        continue
                    stripped = text.strip()
                    if not stripped or stripped.startswith(("#", "___")):
                        continue
                    if bold and stripped.endswith(":"):
                        candidate = " ".join(bold_buf + [stripped.rstrip(":")]).strip()
                        if candidate.startswith("(") and cur_term is not None:
                            # 다의어 항목의 후속 뜻 (예: "(2) (in a package):") —
                            # 새 용어가 아니라 진행 중인 용어의 정의에 이어붙임
                            cur_def.append(stripped)
                            bold_buf = []
                            continue
                        # 새 용어 확정 (직전 볼드 조각들과 결합)
                        if cur_term and cur_def:
                            entries.append((cur_term, cur_def))
                        cur_term = candidate
                        cur_def, bold_buf = [], []
                    elif bold:
                        bold_buf.append(stripped)
                    elif cur_term is not None:
                        # 볼드였지만 용어가 아니었던 조각(예: "Example of ...")은 정의로 편입
                        if bold_buf:
                            cur_def.extend(bold_buf)
                            bold_buf = []
                        cur_def.append(text)
                    else:
                        bold_buf = []
    if cur_term and cur_def:
        entries.append((cur_term, cur_def))
    doc.close()

    lines: list[str] = []
    kept = 0
    for term, def_parts in entries:
        definition = normalize(" ".join(def_parts))
        if not definition or len(definition) < 10:
            continue
        if not matches_jedec_filter(term):
            continue
        lines.append(f"## {normalize(term)}\n")
        lines.append(definition)
        lines.append("")
        kept += 1
    stats = {"total_terms": len(entries), "kept_terms": kept}
    return "JEDEC Dictionary of Terms (JESD88E) — 후공정 필터", "\n".join(lines).strip(), stats


# ─────────────────────────────── 메인 ───────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="raw → clean markdown 정제기")
    ap.add_argument("--source", action="append",
                    choices=["skhynix", "ase", "advantest", "jedec"],
                    help="정제할 소스 (생략 시 전체)")
    args = ap.parse_args()
    sources = args.source or ["skhynix", "ase", "advantest", "jedec"]

    manifest_idx = load_manifest_index()
    report: list[dict] = []

    def record(source, slug, md_path, *, chars, sections, include, reason, extra=None):
        rec = dict(source=source, slug=slug,
                   file=md_path.relative_to(CLEAN_DIR).as_posix() if md_path else None,
                   chars=chars, sections=sections, include=include, reason=reason)
        if extra:
            rec.update(extra)
        report.append(rec)
        mark = "[ok]  " if include else "[제외]"
        warn = f" ⚠ {reason}" if (include and reason) else (f" — {reason}" if not include else "")
        print(f"  {mark} {source}/{slug}: {chars:,}자, 섹션 {sections}개{warn}")

    # ── 뉴스룸 (아티클) ──
    if "skhynix" in sources:
        print("\n=== skhynix: 뉴스룸 아티클 정제 ===")
        for raw_path in sorted((RAW_DIR / "skhynix").glob("*.html")):
            rel = f"skhynix/{raw_path.name}"
            meta_src = manifest_idx.get(rel, {})
            title, body, images = parse_skhynix(raw_path.read_text(encoding="utf-8"))
            slug = raw_path.stem
            sections = body.count("\n## ")
            meta = dict(title=title or meta_src.get("title"), source=meta_src.get("source", "skhynix"),
                        url=meta_src.get("url"), raw_file=rel, language="ko", doc_type="article")
            md_path = write_output("skhynix", slug, meta, body, images)
            if len(body) < MIN_ARTICLE_CHARS:
                record("skhynix", slug, md_path, chars=len(body), sections=sections,
                       include=False, reason=f"본문 {MIN_ARTICLE_CHARS}자 미만 (분량 미달)")
            elif sections == 0:
                record("skhynix", slug, md_path, chars=len(body), sections=0,
                       include=True, reason="섹션 0개 — 파서 확인 필요")
            else:
                record("skhynix", slug, md_path, chars=len(body), sections=sections,
                       include=True, reason=None)

    # ── ASE (아티클) ──
    if "ase" in sources:
        print("\n=== ase: 기술 페이지 정제 ===")
        for raw_path in sorted((RAW_DIR / "ase").glob("*.html")):
            rel = f"ase/{raw_path.name}"
            meta_src = manifest_idx.get(rel, {})
            title, body, images = parse_ase(raw_path.read_text(encoding="utf-8"))
            slug = raw_path.stem
            sections = body.count("\n## ")
            meta = dict(title=title, source="ase", url=meta_src.get("url"),
                        raw_file=rel, language="en", doc_type="article")
            md_path = write_output("ase", slug, meta, body, images)
            include = len(body) >= MIN_ARTICLE_CHARS
            record("ase", slug, md_path, chars=len(body), sections=sections,
                   include=include,
                   reason=None if include else f"본문 {MIN_ARTICLE_CHARS}자 미만 (분량 미달)")

    # ── Advantest (용어집) ──
    if "advantest" in sources:
        print("\n=== advantest: 용어집 정제 ===")
        raw_path = RAW_DIR / "advantest" / "glossary.html"
        title, body, n_terms = parse_advantest(raw_path.read_text(encoding="utf-8"))
        meta = dict(title=title, source="advantest",
                    url=manifest_idx.get("advantest/glossary.html", {}).get("url"),
                    raw_file="advantest/glossary.html", language="en", doc_type="glossary")
        md_path = write_output("advantest", "glossary", meta, body, [])
        record("advantest", "glossary", md_path, chars=len(body), sections=n_terms,
               include=n_terms > 0,
               reason=None if n_terms > 0 else "용어 0개 — 파서 확인 필요",
               extra={"n_terms": n_terms})

    # ── JEDEC (용어집 PDF) ──
    if "jedec" in sources:
        print("\n=== jedec: JESD88E PDF 정제 (후공정 필터) ===")
        pdf_path = RAW_DIR / "jedec" / "JESD88E.pdf"
        title, body, stats = parse_jedec(pdf_path)
        meta = dict(title=title, source="jedec",
                    url=manifest_idx.get("jedec/JESD88E.pdf", {}).get("url"),
                    raw_file="jedec/JESD88E.pdf", language="en", doc_type="glossary")
        md_path = write_output("jedec", "jesd88e-postfab", meta, body, [])
        record("jedec", "jesd88e-postfab", md_path, chars=len(body),
               sections=stats["kept_terms"], include=stats["kept_terms"] > 0,
               reason=f"전체 {stats['total_terms']}개 중 {stats['kept_terms']}개 편입",
               extra=stats)

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    n_ok = sum(1 for r in report if r["include"])
    print(f"\nclean_report 저장: {REPORT_PATH}")
    print(f"편입 {n_ok}건 / 제외 {len(report) - n_ok}건")


if __name__ == "__main__":
    main()
