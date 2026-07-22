"""
01_crawl.py — RAG 코퍼스 원본(raw) 수집기.

수집 대상 (docs/RAG_데이터셋_구축_계획_보고서.docx 2장):
  Tier 1
    - skhynix_postfab : SK하이닉스 뉴스룸 [반도체 후공정] 시리즈 11편 (고정 URL)
    - skhynix_lecture : [반도체 특강] 시리즈 61편 중 후공정 관련 편 (목록 페이지에서
                        제목 키워드로 자동 선별 — 선별 기준이 코드에 남아 재현 가능)
    - ase             : ASE 공개 기술 페이지 4종 (+ 기술 블로그 후보 목록만 기록)
    - advantest       : Advantest IR Glossary 1페이지
  Tier 2
    - skhynix_tag     : 뉴스룸 태그(반도체후공정/패키지) 기사 — 제목 키워드로 선별
  Tier 3
    - jedec           : JESD88E 용어사전 PDF (Renesas 호스팅)

동작 원칙:
  - 원본은 data/raw/<source>/ 에 그대로 저장 (전처리는 02_clean.py 담당)
  - 모든 요청/결과는 data/raw/manifest.json 에 기록 (URL, 파일, sha256, 수집시각)
    → 후보로 발견했지만 내려받지 않은 문서도 candidate 상태로 남겨 선별 근거 보존
  - 재실행 시 이미 받은 파일은 건너뜀 (--force 로 강제 재수집)
  - 요청 간 지연(--delay, 기본 1.5초)으로 대상 사이트 부하 방지

사용법:
  python scripts/01_crawl.py                 # 전체 수집
  python scripts/01_crawl.py --source jedec  # 특정 소스만
  python scripts/01_crawl.py --force         # 전체 재수집
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# Windows 콘솔(cp949)에서도 로그가 깨지지 않도록 UTF-8로 강제
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parents[1]
RAW_DIR = BASE / "data" / "raw"
MANIFEST_PATH = RAW_DIR / "manifest.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "PostFabRAG-crawler/1.0 (personal research project)"
)
# 일부 사이트(ASE, Renesas)는 requests의 TLS 지문을 차단하지만 curl은 정상 응답한다.
# requests가 403을 받으면 curl 서브프로세스로 1회 폴백한다 (아래 fetch_via_curl).
BROWSER_HEADERS = [
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language: en-US,en;q=0.9,ko;q=0.8",
    "Connection: keep-alive",
    "Upgrade-Insecure-Requests: 1",
]
TIMEOUT = 30
RETRIES = 2

# ── Tier 1: [반도체 후공정] 시리즈 11편 (완결, 고정 URL) ─────────────────────
POSTFAB_SERIES_URLS = [
    "https://news.skhynix.co.kr/seominsuk-column-test/",
    "https://news.skhynix.co.kr/seominsuk-column-package-definition/",
    "https://news.skhynix.co.kr/seominsuk-column-types-of-packages-1/",
    "https://news.skhynix.co.kr/seominsuk-column-types-of-packages-2/",
    "https://news.skhynix.co.kr/seominsuk-column-types-of-packages-5/",
    "https://news.skhynix.co.kr/seominsuk-column-types-of-packages-6/",
    "https://news.skhynix.co.kr/seominsuk-column-wafer-level-package/",
    "https://news.skhynix.co.kr/seominsuk-column-wafer-level-package-2/",
    "https://news.skhynix.co.kr/seominsuk-column-package-role-material-1/",
    "https://news.skhynix.co.kr/seominsuk-column-package-role-material-2/",
    "https://news.skhynix.co.kr/seominsuk-column-package-reliability/",
]

# ── Tier 1: [반도체 특강] 시리즈 목록 — 제목이 아래 키워드를 포함하면 후공정 편으로 선별
LECTURE_SERIES_URL = "https://news.skhynix.co.kr/series/special-lecture/"
LECTURE_KEYWORDS = [
    "패키지", "패키징", "테스트", "백그라인딩", "다이본딩", "와이어본딩",
    "인캡슐레이션", "싱귤레이션", "경박단소",
]

# ── Tier 2: 뉴스룸 태그 기사 — 제목이 아래 키워드를 포함하면 선별
TAG_URLS = [
    "https://news.skhynix.co.kr/tag/%EB%B0%98%EB%8F%84%EC%B2%B4%ED%9B%84%EA%B3%B5%EC%A0%95/",  # 반도체후공정
    "https://news.skhynix.co.kr/tag/%ED%8C%A8%ED%82%A4%EC%A7%80/",  # 패키지
]
TAG_KEYWORDS = [
    "HBM", "TSV", "패키징", "패키지", "어드밴스드", "이종집적", "칩렛",
    "MR-MUF", "후공정", "테스트", "팬아웃", "Fan-Out",
]
# 제목에 기술 키워드가 있어도 PR성 기사(인터뷰·수상·인물 소개)는 코퍼스에서 제외
TAG_EXCLUDE_KEYWORDS = ["인터뷰", "수상", "JOB로그", "명장", "신입사원", "앰버서더"]

# ── Tier 1: ASE 공개 기술 페이지 (재직사 — 공개 웹페이지만 수집)
ASE_PAGE_URLS = [
    "https://ase.aseglobal.com/fan-out-packaging/",
    "https://ase.aseglobal.com/fan-out-sip/",
    "https://ase.aseglobal.com/system-in-package/",
    "https://ase.aseglobal.com/heterogeneous-integration/",
]
ASE_BLOG_URL = "https://ase.aseglobal.com/blog/technology-papers/"

# ── Tier 1: Advantest / Tier 3: JEDEC ─────────────────────────────────────
ADVANTEST_URL = "https://www.advantest.com/en/investors/ir-library/glossary/"
JEDEC_PDF_URL = "https://www.renesas.com/en/document/gde/jedec-definition"

ALL_SOURCES = ["skhynix_postfab", "skhynix_lecture", "skhynix_tag", "ase", "advantest", "jedec"]


# ─────────────────────────────── 공통 유틸 ───────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug_of(url: str) -> str:
    """URL 경로 마지막 조각을 파일명용 슬러그로 변환."""
    path = urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else "index"
    return re.sub(r"[^A-Za-z0-9._-]", "_", slug) or "index"


def load_manifest() -> dict:
    """manifest.json 로드 — {url: record} 형태. 없으면 빈 dict."""
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            return {r["url"]: r for r in json.load(f)}
    return {}


def save_manifest(manifest: dict) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    records = sorted(manifest.values(), key=lambda r: (r["source"], r["url"]))
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def fetch(session: requests.Session, url: str, delay: float) -> requests.Response:
    """지연 + 재시도가 있는 GET. 마지막 실패는 예외를 그대로 올린다."""
    last_err = None
    for attempt in range(RETRIES + 1):
        time.sleep(delay if attempt == 0 else delay * 2)
        try:
            resp = session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.HTTPError as e:
            # 4xx는 재시도해도 결과가 같으므로 즉시 반환 (페이지네이션 끝 감지 등)
            if e.response is not None and 400 <= e.response.status_code < 500:
                raise
            last_err = e
            print(f"  [retry {attempt + 1}/{RETRIES}] {url} - {e}")
        except requests.RequestException as e:
            last_err = e
            print(f"  [retry {attempt + 1}/{RETRIES}] {url} - {e}")
    raise last_err


def fetch_via_curl(url: str, delay: float) -> bytes | None:
    """requests가 403으로 차단될 때의 curl 폴백. 실패 시 None."""
    time.sleep(delay)
    cmd = ["curl", "-sL", "--fail", "--max-time", str(TIMEOUT), "--compressed"]
    for h in BROWSER_HEADERS:
        cmd += ["-H", h]
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT + 10)
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


def download(session: requests.Session, manifest: dict, *, source: str, url: str,
             title: str, out_path: Path, delay: float, force: bool, binary: bool = False) -> None:
    """단일 문서 다운로드 + manifest 기록. 이미 있으면 건너뜀."""
    rel = out_path.relative_to(RAW_DIR).as_posix()
    if out_path.exists() and not force:
        print(f"  [skip] {rel} (이미 존재)")
        manifest.setdefault(url, {}).update(
            source=source, url=url, title=title, file=rel, status="downloaded")
        return
    data = None
    fetched_by = "requests"
    try:
        resp = fetch(session, url, delay)
        data = resp.content if binary else resp.text.encode("utf-8")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            data = fetch_via_curl(url, delay)
            fetched_by = "curl"
        if data is None:
            print(f"  [FAIL] {url} - {e}")
            manifest[url] = dict(source=source, url=url, title=title, file=None,
                                 sha256=None, bytes=0, fetched_at=now_iso(),
                                 status="failed", note=str(e))
            return
    except requests.RequestException as e:
        print(f"  [FAIL] {url} - {e}")
        manifest[url] = dict(source=source, url=url, title=title, file=None,
                             sha256=None, bytes=0, fetched_at=now_iso(),
                             status="failed", note=str(e))
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    manifest[url] = dict(
        source=source, url=url, title=title, file=rel,
        sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
        fetched_at=now_iso(), status="downloaded",
        note=None if fetched_by == "requests" else "curl 폴백으로 수집")
    print(f"  [ok]   {rel} ({len(data):,} bytes, {fetched_by})")


def parse_listing(html: str) -> list[dict]:
    """뉴스룸 목록 페이지(a.item-link)에서 (url, title, date) 추출."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.select("a.item-link"):
        href = a.get("href", "")
        title_el = a.select_one(".title .text-inner") or a.select_one(".title")
        date_el = a.select_one(".date")
        if not href or not title_el:
            continue
        items.append({
            "url": href,
            "title": " ".join(title_el.get_text().split()),
            "date": date_el.get_text(strip=True) if date_el else None,
        })
    return items


