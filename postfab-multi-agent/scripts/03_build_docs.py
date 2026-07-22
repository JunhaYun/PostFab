"""
03_build_docs.py — clean markdown → 문서 단위 구조화 JSON (레이어 3).

입력:
  data/clean/<source>/<slug>.md            — 02_clean.py 산출물
  data/clean/<source>/<slug>.captions.json — 02b VLM 캡션 (status=ok만 병합)
  data/clean/clean_report.json             — include=false 문서 제외
  data/docs/*.md                           — 기존 자체 작성 문서 (internal 소스로 흡수)

출력:
  data/structured/<doc_id>.json — 문서당 1파일:
    {doc_id, source, url, title, series, part, language, doc_type,
     sections: [{heading, text}]}
  data/structured/_index.json   — 전체 문서 목록 요약

처리 내용:
  ① 섹션 경계 확정 — '## ' 헤딩 기준으로 sections[] 분리 (헤딩 앞 본문은 서두 섹션)
  ② VLM 캡션 병합 — 각 섹션 끝에 "[그림 설명: ...]" 추가.
     status=ok만 병합하므로 검수에서 excluded_manual로 바꾼 그림은 자동 제외.
     캡션 수정/제외 후 이 스크립트만 재실행하면 반영된다 (API 비용 없음).
  ③ 메타데이터 부착 — 제목·URL·시리즈/편수·언어·문서타입.

사용법:
  python scripts/03_build_docs.py
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
CLEAN_DIR = BASE / "data" / "clean"
INTERNAL_DIR = BASE / "data" / "docs"
OUT_DIR = BASE / "data" / "structured"
REPORT_PATH = CLEAN_DIR / "clean_report.json"

# 기존 자체 작성 문서의 문서타입 매핑 (용어집 형식 vs 해설 문서)
INTERNAL_DOC_TYPES = {
    "postfab_terms": "glossary",
    "field_terms": "glossary",
    "alarm_codes": "glossary",       # 알람코드-설명 쌍 형식
    "troubleshooting_cards": "article",
    "mes_operations": "article",
    "yield_analysis_guide": "article",
}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """'--- ... ---' 프런트매터를 dict로, 나머지를 본문으로 분리."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip() or None
    return meta, text[m.end():]


def split_sections(body: str) -> list[dict]:
    """'## ' 헤딩 기준으로 섹션 분리. 첫 헤딩 앞 본문은 heading=None 서두 섹션."""
    sections = []
    parts = re.split(r"^## (.+)$", body, flags=re.MULTILINE)
    intro = parts[0].strip()
    if intro:
        sections.append({"heading": None, "text": intro})
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if heading and text:
            sections.append({"heading": heading, "text": text})
    return sections


def parse_series(title: str) -> tuple[str | None, int | None]:
    """제목에서 시리즈명/편수 추출: '[반도체 후공정 2편]' → ('반도체 후공정', 2)."""
    if not title:
        return None, None
    m = re.search(r"\[([^\]\d]+?)\s*(\d+)편[^\]]*\]", title)
    if m:
        return m.group(1).strip(), int(m.group(2))
    m = re.search(r"\[([^\]]+)\]", title)
    return (m.group(1).strip(), None) if m else (None, None)


def merge_captions(sections: list[dict], captions_path: Path, has_intro: bool) -> int:
    """VLM 캡션(status=ok)을 해당 섹션 끝에 '[그림 설명: ...]'으로 병합."""
    if not captions_path.exists():
        return 0
    captions = [c for c in json.load(open(captions_path, encoding="utf-8"))
                if c["status"] == "ok" and c.get("caption")]
    merged = 0
    for c in captions:
        # captions.json의 section: 0=서두, 1..N=헤딩 순번 →
        # sections[] 인덱스로 변환 (서두 섹션 존재 여부에 따라 오프셋 보정)
        idx = c["section"] if has_intro else c["section"] - 1
        idx = max(0, min(idx, len(sections) - 1))
        sections[idx]["text"] += f"\n\n[그림 설명: {c['caption']}]"
        merged += 1
    return merged


def build_clean_docs() -> list[dict]:
    """data/clean/의 크롤링 소스 문서들을 구조화."""
    excluded = set()
    if REPORT_PATH.exists():
        for r in json.load(open(REPORT_PATH, encoding="utf-8")):
            if not r["include"]:
                excluded.add((r["source"], r["slug"]))

    docs = []
    for md_path in sorted(CLEAN_DIR.rglob("*.md")):
        source_dir, slug = md_path.parent.name, md_path.stem
        if (source_dir, slug) in excluded:
            print(f"  [제외] {source_dir}/{slug} (clean_report 기준)")
            continue
        meta, body = parse_frontmatter(md_path.read_text(encoding="utf-8"))
        sections = split_sections(body)
        if not sections:
            print(f"  [경고] {source_dir}/{slug}: 섹션 0개 — 건너뜀")
            continue
        has_intro = sections[0]["heading"] is None
        n_caps = merge_captions(sections, md_path.parent / f"{slug}.captions.json", has_intro)
        series, part = parse_series(meta.get("title", ""))
        docs.append({
            "doc_id": f"{source_dir}-{slug}",
            "source": meta.get("source", source_dir),
            "url": meta.get("url"),
            "title": meta.get("title", slug),
            "series": series,
            "part": part,
            "language": meta.get("language", "ko"),
            "doc_type": meta.get("doc_type", "article"),
            "sections": sections,
            "n_captions_merged": n_caps,
        })
    return docs


def build_internal_docs() -> list[dict]:
    """data/docs/의 자체 작성 문서를 internal 소스로 흡수."""
    docs = []
    for md_path in sorted(INTERNAL_DIR.glob("*.md")):
        stem = md_path.stem
        body = md_path.read_text(encoding="utf-8")
        title_m = re.match(r"^# (.+)$", body, re.MULTILINE)
        sections = split_sections(re.sub(r"^# .+\n", "", body, count=1))
        if not sections:
            continue
        docs.append({
            "doc_id": f"internal-{stem}",
            "source": "internal_docs",
            "url": None,
            "title": title_m.group(1).strip() if title_m else stem,
            "series": None,
            "part": None,
            "language": "ko",
            "doc_type": INTERNAL_DOC_TYPES.get(stem, "article"),
            "sections": sections,
            "n_captions_merged": 0,
        })
    return docs


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("*.json"):
        old.unlink()  # 결정론적 재생성 — 이전 산출물 잔재 방지

    docs = build_clean_docs() + build_internal_docs()

    index = []
    total_sections = total_caps = 0
    for doc in docs:
        out_path = OUT_DIR / f"{doc['doc_id']}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        n_sec = len(doc["sections"])
        total_sections += n_sec
        total_caps += doc["n_captions_merged"]
        index.append({"doc_id": doc["doc_id"], "source": doc["source"],
                      "doc_type": doc["doc_type"], "title": doc["title"],
                      "n_sections": n_sec, "n_captions": doc["n_captions_merged"]})
        print(f"  [ok] {doc['doc_id']}: 섹션 {n_sec}개, 캡션 {doc['n_captions_merged']}건")

    with open(OUT_DIR / "_index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"\n총 문서 {len(docs)}건 / 섹션 {total_sections}개 / 캡션 병합 {total_caps}건")
    print(f"저장 위치: {OUT_DIR}")


if __name__ == "__main__":
    main()
