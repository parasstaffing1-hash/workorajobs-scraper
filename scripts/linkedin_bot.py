#!/usr/bin/env python3
"""LinkedIn Login Bot — Bypass 60-job guest limit with authenticated scraping.

Uses Playwright to log in to LinkedIn and scrape unlimited job listings.

Usage:
    python -m scripts.linkedin_bot --email you@email.com --password yourpass
    python -m scripts.linkedin_bot --keyword "software engineer" --location "remote"
"""
from __future__ import annotations
import asyncio, hashlib, json, os, random, re, sqlite3, time
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "linkedin_bot.log"
CREDS_FILE = ROOT / "linkedin_creds.json"


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_creds():
    try:
        with open(CREDS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_creds(email, password):
    with open(CREDS_FILE, "w") as f:
        json.dump({"email": email, "password": password}, f)


def make_key(title, company):
    raw = f"{(title or '').lower().strip()}|{(company or '').lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


def save_jobs(jobs, source="linkedin_bot"):
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
                 source, "linkedin", "",
                 j.get("salary", ""), j.get("posted_at", ""))
            )
            if conn.total_changes:
                count += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return count


async def login_linkedin(page, email, password):
    """Log in to LinkedIn."""
    try:
        log("Navigating to LinkedIn login...")
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        # Enter email
        email_field = await page.query_selector('input#username')
        if email_field:
            await email_field.fill(email)
            await page.wait_for_timeout(500)

        # Enter password
        password_field = await page.query_selector('input#password')
        if password_field:
            await password_field.fill(password)
            await page.wait_for_timeout(500)

        # Click login button
        login_btn = await page.query_selector('button[type="submit"]')
        if login_btn:
            await login_btn.click()
            await page.wait_for_timeout(5000)

        # Check if login successful
        current_url = page.url
        if "feed" in current_url or "mynetwork" in current_url:
            log("✅ Login successful!")
            return True
        elif "challenge" in current_url or "checkpoint" in current_url:
            log("⚠️ LinkedIn security challenge detected. Manual verification may be needed.")
            return False
        else:
            log(f"⚠️ Login may have failed. Current URL: {current_url}")
            return False

    except Exception as e:
        log(f"Login error: {e}")
        return False


async def scrape_linkedin_jobs(page, keyword, location="", page_num=1):
    """Scrape LinkedIn jobs while logged in."""
    jobs = []
    try:
        q = quote_plus(keyword)
        loc = quote_plus(location) if location else ""
        url = f"https://www.linkedin.com/jobs/search/?keywords={q}&location={loc}&f_TPR=r604800&start={page_num * 25}"

        log(f"  Searching: {keyword} in {location}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # Extract job cards
        cards = await page.query_selector_all("li.jobs-search-results__list-item, .base-card, .job-search-card")

        for card in cards[:25]:
            try:
                # Title
                title_el = await card.query_selector("a.base-card__full-link, .job-search-card__title a, h3 a")
                title = await title_el.inner_text() if title_el else ""
                href = await title_el.get_attribute("href") if title_el else ""

                # Company
                comp_el = await card.query_selector("a.hidden-nested-link, .job-search-card__subtitle-link, h4")
                company = await comp_el.inner_text() if comp_el else ""

                # Location
                loc_el = await card.query_selector("span.job-search-card__location")
                location_text = await loc_el.inner_text() if loc_el else ""

                # Salary (if visible)
                salary = ""
                salary_el = await card.query_selector(".salary-text, .salary")
                if salary_el:
                    salary = await salary_el.inner_text()

                if title.strip():
                    jobs.append({
                        "title": title.strip(),
                        "company": company.strip(),
                        "location": location_text.strip(),
                        "url": href or "",
                        "description": "",
                        "salary": salary,
                    })

            except:
                continue

        log(f"  Found {len(jobs)} jobs")

    except Exception as e:
        log(f"  Error scraping LinkedIn: {e}")

    return jobs


async def scrape_linkedin_bot(email, password, keywords=None, locations=None, max_pages=3):
    """Main LinkedIn bot scraping loop."""
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
        keywords = ["software engineer", "python developer"]
    if not locations:
        locations = ["United States", "Remote"]

    total_new = 0
    total_scraped = 0

    log(f"LinkedIn Bot: {len(keywords)} keywords, {len(locations)} locations")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # LinkedIn may block headless
            args=["--disable-blink-features=AutomationControlled"]
        )

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

        # Login
        logged_in = await login_linkedin(page, email, password)
        if not logged_in:
            log("Failed to login. Exiting.")
            await browser.close()
            return 0, 0

        # Scrape jobs
        for loc in locations:
            for kw in keywords:
                for pg in range(max_pages):
                    try:
                        jobs = await scrape_linkedin_jobs(page, kw, loc, pg)
                        if jobs:
                            new = save_jobs(jobs)
                            total_scraped += len(jobs)
                            total_new += new
                            log(f"  [{loc}] {kw[:20]} pg{pg+1}: {len(jobs)} scraped, {new} new")
                        else:
                            break
                    except Exception as e:
                        log(f"  Error: {e}")

                    await asyncio.sleep(random.uniform(2, 5))

        await browser.close()

    log(f"LinkedIn Bot done: {total_scraped} scraped, {total_new} new")
    return total_scraped, total_new


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", help="LinkedIn email")
    parser.add_argument("--password", help="LinkedIn password")
    parser.add_argument("--keyword", default="software engineer")
    parser.add_argument("--location", default="United States,Remote")
    parser.add_argument("--pages", type=int, default=3)
    args = parser.parse_args()

    # Load or save credentials
    creds = load_creds()
    email = args.email or creds.get("email", "")
    password = args.password or creds.get("password", "")

    if not email or not password:
        print("LinkedIn credentials required. Use --email and --password")
        return

    if args.email and args.password:
        save_creds(email, password)

    keywords = [k.strip() for k in args.keyword.split(",") if k.strip()]
    locations = [l.strip() for l in args.location.split(",") if l.strip()]

    log("LinkedIn Bot starting")
    asyncio.run(scrape_linkedin_bot(email, password, keywords, locations, args.pages))


if __name__ == "__main__":
    main()
