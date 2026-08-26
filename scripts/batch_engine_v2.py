#!/usr/bin/env python3
"""BATCH ENGINE v2 — 10,000 batches of 100 unique jobs each.

Architecture:
- 20 worker threads
- Each worker pulls a (site, keyword, location, offset) tuple
- Sites: LinkedIn, Indeed, Google, ZipRecruiter, Glassdoor, Naukri
- Each search paginates (offset 0→50→100→150→200) for maximum yield
- Checkpoint after every batch
- Runs until 1M fresh jobs OR all batches done
- Self-relaunching wrapper (relay) handles restarts

Key optimization: offset-based pagination means each "batch" gets DIFFERENT jobs.
"""
from __future__ import annotations

import json
import os
import queue
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = Path(r"C:\Users\Administrator\Documents\ATS\jobs.db")
CP_PATH = ROOT / "batch_v2_cp.json"
LOG_PATH = ROOT / "batch_v2_log.txt"
TARGET = 1_000_000
WORKERS = 50
RESULTS_PER_SEARCH = 50
MAX_OFFSET = 200  # paginate 0,50,100,150,200 = 5 pages per search

# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

_lock = threading.Lock()

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with _lock:
        try:
            with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# CHECKPOINT
# ═══════════════════════════════════════════════════════════════

def load_cp():
    if CP_PATH.exists():
        try:
            return json.loads(CP_PATH.read_text("utf-8"))
        except Exception:
            pass
    return {"done": [], "total_new": 0, "batches": 0}


def save_cp(cp):
    try:
        CP_PATH.write_text(json.dumps({
            "done": cp["done"][-30000:],
            "total_new": cp["total_new"],
            "batches": cp["batches"],
        }), "utf-8")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# DATABASE (single writer)
# ═══════════════════════════════════════════════════════════════

class JobDB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-128000")
        self.lock = threading.Lock()

    def insert_many(self, jobs: list[dict]) -> int:
        new = 0
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            for j in jobs:
                try:
                    cur = self.conn.execute(
                        "INSERT OR IGNORE INTO jobs "
                        "(dedupe_key,title,company,location,description,url,source,"
                        "source_kind,external_id,posted_at,salary,tags,"
                        "first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            j.get("url", "") or j.get("id", ""),
                            j.get("title", ""),
                            j.get("company", ""),
                            j.get("location", ""),
                            j.get("description", "")[:500],
                            j.get("url", ""),
                            j.get("source", ""),
                            j.get("source_kind", "web"),
                            j.get("id", ""),
                            j.get("posted_at"),
                            j.get("salary", ""),
                            j.get("tags", ""),
                            now, now,
                        ),
                    )
                    if cur.rowcount > 0:
                        new += 1
                except Exception:
                    continue
            if new > 0:
                self.conn.commit()
        return new

    def count(self):
        with self.lock:
            t = self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            f = self.conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE first_seen_at >= datetime('now','-7 days')"
            ).fetchone()[0]
            return t, f

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# WORK ITEMS — (site, keyword, location, offset) tuples
# ═══════════════════════════════════════════════════════════════

SITES = ["linkedin", "indeed", "google", "ziprecruiter", "glassdoor", "naukri"]

