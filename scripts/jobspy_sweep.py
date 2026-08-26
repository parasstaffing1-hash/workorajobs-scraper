#!/usr/bin/env python3
"""Full JobSpy sweep — 50 keywords x 30 locations with pagination.

Each search paginates until duplicates, pulling 200-500 unique jobs.
Checkpoint after each search for resume.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP = ROOT / ".freebuff" / "jobspy_sweep_cp.json"
LOG = ROOT / ".freebuff" / "jobspy_sweep.log"

from jobspy import scrape_jobs


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
    return {"done": [], "stats": {"new": 0, "searches": 0}}


def save_cp(cp):
    CP.parent.mkdir(parents=True, exist_ok=True)
    CP.write_text(json.dumps(cp), "utf-8")


def is_india_location(loc: str) -> bool:
    india = {"bangalore", "bengaluru", "hyderabad", "chennai", "mumbai", "pune",
             "delhi", "noida", "gurgaon", "gurugram", "kolkata", "ahmedabad",
             "jaipur", "kochi", "coimbatore", "thiruvananthapuram", "india"}
    return any(w in loc.lower() for w in india)


def store_jobs(conn, jobs, source_tag) -> int:
    new = 0
    now = datetime.now(timezone.utc).isoformat()
    for j in jobs:
        if not j.get("title") or not j.get("url"):
            continue
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO jobs (dedupe_key,title,company,location,description,url,source,source_kind,external_id,posted_at,salary,tags,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (j["url"] or j.get("id", ""), j["title"], j.get("company", ""),
                 j.get("location", ""), j.get("description", "")[:500], j["url"],
                 j.get("source", source_tag), "web", j.get("id", ""),
                 j.get("posted_at"), "", "", now, now))
            if cur.rowcount > 0:
                new += 1
        except Exception:
            continue
    conn.commit()
    return new


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    if args.reset and CP.exists():
        CP.unlink()

    cp = load_cp() if args.resume else {"done": [], "stats": {"new": 0, "searches": 0}}

    keywords = [
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
    ]

    locations = [
        "", "Bangalore", "Hyderabad", "Chennai", "Mumbai", "Pune",
        "Delhi", "Noida", "Gurgaon", "Kolkata", "Ahmedabad",
        "New York", "San Francisco", "Seattle", "Austin", "Boston",
        "Chicago", "Los Angeles", "Denver", "Atlanta", "Miami",
        "London", "Berlin", "Toronto", "Singapore", "Dubai",
        "Amsterdam", "Dublin", "Sydney", "Remote",
    ]

    # Build search list
    searches = []
    for kw in keywords:
        for loc in locations:
            key = f"{kw}|{loc}"
            if key not in cp["done"]:
                searches.append((kw, loc, key))

    log(f"Total searches: {len(keywords)} x {len(locations)} = {len(keywords)*len(locations)}")
    log(f"Remaining: {len(searches)}")

    conn = sqlite3.connect(DB, check_same_thread=False)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB: {total_before:,} | Gap 1M: {max(0, 1_000_000 - total_before):,}")

    new_total = cp["stats"]["new"]
    searches_done = cp["stats"]["searches"]
    start = time.time()

    for kw, loc, key in searches:
        log(f"  Search: {kw} | {loc or 'global'}")
        all_jobs = []
        seen_ids = set()
        batch_size = 50
        max_pages = 5

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
                    jid = row.get("id", "")
                    if jid in seen_ids:
                        break
                    seen_ids.add(jid)
                    job_url = row.get("job_url", "")
                    title = row.get("title", "")
                    company = row.get("company", "")
                    location_val = row.get("location", "")
                    posted = row.get("date_posted")
                    desc = row.get("description", "")
                    source_name = row.get("site", "jobspy")
                    salary = ""
                    if row.get("min_amount") and row.get("max_amount"):
                        salary = f"{row.get('currency', '')} {row.get('min_amount')}-{row.get('max_amount')} {row.get('interval', '')}"

                    all_jobs.append({
                        "title": str(title),
                        "company": str(company),
                        "location": str(location_val),
                        "url": str(job_url),
                        "posted_at": str(posted) if posted else None,
                        "id": str(jid),
                        "source": f"jobspy:{source_name}",
                        "description": str(desc)[:500] if desc else "",
                        "salary": salary,
                    })
                    new_count += 1

                if new_count == 0:
                    break
                time.sleep(0.5)
            except Exception as e:
                log(f"    Error: {e}")
                break

        if all_jobs:
            new = store_jobs(conn, [{"url": j["url"], "title": j["title"],
                "company": j["company"], "location": j["location"],
                "description": j["description"], "source": j["source"],
                "id": j["id"], "posted_at": j["posted_at"],
                "salary": j["salary"]} for j in all_jobs], "jobspy")
            new_total += new
            log(f"    +{new} new ({len(all_jobs)} scraped)")
        else:
            log(f"    0 scraped")

        searches_done += 1
        cp["done"].append(key)
        cp["stats"]["new"] = new_total
        cp["stats"]["searches"] = searches_done
        save_cp(cp)

        elapsed = time.time() - start
        current = total_before + new_total
        rate = new_total / (elapsed / 60) if elapsed > 0 else 0
        done_pct = searches_done * 100 / (len(keywords) * len(locations))
        remaining_min = ((len(keywords) * len(locations) - searches_done) * (elapsed / max(searches_done, 1))) / 60
        log(f"  [{searches_done}/{len(keywords)*len(locations)}] {done_pct:.0f}% | DB: {current:,} (+{new_total:,}) | {rate:.0f}/min | ETA: {remaining_min:.0f}min | Gap: {max(0, 1_000_000 - current):,}")

        if current >= 1_000_000:
            log(f"\n*** 1M JOBS REACHED! ***")
            break

    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    elapsed = time.time() - start
    conn.close()

    log("")
    log("=" * 60)
    log(f"JOBSPY SWEEP COMPLETE")
    log(f"DB: {final:,} | New: +{new_total:,} | Searches: {searches_done}")
    log(f"Time: {elapsed/60:.1f} min | Rate: {new_total/(elapsed/60):.0f}/min")
    log(f"Gap 1M: {max(0, 1_000_000 - final):,}")
    log("=" * 60)


if __name__ == "__main__":
    main()
