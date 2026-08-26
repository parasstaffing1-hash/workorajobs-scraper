#!/usr/bin/env python3
"""curl_cffi Scraper — Browser-like TLS fingerprint to bypass bot detection.

Uses curl_cffi which impersonates real browser TLS fingerprints,
bypassing Cloudflare, Akamai, and other TLS fingerprint detection.

Usage:
    python -m scripts.curl_scraper --sources indeed,dice --workers 30
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, sys, time, random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "curl_log.txt"

SOURCES = {
    "indeed": {
        "url": "https://www.indeed.com/jobs?q={q}&l={loc}&fromage=7&start={offset}",
        "pattern": r'"jobkey":"([^"]+)"',
        "title_re": r'"title":"([^"]{5,120})"',
        "company_re": r'"company":"([^"]{1,100})"',
        "base": "https://www.indeed.com/viewjob?jk=",
        "offset_step": 10,
    },
    "dice": {
        "url": "https://www.dice.com/jobs?q={q}&location={loc}&page={page}",
        "pattern": r'href="(/job-detail/[^"]+)"',
        "title_re": r'<h1[^>]*>([^<]+)</h1>',
        "base": "https://www.dice.com",
    },
    "glassdoor": {
        "url": "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q}&locT=&locId=&locKeyword={loc}&fromAge=7&page={page}",
        "pattern": r'"jobListingId":(\d+)',
        "title_re": r'"jobTitle":"([^"]{5,120})"',
        "company_re": r'"employer":"([^"]{1,100})"',
        "base": "https://www.glassdoor.com/Job/",
    },
    "ziprecruiter": {
        "url": "https://www.ziprecruiter.com/jobs-search?search={q}&location={loc}&days=7&page={page}",
        "pattern": r'href="(/jobs/[^"]+)"[^>]*>.*?<[^>]*>([^<]{10,100})',
        "base": "https://www.ziprecruiter.com",
    },
    "monster": {
        "url": "https://www.monster.com/jobs/search?q={q}&where={loc}&page=1&so=m.h.sh",
        "pattern": r'data-testid="job-card".*?href="([^"]+)"',
        "title_re": r'class="job-card-title"[^>]*>([^<]+)',
        "base": "https://www.monster.com",
    },
    "talent": {
        "url": "https://www.talent.com/jobs?k={q}&l={loc}&p={page}&period=7",
        "pattern": r'href="(https://[^"]*jobs/[^"]+)"[^>]*>.*?<h2[^>]*>([^<]+)',
        "base": "https://www.talent.com",
    },
    "simplyhired": {
        "url": "https://www.simplyhired.com/search?q={q}&l={loc}&pn={page}",
        "pattern": r'<a[^>]*href="(/job/[^"]+)"[^>]*class="[^"]*jobTitle[^"]*"[^>]*>.*?<span[^>]*>([^<]+)',
        "base": "https://www.simplyhired.com",
    },
    "adzuna": {
        "url": "https://www.adzuna.com/search?q={q}&loc={loc}&locw={loc}&locid=&loc2=&p={page}",
        "pattern": r'href="(https://www\.adzuna\.com/details/[^"]+)"',
        "title_re": r'<h2[^>]*data-qa="srp-title"[^>]*>([^<]+)',
        "company_re": r'data-qa="srp-company"[^>]*>([^<]+)',
    },
}

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

JOB_KEYWORDS = [
    "software engineer", "software developer", "backend developer", "frontend developer",
    "full stack developer", "react developer", "python developer", "java developer",
    "devops engineer", "data engineer", "data scientist", "machine learning engineer",
    "mobile developer", "android developer", "ios developer", "flutter developer",
    "cloud engineer", "platform engineer", "site reliability engineer",
    "QA engineer", "test automation", "embedded engineer", "firmware engineer",
    "blockchain developer", "game developer", "AI engineer", "ML engineer",
    "product engineer", "security engineer", "network engineer", "database administrator",
    "web developer", "typescript developer", "angular developer", "vue developer",
    "kubernetes docker", "aws cloud", "azure cloud", "gcp cloud",
    "terraform", "ruby on rails", "php developer", "kotlin developer",
    "swift developer", "golang developer", "rust developer", "c++ developer",
    "c# developer", "nodejs developer", "spark hadoop", "etl developer",
]

LOCATIONS = [
    "", "remote", "United States", "New York", "San Francisco", "Austin",
    "Seattle", "Boston", "Chicago", "Los Angeles", "Denver", "Atlanta",
    "London", "Berlin", "Toronto", "Sydney", "India", "Bangalore",
]


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def make_key(title, company):
    raw = f"{(title or '').lower().strip()}|{(company or '').lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def fetch_url(url):
    """Fetch URL using curl_cffi with browser TLS fingerprint."""
    try:
        from curl_cffi import requests as cffi_requests
        resp = cffi_requests.get(url, headers=HEADERS, impersonate="chrome131", timeout=15)
        if resp.status_code == 200:
            return resp.text
    except ImportError:
        # Fallback to httpx
        import httpx
        resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        log(f"  fetch error: {e}")
    return ""


def extract_jobs_from_html(html, source):
    """Extract job listings from HTML using regex patterns."""
    import re
    jobs = []
    cfg = SOURCES.get(source, {})

    if not cfg.get("pattern"):
        return jobs

    # Find all job URLs
    matches = re.findall(cfg["pattern"], html, re.DOTALL | re.IGNORECASE)

    # Extract titles nearby
    title_matches = re.findall(cfg.get("title_re", r'"title":"([^"]{5,120})"'), html, re.IGNORECASE)
    company_matches = re.findall(cfg.get("company_re", r'"company":"([^"]{1,100})"'), html, re.IGNORECASE)

    base = cfg.get("base", "")

    for i, m in enumerate(matches):
        if isinstance(m, tuple):
            url_path = m[0]
            title = m[1] if len(m) > 1 else (title_matches[i] if i < len(title_matches) else "")
        else:
            url_path = m
            title = title_matches[i] if i < len(title_matches) else ""

        if not title or len(title.strip()) < 4:
            continue

        if not url_path.startswith("http"):
            url_path = base + url_path

        company = company_matches[i] if i < len(company_matches) else ""

        jobs.append({
            "title": title.strip()[:200],
            "company": company.strip()[:200] if company else "",
            "location": "",
            "url": url_path,
        })

    return jobs[:30]


def save_jobs(jobs, source):
    conn = get_db()
    count = 0
    for j in jobs:
        key = make_key(j.get("title", ""), j.get("company", ""))
        try:
            conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (dedupe_key, title, company, location, url, description, tags,
                    source, source_kind, external_id, salary, posted_at,
                    first_seen_at, last_seen_at, is_active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'),1)""",
                (key, j.get("title", ""), j.get("company", ""),
                 j.get("location", ""), j.get("url", ""),
                 j.get("description", ""), json.dumps(j.get("tags", [])),
                 source, "curl_cffi", "", j.get("salary", ""),
                 j.get("posted_at", ""))
            )
            if conn.total_changes:
                count += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return count


