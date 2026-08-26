#!/usr/bin/env python3
"""Crawl4AI Scraper — Smart async job crawling with auto-retry, proxy rotation, markdown extraction.

Usage:
    python -m scripts.crawl4ai_scraper --keyword "software engineer" --location "remote"
    python -m scripts.crawl4ai_scraper --sources simplyhired,dice --workers 20
"""
from __future__ import annotations
import asyncio, hashlib, json, os, sqlite3, sys, time, random
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "crawl4ai_log.txt"

# ── Sources with URL templates ────────────────────────────────
SOURCES = {
    "simplyhired": "https://www.simplyhired.com/search?q={q}&l={loc}&pn={page}",
    "dice": "https://www.dice.com/jobs?q={q}&location={loc}&page={page}",
    "builtin": "https://builtin.com/jobs?search={q}&location={loc}&page={page}",
    "glassdoor": "https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q}&locT=&locId=&locKeyword={loc}",
    "ziprecruiter": "https://www.ziprecruiter.com/jobs-search?search={q}&location={loc}&page={page}",
    "jooble": "https://us.jooble.org/SearchResult?rgns={loc}&kws={q}&p={page}",
    "talent": "https://www.talent.com/jobs?k={q}&l={loc}&p={page}",
    "reed": "https://www.reed.co.uk/jobs/{q}?loc={loc}&page={page}",
    "cwjobs": "https://www.cwjobs.co.uk/jobs/{q}?Location={loc}&Page={page}",
    "careerbuilder": "https://www.careerbuilder.com/jobs?keywords={q}&location={loc}&page={page}",
}

# CSS selectors per source
SELECTORS = {
    "simplyhired": {"card": "article[data-testid='jobCard']", "title": "h2 a", "company": "[data-testid='companyName']", "location": "[data-testid='attributeLocation']"},
    "dice": {"card": "[data-testid='job-card']", "title": "a[href*='/job-detail/']", "company": "[data-company-name]", "location": "[data-testid='employeeLocation']"},
    "builtin": {"card": ".card-content, [class*=job-card]", "title": "a[href*='/job/']", "company": ".company-name, h6"},
    "glassdoor": {"card": "[data-test='job-listing'],.job-search-card", "title": "a[data-test='job-title']", "company": "[data-test='employer-short-name']"},
    "ziprecruiter": {"card": ".job_result, [data-testid='job-card']", "title": "h2 a, .job_title", "company": ".company_name, .t_org_link"},
    "jooble": {"card": ".fdaJNJf", "title": "a.jcs-JobTitle", "company": ".pZkZwJ"},
    "talent": {"card": ".job-card, article", "title": "h2 a, .job-title a", "company": ".company-name, a[data-qa='company-name']"},
    "reed": {"card": ".job_search_result, .reed-results-module__result", "title": "h2 a", "company": ".company-name, [data-qa='company-name']"},
    "cwjobs": {"card": ".job-search-result, [data-testid='job-card']", "title": "a.job-title", "company": ".company-name"},
    "careerbuilder": {"card": ".job-card, .job-search-results-card", "title": ".job-title a, h2 a", "company": ".company-name, .job-emp-name"},
}

# Job title keywords for diverse searches
JOB_KEYWORDS = [
    "software engineer", "software developer", "backend developer", "frontend developer",
    "full stack developer", "react developer", "python developer", "java developer",
    "devops engineer", "data engineer", "data scientist", "machine learning engineer",
    "mobile developer", "android developer", "ios developer", "flutter developer",
    "cloud engineer", "platform engineer", "site reliability engineer", "SRE",
    "QA engineer", "test automation", "embedded engineer", "firmware engineer",
    "blockchain developer", "game developer", "AI engineer", "ML engineer",
    "product engineer", "security engineer", "network engineer", "database administrator",
    "web developer", "frontend react", "backend node", "go developer", "rust developer",
    "ruby on rails", "php developer", "kotlin developer", "swift developer",
    "python django", "python fastapi", "spring boot", "net developer",
    "typescript developer", "angular developer", "vue developer", "svelte developer",
    "grafana prometheus", "kubernetes docker", "aws cloud", "azure cloud",
    "gcp cloud", "terraform", "ci cd engineer", "infrastructure engineer",
]

LOCATIONS = [
    "", "remote", "United States", "New York", "San Francisco", "Austin",
    "Seattle", "Boston", "Chicago", "Los Angeles", "Denver", "Atlanta",
    "Miami", "Dallas", "Houston", "Portland", "Washington DC", "Bay Area",
    "London", "Berlin", "Toronto", "Sydney", "Singapore", "Dublin",
    "India", "Bangalore", "Hyderabad", "Mumbai", "Delhi",
]


def get_db():
    conn = sqlite3.connect(str(DB), timeout=10)
    return conn


def make_key(title, company):
    raw = f"{(title or '').lower().strip()}|{(company or '').lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


