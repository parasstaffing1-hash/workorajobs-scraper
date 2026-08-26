#!/usr/bin/env python3
"""
Stealth web scraper that bypasses bot detection for job sites.

Key techniques:
1. Real browser fingerprinting (not headless detection)
2. Network request interception to capture API responses
3. Human-like behavior (random delays, scrolling patterns)
4. Cookie persistence across requests
5. Multiple fallback strategies per site

Usage:
    python scripts/stealth_scraper.py --query "Software Engineer" --location "Delhi"
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"


def create_stealth_context(pw):
    """Create a browser context that looks like a real user, not a bot."""
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-web-security",
        ]
    )

    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York",
        permissions=["geolocation"],
        geolocation={"latitude": 40.7128, "longitude": -74.0060},
    )

    # Remove webdriver detection
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        window.chrome = {runtime: {}};
    """)

    return browser, context


def human_delay(min_s=1, max_s=3):
    """Random delay to mimic human behavior."""
    time.sleep(random.uniform(min_s, max_s))


def human_scroll(page, times=5):
    """Scroll like a human - not perfectly straight."""
    for _ in range(times):
        # Random scroll amount
        scroll_amount = random.randint(200, 500)
        page.mouse.wheel(0, scroll_amount)
        human_delay(0.5, 1.5)


def intercept_jobs(page, site_name):
    """Intercept network requests to capture job data from API calls."""
    captured_jobs = []

    def handle_response(response):
        url = response.url
        # Look for job-related API endpoints
        if any(pattern in url.lower() for pattern in [
            '/jobs', '/search', '/api', '/graphql', '/feed',
            'jobid', 'jobkey', 'job_id', 'position'
        ]):
            try:
                content_type = response.headers.get('content-type', '')
                if 'json' in content_type:
                    data = response.json()
                    # Try to extract jobs from the response
                    if isinstance(data, dict):
                        # Common patterns for job data
                        for key in ['jobs', 'results', 'data', 'items', 'positions']:
                            if key in data and isinstance(data[key], list):
                                captured_jobs.extend(data[key])
                                break
                    elif isinstance(data, list):
                        captured_jobs.extend(data)
            except Exception:
                pass

    page.on("response", handle_response)
    return captured_jobs


# ---------------------------------------------------------------- Google Jobs (bypass CAPTCHA)
def scrape_google_jobs_stealth(p, query, location) -> list[dict]:
    """Google Jobs with stealth techniques to bypass CAPTCHA."""
    q = query.replace(" ", "+")
    l = location.replace(" ", "+") if location else ""

    # Try Google's job search with different URL patterns
    urls_to_try = [
        f"https://www.google.com/search?q={q}+jobs+in+{l}",
        f"https://www.google.com/search?q={q}+jobs&ibp=htl;jobs",
        f"https://www.google.com/search?q={q}+vacancies+in+{l}",
    ]

    all_jobs = []
    for url in urls_to_try:
        print(f"[google-stealth] Trying: {url}")
        try:
            # Set up network interception
            captured = intercept_jobs(p, "google")

            if not p.goto(url, timeout=30000, wait_until="domcontentloaded"):
                continue
            human_delay(3, 5)

            # Check for CAPTCHA
            html = p.content()
            if "captcha" in html.lower() or "recaptcha" in html.lower():
                print("  -> CAPTCHA detected, trying next URL")
                continue

            # Scroll to load content
            human_scroll(p, 8)

            # Try to extract from page
            html = p.content()

            # Method 1: Look for JSON-LD structured data
            for m in re.finditer(r'application/ld\+json"', html):
                try:
                    blob = html[m.end():]
                    j = blob[blob.find(">") + 1:blob.find("</script>")]
                    data = json.loads(j)
                    if isinstance(data, dict) and data.get("@type") == "JobPosting":
                        all_jobs.append({
                            "title": data.get("name", ""),
                            "company": data.get("hiringOrganization", {}).get("name", ""),
                            "location": data.get("jobLocation", {}).get("address", {}).get("addressLocality", ""),
                            "url": data.get("url", ""),
                            "posted_at": data.get("datePosted", ""),
                            "posted_text": "",
                            "jobkey": data.get("identifier", {}).get("value", ""),
                            "source": "google_jobs",
                        })
                except Exception:
                    continue

            # Method 2: Parse from intercepted API calls
            all_jobs.extend(captured)

            if all_jobs:
                break

        except Exception as e:
            print(f"  -> Error: {e}")
            continue

    return all_jobs