def matches(title: str, keywords: list[str]) -> bool:
    lowered = title.lower()
    return any(k.lower() in lowered for k in keywords)


# ─────────────────────────────── 소스별 수집 ───────────────────────────────

def crawl_skhynix_postfab(session, manifest, delay, force):
    print(f"\n=== skhynix_postfab: [반도체 후공정] 시리즈 {len(POSTFAB_SERIES_URLS)}편 ===")
    for url in POSTFAB_SERIES_URLS:
        out = RAW_DIR / "skhynix" / f"{slug_of(url)}.html"
        download(session, manifest, source="skhynix_postfab", url=url,
                 title=None, out_path=out, delay=delay, force=force)


def crawl_skhynix_lecture(session, manifest, delay, force):
    """특강 시리즈 목록에서 후공정 편을 키워드 선별 후 다운로드."""
    print("\n=== skhynix_lecture: [반도체 특강] 목록에서 후공정 편 선별 ===")
    try:
        listing = fetch(session, LECTURE_SERIES_URL, delay)
    except requests.RequestException as e:
        print(f"  [FAIL] 목록 페이지 수집 실패 — {e}")
        return
    items = parse_listing(listing.text)
    print(f"  목록 {len(items)}편 발견")
    selected = [it for it in items if matches(it["title"], LECTURE_KEYWORDS)]
    print(f"  키워드 선별 {len(selected)}편: {LECTURE_KEYWORDS}")
    known = {u for u in POSTFAB_SERIES_URLS}
    for it in items:
        if it["url"] in known:
            continue
        if it in selected:
            out = RAW_DIR / "skhynix" / f"{slug_of(it['url'])}.html"
            download(session, manifest, source="skhynix_lecture", url=it["url"],
                     title=it["title"], out_path=out, delay=delay, force=force)
        else:
            # 선별 제외 편도 manifest에 남겨 선별 근거를 추적 가능하게 한다
            manifest.setdefault(it["url"], dict(
                source="skhynix_lecture", url=it["url"], title=it["title"],
                file=None, sha256=None, bytes=0, fetched_at=now_iso(),
                status="candidate_skipped", note="LECTURE_KEYWORDS 불일치"))


