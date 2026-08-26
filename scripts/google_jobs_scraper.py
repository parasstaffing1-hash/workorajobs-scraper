#!/usr/bin/env python3
"""Google Jobs Scraper — Scrape Google Jobs carousel results.

Uses Playwright to search Google for jobs and extract the carousel results.

Usage:
    python -m scripts.google_jobs_scraper --keyword "software engineer" --location "remote"
"""
from __future__ import annotations
import asyncio, hashlib, json, os, random, re, sqlite3, time
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "google_jobs_log.txt"


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def make_key(title, company):
    raw = f"{(title or '').lower().strip()}|{(company or '').lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


def save_jobs(jobs, source="google_jobs"):
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
                 j.get("description", "")[:2000],
                 json.dumps(j.get("tags", [])),
                 source, "web", "",
                 j.get("salary", ""), j.get("posted_at", ""))
            )
            if conn.total_changes:
                count += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return count


async def scrape_google_jobs(page, keyword, location=""):
    """Scrape Google Jobs carousel."""
    jobs = []

    try:
        # Search Google Jobs
        q = f"{keyword} jobs"
        if location:
            q += f" in {location}"

        url = f"https://www.google.com/search?q={quote_plus(q)}&ibp=htl;jobs"
        log(f"  Searching: {q}")

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # Check if we got results
        content = await page.content()

        # Try to find job cards in Google Jobs
        job_selectors = [
            ".iFjolb",  # Google Jobs card
            ".JobSearchjob_OpQiJ",
            ".job-card",
            "[data-ved]",
        ]

        cards = []
        for sel in job_selectors:
            cards = await page.query_selector_all(sel)
            if cards and len(cards) > 2:
                break

        if not cards:
            # Try to extract from page content directly
            # Look for structured data
            scripts = await page.query_selector_all('script[type="application/ld+json"]')
            for script in scripts:
                try:
                    text = await script.inner_text()
                    data = json.loads(text)
                    if isinstance(data, list):
                        for item in data:
                            if item.get("@type") == "Job":
                                jobs.append({
                                    "title": item.get("title", ""),
                                    "company": item.get("hiringOrganization", {}).get("name", ""),
                                    "location": item.get("jobLocation", {}).get("address", {}).get("addressLocality", ""),
                                    "url": item.get("url", ""),
                                    "description": item.get("description", "")[:1000],
                                    "salary": str(item.get("baseSalary", {}).get("value", "")),
                                    "posted_at": item.get("datePosted", ""),
                                })
                    elif isinstance(data, dict) and data.get("@type") == "Job":
                        jobs.append({
                            "title": data.get("title", ""),
                            "company": data.get("hiringOrganization", {}).get("name", ""),
                            "location": data.get("jobLocation", {}).get("address", {}).get("addressLocality", ""),
                            "url": data.get("url", ""),
                            "description": data.get("description", "")[:1000],
                            "salary": str(data.get("baseSalary", {}).get("value", "")),
                            "posted_at": data.get("datePosted", ""),
                        })
                except:
                    continue

        # Try extracting from visible elements
        if not jobs:
            elements = await page.query_selector_all('[role="heading"], [data-text], h3')
            for el in elements[:30]:
                try:
                    text = (await el.inner_text()).strip()
                    if len(text) > 10 and any(kw in text.lower() for kw in ["engineer", "developer", "manager", "analyst"]):
                        parent = await el.evaluate_handle("el => el.closest('[data-ved]') || el.parentElement")
                        link = await parent.query_selector("a[href]") if parent else None
                        url = await link.get_attribute("href") if link else ""

                        if text:
                            jobs.append({
                                "title": text[:200],
                                "company": "",
                                "location": "",
                                "url": url or "",
                                "description": "",
                            })
                except:
                    continue

        log(f"  Found {len(jobs)} jobs from Google")

    except Exception as e:
        log(f"  Error scraping Google: {e}")

    return jobs[:25]


async def scrape_google_jobs(keywords=None, locations=None, max_pages=3):
    """Main Google Jobs scraping loop."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("Playwright not installed")
        return 0, 0

    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
        use_stealth = True
    except:
        use_stealth = False

    if not keywords:
        keywords = ["software engineer", "python developer", "react developer"]
    if not locations:
        locations = ["remote", "United States"]

    total_new = 0
    total_scraped = 0

    log(f"Google Jobs Scraper: {len(keywords)} keywords, {len(locations)} locations")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )

        for loc in locations:
            for kw in keywords:
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                )

                if use_stealth:
                    try:
                        await stealth.apply_stealth_async(context)
                    except:
                        pass

                page = await context.new_page()
                try:
                    q = f"{kw} jobs in {loc}" if loc else f"{kw} jobs"
                    jobs = await scrape_google_page(page, q)
                    if jobs:
                        new = save_jobs(jobs)
                        total_scraped += len(jobs)
                        total_new += new
                        log(f"  [{loc}] {kw[:20]}: {len(jobs)} scraped, {new} new")
                except Exception as e:
                    log(f"  Error: {e}")
                finally:
                    await page.close()
                    await context.close()

                await asyncio.sleep(random.uniform(2, 5))

        await browser.close()

    log(f"Google Jobs done: {total_scraped} scraped, {total_new} new")
    return total_scraped, total_new


async def scrape_google_page(page, query):
    """Scrape a single Google search for jobs."""
    jobs = []
    try:
        url = f"https://www.google.com/search?q={quote_plus(query)}&ibp=htl;jobs"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # Try to extract job listings
        content = await page.content()

        # Look for job data in page source
        job_pattern = r'"title":"([^"]{5,100})".*?"companyName":"([^"]{1,100})".*?"location":"([^"]{1,100})"'
        matches = re.findall(job_pattern, content)

        for title, company, location in matches[:25]:
            # Try to find URL
            url_pattern = f'{re.escape(title)}.*?"url":"([^"]+)"'
            url_match = re.search(url_pattern, content)
            url = url_match.group(1) if url_match else ""

            jobs.append({
                "title": title,
                "company": company,
                "location": location,
                "url": url,
                "description": "",
            })

        # Also try clicking on "Jobs" tab if exists
        try:
            jobs_tab = await page.query_selector('a[href*="htl;jobs"]')
            if jobs_tab:
                await jobs_tab.click()
                await page.wait_for_timeout(2000)
                # Re-extract after clicking
                content = await page.content()
                matches = re.findall(job_pattern, content)
                for title, company, location in matches[:25]:
                    if not any(j["title"] == title for j in jobs):
                        jobs.append({
                            "title": title,
                            "company": company,
                            "location": location,
                            "url": "",
                            "description": "",
                        })
        except:
            pass

    except Exception as e:
        log(f"  Error scraping Google page: {e}")

    return jobs[:25]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", default="software engineer")
    parser.add_argument("--location", default="remote,United States,New York")
    parser.add_argument("--max-keywords", type=int, default=5)
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keyword.split(",") if k.strip()]
    locations = [l.strip() for l in args.location.split(",") if l.strip()]

    log("Google Jobs Scraper starting")
    asyncio.run(scrape_google_jobs(keywords[:args.max_keywords], locations))


if __name__ == "__main__":
    main()
