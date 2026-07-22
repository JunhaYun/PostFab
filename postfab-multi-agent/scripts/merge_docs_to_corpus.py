"""
data/docs/의 지식 문서(마크다운)를 파싱해 corpus_raw.json에 병합.
`## ` 헤더 단위로 청크를 나누며, 재실행해도 중복되지 않는다(기존 병합분 교체).
실행 후 src/rag/build_vectorstore.py로 벡터스토어를 재구축해야 반영된다.
"""
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS_PATH = os.path.join(BASE, "..", "data", "corpus_raw.json")
DOCS_DIR = os.path.join(BASE, "data", "docs")

# 파일명 → source 태그. 여기 등록된 문서만 병합 대상.
DOC_SOURCES = {
    "postfab_terms.md": "postfab_terms",
    "troubleshooting_cards.md": "troubleshooting_kb",
    "alarm_codes.md": "alarm_codes",
    "mes_operations.md": "mes_operations",
    "yield_analysis_guide.md": "yield_analysis",
}


def parse_sections(md_path: str) -> list[dict]:
    """`## ` 헤더 기준으로 (title, text) 청크 목록을 반환."""
    with open(md_path, encoding="utf-8") as f:
        content = f.read()

    sections = []
    parts = re.split(r"^## ", content, flags=re.MULTILINE)[1:]  # 첫 조각은 문서 머리말
    for part in parts:
        lines = part.strip().split("\n")
        title = lines[0].strip()
        text = "\n".join(lines[1:]).strip()
        text = text.replace("**", "")  # 굵게 마커 제거 (임베딩 입력 정리)
        if title and text:
            sections.append({"title": title, "text": text})
    return sections


def main():
    with open(CORPUS_PATH, encoding="utf-8") as f:
        corpus = json.load(f)

    new_sources = set(DOC_SOURCES.values())
    kept = [d for d in corpus["documents"] if d["source"] not in new_sources]
    removed = len(corpus["documents"]) - len(kept)

    next_num = max(int(d["id"][1:]) for d in kept) + 1
    added = []
    for filename, source in DOC_SOURCES.items():
        path = os.path.join(DOCS_DIR, filename)
        if not os.path.exists(path):
            print(f"[skip] {filename} 없음")
            continue
        for sec in parse_sections(path):
            added.append({
                "id": f"d{next_num:03d}",
                "source": source,
                "title": sec["title"],
                "text": sec["text"],
            })
            next_num += 1
        print(f"[merge] {filename} ({source})")

    corpus["documents"] = kept + added
    with open(CORPUS_PATH, "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=2)

    print(f"기존 유지 {len(kept)}개 / 기존 병합분 제거 {removed}개 / 신규 추가 {len(added)}개")
    print(f"총 {len(corpus['documents'])}개 문서")


if __name__ == "__main__":
    main()