# ---------------------------------------------------------------- Monster (bypass bot detection)
def scrape_monster_stealth(p, query, location) -> list[dict]:
    """Monster with stealth techniques."""
    q = query.replace(" ", "+")
    l = location.replace(" ", "+") if location else ""

    # Monster's job search API
    url = f"https://www.monster.com/jobs/search?q={q}&where={l}&page=1"
    print(f"[monster-stealth] {url}")

    captured = intercept_jobs(p, "monster")

    if not p.goto(url, timeout=30000, wait_until="domcontentloaded"):
        return []
    human_delay(3, 5)
    human_scroll(p, 6)

    html = p.content()

    # Check for bot detection
    if "access denied" in html.lower() or "captcha" in html.lower():
        print("  -> Bot detected")
        return []

    jobs = []

    # Method 1: JSON-LD
    for m in re.finditer(r'application/ld\+json"', html):
        try:
            blob = html[m.end():]
            j = blob[blob.find(">") + 1:blob.find("</script>")]
            data = json.loads(j)
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                jobs.append({
                    "title": data.get("name", ""),
                    "company": data.get("hiringOrganization", {}).get("name", ""),
                    "location": data.get("jobLocation", {}).get("address", {}).get("addressLocality", ""),
                    "url": data.get("url", ""),
                    "posted_at": data.get("datePosted", ""),
                    "posted_text": "",
                    "jobkey": data.get("identifier", {}).get("value", ""),
                    "source": "monster",
                })
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        jobs.append({
                            "title": item.get("name", ""),
                            "company": item.get("hiringOrganization", {}).get("name", ""),
                            "location": item.get("jobLocation", {}).get("address", {}).get("addressLocality", ""),
                            "url": item.get("url", ""),
                            "posted_at": item.get("datePosted", ""),
                            "posted_text": "",
                            "jobkey": item.get("identifier", {}).get("value", ""),
                            "source": "monster",
                        })
        except Exception:
            continue

    # Method 2: Intercepted API calls
    jobs.extend(captured)

    # Method 3: Parse rendered cards
    if not jobs:
        from parsel import Selector
        sel = Selector(text=html)
        for card in sel.css("[class*='job-card'], [class*='JobCard'], [class*='card-content']"):
            title = card.css("h2::text, [class*='title']::text").get("")
            company = card.css("[class*='company']::text").get("")
            loc = card.css("[class*='location']::text").get("")
            href = card.css("a::attr(href)").get("")
            if title and len(title) > 3:
                jobs.append({
                    "title": title.strip(),
                    "company": company.strip(),
                    "location": loc.strip(),
                    "url": href or "",
                    "posted_at": None,
                    "posted_text": "",
                    "jobkey": "",
                    "source": "monster",
                })

    return jobs


