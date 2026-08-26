#!/usr/bin/env python3
"""Playwright-based company directory discoverer.
Navigates to Greenhouse/Lever/etc. and scrapes company names from their actual
customer/partner pages, then probes all found slugs.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import httpx

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP_FILE = ROOT / ".freebuff" / "pw_discover_checkpoint.json"
LOG_FILE = ROOT / ".freebuff" / "pw_discover.log"
DB_LOCK = Lock()


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
        f.write(line + "\n")


# =====================================================================
# STEP 1: Discover slugs via Playwright
# =====================================================================

def discover_with_playwright() -> set[str]:
    """Use Playwright to scrape company directories and discover ATS slugs."""
    from playwright.sync_api import sync_playwright
    
    slugs = set()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
        ])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        
        # Method 1: Scrape Greenhouse's actual job board listing pages
        gh_urls = [
            "https://www.greenhouse.com/company-directory",
            "https://www.greenhouse.com/partners",
            "https://developers.greenhouse.io/job-board.html",
        ]
        
        for url in gh_urls:
            try:
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                time.sleep(2)
                # Extract all links containing greenhouse board slugs
                html = page.content()
                # Look for boards.greenhouse.io/{slug}
                matches = re.findall(r'boards\.greenhouse\.io/([a-zA-Z0-9_-]+)', html)
                slugs.update(matches)
                # Also extract from href attributes
                links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                for link in links:
                    m = re.search(r'boards\.greenhouse\.io/([a-zA-Z0-9_-]+)', link)
                    if m:
                        slugs.add(m.group(1))
                    m = re.search(r'greenhouse\.io/([a-zA-Z0-9_-]+)', link)
                    if m and m.group(1) not in ("company-directory", "partners", "blog", "pricing", "solutions"):
                        slugs.add(m.group(1))
                log(f"  Greenhouse {url}: found {len(matches)} direct slugs")
            except Exception as e:
                log(f"  Greenhouse {url} failed: {e}")
        
        # Method 2: Scrape Lever company directory
        lever_urls = [
            "https://lever.co/customers",
            "https://www.lever.co/customer-stories/",
            "https://jobs.lever.co/",
        ]
        
        for url in lever_urls:
            try:
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                time.sleep(2)
                html = page.content()
                matches = re.findall(r'jobs\.lever\.co/([a-zA-Z0-9_-]+)', html)
                slugs.update(matches)
                links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                for link in links:
                    m = re.search(r'jobs\.lever\.co/([a-zA-Z0-9_-]+)', link)
                    if m:
                        slugs.add(m.group(1))
                    m = re.search(r'lever\.co/([a-zA-Z0-9_-]+)', link)
                    if m and m.group(1) not in ("customers", "lever", "about", "blog", "pricing"):
                        slugs.add(m.group(1))
                log(f"  Lever {url}: found lever slugs")
            except Exception as e:
                log(f"  Lever {url} failed: {e}")
        
        # Method 3: Scrape Ashby directory
        ashby_urls = [
            "https://www.ashbyhq.com/customers",
            "https://jobs.ashbyhq.com/",
        ]
        
        for url in ashby_urls:
            try:
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                time.sleep(2)
                html = page.content()
                matches = re.findall(r'jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)', html)
                slugs.update(matches)
                links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                for link in links:
                    m = re.search(r'jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)', link)
                    if m:
                        slugs.add(m.group(1))
                    m = re.search(r'ashbyhq\.com/([a-zA-Z0-9_-]+)', link)
                    if m and m.group(1) not in ("customers", "ashby", "about", "blog", "pricing"):
                        slugs.add(m.group(1))
                log(f"  Ashby {url}: found ashby slugs")
            except Exception as e:
                log(f"  Ashby {url} failed: {e}")
        
        # Method 4: Scrape Workable directory
        workable_urls = [
            "https://www.workable.com/customers",
            "https://apply.workable.com/",
        ]
        
        for url in workable_urls:
            try:
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                time.sleep(2)
                html = page.content()
                matches = re.findall(r'apply\.workable\.com/([a-zA-Z0-9_-]+)', html)
                slugs.update(matches)
                links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
                for link in links:
                    m = re.search(r'apply\.workable\.com/([a-zA-Z0-9_-]+)', link)
                    if m:
                        slugs.add(m.group(1))
                log(f"  Workable {url}: found workable slugs")
            except Exception as e:
                log(f"  Workable {url} failed: {e}")
        
        # Method 5: Scrape Wellfound (AngelList) for company directory
        try:
            page.goto("https://wellfound.com/hiring", timeout=15000, wait_until="domcontentloaded")
            time.sleep(2)
            html = page.content()
            # Extract company names from the page
            company_elements = page.query_selector_all("a[data-test='StartupResult']")
            for el in company_elements:
                href = el.get_attribute("href") or ""
                m = re.search(r'/company/([a-zA-Z0-9_-]+)', href)
                if m:
                    slugs.add(m.group(1).lower())
            log(f"  Wellfound: found company slugs")
        except Exception as e:
            log(f"  Wellfound failed: {e}")
        
        # Method 6: Scrape BuiltIn for company lists
        builtin_urls = [
            "https://builtin.com/companies/tech",
            "https://builtin.com/companies/startups",
            "https://builtin.com/companies/enterprise-tech",
        ]
        
        for url in builtin_urls:
            try:
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
                time.sleep(2)
                html = page.content()
                # Extract company slugs from links
                company_links = page.eval_on_selector_all("a[href*='/companies/']", "els => els.map(e => e.href)")
                for link in company_links:
                    m = re.search(r'/companies/([a-zA-Z0-9_-]+)', link)
                    if m and m.group(1) not in ("tech", "startups", "enterprise-tech", "fintech", "healthtech", "edtech"):
                        slugs.add(m.group(1))
                log(f"  BuiltIn {url}: found company slugs")
            except Exception as e:
                log(f"  BuiltIn {url} failed: {e}")
        
        # Method 7: Scrape Crunchbase list
        try:
            page.goto("https://www.crunchbase.com/hub/greenhouse-companies", timeout=15000, wait_until="domcontentloaded")
            time.sleep(3)
            html = page.content()
            # Extract organization names
            org_links = page.eval_on_selector_all("a[href*='/organization/']", "els => els.map(e => ({href: e.href, text: e.textContent}))")
            for item in org_links:
                m = re.search(r'/organization/([a-zA-Z0-9_-]+)', item.get("href", ""))
                if m:
                    slugs.add(m.group(1).lower().replace("-", ""))
            log(f"  Crunchbase: found org slugs")
        except Exception as e:
            log(f"  Crunchbase failed: {e}")
        
        browser.close()
    
    return slugs


# =====================================================================
# ATS SCRAPERS
# =====================================================================

def scrape_greenhouse(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200: return []
        data = r.json()
        jobs = data.get("jobs", [])
        if not jobs: return []
        return [{
            "title": j.get("title", ""),
            "company": data.get("name", slug),
            "location": (j.get("location", {}) or {}).get("name", "") if isinstance(j.get("location"), dict) else str(j.get("location", "")),
            "url": j.get("absolute_url", ""),
            "posted_at": j.get("updated_at") or j.get("created_at"),
            "external_id": str(j.get("id", "")),
            "source": f"greenhouse:{slug}",
            "description": (j.get("content") or "")[:500],
            "tags": (j.get("departments") or [{}])[0].get("name", "") if j.get("departments") else "",
        } for j in jobs]
    except Exception: return []


def scrape_lever(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://api.lever.co/v0/postings/{slug}?mode=json",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200: return []
        data = r.json()
        if not isinstance(data, list) or not data: return []
        return [{
            "title": j.get("text", ""),
            "company": (j.get("categories", {}) or {}).get("team", slug),
            "location": (j.get("categories", {}) or {}).get("location", ""),
            "url": j.get("hostedUrl", ""),
            "posted_at": datetime.fromtimestamp(j.get("createdAt", 0) / 1000, tz=timezone.utc).isoformat() if j.get("createdAt") else None,
            "external_id": j.get("id", ""),
            "source": f"lever:{slug}",
            "description": (j.get("descriptionPlain") or "")[:500],
            "tags": j.get("teamsPlain", ""),
        } for j in data]
    except Exception: return []


def scrape_ashby(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200: return []
        data = r.json()
        board = data.get("jobBoard", {})
        openings = board.get("openings", [])
        if not openings: return []
        return [{
            "title": j.get("title", ""),
            "company": board.get("name", slug),
            "location": j.get("locationName", ""),
            "url": j.get("url", ""),
            "posted_at": j.get("publishedAt"),
            "external_id": j.get("id", ""),
            "source": f"ashby:{slug}",
            "description": "",
            "tags": j.get("departmentName", ""),
        } for j in openings]
    except Exception: return []


def scrape_smartrecruiters(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200: return []
        data = r.json()
        content = data.get("content", [])
        if not content: return []
        return [{
            "title": j.get("name", ""),
            "company": (j.get("company") or {}).get("name", slug),
            "location": f"{(j.get('location') or {}).get('city', '')}, {(j.get('location') or {}).get('country', '')}".strip(", "),
            "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('ref', '')}",
            "posted_at": j.get("releasedDate"),
            "external_id": str(j.get("id", "")),
            "source": f"smartrecruiters:{slug}",
            "description": "",
            "tags": "",
        } for j in content]
    except Exception: return []


def scrape_workable(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200: return []
        data = r.json()
        jobs = data.get("jobs", [])
        if not jobs: return []
        return [{
            "title": j.get("title", ""),
            "company": data.get("name", slug),
            "location": f"{j.get('city', '')}, {j.get('country', '')}".strip(", "),
            "url": j.get("url", ""),
            "posted_at": j.get("date"),
            "external_id": j.get("id", ""),
            "source": f"workable:{slug}",
            "description": "",
            "tags": j.get("department", ""),
        } for j in jobs]
    except Exception: return []


SCRAPERS = [scrape_greenhouse, scrape_lever, scrape_ashby, scrape_smartrecruiters, scrape_workable]

def probe_slug(slug: str) -> list[dict]:
    for scraper in SCRAPERS:
        jobs = scraper(slug)
        if jobs:
            return jobs
    return []


def store_jobs(conn, jobs, tag) -> int:
    new = 0
    now = datetime.now(timezone.utc).isoformat()
    with DB_LOCK:
        for j in jobs:
            if not j.get("title") or not j.get("url"): continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO jobs (dedupe_key,title,company,location,description,url,source,source_kind,external_id,posted_at,salary,tags,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (j["url"] or j.get("external_id",""), j["title"], j.get("company",""), j.get("location",""), j.get("description",""), j["url"], j["source"], "ats", j.get("external_id",""), j.get("posted_at"), j.get("salary",""), tag, now, now),
                )
                if cur.rowcount > 0: new += 1
            except: continue
        conn.commit()
    return new


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=40)
    ap.add_argument("--skip-discovery", action="store_true")
    args = ap.parse_args()
    
    # Step 1: Discover slugs
    if args.skip_discovery:
        cp = load_checkpoint()
        all_slugs = cp.get("found", [])
        if isinstance(all_slugs, list):
            all_slugs = set(all_slugs)
        log(f"Loaded {len(all_slugs)} previously discovered slugs")
    else:
        log("Phase 1: Discovering company slugs via Playwright...")
        pw_slugs = discover_with_playwright()
        log(f"Playwright discovered {len(pw_slugs)} slugs")
        
        # Save discovered slugs
        cp = load_checkpoint()
        existing = set(cp.get("found", []))
        all_slugs = pw_slugs | existing
        cp["found"] = list(all_slugs)
        save_checkpoint(cp)
    
    # Step 2: Probe all slugs
    scraped_set = set(cp.get("scraped", []))
    remaining = [s for s in all_slugs if s not in scraped_set]
    log(f"Phase 2: Probing {len(remaining)} slugs ({len(scraped_set)} already done)")
    
    if not remaining:
        log("All slugs already probed!")
        return
    
    conn = sqlite3.connect(DB)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {total_before:,}")
    
    grand_new = cp.get("stats", {}).get("new", 0)
    boards = cp.get("stats", {}).get("boards", 0)
    start = time.time()
    BATCH = args.threads * 10
    
    for bi in range(0, len(remaining), BATCH):
        batch = remaining[bi:bi+BATCH]
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(probe_slug, s): s for s in batch}
            for f in as_completed(futures):
                slug = futures[f]
                scraped_set.add(slug)
                try:
                    jobs = f.result()
                    if jobs:
                        boards += 1
                        src = jobs[0].get("source", "?")
                        new = store_jobs(conn, jobs, f"pw,{slug}")
                        grand_new += new
                        log(f"  {slug:30s} -> {src:30s}: {len(jobs):4d} jobs, +{new:4d}")
                except: pass
        
        cp["scraped"] = list(scraped_set)
        cp["stats"] = {"new": grand_new, "errors": 0, "boards": boards}
        save_checkpoint(cp)
        
        elapsed = time.time() - start
        current = total_before + grand_new
        log(f"  [{len(scraped_set)}/{len(all_slugs)}] DB: {current:,} (+{grand_new:,}) | Boards: {boards}")
    
    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    elapsed = time.time() - start
    conn.close()
    
    log("")
    log("=" * 60)
    log(f"COMPLETE: {len(scraped_set)} probed, {boards} boards, +{grand_new:,} jobs")
    log(f"DB: {final:,} | Gap to 1M: {max(0, 1_000_000 - final):,}")
    log(f"Time: {elapsed/60:.1f} min")
    log("=" * 60)


if __name__ == "__main__":
    main()
