#!/usr/bin/env python3
"""Scrape career pages from thousands of company websites.

Strategy:
1. Take a list of company names
2. Try common career page URL patterns for each
3. Use Playwright to render and extract job listings
4. Detect which ATS they use from page content
5. Store discovered jobs in the database

Usage:
    python scripts/scrape_career_pages.py --limit 5000 --workers 5
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Career page URL patterns to try for each company
CAREER_PATTERNS = [
    "https://{slug}.greenhouse.io",
    "https://jobs.lever.co/{slug}",
    "https://boards.greenhouse.io/{slug}",
    "https://jobs.ashbyhq.com/{slug}",
    "https://careers.smartrecruiters.com/{slug}",
    "https://{slug}.workable.com",
    "https://{slug}.bamboohr.com/careers",
    "https://{slug}.teamtailor.com",
    "https://{slug}.recruitee.com",
    "https://careers.{slug}.com",
    "https://www.{slug}.com/careers",
    "https://www.{slug}.com/jobs",
    "https://{slug}.com/careers",
    "https://{slug}.com/jobs",
    "https://jobs.{slug}.com",
    "https://work.{slug}.com",
    "https://hire.{slug}.com",
    "https://talent.{slug}.com",
    "https://jobs.{slug}.io",
    "https://{slug}.io/careers",
    "https://{slug}.ai/careers",
]

# Greenhouse JSON API pattern (most reliable)
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_API = "https://api.lever.co/v0/postings/{slug}"
ASHBY_API = "https://jobs.ashbyhq.com/api/non-user-graphql"


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = slug.replace(" ", "").replace(".", "").replace("'", "")
    slug = slug.replace("&", "and").replace(",", "")
    slug = re.sub(r"[^a-z0-9]", "", slug)
    return slug


def _probe_greenhouse_api(client: httpx.Client, slug: str) -> list[dict] | None:
    """Probe Greenhouse API directly — most reliable method."""
    try:
        resp = client.get(GREENHOUSE_API.format(slug=slug), timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobs", [])
            if jobs:
                return [
                    {
                        "title": j.get("title", ""),
                        "url": j.get("absolute_url", ""),
                        "location": j.get("location", {}).get("name", "") if isinstance(j.get("location"), dict) else "",
                        "company": slug,
                        "source": f"greenhouse:{slug}",
                        "source_kind": "greenhouse",
                        "external_id": str(j.get("id", "")),
                    }
                    for j in jobs[:200]
                ]
    except Exception:
        pass
    return None


def _probe_lever_api(client: httpx.Client, slug: str) -> list[dict] | None:
    """Probe Lever API directly."""
    try:
        resp = client.get(LEVER_API.format(slug=slug), timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                return [
                    {
                        "title": j.get("text", ""),
                        "url": j.get("hostedUrl", ""),
                        "location": j.get("categories", {}).get("location", "") if isinstance(j.get("categories"), dict) else "",
                        "company": slug,
                        "source": f"lever:{slug}",
                        "source_kind": "lever",
                        "external_id": j.get("id", ""),
                    }
                    for j in data[:200]
                ]
    except Exception:
        pass
    return None


def _probe_ashby_api(client: httpx.Client, slug: str) -> list[dict] | None:
    """Probe Ashby API directly."""
    try:
        resp = client.post(
            ASHBY_API,
            json={
                "operationName": "ApiJobBoardWithTeams",
                "variables": {"organizationHostedJobsPageName": slug},
                "query": "query ApiJobBoardWithTeams($o: String!) { jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $o) { id name jobPostings { id title locationName teamName employmentType } } }",
            },
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            board = (data.get("data") or {}).get("jobBoard")
            if board:
                postings = board.get("jobPostings", [])
                if postings:
                    return [
                        {
                            "title": p.get("title", ""),
                            "url": f"https://jobs.ashbyhq.com/{slug}",
                            "location": p.get("locationName", ""),
                            "company": board.get("name", slug),
                            "source": f"ashby:{slug}",
                            "source_kind": "ashby",
                            "external_id": p.get("id", ""),
                        }
                        for p in postings[:200]
                    ]
    except Exception:
        pass
    return None


def _probe_smartrecruiters_api(client: httpx.Client, slug: str) -> list[dict] | None:
    """Probe SmartRecruiters API."""
    try:
        resp = client.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("content", [])
            if content:
                return [
                    {
                        "title": j.get("name", ""),
                        "url": j.get("ref", ""),
                        "location": j.get("location", {}).get("city", "") if isinstance(j.get("location"), dict) else "",
                        "company": j.get("company", {}).get("name", slug) if isinstance(j.get("company"), dict) else slug,
                        "source": f"smartrecruiters:{slug}",
                        "source_kind": "smartrecruiters",
                        "external_id": j.get("id", ""),
                    }
                    for j in content[:200]
                ]
    except Exception:
        pass
    return None


def _probe_workday_api(client: httpx.Client, slug: str) -> list[dict] | None:
    """Probe Workday API."""
    try:
        resp = client.post(
            f"https://wd5.myworkdayjobs.com/wday/cxs/{slug}/External",
            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
            headers={"Content-Type": "application/json"},
            timeout=8,
        )
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobPostings", [])
            if jobs:
                return [
                    {
                        "title": j.get("title", ""),
                        "url": j.get("externalPath", ""),
                        "location": j.get("locationsText", ""),
                        "company": slug,
                        "source": f"workday:{slug}",
                        "source_kind": "workday",
                        "external_id": j.get("bulletFields", [""])[0] if j.get("bulletFields") else "",
                    }
                    for j in jobs[:200]
                ]
    except Exception:
        pass
    return None


def _probe_icims_api(client: httpx.Client, slug: str) -> list[dict] | None:
    """Probe iCIMS career page."""
    try:
        resp = client.get(
            f"https://careers-{slug}.icims.com/jobs/search?ss=1&searchKeyword=",
            timeout=8,
            follow_redirects=True,
        )
        if resp.status_code == 200 and "job" in resp.text.lower():
            return [{"title": "detected", "url": resp.url, "company": slug,
                     "source": f"icims:{slug}", "source_kind": "icims"}]
    except Exception:
        pass
    return None


API_PROBES = [
    _probe_greenhouse_api,
    _probe_lever_api,
    _probe_ashby_api,
    _probe_smartrecruiters_api,
    _probe_workday_api,
    _probe_icims_api,
]


def probe_company(client: httpx.Client, name: str) -> list[dict]:
    """Try all ATS API probes for a company."""
    slug = _slugify(name)

    # Try each ATS API
    for probe in API_PROBES:
        result = probe(client, slug)
        if result:
            return result

    # Try alternative slugs
    alt_slugs = [
        name.lower().replace(" ", "-"),
        name.lower().replace(" ", ""),
        name.split()[0].lower() if " " in name else "",
    ]
    for alt in alt_slugs:
        if not alt or alt == slug:
            continue
        for probe in API_PROBES:
            result = probe(client, alt)
            if result:
                return result

    return []


def main():
    parser = argparse.ArgumentParser(description="Scrape career pages from 10K+ companies")
    parser.add_argument("--file", default="data/company_slugs.txt", help="Company names file")
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--db", default="jobs.db")
    args = parser.parse_args()

    names = [l.strip() for l in Path(args.file).read_text().splitlines() if l.strip()]
    names = names[:args.limit]
    print(f"Probing {len(names)} companies for ATS career pages...")

    all_jobs = []
    found_companies = 0
    done = 0

    with httpx.Client(
        headers={"User-Agent": "JobCollector/1.0 (career-page-probe)"},
        follow_redirects=True,
        timeout=10,
    ) as client:
        def _check(name: str) -> tuple[str, list[dict]]:
            return name, probe_company(client, name)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_check, n): n for n in names}

            for fut in as_completed(futures):
                done += 1
                name = futures[fut]
                try:
                    _, jobs = fut.result()
                    if jobs:
                        found_companies += 1
                        all_jobs.extend(jobs)
                        print(f"  [OK] {name}: {len(jobs)} jobs")
                except Exception:
                    pass

                if done % 100 == 0:
                    print(f"  [{done}/{len(names)}] found {found_companies} companies, {len(all_jobs)} jobs", flush=True)

    print(f"\n[DONE] {found_companies} companies with active career pages")
    print(f"[DONE] {len(all_jobs)} total jobs discovered")

    # Persist to SQLite
    if all_jobs:
        conn = sqlite3.connect(args.db)
        new = 0
        for job in all_jobs:
            if not job.get("title") or not job.get("url"):
                continue
            try:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO jobs
                       (dedupe_key, title, company, location, description, url, source,
                        source_kind, external_id, posted_at, salary, tags,
                        first_seen_at, last_seen_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job["url"], job["title"], job.get("company", ""),
                        job.get("location", ""), job.get("description", ""),
                        job["url"], job.get("source", ""), job.get("source_kind", ""),
                        job.get("external_id", ""),
                        datetime.now(timezone.utc).isoformat(),
                        job.get("salary", ""), job.get("tags", ""),
                        datetime.now(timezone.utc).isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                if cur.rowcount > 0:
                    new += 1
            except Exception:
                continue
        conn.commit()
        conn.close()
        print(f"[DB] {new} new jobs inserted into {args.db}")

    # Save raw results
    Path("data").mkdir(exist_ok=True)
    import json
    Path("data/career_probe_results.json").write_text(json.dumps(all_jobs[:1000], indent=2))
    print(f"[SAVE] Raw results saved to data/career_probe_results.json")


if __name__ == "__main__":
    main()
