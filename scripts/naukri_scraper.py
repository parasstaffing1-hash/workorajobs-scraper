#!/usr/bin/env python3
"""Naukri.com Scraper — India's #1 job board with millions of jobs.

Uses Playwright with stealth to bypass anti-bot protection.

Usage:
    python -m scripts.naukri_scraper --keyword "software engineer" --location "Bangalore"
    python -m scripts.naukri_scraper --pages 10
"""
from __future__ import annotations
import asyncio, hashlib, json, os, random, re, sqlite3, time
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "naukri_log.txt"

# Naukri city slugs
NAUKRI_CITIES = {
    "mumbai": "mumbai", "delhi": "delhi", "bangalore": "bangalore",
    "bengaluru": "bangalore", "hyderabad": "hyderabad", "chennai": "chennai",
    "pune": "pune", "kolkata": "kolkata", "ahmedabad": "ahmedabad",
    "jaipur": "jaipur", "lucknow": "lucknow", "noida": "noida",
    "gurgaon": "gurugram-gurgaon", "gurugram": "gurugram-gurgaon",
    "indore": "indore", "coimbatore": "coimbatore", "kochi": "kochi",
    "remote": "india", "india": "india",
}

# Indian tech job keywords
JOB_KEYWORDS = [
    "software engineer", "software developer", "full stack developer",
    "frontend developer", "backend developer", "react developer",
    "python developer", "java developer", "dotnet developer",
    "devops engineer", "cloud engineer", "data engineer",
    "data scientist", "machine learning engineer", "ai engineer",
    "mobile developer", "android developer", "ios developer",
    "flutter developer", "react native developer", "qa engineer",
    "automation tester", "manual tester", "database administrator",
    "network engineer", "system administrator", "security engineer",
    "blockchain developer", "embedded engineer", "firmware engineer",
    "sap consultant", "salesforce developer", "aws architect",
    "python django developer", "java spring boot developer",
    "angular developer", "vue developer", "nodejs developer",
    "php laravel developer", "ruby on rails developer",
    "go developer", "kotlin developer", "swift developer",
    "linux administrator", "network administrator",
    "project manager", "scrum master", "product manager",
    "business analyst", "data analyst", "technical writer",
    "ui ux designer", "graphic designer", "web designer",
    "sql developer", "etl developer", "spark developer",
    "kubernetes engineer", "docker engineer", "terraform engineer",
    "cyber security analyst", "penetration tester", "soc analyst",
    "tech lead", "engineering manager", "cto", "vp engineering",
    "chief technology officer", "architect", "solution architect",
    "cloud architect", "platform engineer", "site reliability engineer",
]


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


def save_jobs(jobs, source="naukri"):
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


