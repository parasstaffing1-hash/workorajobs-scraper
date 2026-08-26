#!/usr/bin/env python3
"""JobSpy Scraper — One-liner scraping from LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter.

Uses the python-jobspy library for a unified API across major job boards.

Usage:
    python -m scripts.jobspy_scraper --keyword "software engineer" --location "remote"
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "jobspy_log.txt"

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
    "kubernetes engineer", "aws engineer", "azure engineer", "gcp engineer",
    "terraform engineer", "ruby developer", "php developer", "kotlin developer",
    "swift developer", "golang developer", "rust developer", "c developer",
    "c# developer", "node.js developer", "spark developer", "etl developer",
]

LOCATIONS = [
    "", "United States", "New York", "San Francisco", "Austin",
    "Seattle", "Boston", "Chicago", "Los Angeles", "Denver",
    "Remote", "London", "Berlin", "Toronto", "Sydney",
    "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Dublin",
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


def save_jobs(jobs_list, source_name):
    conn = get_db()
    count = 0
    for j in jobs_list:
        title = getattr(j, "title", "") or ""
        company = getattr(j, "company", {}) if hasattr(j, "company") else {}
        if isinstance(company, dict):
            company_name = company.get("name", "") or ""
        else:
            company_name = str(company)
        location_obj = getattr(j, "location", {}) if hasattr(j, "location") else {}
        if isinstance(location_obj, dict):
            location = location_obj.get("city", "") + ", " + location_obj.get("state", "")
        else:
            location = str(location_obj)
        url = getattr(j, "job_url", "") or getattr(j, "url", "") or ""
        description = getattr(j, "description", "") or ""
        salary = getattr(j, "salary_min", "") or ""
        posted = getattr(j, "date_posted", "") or ""

        key = make_key(title, company_name)
        try:
            conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (dedupe_key, title, company, location, url, description, tags,
                    source, source_kind, external_id, salary, posted_at,
                    first_seen_at, last_seen_at, is_active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'),1)""",
                (key, title, company_name, location, url,
                 description[:2000] if description else "",
                 json.dumps([]), source_name, "jobspy", "",
                 str(salary), posted)
            )
            if conn.total_changes:
                count += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return count


def run_jobspy(sites=None, keywords=None, locations=None, max_per_search=50):
    """Main JobSpy scraping loop."""
    from jobspy import scrape_jobs

    if not sites:
        sites = ["linkedin", "indeed", "glassdoor", "zip_recruiter", "google"]
    if not keywords:
        keywords = JOB_KEYWORDS[:15]
    if not locations:
        locations = LOCATIONS[:10]

    total_new = 0
    total_scraped = 0
    log(f"JobSpy: sites={sites} keywords={len(keywords)} locations={len(locations)}")

    for site in sites:
        for kw in keywords:
            for loc in locations[:5]:
                try:
                    results = scrape_jobs(
                        site_name=[site],
                        search_term=kw,
                        location=loc,
                        results_wanted=min(max_per_search, 50),
                        hours_old=168,  # Last 7 days
                    )

                    job_list = []
                    if hasattr(results, 'jobs') and results.jobs:
                        job_list = results.jobs
                    elif isinstance(results, dict) and 'jobs' in results:
                        job_list = results['jobs']

                    if job_list:
                        new = save_jobs(job_list, f"jobspy:{site}")
                        total_scraped += len(job_list)
                        total_new += new
                        log(f"  [{site}] {kw[:20]}@{loc[:10]}: {len(job_list)} scraped, {new} new")

                    time.sleep(1)  # Rate limit

                except Exception as e:
                    log(f"  [{site}] Error: {str(e)[:100]}")

    log(f"JobSpy done: {total_scraped} scraped, {total_new} new")
    return total_scraped, total_new


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", default="linkedin,indeed,glassdoor,zip_recruiter,google")
    parser.add_argument("--keywords", default="software engineer,python developer,react developer")
    parser.add_argument("--locations", default="remote,United States,New York")
    parser.add_argument("--max", type=int, default=50, help="Max results per search")
    args = parser.parse_args()

    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    locations = [l.strip() for l in args.locations.split(",") if l.strip()]

    log("JobSpy Scraper starting")
    run_jobspy(sites, keywords, locations, args.max)


if __name__ == "__main__":
    main()