# ---------------------------------------------------------------- Dice (tech jobs)
def scrape_dice_stealth(p, query, location) -> list[dict]:
    """Dice - tech-focused job board with stealth."""
    q = query.replace(" ", "%20")
    l = location.replace(" ", "%20") if location else ""

    url = f"https://www.dice.com/jobs?q={q}&location={l}&postedDate=ONE&page=1&pageSize=20"
    print(f"[dice-stealth] {url}")

    captured = intercept_jobs(p, "dice")

    if not p.goto(url, timeout=30000, wait_until="domcontentloaded"):
        return []
    human_delay(3, 5)
    human_scroll(p, 5)

    html = p.content()

    jobs = []

    # JSON-LD
    for m in re.finditer(r'application/ld\+json"', html):
        try:
            blob = html[m.end():]
            j = blob[blob.find(">") + 1:blob.find("</script>")]
            data = json.loads(j)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        jobs.append({
                            "title": item.get("name", ""),
                            "company": item.get("hiringOrganization", {}).get("name", ""),
                            "location": item.get("jobLocation", {}).get("address", {}).get("addressLocality", ""),
                            "url": item.get("url", ""),
                            "posted_at": item.get("datePosted", ""),
                            "posted_text": "",
                            "jobkey": item.get("identifier", {}).get("value", ""),
                            "source": "dice",
                        })
        except Exception:
            continue

    jobs.extend(captured)

    # Fallback: parse rendered cards
    if not jobs:
        from parsel import Selector
        sel = Selector(text=html)
        for card in sel.css("[class*='job-card'], [class*='JobCard'], [id*='job-card']"):
            title = card.css("h3::text, [class*='title']::text").get("")
            company = card.css("[class*='company']::text").get("")
            loc = card.css("[class*='location']::text").get("")
            href = card.css("a::attr(href)").get("")
            if title and len(title) > 3:
                jobs.append({
                    "title": title.strip(),
                    "company": company.strip(),
                    "location": loc.strip(),
                    "url": href or "",
                    "posted_at": None,
                    "posted_text": "",
                    "jobkey": "",
                    "source": "dice",
                })

    return jobs


# ---------------------------------------------------------------- LinkedIn (bypass 60-job cap)
def scrape_linkedin_stealth(p, query, location, max_pages=15) -> list[dict]:
    """LinkedIn with pagination and stealth to get more than 60 jobs."""
    q = query.replace(" ", "%20")
    loc = location.replace(" ", "%20") if location else ""
    all_jobs = []

    for start in range(0, max_pages * 25, 25):
        if q and loc:
            url = f"https://www.linkedin.com/jobs/search?keywords={q}&location={loc}&start={start}&f_TPR=r86400"
        elif loc and not q:
            slug = location.lower().split(",")[0].strip().replace(" ", "-")
            url = f"https://www.linkedin.com/jobs/{slug}-jobs?start={start}&f_TPR=r86400"
        elif q and not loc:
            url = f"https://www.linkedin.com/jobs/search?keywords={q}&start={start}&f_TPR=r86400"
        else:
            url = f"https://www.linkedin.com/jobs?f_TPR=r86400&start={start}"

        print(f"[linkedin-stealth] page {start//25 + 1}: {url}")

        captured = intercept_jobs(p, "linkedin")

        if not p.goto(url, timeout=30000, wait_until="domcontentloaded"):
            break
        human_delay(3, 5)
        human_scroll(p, 5)

        html = p.content()

        # Check for login wall
        if "sign in" in html.lower() and "linkedin.com/login" in html:
            print("  -> Login wall hit")
            break

        from parsel import Selector
        sel = Selector(text=html)
        page_jobs = []

        for c in sel.css("li.base-card, .job-search-card, .base-search-card"):
            lines = [l.strip() for l in c.css("::text").getall() if l.strip()]
            if len(lines) < 3:
                continue
            title = lines[0]
            company = lines[2] if len(lines) > 2 else ""
            loc = ""
            rel = ""
            for line in lines[3:]:
                low = line.lower()
                if "ago" in low or "hour" in low or "day" in low or "week" in low:
                    rel = line
                elif "apply" in low or "promoted" in low or "new" == low:
                    continue
                elif not loc and ("india" in low or "," in line):
                    loc = line

            date = c.css("time::attr(datetime)").get("")
            href = c.css("a::attr(href)").get("")
            posted_at = None
            if date:
                posted_at = date[:10] + "T00:00:00+00:00"
            elif rel:
                now = datetime.now(timezone.utc)
                mh = re.search(r"(\d+)\s*hour", rel)
                md = re.search(r"(\d+)\s*day", rel)
                if mh:
                    posted_at = (now - timedelta(hours=int(mh.group(1)))).isoformat()
                elif md:
                    posted_at = (now - timedelta(days=int(md.group(1)))).isoformat()

            jid = re.search(r"view/(\d+)", href or "")
            page_jobs.append({
                "title": title,
                "company": company,
                "location": loc,
                "url": href.split("?")[0] if href else "",
                "posted_at": posted_at,
                "posted_text": rel or (date[:10] if date else ""),
                "jobkey": jid.group(1) if jid else "",
                "source": "linkedin",
            })

        if not page_jobs:
            break
        all_jobs.extend(page_jobs)
        print(f"  -> {len(page_jobs)} jobs (total {len(all_jobs)})")
        human_delay(2, 4)

    return all_jobs