async def scrape_naukri_page(page, keyword, city="", page_num=1):
    """Scrape a single Naukri search results page."""
    jobs = []
    try:
        slug = NAUKRI_CITIES.get(city.lower().strip(), "india") if city else "india"
        q = quote_plus(keyword)

        if city and city.lower() != "india":
            url = f"https://www.naukri.com/{q}-jobs-in-{slug}?pageNo={page_num}&experience=0&sort=date"
        else:
            url = f"https://www.naukri.com/{q}-jobs?experience=0&sort=date"

        log(f"  Navigating: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # Try multiple selectors for job cards
        card_selectors = [
            ".srp-grid li",
            "article[data-job-id]",
            ".jobTuple",
            ".styles_jlc__main__VdKEF",
            "li[data-jid]",
            ".styles_jlc__card__MOyEj",
        ]

        cards = []
        for sel in card_selectors:
            cards = await page.query_selector_all(sel)
            if cards:
                break

        if not cards:
            # Try to get job links directly
            links = await page.query_selector_all('a[href*="/jobsearch"]')
            log(f"  Found {len(links)} job links")
            for link in links[:20]:
                try:
                    title = await link.inner_text()
                    href = await link.get_attribute("href")
                    if title and len(title.strip()) > 5:
                        jobs.append({
                            "title": title.strip()[:200],
                            "company": "",
                            "location": city or "India",
                            "url": href or "",
                            "description": "",
                        })
                except:
                    continue
            return jobs

        log(f"  Found {len(cards)} job cards")

        for card in cards[:25]:
            try:
                # Title
                title = ""
                title_selectors = [
                    "a.title", "h2 a", ".title a", "a[data-title]",
                    "a.fullwidth", ".jobTuple .title", "a[title]",
                ]
                for sel in title_selectors:
                    el = await card.query_selector(sel)
                    if el:
                        title = await el.get_attribute("title") or await el.inner_text()
                        if title and len(title.strip()) > 3:
                            title = title.strip()
                            break

                # Company
                company = ""
                comp_selectors = [
                    ".companyName a", ".companyName", "a.companyName",
                    ".company", "h3 a", ".subTitle",
                ]
                for sel in comp_selectors:
                    el = await card.query_selector(sel)
                    if el:
                        company = (await el.inner_text()).strip()
                        if company:
                            break

                # Location
                loc = ""
                loc_selectors = [
                    ".location", ".locationВс", ".locWdth",
                    ".jobCardGenericCons__locations", ".subTitle span",
                ]
                for sel in loc_selectors:
                    el = await card.query_selector(sel)
                    if el:
                        loc = (await el.inner_text()).strip()
                        if loc:
                            break

                # Link
                link_el = await card.query_selector("a[href]")
                url = ""
                if link_el:
                    url = await link_el.get_attribute("href") or ""
                    if not url.startswith("http"):
                        url = "https://www.naukri.com" + url

                # Description
                desc = ""
                desc_selectors = [".jobDescription", ".description", ".jdDesc"]
                for sel in desc_selectors:
                    el = await card.query_selector(sel)
                    if el:
                        desc = (await el.inner_text()).strip()[:1000]
                        break

                if title:
                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": loc or city or "India",
                        "url": url,
                        "description": desc,
                    })

            except Exception as e:
                continue

    except Exception as e:
        log(f"  Error scraping page: {e}")

    return jobs


async def scrape_naukri(keywords=None, cities=None, max_pages=5):
    """Main Naukri scraping loop."""
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
        keywords = JOB_KEYWORDS[:5]
    if not cities:
        cities = ["Bangalore", "Mumbai", "Delhi", "Hyderabad", "Pune"]

    total_new = 0
    total_scraped = 0

    log(f"Naukri Scraper: {len(keywords)} keywords, {len(cities)} cities, {max_pages} pages")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )

        for city in cities:
            for kw in keywords:
                for pg in range(1, max_pages + 1):
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
                        jobs = await scrape_naukri_page(page, kw, city, pg)
                        if jobs:
                            new = save_jobs(jobs)
                            total_scraped += len(jobs)
                            total_new += new
                            log(f"  [{city}] {kw[:20]} pg{pg}: {len(jobs)} scraped, {new} new")
                        else:
                            break  # No more results
                    except Exception as e:
                        log(f"  Error: {e}")
                    finally:
                        await page.close()
                        await context.close()

                    await asyncio.sleep(random.uniform(2, 5))

        await browser.close()

    log(f"Naukri done: {total_scraped} scraped, {total_new} new")
    return total_scraped, total_new


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", default="software engineer")
    parser.add_argument("--location", default="Bangalore,Mumbai,Delhi,Hyderabad,Pune")
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--max-keywords", type=int, default=10)
    args = parser.parse_args()

    keywords = JOB_KEYWORDS[:args.max_keywords]
    cities = [c.strip() for c in args.location.split(",") if c.strip()]

    log("Naukri Scraper starting")
    asyncio.run(scrape_naukri(keywords, cities, args.pages))


if __name__ == "__main__":
    main()