def scrape_one(args):
    """Scrape a single keyword+location+source combination. Thread-safe."""
    source, keyword, location, offset = args
    try:
        cfg = SOURCES[source]
        q = quote_plus(keyword)
        loc = quote_plus(location) if location else ""
        url = cfg["url"].format(q=q, loc=loc, page=1, offset=offset)

        html = fetch_url(url)
        if not html:
            return 0

        jobs = extract_jobs_from_html(html, source)
        if jobs:
            return save_jobs(jobs, f"cffi:{source}")
        return 0
    except Exception as e:
        return 0


def run_curl_scraper(sources=None, keywords=None, locations=None, max_items=1000, workers=30):
    """Main curl_cffi scraping loop."""
    if not sources:
        sources = list(SOURCES.keys())[:5]
    sources = [s for s in sources if s in SOURCES]
    if not keywords:
        keywords = JOB_KEYWORDS[:10]
    if not locations:
        locations = LOCATIONS[:5]

    # Build work items
    work = []
    for src in sources:
        for kw in keywords:
            for loc in locations[:3]:
                for offset in range(0, 50, 10):
                    work.append((src, kw, loc, offset))
                    if len(work) >= max_items:
                        break
                if len(work) >= max_items:
                    break
            if len(work) >= max_items:
                break
        if len(work) >= max_items:
            break

    log(f"curl_cffi: {len(work)} items, {workers} workers, {len(sources)} sources")

    total_new = 0
    total_done = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scrape_one, w): w for w in work}
        for future in as_completed(futures):
            try:
                result = future.result()
                total_new += result
                total_done += 1
                if total_done % 50 == 0:
                    log(f"  Progress: {total_done}/{len(work)} done, {total_new} new")
            except:
                total_done += 1

    log(f"curl_cffi done: {total_done} scanned, {total_new} new")
    return total_done, total_new


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="indeed,glassdoor,ziprecruiter,simplyhired,talent")
    parser.add_argument("--keywords", default="software engineer,python developer,react developer")
    parser.add_argument("--locations", default="remote,New York,San Francisco")
    parser.add_argument("--max", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=30)
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    locations = [l.strip() for l in args.locations.split(",") if l.strip()]

    log("curl_cffi Scraper starting")
    run_curl_scraper(sources, keywords, locations, args.max, args.workers)


if __name__ == "__main__":
    main()