# ---------------------------------------------------------------- main
def main():
    import argparse

    ap = argparse.ArgumentParser(description="Stealth web scraper for job sites")
    ap.add_argument("--query", default="Software Engineer", help="job keyword(s)")
    ap.add_argument("--location", default="", help="city/region")
    ap.add_argument("--hours", type=int, default=24, help="freshness window")
    ap.add_argument("--dry-run", action="store_true", help="don't write to DB")
    ap.add_argument("--db", default=str(DB), help="path to SQLite database")
    ap.add_argument("--sources", default="google,monster,dice,linkedin",
                    help="comma-separated sources to scrape")
    args = ap.parse_args()

    db_path = Path(args.db)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser, context = create_stealth_context(pw)
    page = context.new_page()

    all_jobs = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)

    for source in sources:
        try:
            if source == "google":
                jobs = scrape_google_jobs_stealth(page, args.query, args.location)
            elif source == "monster":
                jobs = scrape_monster_stealth(page, args.query, args.location)
            elif source == "dice":
                jobs = scrape_dice_stealth(page, args.query, args.location)
            elif source == "linkedin":
                jobs = scrape_linkedin_stealth(page, args.query, args.location)
            else:
                print(f"Unknown source: {source}")
                continue

            print(f"[{source}] {len(jobs)} jobs scraped")
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"[{source}] FAILED: {e}")
        human_delay(3, 5)

    page.close()
    context.close()
    browser.close()
    pw.stop()

    # Filter by freshness
    fresh = []
    for j in all_jobs:
        pa = j.get("posted_at")
        if not pa:
            continue
        try:
            dt = datetime.fromisoformat(pa.replace("Z", "+00:00"))
        except Exception:
            continue
        if dt >= cutoff:
            fresh.append(j)

    print(f"\nTotal scraped: {len(all_jobs)} | Fresh (<{args.hours}h): {len(fresh)}")

    if args.dry_run:
        for j in sorted(fresh, key=lambda x: x.get("posted_at", ""), reverse=True)[:30]:
            print(f"  [{j['source']}] {j['title'][:45]} | {j['company'][:25]} | {j['location'][:20]}")
        return

    # Write to DB
    conn = sqlite3.connect(db_path)
    new = 0
    for j in fresh:
        if not j["title"] or not j["url"]:
            continue
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (dedupe_key, title, company, location, description, url, source,
                    source_kind, external_id, posted_at, salary, tags,
                    first_seen_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    j["url"] or j["jobkey"], j["title"], j["company"], j["location"],
                    "", j["url"], j["source"], "browser", j["jobkey"],
                    j["posted_at"], "", f"stealth,{j['source']}",
                    now.isoformat(), now.isoformat(),
                ),
            )
            if cur.rowcount > 0:
                new += 1
        except Exception:
            continue
    conn.commit()
    conn.close()
    print(f"\n[DB] {new} new jobs inserted into {db_path}")


if __name__ == "__main__":
    main()