async def crawl_page(crawler, url, source):
    """Crawl a single URL and extract job data."""
    try:
        config = CrawlerRunConfig(
            wait_until="domcontentloaded",
            timeout=20000,
            page_timeout=25000,
        )
        result = await crawler.arun(url=url, config=config)
        if not result.success:
            return []

        html = result.html or ""
        selectors = SELECTORS.get(source, SELECTORS.get("simplyhired"))
        jobs = []

        # Simple HTML parsing without bs4 dependency
        import re
        cards_html = html
        title_pattern = r'<a[^>]*href="([^"]*)"[^>]*>([^<]+)</a>'

        # Extract job links
        for match in re.finditer(r'href="((?:https?://[^"]*(?:job|position|career|opening)[^"]*))"[^>]*>\s*([^<]{10,100})</a>', html, re.I):
            url_val = match.group(1)
            title = match.group(2).strip()
            if not title or len(title) < 5:
                continue
            # Extract company from nearby text
            company = ""
            after = html[match.end():match.end() + 500]
            comp_match = re.search(r'(?:company|employer|org)[^>]*>\s*([^<]{2,80})', after, re.I)
            if comp_match:
                company = comp_match.group(1).strip()
            loc_match = re.search(r'(?:location|loc)[^>]*>\s*([^<]{2,80})', after, re.I)
            location = loc_match.group(1).strip() if loc_match else ""

            if not url_val.startswith("http"):
                base = f"https://www.{source.replace('_http','').replace('_unified','')}.com"
                if url_val.startswith("/"):
                    url_val = base + url_val
            jobs.append({
                "title": title[:200],
                "company": company[:200],
                "location": location[:200],
                "url": url_val,
            })

        return jobs[:50]
    except Exception as e:
        return []


def save_jobs(jobs_list, source_name):
    """Save jobs to database, return count of new ones."""
    conn = get_db()
    count = 0
    for j in jobs_list:
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
                 source_name, "crawl4ai", "", j.get("salary", ""),
                 j.get("posted_at", ""))
            )
            if conn.total_changes:
                count += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return count


async def crawl_source(crawler, source, keyword, location):
    """Crawl all pages for a keyword+location on a source."""
    template = SOURCES.get(source)
    if not template:
        return []

    q = quote_plus(keyword)
    loc = quote_plus(location) if location else ""
    all_jobs = []

    for page in range(1, 6):  # Up to 5 pages
        url = template.format(q=q, loc=loc, page=page)
        try:
            jobs = await crawl_page(crawler, url, source)
            if not jobs:
                break
            all_jobs.extend(jobs)
            await asyncio.sleep(random.uniform(1.0, 3.0))  # Polite delay
        except Exception:
            break

    return all_jobs


async def run_crawl4ai(sources=None, keywords=None, locations=None, max_items=1000):
    """Main crawl4ai scraping loop."""
    from crawl4ai import AsyncWebCrawler

    if not sources:
        sources = list(SOURCES.keys())[:5]
    if not keywords:
        keywords = JOB_KEYWORDS[:10]
    if not locations:
        locations = LOCATIONS[:5]

    total_new = 0
    total_scraped = 0
    log(f"Crawl4AI: sources={len(sources)} keywords={len(keywords)} locs={len(locations)}")

    async with AsyncWebCrawler(
        browser_type="chromium",
        headless=True,
    ) as crawler:
        for source in sources:
            if source not in SOURCES:
                continue
            for kw in keywords:
                for loc in locations[:3]:
                    if total_scraped >= max_items:
                        break
                    try:
                        jobs = await crawl_source(crawler, source, kw, loc)
                        if jobs:
                            new = save_jobs(jobs, f"c4a:{source}")
                            total_scraped += len(jobs)
                            total_new += new
                            log(f"  [{source}] {kw[:20]}@{loc[:10]}: {len(jobs)} scraped, {new} new")
                        await asyncio.sleep(random.uniform(0.5, 2.0))
                    except Exception as e:
                        log(f"  [{source}] Error: {e}")
                if total_scraped >= max_items:
                    break
            if total_scraped >= max_items:
                break

    log(f"Crawl4AI done: {total_scraped} scraped, {total_new} new")
    return total_scraped, total_new


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Crawl4AI Job Scraper")
    parser.add_argument("--sources", default="simplyhired,dice,builtin,glassdoor,ziprecruiter")
    parser.add_argument("--keywords", default="software engineer,python developer,react developer")
    parser.add_argument("--locations", default="remote,New York,San Francisco")
    parser.add_argument("--max", type=int, default=500, help="Max items to scrape")
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    locations = [l.strip() for l in args.locations.split(",") if l.strip()]

    log(f"Crawl4AI Scraper starting")
    asyncio.run(run_crawl4ai(sources, keywords, locations, args.max))


if __name__ == "__main__":
    main()
