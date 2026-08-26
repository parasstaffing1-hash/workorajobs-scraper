#!/usr/bin/env python3
"""Parallel job scraper — runs multiple JobSpy searches simultaneously.

Key optimization: use ThreadPoolExecutor to run 10+ searches in parallel,
reducing wall-clock time from 250s/search to ~25s/search (effective).

Strategy:
  1. Prioritize Indian cities (200-450 new jobs each) over global (0-12)
  2. Use diverse keywords (not just "Software Engineer" variations)
  3. Run 10 threads simultaneously
  4. Checkpoint after each batch for resume
  5. Skip already-completed searches
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP_FILE = ROOT / ".freebuff" / "parallel_checkpoint.json"
LOG_FILE = ROOT / ".freebuff" / "parallel_scraper.log"

# Thread-safe DB lock
DB_LOCK = Lock()

# ════════════════════════════════════════════════════════════════
# HIGH-YIELD searches (Indian cities get 200-450 new jobs each)
# ════════════════════════════════════════════════════════════════
INDIAN_CITIES = [
    "Delhi", "Bengaluru", "Hyderabad", "Pune", "Mumbai",
    "Chennai", "Kolkata", "Noida", "Gurgaon", "Ahmedabad",
    "Jaipur", "Lucknow", "Kochi", "Indore", "Bhopal",
    "Coimbatore", "Visakhapatnam", "Chandigarh",
]

# Global cities with lower yield but still worth checking
GLOBAL_CITIES = [
    "", "Remote", "New York", "San Francisco", "Seattle",
    "London", "Berlin", "Toronto", "Singapore", "Dubai",
]

# Comprehensive keyword list — each gives different job results
KEYWORDS = [
    # Core SWE
    "Software Engineer", "Software Developer", "SDE", "SWE",
    "Application Developer", "Product Engineer", "Software Architect",
    # Backend
    "Backend Engineer", "Back-End Developer", "API Engineer",
    "Distributed Systems Engineer", "Platform Engineer",
    # Frontend
    "Frontend Engineer", "UI Engineer", "Web Developer",
    # Full-stack
    "Full Stack Developer", "Full Stack Engineer",
    # Mobile
    "Mobile Engineer", "Android Developer", "iOS Developer",
    "React Native Developer", "Flutter Developer",
    # Cloud / DevOps
    "Cloud Engineer", "DevOps Engineer", "SRE", "Site Reliability Engineer",
    "Infrastructure Engineer", "Platform Engineer",
    # AI / ML
    "Machine Learning Engineer", "ML Engineer", "AI Engineer",
    "Data Scientist", "Deep Learning Engineer", "NLP Engineer",
    "Computer Vision Engineer",
    # Data
    "Data Engineer", "Data Platform Engineer", "Database Engineer",
    "Big Data Engineer", "ETL Engineer",
    # Security
    "Security Engineer", "Cybersecurity Engineer", "AppSec Engineer",
    # Systems
    "Systems Engineer", "Embedded Engineer", "Firmware Engineer",
    # QA
    "SDET", "Test Automation Engineer", "QA Engineer",
    # Languages
    "Python Developer", "Java Developer", "JavaScript Developer",
    "TypeScript Developer", "Go Developer", "Rust Developer",
    "C++ Developer", "Ruby Developer", "PHP Developer",
    "Kotlin Developer", "Swift Developer",
    # Frameworks
    "React Developer", "Angular Developer", "Vue Developer",
    "Node.js Developer", "Django Developer", "Spring Boot Developer",
    ".NET Developer", "Laravel Developer",
    # Architecture
    "Solutions Architect", "Technical Architect", "Cloud Architect",
    # Management
    "Engineering Manager", "Technical Lead", "Staff Engineer",
    "Principal Engineer",
    # High-volume generic
    "Developer", "Programmer", "Consultant",
    "Project Manager", "Product Manager", "Business Analyst",
    "UX Designer", "Technical Writer",
    "System Administrator", "Network Engineer",
    "IT Support", "IT Manager",
]


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


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
# Single search function (runs in a thread)
# ════════════════════════════════════════════════════════════════
def search_one(query: str, location: str, max_pages: int = 5) -> dict:
    """Run a single JobSpy search with pagination. Thread-safe."""
    from jobspy import scrape_jobs, Country

    loc = f"{location}, India" if location else ""
    all_jobs = []
    seen_urls = set()
    batch_size = 50
    offset = 0
    empty_streak = 0

    for page in range(max_pages):
        try:
            result = scrape_jobs(
                site_name=["linkedin", "indeed"],
                search_term=query,
                location=loc,
                results_wanted=batch_size,
                country=Country.INDIA if location else None,
                offset=offset,
            )
        except Exception as e:
            return {"query": query, "location": location, "jobs": [], "error": str(e)}

        if result is None or len(result) == 0:
            empty_streak += 1
            if empty_streak >= 2:
                break
            offset += batch_size
            time.sleep(0.5)
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
        time.sleep(0.2)

    return {"query": query, "location": location, "jobs": all_jobs, "error": None}


# ════════════════════════════════════════════════════════════════
# Store to DB (thread-safe)
# ════════════════════════════════════════════════════════════════
def store_jobs(conn, jobs, tag) -> int:
    new = 0
    now = datetime.now(timezone.utc).isoformat()
    with DB_LOCK:
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
# Main parallel runner
# ════════════════════════════════════════════════════════════════
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Parallel job scraper — 10x faster")
    ap.add_argument("--threads", type=int, default=10, help="parallel threads")
    ap.add_argument("--max-pages", type=int, default=5, help="JobSpy pages per search")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--batch", type=int, default=100, help="searches per batch before checkpoint")
    args = ap.parse_args()

    # Build search list — prioritize Indian cities first
    searches = []
    for kw in KEYWORDS:
        for loc in INDIAN_CITIES:
            searches.append((kw, loc))
    for kw in KEYWORDS:
        for loc in GLOBAL_CITIES:
            searches.append((kw, loc))

    log("=" * 60)
    log(f"PARALLEL SCRAPER: {len(searches)} searches, {args.threads} threads")
    log(f"Indian cities: {len(INDIAN_CITIES)} (high yield: 200-450 new each)")
    log(f"Global cities: {len(GLOBAL_CITIES)} (lower yield)")
    log("=" * 60)

    # Resume checkpoint
    cp = load_checkpoint() if args.resume else {"completed": [], "stats": {"scraped": 0, "new": 0, "errors": 0}}
    completed_set = set(cp["completed"])
    remaining = [(kw, loc) for kw, loc in searches if f"{kw}|{loc}" not in completed_set]
    log(f"Completed: {len(completed_set)}, Remaining: {len(remaining)}")

    if not remaining:
        log("All searches completed!")
        return

    conn = sqlite3.connect(args.db)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {total_before:,} jobs")

    grand_scraped = cp["stats"]["scraped"]
    grand_new = cp["stats"]["new"]
    grand_errors = cp["stats"]["errors"]
    start_time = time.time()

    # Process in batches
    for batch_start in range(0, len(remaining), args.batch):
        batch = remaining[batch_start:batch_start + args.batch]
        log(f"\n--- Batch {batch_start//args.batch + 1}: {len(batch)} searches, {args.threads} threads ---")

        batch_new = 0
        batch_scraped = 0

        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            futures = {}
            for kw, loc in batch:
                key = f"{kw}|{loc}"
                f = executor.submit(search_one, kw, loc, args.max_pages)
                futures[f] = (kw, loc, key)

            for future in as_completed(futures):
                kw, loc, key = futures[future]
                try:
                    result = future.result()
                    jobs = result["jobs"]
                    batch_scraped += len(jobs)
                    grand_scraped += len(jobs)

                    if result["error"]:
                        grand_errors += 1
                        log(f"  ERROR {kw}|{loc}: {result['error'][:80]}")
                        continue

                    tag = f"parallel,{kw.lower().replace(' ', '-')[:30]},{loc.lower().replace(' ', '-')[:20]}"
                    new = store_jobs(conn, jobs, tag)
                    grand_new += new
                    batch_new += new
                    completed_set.add(key)

                    log(f"  {kw:35s} | {loc or 'global':12s}: {len(jobs):4d} scraped, {new:4d} new")
                except Exception as e:
                    grand_errors += 1
                    log(f"  EXCEPTION {kw}|{loc}: {e}")

        # Save checkpoint after each batch
        cp = {
            "completed": list(completed_set),
            "stats": {"scraped": grand_scraped, "new": grand_new, "errors": grand_errors},
        }
        save_checkpoint(cp)

        elapsed = time.time() - start_time
        rate = grand_new / (elapsed / 60) if elapsed > 0 else 0
        current_total = total_before + grand_new
        log(f"  BATCH DONE: +{batch_new} new (batch) | Total: {current_total:,} ({grand_new:,} new) "
            f"| {rate:.0f} new/min | {elapsed/60:.1f} min")

    # Final
    elapsed = time.time() - start_time
    final_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    log("")
    log("=" * 60)
    log(f"PARALLEL SCRAPE COMPLETE")
    log(f"Total scraped: {grand_scraped:,}")
    log(f"New inserted:  {grand_new:,}")
    log(f"Errors:        {grand_errors}")
    log(f"Time:          {elapsed/60:.1f} minutes")
    log(f"Rate:          {grand_new/(elapsed/60):.0f} new jobs/min")
    log(f"DB total:      {final_total:,}")
    log(f"Gap to 1M:     {max(0, 1000000 - final_total):,}")
    log("=" * 60)


if __name__ == "__main__":
    main()
