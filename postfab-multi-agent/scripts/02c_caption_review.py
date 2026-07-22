"""
02c_caption_review.py — VLM 캡션 집계 + 사람 검수용 HTML 페이지 생성.

입력:  data/clean/<source>/<slug>.captions.json (02b 산출물)
출력:  data/clean/caption_review.html — 그림(원본 URL 핫링크)과 캡션을 나란히,
       검수 위험도 순으로 정렬한 페이지. 이미지 파일은 저장하지 않는다.

위험도 분류 (오류 시 코퍼스 피해가 큰 순):
  1. flow      — 공정 순서·화살표 서술 (순서가 틀리면 오답 근거가 됨)
  2. structure — 층 구성·단면 서술 (층 순서 오독 위험)
  3. suspect   — 짧거나 회피 표현·장식성 언급 (코퍼스 제외 후보)
  4. other     — 나머지

검수 후 처리:
  ① 페이지에서 제외할 항목에 체크 → 상단 "제외 목록 다운로드" 버튼 → exclusions.json 저장
  ② python scripts/02c_caption_review.py --apply <exclusions.json 경로>
     → 해당 캡션들의 status가 excluded_manual로 바뀜 (03이 병합에서 제외)
  ③ python scripts/03_build_docs.py 재실행으로 반영
  - 캡션 내용만 고치고 싶으면: captions.json에서 caption 필드 직접 수정
"""
import argparse
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
CLEAN_DIR = BASE / "data" / "clean"
OUT_PATH = CLEAN_DIR / "caption_review.html"

FLOW_PAT = re.compile(r"→|단계|순서|공정 흐름|이어진다")
STRUCT_PAT = re.compile(r"단면|적층|층|상단|하단|위에|아래에")
SUSPECT_PAT = re.compile(r"로고|배너|홍보|썸네일|표지|알 수 없|보이지 않|식별.*(어렵|불가)|명확하지 않")


def classify(caption: str) -> str:
    if len(caption) < 50 or SUSPECT_PAT.search(caption):
        return "suspect"
    if FLOW_PAT.search(caption):
        return "flow"
    if STRUCT_PAT.search(caption):
        return "structure"
    return "other"


