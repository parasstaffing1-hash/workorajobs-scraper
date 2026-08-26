#!/usr/bin/env python3
"""Batch scraper: 100 unique jobs per run.

Each run:
1. Pick next keyword+location from queue
2. Scrape via JobSpy (LinkedIn + Indeed) with pagination
3. Store only NEW jobs (dedup by URL)
4. Checkpoint progress
5. Exit after 100 new jobs OR search exhausted

Designed to be called repeatedly by the wrapper.
"""
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP = ROOT / ".freebuff" / "batch_cp.json"
LOG = ROOT / ".freebuff" / "batch_log.txt"

try:
    from jobspy import scrape_jobs
except ImportError:
    print("ERROR: jobspy not installed", flush=True)
    sys.exit(1)

KEYWORDS = [
    "software engineer", "backend engineer", "frontend developer",
    "full stack developer", "data engineer", "devops engineer",
    "machine learning engineer", "product manager", "data scientist",
    "cloud engineer", "android developer", "ios developer",
    "python developer", "java developer", "react developer",
    "AI engineer", "blockchain developer", "security engineer",
    "QA engineer", "site reliability engineer",
    "platform engineer", "infrastructure engineer",
    "mobile developer", "web developer", "software developer",
    "system administrator", "database administrator",
    "network engineer", "solutions architect",
    "technical lead", "engineering manager",
    "staff engineer", "principal engineer",
    "senior software engineer", "junior software engineer",
    "remote software engineer", "remote backend developer",
    "C++ engineer", "ruby developer", "PHP developer",
    "scala developer", "kotlin developer", "swift developer",
    "Vue.js developer", "Angular developer",
    "technical writer", "data analyst",
    "IT recruiter", "talent acquisition",
    "scrum master", "business analyst",
    "UX designer", "UI developer",
    "robotics engineer", "embedded systems engineer",
    "firmware engineer", "game developer",
    "graphics engineer", "video engineer",
    "network engineer", "database engineer",
    "storage engineer", "compiler engineer",
    "fintech engineer", "payments engineer",
    "enterprise architect", "solutions engineer",
    "sales engineer", "systems engineer",
    "cloud architect", "security architect",
    "ML platform engineer", "data platform engineer",
    "backend developer", "frontend engineer",
    "full stack engineer", "web application developer",
    "API engineer", "microservices engineer",
    "distributed systems engineer", "platform developer",
    "infrastructure developer", "cloud native engineer",
    "container engineer", "kubernetes engineer",
    "terraform engineer", "ansible engineer",
    "CI/CD engineer", "release engineer",
    "build engineer", "automation engineer",
    "test automation engineer", "SDET",
    "software development engineer", "SDE",
    "staff software engineer", "principal software engineer",
    "senior staff engineer", "distinguished engineer",
    "engineering director", "VP engineering",
    "CTO", "head of engineering",
    "technical program manager", "project manager",
    "product owner", "scrum master",
    "Agile coach", "release manager",
    "DevOps lead", "SRE lead",
    "platform lead", "infrastructure lead",
]