def crawl_skhynix_tag(session, manifest, delay, force, max_pages):
    """태그 목록(페이지네이션 포함)에서 기사 후보 수집 후 키워드 선별 다운로드."""
    print("\n=== skhynix_tag: 태그 기사 선별 (Tier 2) ===")
    # 타 소스(시리즈/특강)에서 이미 받은 기사만 중복 제외 —
    # skhynix_tag 자신의 기존 다운로드는 선별 규칙 변경 시 재평가되어야 하므로 포함하지 않음
    already = set(POSTFAB_SERIES_URLS) | {
        u for u, r in manifest.items()
        if r.get("status") == "downloaded" and r.get("source") != "skhynix_tag"}
    seen: dict[str, dict] = {}
    for tag_url in TAG_URLS:
        for page in range(1, max_pages + 1):
            page_url = tag_url if page == 1 else f"{tag_url}page/{page}/"
            try:
                resp = fetch(session, page_url, delay)
            except requests.RequestException:
                break  # 페이지 없음 → 다음 태그로
            items = parse_listing(resp.text)
            if not items:
                break
            print(f"  {page_url} → {len(items)}건")
            for it in items:
                seen.setdefault(it["url"], it)
    print(f"  후보 총 {len(seen)}건 (중복 제거)")
    for url, it in seen.items():
        if url in already:
            continue
        included = matches(it["title"], TAG_KEYWORDS)
        excluded = matches(it["title"], TAG_EXCLUDE_KEYWORDS)
        if included and not excluded:
            out = RAW_DIR / "skhynix" / f"{slug_of(url)}.html"
            download(session, manifest, source="skhynix_tag", url=url,
                     title=it["title"], out_path=out, delay=delay, force=force)
        else:
            reason = "TAG_EXCLUDE_KEYWORDS 일치 (PR성 기사)" if excluded else "TAG_KEYWORDS 불일치"
            # 규칙 변경으로 제외된 기사는 기존 다운로드 기록을 덮어쓰고 파일도 정리
            stale = RAW_DIR / "skhynix" / f"{slug_of(url)}.html"
            if manifest.get(url, {}).get("status") == "downloaded" and stale.exists():
                stale.unlink()
                print(f"  [drop] {stale.name} ({reason})")
            manifest[url] = dict(
                source="skhynix_tag", url=url, title=it["title"],
                file=None, sha256=None, bytes=0, fetched_at=now_iso(),
                status="candidate_skipped", note=reason)


