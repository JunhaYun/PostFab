"""
02b_caption.py — 본문 그림을 비전 모델로 한국어 텍스트 설명으로 변환 (VLM 캡셔닝).

배경 (보고서 3.4절):
  - 검색·답변 시스템은 텍스트 전용으로 유지하되, 그림에만 있는 정보(단면 구조,
    공정 흐름)를 전처리 단계에서 텍스트로 변환해 코퍼스에 편입한다.
  - 이미지 파일 자체는 저장하지 않는다 (ⓒ한올출판사 등 저작권) — 다운로드는
    메모리에서만 사용하고 설명 텍스트 + 원본 URL만 남긴다.

입력:  data/clean/<source>/<slug>.images.json (02_clean.py 산출물)
출력:  data/clean/<source>/<slug>.captions.json
       [{src, section, caption, model, created_at, status}]
       → 03_build_docs.py가 섹션 text에 [그림 설명: ...]으로 병합

동작:
  - clean_report.json에서 include=false 문서(예: 인포툰)는 건너뜀
  - 이미 캡셔닝된 src는 재호출하지 않음 (재실행 안전 — API 비용 보호)
  - 이미지는 로컬에서 다운로드해 base64로 전달 (ASE는 Cloudflare 차단으로
    서버측 URL 페치가 불가할 수 있어 requests→curl 폴백으로 직접 받는다)

사용법:
  python scripts/02b_caption.py --limit 3   # 스모크 테스트
  python scripts/02b_caption.py             # 전체 (약 200장)
  python scripts/02b_caption.py --source skhynix --model claude-opus-4-8
"""
import argparse
import base64
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import io

import anthropic
import requests
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
CLEAN_DIR = BASE / "data" / "clean"
REPORT_PATH = CLEAN_DIR / "clean_report.json"

DEFAULT_MODEL = "claude-opus-4-8"
MAX_TOKENS = 600
REQUEST_DELAY = 0.5  # API 호출 간 지연(초)

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
}
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
MAX_IMAGE_BYTES = 3_500_000   # 초과 시 리사이즈 (API 요청 한도 회피)
MAX_IMAGE_DIM = 2000          # 리사이즈 시 긴 변 상한 (px)


def shrink_image(data: bytes) -> tuple[bytes, str]:
    """대용량 이미지를 PNG/JPEG로 축소 재인코딩 (움짤 GIF는 첫 프레임 사용)."""
    img = Image.open(io.BytesIO(data))
    n_frames = getattr(img, "n_frames", 1)
    if n_frames > 1:
        img.seek(n_frames // 2)       # 움짤: 첫 프레임이 검은 화면인 경우가 있어 중간 프레임 사용
    img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"

PROMPT_TEMPLATE = """반도체 후공정 기술 문서에 포함된 그림입니다.

문서 제목: {title}
포함된 섹션: {section}
이미지 대체텍스트: {alt}

이 그림이 전달하는 기술 정보를 한국어 2~4문장으로 설명해 주세요. RAG 검색 코퍼스에 텍스트로 들어갈 설명이므로:
- 구조(층 구성, 배치), 순서(공정 흐름·화살표 방향), 라벨(용어·수치)을 우선 서술
- 그림에 실제로 보이는 내용만 서술하고, 보이지 않는 내용은 추측하지 말 것
- "이 그림은" 같은 서두 없이 바로 내용부터 서술
- 그림 속 영문 용어는 원문 그대로 유지"""


def load_env_key() -> str | None:
    """환경변수 → .env 순으로 ANTHROPIC_API_KEY 탐색."""
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


def fetch_image(url: str) -> tuple[bytes, str] | None:
    """이미지 다운로드 (메모리 전용). 반환: (bytes, media_type) 또는 None."""
    ext = Path(url.split("?")[0]).suffix.lower()
    media_type = MEDIA_TYPES.get(ext, "image/png")
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", media_type).split(";")[0]
    except requests.RequestException:
        pass
    # Cloudflare 등 requests 차단 시 curl 폴백 (01_crawl.py와 동일한 사유)
    cmd = ["curl", "-sL", "--fail", "--max-time", "30"]
    for k, v in BROWSER_HEADERS.items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=40)
        if result.returncode == 0 and result.stdout:
            return result.stdout, media_type
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


