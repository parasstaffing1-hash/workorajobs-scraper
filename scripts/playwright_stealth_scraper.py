#!/usr/bin/env python3
"""Playwright Stealth Scraper — Anti-detection browser scraping for job boards.

Uses patchright (undetected playwright) + stealth patches to bypass
Cloudflare, DataDome, and other anti-bot systems.

Usage:
    python -m scripts.playwright_stealth_scraper --sources indeed,linkedin --workers 5
"""
from __future__ import annotations
import asyncio, hashlib, json, os, sqlite3, random, time
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "pw_stealth_log.txt"

SOURCES = {
    "indeed": {"base": "https://www.indeed.com", "search": "/jobs?q={q}&l={loc}&start={offset}", "job": "/rc/clk?jk="},
    "linkedin": {"base": "https://www.linkedin.com/jobs/search?keywords={q}&location={loc}&start={offset}"},
    "glassdoor": {"base": "https://www.glassdoor.com", "search": "/Job/jobs.htm?sc.keyword={q}&locT=&locId=&locKeyword={loc}"},
    "bayt": {"base": "https://www.bayt.com", "search": "/en/{loc}/jobs/{q}/?page={page}"},
    "naukri": {"base": "https://www.naukri.com", "search": "/{q}-jobs-in-{loc}?page={page}"},
}

JOB_KEYWORDS = [
    "software engineer", "software developer", "backend developer", "frontend developer",
    "full stack developer", "react developer", "python developer", "java developer",
    "devops engineer", "data engineer", "data scientist", "machine learning engineer",
    "mobile developer", "android developer", "ios developer", "flutter developer",
    "cloud engineer", "platform engineer", "site reliability engineer", "SRE",
    "QA engineer", "test automation", "embedded engineer", "firmware engineer",
    "blockchain developer", "game developer", "AI engineer", "ML engineer",
    "product engineer", "security engineer", "network engineer", "database administrator",
    "web developer", "typescript developer", "angular developer", "vue developer",
    "kubernetes docker", "aws cloud engineer", "azure cloud engineer", "gcp engineer",
    "terraform engineer", "ci cd engineer", "infrastructure engineer", "ruby on rails",
    "php developer", "kotlin developer", "swift developer", "golang developer",
    "rust developer", "c++ developer", "c# developer", "nodejs developer",
]

LOCATIONS = [
    "", "United States", "New York", "San Francisco", "Austin",
    "Seattle", "Boston", "Chicago", "Los Angeles", "Denver",
    "Remote", "London", "Berlin", "Toronto", "Sydney",
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
                 source, "stealth", "", j.get("salary", ""),
                 j.get("posted_at", ""))
            )
            if conn.total_changes:
                count += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return count


async def scrape_indeed(page, keyword, location):
    """Scrape Indeed using stealth browser."""
    jobs = []
    try:
        q = quote_plus(keyword)
        loc = quote_plus(location) if location else ""
        url = f"https://www.indeed.com/jobs?q={q}&l={loc}&fromage=7&start=0"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        cards = await page.query_selector_all("div.job_seen_beacon, div.jobsearch-ResultsList div.result, div.jobsearch-SerpJobCard")
        if not cards:
            cards = await page.query_selector_all("[data-testid='job-card'], .tapItem")

        for card in cards[:20]:
            try:
                title_el = await card.query_selector("h2.jobTitle a, a.jcs-JobTitle, .jobTitle a")
                comp_el = await card.query_selector("span[data-testid='company-name'], .companyName, .company_name")
                loc_el = await card.query_selector("div[data-testid='text-location'], .companyLocation")
                title = await title_el.inner_text() if title_el else ""
                company = await comp_el.inner_text() if comp_el else ""
                loc_text = await loc_el.inner_text() if loc_el else ""
                href = await title_el.get_attribute("href") if title_el else ""
                if href and not href.startswith("http"):
                    href = "https://www.indeed.com" + href
                if title.strip():
                    jobs.append({"title": title.strip(), "company": company.strip(), "location": loc_text.strip(), "url": href or ""})
            except:
                continue
    except Exception as e:
        log(f"  Indeed error: {e}")
    return jobs