KEYWORDS = [
    "software engineer", "backend engineer", "frontend developer",
    "full stack developer", "data engineer", "devops engineer",
    "machine learning engineer", "product manager", "data scientist",
    "cloud engineer", "android developer", "ios developer",
    "python developer", "java developer", "react developer",
    "AI engineer", "blockchain developer", "security engineer",
    "QA engineer", "site reliability engineer", "platform engineer",
    "infrastructure engineer", "mobile developer", "web developer",
    "software developer", "technical lead", "engineering manager",
    "staff engineer", "principal engineer", "SDE", "SRE",
    "senior software engineer", "junior software engineer",
    "remote software engineer", "C++ engineer", "ruby developer",
    "PHP developer", "scala developer", "kotlin developer", "swift developer",
    "Vue.js developer", "Angular developer", "data analyst",
    "IT recruiter", "scrum master", "business analyst",
    "UX designer", "robotics engineer", "embedded systems engineer",
    "firmware engineer", "game developer", "graphics engineer",
    "database engineer", "fintech engineer", "solutions architect",
    "cloud architect", "go developer", "node.js developer",
    "django developer", "flask developer", "fastapi developer",
    "spring boot developer", ".NET developer", "laravel developer",
    "aws engineer", "azure engineer", "gcp engineer",
    "kubernetes engineer", "terraform engineer", "CI/CD engineer",
    "test automation engineer", "MLE", "DevOps", "golang engineer",
    "typescript developer", "javascript developer", "rust developer",
    "elixir developer", "haskell developer", "perl developer",
    "web3 developer", "solidity developer", "smart contract",
    "unity developer", "unreal developer", "NLP engineer",
    "computer vision engineer", "IoT engineer", "devrel",
    "developer advocate", "tech lead", "cto",
    "penetration tester", "SOC engineer", "cloud security engineer",
    "MLOps engineer", "data platform engineer", "ML platform engineer",
    "search engineer", "recommendation engineer", "growth engineer",
    "analytics engineer", "quantitative developer", "algorithm engineer",
    "video engineer", "audio engineer", "network engineer",
    "storage engineer", "kernel engineer", "compiler engineer",
    "distributed systems engineer", "microservices engineer",
    "API engineer", "full stack engineer", "frontend engineer",
    "backend developer", "web application developer", "systems engineer",
    "release engineer", "build engineer", "automation engineer",
    "infrastructure developer", "platform developer", "container engineer",
    "docker engineer", "monitoring engineer", "observability engineer",
    "data pipeline engineer", "ETL engineer", "airflow engineer",
    "streaming engineer", "payments engineer", "trading systems engineer",
    "AR engineer", "VR engineer", "XR developer",
    "DevTools engineer", "developer experience engineer",
]

LOCATIONS = [
    "", "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Mumbai",
    "Pune", "Delhi", "Noida", "Gurgaon", "Gurugram", "Kolkata",
    "Ahmedabad", "Jaipur", "Kochi", "Coimbatore", "Indore",
    "New York", "San Francisco", "Seattle", "Austin", "Boston",
    "Chicago", "Los Angeles", "Denver", "Atlanta", "Miami",
    "Washington DC", "Portland", "San Diego", "Dallas", "Houston",
    "San Jose", "Raleigh", "Charlotte", "Phoenix", "Nashville",
    "London", "Manchester", "Edinburgh", "Berlin", "Munich", "Paris",
    "Amsterdam", "Dublin", "Toronto", "Vancouver", "Montreal",
    "Singapore", "Hong Kong", "Tokyo", "Seoul", "Sydney",
    "Melbourne", "Dubai", "Stockholm", "Warsaw", "Prague",
    "Sao Paulo", "Mexico City", "Cape Town", "Lagos", "Remote",
]


def build_work_items() -> list[tuple[str, str, str, int]]:
    """Build (site, keyword, location, offset) tuples."""
    items = []
    for site in SITES:
        for kw in KEYWORDS:
            for loc in LOCATIONS:
                for offset in range(0, MAX_OFFSET + 1, RESULTS_PER_SEARCH):
                    items.append((site, kw, loc, offset))
    return items


# ═══════════════════════════════════════════════════════════════
# SCRAPER — one search via JobSpy
# ═══════════════════════════════════════════════════════════════