def parse_md_context(md_path: Path) -> tuple[str, list[str]]:
    """clean markdown에서 (제목, 섹션 헤딩 목록) 추출."""
    text = md_path.read_text(encoding="utf-8")
    title_m = re.search(r"^title:\s*(.+)$", text, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else md_path.stem
    headings = re.findall(r"^## (.+)$", text, re.MULTILINE)
    return title, headings


def caption_image(client: anthropic.Anthropic, model: str, *, data: bytes,
                  media_type: str, title: str, section: str, alt: str) -> str:
    prompt = PROMPT_TEMPLATE.format(title=title, section=section or "(본문 서두)", alt=alt or "(없음)")
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type,
                            "data": base64.standard_b64encode(data).decode("utf-8")}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("모델이 응답을 거부함")
    return next(b.text for b in response.content if b.type == "text").strip()


def main():
    ap = argparse.ArgumentParser(description="본문 그림 VLM 캡셔닝")
    ap.add_argument("--source", action="append", help="대상 소스 디렉토리 (기본 전체)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=0, help="최대 처리 장수 (0=전체)")
    args = ap.parse_args()

    api_key = load_env_key()
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY가 환경변수/.env에 없습니다")
    client = anthropic.Anthropic(api_key=api_key)

    # 코퍼스에서 제외된 문서(인포툰 등)는 캡셔닝하지 않음
    excluded = set()
    if REPORT_PATH.exists():
        for r in json.load(open(REPORT_PATH, encoding="utf-8")):
            if not r["include"]:
                excluded.add((r["source"], r["slug"]))

    done = failed = skipped = 0
    for images_path in sorted(CLEAN_DIR.rglob("*.images.json")):
        source = images_path.parent.name
        slug = images_path.name.removesuffix(".images.json")
        if args.source and source not in args.source:
            continue
        if (source, slug) in excluded:
            print(f"[제외 문서] {source}/{slug}")
            continue

        md_path = images_path.parent / f"{slug}.md"
        title, headings = parse_md_context(md_path)
        images = json.load(open(images_path, encoding="utf-8"))

        captions_path = images_path.parent / f"{slug}.captions.json"
        captions = json.load(open(captions_path, encoding="utf-8")) if captions_path.exists() else []
        cached_srcs = {c["src"] for c in captions if c.get("status") == "ok"}

        for img in images:
            if args.limit and done >= args.limit:
                break
            src = img["src"]
            if src in cached_srcs:
                skipped += 1
                continue
            sec_idx = img.get("section", 0)
            section = headings[sec_idx - 1] if 0 < sec_idx <= len(headings) else ""

            if src.split("?")[0].lower().endswith(".svg"):
                # API 비전 입력이 SVG를 지원하지 않음 — 기록만 남기고 건너뜀
                captions = [c for c in captions if c["src"] != src]
                captions.append(dict(src=src, section=sec_idx, caption=None, model=None,
                                     created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                     status="svg_unsupported"))
                with open(captions_path, "w", encoding="utf-8") as f:
                    json.dump(captions, f, ensure_ascii=False, indent=2)
                print(f"  [skip svg] {src[:80]}")
                continue

            fetched = fetch_image(src)
            if fetched is None:
                print(f"  [FAIL 다운로드] {src[:90]}")
                captions = [c for c in captions if c["src"] != src]
                captions.append(dict(src=src, section=sec_idx, caption=None,
                                     model=None, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                     status="download_failed"))
                failed += 1
                continue
            data, media_type = fetched
            if len(data) > MAX_IMAGE_BYTES:
                try:
                    data, media_type = shrink_image(data)
                    print(f"  [resize] {src[:70]} → {len(data):,} bytes")
                except Exception as e:
                    print(f"  [FAIL 리사이즈] {src[:70]} - {e}")
            try:
                time.sleep(REQUEST_DELAY)
                text = caption_image(client, args.model, data=data, media_type=media_type,
                                     title=title, section=section, alt=img.get("alt", ""))
                captions = [c for c in captions if c["src"] != src]
                captions.append(dict(src=src, section=sec_idx, caption=text,
                                     model=args.model,
                                     created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                     status="ok"))
                done += 1
                print(f"  [ok {done}] {source}/{slug} sec{sec_idx}: {text[:60]}...")
            except Exception as e:
                print(f"  [FAIL API] {src[:70]} - {e}")
                captions = [c for c in captions if c["src"] != src]
                captions.append(dict(src=src, section=sec_idx, caption=None, model=args.model,
                                     created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                     status=f"api_failed: {e}"))
                failed += 1
            # 이미지 1장 처리할 때마다 저장 (중단돼도 진행분 보존)
            with open(captions_path, "w", encoding="utf-8") as f:
                json.dump(captions, f, ensure_ascii=False, indent=2)

        if args.limit and done >= args.limit:
            break

    print(f"\n완료 {done}장 / 실패 {failed}장 / 캐시 스킵 {skipped}장")


if __name__ == "__main__":
    main()