async def scrape_linkedin(page, keyword, location):
    """Scrape LinkedIn jobs (public, no login required)."""
    jobs = []
    try:
        q = quote_plus(keyword)
        loc = quote_plus(location) if location else ""
        url = f"https://www.linkedin.com/jobs/search/?keywords={q}&location={loc}&f_TPR=r604800"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        cards = await page.query_selector_all("li.jobs-search-results__list-item")
        if not cards:
            cards = await page.query_selector_all(".base-card, .job-search-card")

        for card in cards[:25]:
            try:
                title_el = await card.query_selector("a.base-card__full-link, .job-search-card__title a, h3 a")
                comp_el = await card.query_selector("a.hidden-nested-link, .job-search-card__subtitle-link")
                loc_el = await card.query_selector("span.job-search-card__location")
                title = await title_el.inner_text() if title_el else ""
                company = await comp_el.inner_text() if comp_el else ""
                loc_text = await loc_el.inner_text() if loc_el else ""
                href = await title_el.get_attribute("href") if title_el else ""
                if title.strip():
                    jobs.append({"title": title.strip(), "company": company.strip(), "location": loc_text.strip(), "url": href or ""})
            except:
                continue
    except Exception as e:
        log(f"  LinkedIn error: {e}")
    return jobs


async def scrape_glassdoor(page, keyword, location):
    """Scrape Glassdoor jobs."""
    jobs = []
    try:
        q = quote_plus(keyword)
        loc = quote_plus(location) if location else ""
        url = f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q}&locT=&locId=&locKeyword={loc}&fromAge=7"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        cards = await page.query_selector_all("[data-test='job-card'], li.JobsListstyle__ListItems BorderslessBorder")
        if not cards:
            cards = await page.query_selector_all(".job-search-card, .job-card")

        for card in cards[:20]:
            try:
                title_el = await card.query_selector("a[data-test='job-title'], .jobTitle a, h2 a")
                comp_el = await card.query_selector("a[data-test='employer-short-name'], .companyName")
                title = await title_el.inner_text() if title_el else ""
                company = await comp_el.inner_text() if comp_el else ""
                href = await title_el.get_attribute("href") if title_el else ""
                if href and not href.startswith("http"):
                    href = "https://www.glassdoor.com" + href
                if title.strip():
                    jobs.append({"title": title.strip(), "company": company.strip(), "location": location, "url": href or ""})
            except:
                continue
    except Exception as e:
        log(f"  Glassdoor error: {e}")
    return jobs


SCRAPERS = {
    "indeed": scrape_indeed,
    "linkedin": scrape_linkedin,
    "glassdoor": scrape_glassdoor,
}


async def run_stealth(sources=None, keywords=None, locations=None, max_items=1000, workers=3):
    """Main stealth scraping loop."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("ERROR: playwright not installed. pip install playwright")
        return 0, 0

    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
        use_stealth = True
    except:
        use_stealth = False

    if not sources:
        sources = list(SCRAPERS.keys())
    sources = [s for s in sources if s in SCRAPERS]
    if not sources:
        sources = ["indeed"]
    if not keywords:
        keywords = JOB_KEYWORDS[:10]
    if not locations:
        locations = LOCATIONS[:5]

    total_new = 0
    total_scraped = 0

    log(f"Playwright Stealth: sources={sources} kw={len(keywords)} locs={len(locations)} workers={workers}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ]
        )

        for source in sources:
            scraper_fn = SCRAPERS[source]
            for kw in keywords:
                for loc in locations[:3]:
                    if total_scraped >= max_items:
                        break

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
                        jobs = await scraper_fn(page, kw, loc)
                        if jobs:
                            new = save_jobs(jobs, f"pw:{source}")
                            total_scraped += len(jobs)
                            total_new += new
                            log(f"  [{source}] {kw[:20]}@{loc[:10]}: {len(jobs)} scraped, {new} new")
                    except Exception as e:
                        log(f"  [{source}] Error: {e}")
                    finally:
                        await page.close()
                        await context.close()

                    await asyncio.sleep(random.uniform(2, 5))

                    if total_scraped >= max_items:
                        break
                if total_scraped >= max_items:
                    break
            if total_scraped >= max_items:
                break

        await browser.close()

    log(f"Playwright Stealth done: {total_scraped} scraped, {total_new} new")
    return total_scraped, total_new


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="indeed,linkedin,glassdoor")
    parser.add_argument("--keywords", default="software engineer,python developer,react developer")
    parser.add_argument("--locations", default="remote,New York,San Francisco")
    parser.add_argument("--max", type=int, default=500)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    locations = [l.strip() for l in args.locations.split(",") if l.strip()]

    log("Playwright Stealth Scraper starting")
    asyncio.run(run_stealth(sources, keywords, locations, args.max, args.workers))


if __name__ == "__main__":
    main()