def scrape_one(site: str, kw: str, loc: str, offset: int) -> list[dict]:
    """Scrape one (site, keyword, location, offset) via JobSpy."""
    try:
        from jobspy import scrape_jobs
        results = scrape_jobs(
            site_name=[site],
            search_term=kw,
            location=loc if loc else None,
            results_wanted=RESULTS_PER_SEARCH,
            offset=offset,
        )
        if results is None or results.empty:
            return []

        jobs = []
        for _, row in results.iterrows():
            url = str(row.get("job_url", ""))
            if not url:
                continue
            salary = ""
            if row.get("min_amount") and row.get("max_amount"):
                salary = f"{row.get('currency', '')} {row.get('min_amount')}-{row.get('max_amount')} {row.get('interval', '')}"
            jobs.append({
                "title": str(row.get("title", "")),
                "company": str(row.get("company", "")),
                "location": str(row.get("location", "")),
                "url": url,
                "description": str(row.get("description", "") or "")[:500],
                "source": f"jobspy:{site}",
                "source_kind": "web",
                "id": str(row.get("id", "")),
                "posted_at": str(row.get("date_posted")) if row.get("date_posted") else None,
                "salary": salary,
                "tags": "",
            })
        return jobs
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    max_hours = 8
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--hours":
            try:
                max_hours = float(sys.argv[i + 2])
            except Exception:
                pass

    log("=" * 60)
    log(f"BATCH ENGINE v2 — {WORKERS} workers, {max_hours}h max")
    log("=" * 60)

    # Build work items
    all_items = build_work_items()
    log(f"Total work items: {len(all_items)}")

    # Load checkpoint
    cp = load_cp()
    done_set = set(tuple(d) for d in cp["done"])
    remaining = [i for i in all_items if tuple(i) not in done_set]
    log(f"Done: {len(done_set)}, Remaining: {len(remaining)}")

    if not remaining:
        log("All done! Resetting...")
        done_set.clear()
        remaining = all_items[:]
        cp["done"] = []

    db = JobDB()
    t0, f0 = db.count()
    log(f"DB: {t0:,} | Fresh7d: {f0:,} | Gap: {max(0, TARGET - t0):,}")

    # Work queue
    wq = queue.Queue()
    for item in remaining:
        wq.put(item)

    total_items = wq.qsize()
    start = time.time()
    counter = {"done": 0, "new": 0}
    c_lock = threading.Lock()

    def worker(wid: int):
        while True:
            elapsed_h = (time.time() - start) / 3600
            if elapsed_h >= max_hours:
                break
            try:
                site, kw, loc, offset = wq.get(timeout=3)
            except queue.Empty:
                break

            jobs = scrape_one(site, kw, loc, offset)
            new = db.insert_many(jobs) if jobs else 0

            with c_lock:
                counter["done"] += 1
                counter["new"] += new
                done_set.add((site, kw, loc, offset))
                d = counter["done"]
                n = counter["new"]

            if new > 20:
                log(f"  W{wid:02d} +{new:3d} {site}:{kw[:20]}|{loc[:10]} o={offset}")

            # Checkpoint every 20 batches
            if d % 20 == 0:
                cp["done"] = list(done_set)
                cp["total_new"] = n
                cp["batches"] = d
                save_cp(cp)

            if d % 200 == 0:
                ct, cf = db.count()
                rate = n / max((time.time() - start) / 60, 0.1)
                log(f"  [{d}/{total_items}] +{n:,} new | DB={ct:,} | Fresh7d={cf:,} | {rate:.0f}/min | Gap={max(0, TARGET-ct):,}")

            wq.task_done()

    # Launch workers
    log(f"Launching {WORKERS} workers on {total_items} items...")
    threads = []
    for i in range(WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)

    try:
        wq.join()
    except Exception:
        pass

    for t in threads:
        t.join(timeout=10)

    cp["done"] = list(done_set)
    save_cp(cp)

    ft, ff = db.count()
    elapsed = (time.time() - start) / 60
    db.close()

    log("")
    log("=" * 60)
    log(f"BATCH COMPLETE | Batches: {counter['done']} | New: +{counter['new']:,}")
    log(f"DB: {ft:,} | Fresh7d: {ff:,} | Gap: {max(0, TARGET - ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 60)

    # SELF-RELAUNCH: write a .bat and os.startfile it (truly independent on Windows)
    if ft < TARGET and counter["new"] > 0:
        log("Self-relaunching in 5s...")
        time.sleep(5)
        try:
            bat = ROOT / "_relaunch.bat"
            bat.write_text(
                '@echo off\n'
                'cd /d C:\\Users\\Administrator\\Documents\\ATS\n'
                '.venv\\Scripts\\pythonw.exe -B scripts\\batch_engine_v2.py --hours ' + str(max_hours) + '\n'
                'del "' + str(bat) + '" 2>nul\n',
                encoding="utf-8",
            )
            os.startfile(str(bat))
            log("Self-relaunch sent via os.startfile!")
        except Exception as e:
            log(f"Self-relaunch failed: {e}")
    else:
        log("Target reached or no new jobs. Stopping.")


if __name__ == "__main__":
    main()