LOCATIONS = [
    "", "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Mumbai", "Pune",
    "Delhi", "Noida", "Gurgaon", "Gurugram", "Kolkata", "Ahmedabad",
    "Jaipur", "Kochi", "Coimbatore",
    "New York", "San Francisco", "Seattle", "Austin", "Boston",
    "Chicago", "Los Angeles", "Denver", "Atlanta", "Miami",
    "Washington DC", "Portland", "San Diego", "Dallas", "Houston",
    "London", "Berlin", "Toronto", "Singapore", "Dubai",
    "Amsterdam", "Dublin", "Sydney", "Remote",
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8", errors="replace") as f:
        f.write(line + "\n")


def load_cp():
    if CP.exists():
        try:
            return json.loads(CP.read_text("utf-8"))
        except Exception:
            pass
    # Build full search queue
    searches = []
    for kw in KEYWORDS:
        for loc in LOCATIONS:
            searches.append({"kw": kw, "loc": loc, "done": False})
    return {"searches": searches, "idx": 0, "total_new": 0, "total_batches": 0}


def save_cp(cp):
    CP.parent.mkdir(parents=True, exist_ok=True)
    CP.write_text(json.dumps(cp), "utf-8")


def store_batch(conn, jobs) -> int:
    new = 0
    now = datetime.now(timezone.utc).isoformat()
    for j in jobs:
        if not j.get("title") or not j.get("url"):
            continue
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO jobs (dedupe_key,title,company,location,description,url,source,source_kind,external_id,posted_at,salary,tags,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (j["url"] or j["id"], j["title"], j["company"],
                 j["location"], j["description"], j["url"],
                 j["source"], "web", j["id"],
                 j["posted_at"], j["salary"], "", now, now))
            if cur.rowcount > 0:
                new += 1
        except Exception:
            continue
    conn.commit()
    return new


def scrape_one_search(kw, loc, target_new=100) -> list[dict]:
    """Scrape one keyword+location, return up to target_new unique jobs."""
    all_jobs = []
    seen = set()
    batch_size = 50
    max_pages = 10  # Up to 500 results per search

    for page in range(max_pages):
        offset = page * batch_size
        try:
            results = scrape_jobs(
                site_name=["linkedin", "indeed"],
                search_term=kw,
                location=loc if loc else None,
                results_wanted=batch_size,
                offset=offset,
            )
            if results is None or results.empty:
                break

            new_count = 0
            for _, row in results.iterrows():
                jid = str(row.get("id", ""))
                if jid in seen:
                    break
                seen.add(jid)

                job_url = str(row.get("job_url", ""))
                title = str(row.get("title", ""))
                company = str(row.get("company", ""))
                loc_val = str(row.get("location", ""))
                posted = row.get("date_posted")
                desc = str(row.get("description", "") or "")[:500]
                source_name = str(row.get("site", "jobspy"))

                salary = ""
                if row.get("min_amount") and row.get("max_amount"):
                    salary = f"{row.get('currency', '')} {row.get('min_amount')}-{row.get('max_amount')} {row.get('interval', '')}"

                all_jobs.append({
                    "title": title,
                    "company": company,
                    "location": loc_val,
                    "url": job_url,
                    "posted_at": str(posted) if posted else None,
                    "id": jid,
                    "source": f"jobspy:{source_name}",
                    "description": desc,
                    "salary": salary,
                })
                new_count += 1

                if len(all_jobs) >= target_new + 50:  # Get extra for dedup
                    break

            if new_count == 0:
                break

            if len(all_jobs) >= target_new + 50:
                break

            time.sleep(0.3)
        except Exception as e:
            log(f"  Error: {e}")
            break

    return all_jobs


def main():
    cp = load_cp()
    searches = cp["searches"]
    idx = cp["idx"]
    total_new = cp["total_new"]
    total_batches = cp["total_batches"]

    log(f"=== BATCH {total_batches+1} | Search {idx}/{len(searches)} | Total new: {total_new:,} ===")

    if idx >= len(searches):
        log("All searches exhausted! Resetting queue...")
        idx = 0
        for s in searches:
            s["done"] = False

    conn = sqlite3.connect(DB, check_same_thread=False)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB: {total_before:,} | Gap 1M: {max(0, 1_000_000 - total_before):,}")

    # Find next unfinished search
    batch_new = 0
    attempts = 0
    while attempts < len(searches) and batch_new < 100:
        s = searches[idx]
        if s["done"]:
            idx = (idx + 1) % len(searches)
            attempts += 1
            continue

        kw, loc = s["kw"], s["loc"]
        log(f"  Scraping: {kw} | {loc or 'global'}")

        jobs = scrape_one_search(kw, loc, target_new=100 - batch_new)

        if jobs:
            new = store_batch(conn, jobs)
            batch_new += new
            total_new += new
            log(f"  +{new} new (scraped {len(jobs)}) | Batch: {batch_new} | Total: {total_new:,}")

        s["done"] = True
        idx = (idx + 1) % len(searches)
        attempts += 1

        # Save checkpoint after each search
        cp["idx"] = idx
        cp["total_new"] = total_new
        cp["total_batches"] = total_batches + 1
        save_cp(cp)

        if batch_new >= 100:
            break

    # Final save
    cp["idx"] = idx
    cp["total_new"] = total_new
    cp["total_batches"] = total_batches + 1
    save_cp(cp)

    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    f7 = conn.execute("SELECT COUNT(*) FROM jobs WHERE posted_at >= date('now', '-7 days')").fetchone()[0]
    conn.close()

    log(f"  DONE: +{batch_new} new | DB: {final:,} | Fresh7d: {f7:,} | Gap: {max(0, 1_000_000-final):,}")
    log(f"  Remaining searches: {sum(1 for s in searches if not s['done'])}")


if __name__ == "__main__":
    main()
