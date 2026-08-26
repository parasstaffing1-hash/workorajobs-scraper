#!/usr/bin/env python3
"""Stress test: massive keyword × location matrix to collect 1M jobs from the web.

Strategy:
  1. JobSpy pagination loop (LinkedIn+Indeed) — 200-500 unique per search
  2. Surf sources (apna, Shine, LinkedIn, Indeed India) — 200-300 per search  
  3. Adzuna API — thousands per country
  4. All ATS APIs — already collecting thousands per company

Target: 1,000,000 fresh jobs (posted in last 7 days)
Each keyword×location combo is a separate search that runs until duplicates.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP_FILE = ROOT / ".freebuff" / "stress_checkpoint.json"
LOG_FILE = ROOT / ".freebuff" / "stress_test.log"

# ════════════════════════════════════════════════════════════════
# KEYWORD MATRIX — all software engineering titles + tech roles
# ════════════════════════════════════════════════════════════════
KEYWORDS = [
    # Core SWE
    "Software Engineer", "Software Developer", "SDE", "SWE",
    "Application Developer", "Application Engineer",
    "Product Engineer", "Software Architect",
    # Entry-level / graduate
    "Junior Software Engineer", "Associate Software Engineer",
    "Graduate Engineer", "Trainee Engineer", "Fresher Software Engineer",
    # Backend
    "Backend Engineer", "Back-End Developer", "Server Side Engineer",
    "API Engineer", "Distributed Systems Engineer",
    "Microservices Engineer", "Platform Engineer",
    # Frontend
    "Frontend Engineer", "Front-End Developer", "UI Engineer",
    "Web Application Developer", "Client Side Engineer",
    # Full-stack
    "Full Stack Developer", "Full Stack Engineer", "Web Developer",
    # Mobile
    "Mobile Engineer", "Android Developer", "iOS Developer",
    "React Native Developer", "Flutter Developer",
    # Cloud / infra
    "Cloud Engineer", "Cloud Developer", "Infrastructure Engineer",
    "Cloud Infrastructure Engineer", "Platform Engineer",
    # DevOps / SRE
    "DevOps Engineer", "Site Reliability Engineer", "SRE",
    "Build Engineer", "Release Engineer", "CI/CD Engineer",
    "Automation Engineer",
    # Systems
    "Systems Engineer", "Systems Programmer", "Operating Systems Engineer",
    "Kernel Engineer", "Embedded Systems Engineer",
    # Embedded / firmware
    "Embedded Engineer", "Firmware Engineer", "RTOS Engineer",
    "Device Driver Engineer",
    # AI / ML
    "Machine Learning Engineer", "ML Engineer", "AI Engineer",
    "Deep Learning Engineer", "AI Platform Engineer",
    "Inference Engineer", "Generative AI Engineer", "LLM Engineer",
    "NLP Engineer", "Computer Vision Engineer",
    # Data
    "Data Engineer", "Data Platform Engineer", "Big Data Engineer",
    "Database Engineer", "ETL Engineer", "Data Pipeline Engineer",
    "Data Scientist", "Data Analyst",
    # Security
    "Security Engineer", "Application Security Engineer",
    "Cloud Security Engineer", "Cybersecurity Engineer",
    "Security Architect",
    # Blockchain
    "Blockchain Engineer", "Smart Contract Developer",
    "Web3 Developer", "Protocol Engineer",
    # Game
    "Game Engineer", "Gameplay Programmer", "Game Developer",
    "Unity Developer", "Unreal Engine Developer",
    # Graphics
    "Graphics Engineer", "Rendering Engineer", "GPU Engineer",
    # Video / media
    "Video Engineer", "Streaming Engineer", "Media Engineer",
    "Audio Engineer",
    # Robotics / autonomous
    "Robotics Engineer", "Robotics Software Engineer",
    "Autonomous Systems Engineer",
    # AR/VR
    "AR Engineer", "VR Engineer", "XR Engineer",
    "Spatial Computing Engineer",
    # QA
    "SDET", "Test Automation Engineer", "QA Automation Engineer",
    "Software Test Engineer",
    # Dev tools
    "Compiler Engineer", "Language Engineer", "Runtime Engineer",
    # DB / storage
    "Storage Engineer", "File Systems Engineer", "Search Engineer",
    # Networking
    "Network Engineer", "Network Software Engineer",
    # Fintech
    "Fintech Engineer", "Payments Engineer", "Quantitative Engineer",
    "Trading Systems Engineer",
    # Enterprise
    "Enterprise Application Engineer", "ERP Developer",
    "SaaS Engineer", "CRM Developer",
    # API
    "GraphQL Engineer", "Integration Engineer", "Middleware Engineer",
    # Architecture
    "Software Architect", "Solutions Architect", "Technical Architect",
    "Platform Architect", "Cloud Architect",
    # Management
    "Engineering Manager", "Technical Lead", "Tech Lead",
    "Staff Engineer", "Principal Engineer", "Distinguished Engineer",
    # Generic high-volume
    "Python Developer", "Java Developer", "JavaScript Developer",
    "TypeScript Developer", "Go Developer", "Rust Developer",
    "C++ Developer", "Ruby Developer", "PHP Developer",
    "Kotlin Developer", "Swift Developer", "Scala Developer",
    "React Developer", "Angular Developer", "Vue Developer",
    "Node.js Developer", "Django Developer", "Spring Boot Developer",
    ".NET Developer", "Laravel Developer", "Rails Developer",
    # Non-tech but common
    "Project Manager", "Product Manager", "Scrum Master",
    "Business Analyst", "System Administrator",
    "Network Administrator", "IT Manager",
    "UX Designer", "UI/UX Designer", "Graphic Designer",
    "Technical Writer",
]

# Indian metros + global cities
LOCATIONS_IN = [
    "Delhi", "Bengaluru", "Hyderabad", "Pune", "Mumbai",
    "Chennai", "Kolkata", "Noida", "Gurgaon", "Ahmedabad",
    "Jaipur", "Lucknow", "Kochi", "Indore", "Bhopal",
    "Coimbatore", "Visakhapatnam", "Chandigarh",
]

LOCATIONS_GLOBAL = [
    "",  # worldwide
    "New York", "San Francisco", "Seattle", "Austin", "Boston",
    "Chicago", "Los Angeles", "Denver", "Atlanta", "Miami",
    "London", "Berlin", "Toronto", "Singapore", "Dubai",
    "Amsterdam", "Paris", "Sydney", "Tokyo", "Remote",
]

LOCATIONS_ALL = LOCATIONS_IN + LOCATIONS_GLOBAL


# ════════════════════════════════════════════════════════════════
# Checkpoint
# ════════════════════════════════════════════════════════════════
def load_checkpoint() -> dict:
    if CP_FILE.exists():
        try:
            return json.loads(CP_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"completed": [], "stats": {"scraped": 0, "new": 0, "errors": 0}}


def save_checkpoint(cp: dict):
    CP_FILE.parent.mkdir(parents=True, exist_ok=True)
    CP_FILE.write_text(json.dumps(cp, indent=2), encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# Logging
# ════════════════════════════════════════════════════════════════
def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ════════════════════════════════════════════════════════════════
# DB storage
# ════════════════════════════════════════════════════════════════
def store_jobs(conn, jobs, tag) -> int:
    new = 0
    now = datetime.now(timezone.utc).isoformat()
    for j in jobs:
        if not j.get("title") or not j.get("url"):
            continue
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (dedupe_key, title, company, location, description, url, source,
                    source_kind, external_id, posted_at, salary, tags,
                    first_seen_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    j["url"] or j.get("jobkey", ""),
                    j["title"],
                    j.get("company", ""),
                    j.get("location", ""),
                    j.get("description", ""),
                    j["url"],
                    j["source"],
                    "browser",
                    j.get("jobkey", ""),
                    j.get("posted_at"),
                    j.get("salary", ""),
                    tag,
                    now,
                    now,
                ),
            )
            if cur.rowcount > 0:
                new += 1
        except Exception:
            continue
    conn.commit()
    return new


# ════════════════════════════════════════════════════════════════
# JobSpy pagination loop
# ════════════════════════════════════════════════════════════════
def scrape_jobspy_loop(query: str, location: str, max_pages: int = 10) -> list[dict]:
    """Paginate JobSpy until we get all duplicates."""
    from jobspy import scrape_jobs, Country

    loc = f"{location}, India" if location else ""
    safe_sites = ["linkedin", "indeed"]
    all_jobs = []
    seen_urls = set()
    batch_size = 50
    offset = 0
    empty_streak = 0

    for page in range(max_pages):
        try:
            result = scrape_jobs(
                site_name=safe_sites,
                search_term=query,
                location=loc,
                results_wanted=batch_size,
                country=Country.INDIA if location else None,
                offset=offset,
            )
        except Exception as e:
            log(f"  [jobspy] page {page+1} ERROR: {e}")
            break

        if result is None or len(result) == 0:
            empty_streak += 1
            if empty_streak >= 2:
                break
            offset += batch_size
            time.sleep(1)
            continue

        empty_streak = 0
        new_count = 0
        for _, row in result.iterrows():
            url = row.get("job_url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            new_count += 1

            posted = None
            dp = row.get("date_posted")
            if dp is not None:
                try:
                    posted = dp.isoformat() if hasattr(dp, "isoformat") else str(dp)
                except Exception:
                    pass

            salary = ""
            if row.get("min_amount") and row.get("max_amount"):
                salary = f"{row['currency'] or ''} {row['min_amount']}-{row['max_amount']} {row.get('interval', '')}"

            all_jobs.append({
                "title": str(row.get("title", "")),
                "company": str(row.get("company", "")),
                "location": str(row.get("location", "")),
                "url": url,
                "posted_at": posted,
                "jobkey": str(row.get("id", "")),
                "source": f"jobspy:{row.get('site', 'unknown')}",
                "salary": salary,
                "description": str(row.get("description", ""))[:500] if row.get("description") else "",
                "tags": ",".join(str(s) for s in (row.get("skills") or []) if s),
            })

        if new_count == 0:
            break
        offset += batch_size
        time.sleep(0.3)

    return all_jobs


# ════════════════════════════════════════════════════════════════
# Main stress test
# ════════════════════════════════════════════════════════════════
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Stress test: collect 1M jobs")
    ap.add_argument("--max-pages", type=int, default=10, help="max JobSpy pages per search (10×50=500)")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--resume", action="store_true", help="resume from checkpoint")
    ap.add_argument("--max-searches", type=int, default=0, help="0=unlimited")
    args = ap.parse_args()

    # Build search matrix
    searches = []
    for kw in KEYWORDS:
        for loc in LOCATIONS_ALL:
            searches.append((kw, loc))

    log(f"=" * 55)
    log(f"STRESS TEST: {len(KEYWORDS)} keywords x {len(LOCATIONS_ALL)} locations = {len(searches)} searches")
    log(f"Max pages per search: {args.max_pages} (up to {args.max_pages * 50} unique jobs)")
    log(f"=" * 55)

    # Resume checkpoint
    cp = load_checkpoint() if args.resume else {"completed": [], "stats": {"scraped": 0, "new": 0, "errors": 0}}
    completed_set = set(cp["completed"])
    if completed_set:
        log(f"Resuming: {len(completed_set)} searches already done")

    conn = sqlite3.connect(args.db)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {total_before:,} jobs")

    grand_scraped = cp["stats"]["scraped"]
    grand_new = cp["stats"]["new"]
    grand_errors = cp["stats"]["errors"]
    batch_num = 0
    batch_new = 0
    start_time = time.time()

    for i, (kw, loc) in enumerate(searches):
        key = f"{kw}|{loc}"
        if key in completed_set:
            continue

        if args.max_searches and i >= args.max_searches:
            break

        t0 = time.time()
        try:
            jobs = scrape_jobspy_loop(kw, loc, max_pages=args.max_pages)
            dt = time.time() - t0
            grand_scraped += len(jobs)

            # Store
            tag = f"stress,{kw.lower().replace(' ', '-')[:30]},{loc.lower().replace(' ', '-')[:20]}"
            new = store_jobs(conn, jobs, tag)
            grand_new += new
            batch_new += new
            batch_num += 1

            log(f"[{i+1}/{len(searches)}] {kw} | {loc or 'global'}: "
                f"{len(jobs)} scraped, {new} new ({dt:.0f}s) "
                f"[total: +{grand_new}]")

        except Exception as e:
            grand_errors += 1
            log(f"[{i+1}/{len(searches)}] {kw} | {loc or 'global'}: ERROR {e}")

        # Save checkpoint every 10 searches
        completed_set.add(key)
        if batch_num % 10 == 0:
            cp = {
                "completed": list(completed_set),
                "stats": {
                    "scraped": grand_scraped,
                    "new": grand_new,
                    "errors": grand_errors,
                },
            }
            save_checkpoint(cp)

            # Progress report
            elapsed = time.time() - start_time
            rate = grand_new / (elapsed / 60) if elapsed > 0 else 0
            current_total = total_before + grand_new
            log(f"  --- PROGRESS: {current_total:,} total ({grand_new:,} new) "
                f"| {rate:.0f} new/min | {elapsed/60:.1f} min elapsed ---")

    # Final save
    cp = {
        "completed": list(completed_set),
        "stats": {
            "scraped": grand_scraped,
            "new": grand_new,
            "errors": grand_errors,
        },
    }
    save_checkpoint(cp)

    elapsed = time.time() - start_time
    final_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    log(f"")
    log(f"=" * 55)
    log(f"STRESS TEST COMPLETE")
    log(f"=" * 55)
    log(f"Searches run:  {batch_num}")
    log(f"Total scraped: {grand_scraped:,}")
    log(f"New inserted:  {grand_new:,}")
    log(f"Errors:        {grand_errors}")
    log(f"Time:          {elapsed/60:.1f} minutes")
    log(f"Rate:          {grand_new/(elapsed/60):.0f} new jobs/min")
    log(f"DB total:      {final_total:,}")
    log(f"Target:        1,000,000")
    log(f"Gap remaining: {max(0, 1000000 - final_total):,}")
    log(f"=" * 55)


if __name__ == "__main__":
    main()