def main():
    rows = []
    status_counts = Counter()
    for path in sorted(CLEAN_DIR.rglob("*.captions.json")):
        source, slug = path.parent.name, path.name.removesuffix(".captions.json")
        for c in json.load(open(path, encoding="utf-8")):
            status_counts[c["status"] if c["status"].startswith(("ok", "svg", "download")) else "api_failed"] += 1
            if c["status"] == "ok" and c.get("caption"):
                rows.append(dict(source=source, slug=slug, section=c["section"],
                                 src=c["src"], caption=c["caption"],
                                 category=classify(c["caption"])))

    print("=== 상태 집계 ===")
    for status, n in status_counts.most_common():
        print(f"  {status}: {n}건")
    cat_counts = Counter(r["category"] for r in rows)
    print("=== 위험도 분류 ===")
    for cat, n in cat_counts.most_common():
        print(f"  {cat}: {n}건")

    order = {"flow": 0, "structure": 1, "suspect": 2, "other": 3}
    rows.sort(key=lambda r: (order[r["category"]], r["source"], r["slug"]))

    cat_labels = {
        "flow": ("1순위 · 공정 흐름/순서 — 순서가 틀리면 오답의 근거가 됩니다. 화살표 방향·단계 순서를 그림과 대조하세요.", "#c0392b"),
        "structure": ("2순위 · 층 구조/단면 — 층의 상하 순서와 명칭을 대조하세요.", "#d35400"),
        "suspect": ("의심 캡션 — 장식성/판독 실패 후보. 코퍼스에서 뺄지 판단하세요.", "#7f8c8d"),
        "other": ("3순위 · 일반 — 표본만 훑어보세요.", "#2c3e50"),
    }

    parts = ["""<meta charset="utf-8"><title>VLM 캡션 검수</title>
<style>
 body{font-family:'Malgun Gothic',sans-serif;max-width:1100px;margin:20px auto;padding:0 16px;line-height:1.6}
 .item{display:flex;gap:16px;border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0}
 .item.checked{background:#fdecea;border-color:#e74c3c}
 .item img{max-width:420px;max-height:300px;object-fit:contain;flex-shrink:0}
 .meta{color:#888;font-size:12px;margin-bottom:6px}
 h2{padding:6px 10px;color:#fff;border-radius:6px;font-size:15px}
 .cap{white-space:pre-wrap}
 .toolbar{position:sticky;top:0;background:#fff;padding:10px 0;border-bottom:2px solid #333;z-index:9}
 .toolbar button{font-size:15px;padding:8px 14px;cursor:pointer}
 label.ex{display:inline-flex;align-items:center;gap:4px;color:#c0392b;font-weight:bold;cursor:pointer}
</style>
<h1>VLM 캡션 검수 페이지</h1>
<div class="toolbar">
 <button onclick="exportExclusions()">☑ 제외 목록 다운로드 (exclusions.json)</button>
 <span id="count">선택 0건</span>
</div>
<p>체크 포인트 3가지: ① 순서가 맞나 ② 라벨(용어)을 오독하지 않았나 ③ 그림에 없는 내용을 지어내지 않았나.<br>
틀렸거나 쓸모없는 항목은 <b>[제외]를 체크</b> → 위 버튼으로 목록 다운로드 →
<code>python scripts/02c_caption_review.py --apply &lt;다운로드한 파일&gt;</code> → <code>python scripts/03_build_docs.py</code></p>
<script>
function refresh(){
  document.querySelectorAll('.item').forEach(d=>{
    d.classList.toggle('checked', d.querySelector('input').checked)});
  const n=document.querySelectorAll('.item input:checked').length;
  document.getElementById('count').textContent='선택 '+n+'건';
}
function exportExclusions(){
  const srcs=[...document.querySelectorAll('.item input:checked')].map(c=>c.dataset.src);
  const blob=new Blob([JSON.stringify(srcs,null,2)],{type:'application/json'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob); a.download='exclusions.json'; a.click();
}
</script>"""]
    for cat in ["flow", "structure", "suspect", "other"]:
        label, color = cat_labels[cat]
        group = [r for r in rows if r["category"] == cat]
        parts.append(f'<h2 style="background:{color}">{label} ({len(group)}건)</h2>')
        for r in group:
            src_esc = html.escape(r["src"], quote=True)
            parts.append(
                f'<div class="item"><img src="{src_esc}" loading="lazy">'
                f'<div><div class="meta">{r["source"]}/{r["slug"]} · sec{r["section"]} · '
                f'<a href="{src_esc}" target="_blank">원본</a> · '
                f'<label class="ex"><input type="checkbox" data-src="{src_esc}" onchange="refresh()">제외</label></div>'
                f'<div class="cap">{html.escape(r["caption"])}</div></div></div>')

    OUT_PATH.write_text("\n".join(parts), encoding="utf-8")
    print(f"\n검수 페이지 저장: {OUT_PATH} (브라우저로 여세요)")


def apply_exclusions(exclusions_path: Path):
    """exclusions.json의 src 목록을 excluded_manual로 일괄 반영."""
    targets = set(json.load(open(exclusions_path, encoding="utf-8")))
    changed = 0
    for path in sorted(CLEAN_DIR.rglob("*.captions.json")):
        captions = json.load(open(path, encoding="utf-8"))
        touched = False
        for c in captions:
            if c["src"] in targets and c["status"] == "ok":
                c["status"] = "excluded_manual"
                changed += 1
                touched = True
        if touched:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(captions, f, ensure_ascii=False, indent=2)
    print(f"제외 처리 {changed}건 / 요청 {len(targets)}건")
    if changed < len(targets):
        print("(차이가 나면 이미 제외됐거나 src가 목록에 없는 경우입니다)")
    print("이제 python scripts/03_build_docs.py 를 재실행하면 코퍼스 반영이 끝납니다.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="캡션 검수 페이지 생성 / 제외 목록 반영")
    ap.add_argument("--apply", metavar="EXCLUSIONS_JSON",
                    help="검수 페이지에서 다운로드한 exclusions.json 경로")
    args = ap.parse_args()
    if args.apply:
        apply_exclusions(Path(args.apply))
    else:
        main()