def crawl_ase(session, manifest, delay, force):
    """ASE 공개 기술 페이지 4종 + 기술 블로그 글 후보 목록(다운로드는 하지 않음)."""
    print("\n=== ase: 공개 기술 페이지 ===")
    for url in ASE_PAGE_URLS:
        out = RAW_DIR / "ase" / f"{slug_of(url)}.html"
        download(session, manifest, source="ase", url=url,
                 title=None, out_path=out, delay=delay, force=force)
    # 기술 블로그는 후보 목록만 manifest에 기록 → 수동 선별 후 ASE_PAGE_URLS에 추가
    print("  기술 블로그 후보 목록 수집 (다운로드 안 함)")
    try:
        try:
            html = fetch(session, ASE_BLOG_URL, delay).text
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                raw = fetch_via_curl(ASE_BLOG_URL, delay)
                if raw is None:
                    raise
                html = raw.decode("utf-8", errors="replace")
            else:
                raise
        soup = BeautifulSoup(html, "html.parser")
        post_urls = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/blog/" in href and href.rstrip("/") != ASE_BLOG_URL.rstrip("/") \
                    and "aseglobal.com" in href and "/tag/" not in href and "/category/" not in href:
                post_urls.add(href)
        for u in sorted(post_urls):
            manifest.setdefault(u, dict(
                source="ase_blog", url=u, title=None, file=None, sha256=None,
                bytes=0, fetched_at=now_iso(), status="candidate",
                note="기술 블로그 글 — 검토 후 ASE_PAGE_URLS에 추가"))
        print(f"  [ok]   블로그 후보 {len(post_urls)}건 기록")
    except requests.RequestException as e:
        print(f"  [FAIL] 블로그 목록 — {e}")


def crawl_advantest(session, manifest, delay, force):
    print("\n=== advantest: IR Glossary ===")
    out = RAW_DIR / "advantest" / "glossary.html"
    download(session, manifest, source="advantest", url=ADVANTEST_URL,
             title="Advantest IR Glossary", out_path=out, delay=delay, force=force)


def crawl_jedec(session, manifest, delay, force):
    print("\n=== jedec: JESD88E 용어사전 PDF ===")
    out = RAW_DIR / "jedec" / "JESD88E.pdf"
    download(session, manifest, source="jedec", url=JEDEC_PDF_URL,
             title="JEDEC Dictionary of Terms (JESD88E)", out_path=out,
             delay=delay, force=force, binary=True)


# ─────────────────────────────── 메인 ───────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="RAG 코퍼스 raw 수집기")
    ap.add_argument("--source", action="append", choices=ALL_SOURCES,
                    help="수집할 소스 (생략 시 전체, 반복 지정 가능)")
    ap.add_argument("--force", action="store_true", help="기존 파일 무시하고 재수집")
    ap.add_argument("--delay", type=float, default=1.5, help="요청 간 지연(초)")
    ap.add_argument("--max-tag-pages", type=int, default=5, help="태그당 최대 목록 페이지 수")
    args = ap.parse_args()
    sources = args.source or ALL_SOURCES

    manifest = load_manifest()
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    if "skhynix_postfab" in sources:
        crawl_skhynix_postfab(session, manifest, args.delay, args.force)
    if "skhynix_lecture" in sources:
        crawl_skhynix_lecture(session, manifest, args.delay, args.force)
    if "skhynix_tag" in sources:
        crawl_skhynix_tag(session, manifest, args.delay, args.force, args.max_tag_pages)
    if "ase" in sources:
        crawl_ase(session, manifest, args.delay, args.force)
    if "advantest" in sources:
        crawl_advantest(session, manifest, args.delay, args.force)
    if "jedec" in sources:
        crawl_jedec(session, manifest, args.delay, args.force)

    save_manifest(manifest)
    counts = {}
    for r in manifest.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"\nmanifest 저장: {MANIFEST_PATH}")
    print(f"상태 요약: {counts}")


if __name__ == "__main__":
    main()
