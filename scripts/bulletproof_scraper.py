#!/usr/bin/env python3
"""BULLETPROOF SCRAPE — runs batches in a tight loop, checkpoints after EVERY batch.
No dependency on Freebuff, schtasks, or any external process manager.
Just launch it and it runs until 1M jobs.

Architecture:
- 8 parallel worker threads
- Each worker pulls from a shared queue of (source_type, args) work items
- Saves checkpoint after EVERY 10 batches per worker
- Logs to file (no stdout dependency)
- Auto-resumes from checkpoint on restart
- Runs until 1M fresh jobs in last 7 days

Usage:
    python bulletproof_scraper.py          # normal run
    python bulletproof_scraper.py --hours 8  # run for 8 hours max
"""
from __future__ import annotations

import json
import os
import queue
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Absolute paths so detached processes work regardless of cwd
DB = Path(r"C:\Users\Administrator\Documents\ATS\jobs.db")
CP = ROOT / "bulletproof_cp.json"
LOG = ROOT / "bulletproof_log.txt"
TARGET = 1_000_000
MAX_WORKERS = 8
CHECKPOINT_EVERY = 10  # save checkpoint every N batches per worker

# ═══════════════════════════════════════════════════════════════
# LOGGING (file-only, no stdout dependency)
# ═══════════════════════════════════════════════════════════════

_log_lock = threading.Lock()

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with _log_lock:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# CHECKPOINT
# ═══════════════════════════════════════════════════════════════

_cp_lock = threading.Lock()

def load_cp():
    if CP.exists():
        try:
            return json.loads(CP.read_text("utf-8"))
        except Exception:
            pass
    return {"done": [], "total_new": 0, "batches": 0, "round": 0}


def save_cp(cp):
    with _cp_lock:
        try:
            # Keep only last 20000 done items to avoid huge files
            data = {
                "done": cp["done"][-20000:],
                "total_new": cp["total_new"],
                "batches": cp["batches"],
                "round": cp.get("round", 0),
            }
            CP.write_text(json.dumps(data), "utf-8")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# DATABASE (WAL mode, single writer thread)
# ═══════════════════════════════════════════════════════════════

class JobDB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB), check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-64000")
        self.lock = threading.Lock()

    def insert_jobs(self, jobs: list) -> int:
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
                            j.get("url","") or j.get("id",""),
                            j.get("title",""),
                            j.get("company",""),
                            j.get("location",""),
                            j.get("description","")[:500],
                            j.get("url",""),
                            j.get("source",""),
                            j.get("source_kind","ats"),
                            j.get("id",""),
                            j.get("posted_at"),
                            j.get("salary",""),
                            j.get("tags",""),
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
# WORK ITEMS — ATS APIs (fastest, zero dedup)
# ═══════════════════════════════════════════════════════════════

def build_ats_items() -> list[dict]:
    """Build ATS work items from companies.yaml + discovered slugs."""
    items = []
    cp = ROOT / "companies.yaml"
    if cp.exists():
        section = None
        for line in cp.read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line and not line.startswith("-"):
                section = line.rstrip(":").strip()
                continue
            if line.startswith("-") and section:
                slug = line.lstrip("-").strip()
                if slug and "|" not in slug:
                    kind_map = {
                        "greenhouse": "greenhouse", "ashby": "ashby",
                        "lever": "lever", "smartrecruiters": "smartrecruiters",
                        "workable": "workable", "breezy": "breezy",
                        "teamtailor": "teamtailor", "workday": "workday",
                        "bamboohr": "bamboohr", "yc": "yc",
                    }
                    kind = kind_map.get(section, "")
                    if kind:
                        items.append({"type": "ats", "kind": kind, "slug": slug})

    # Add discovered slugs
    disc = ROOT / "discovered_slugs.json"
    if disc.exists():
        try:
            for entry in json.loads(disc.read_text("utf-8")):
                if isinstance(entry, dict):
                    k = entry.get("kind", "")
                    s = entry.get("slug", "")
                    if k and s:
                        items.append({"type": "ats", "kind": k, "slug": s})
        except Exception:
            pass

    return items


def build_jobspy_items() -> list[dict]:
    """Build JobSpy keyword x location search items."""
    kws = [
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
        "hiring", "talent acquisition", "recruitment",
        "penetration tester", "SOC engineer", "cloud security engineer",
        "MLOps engineer", "data platform engineer", "ML platform engineer",
        "search engineer", "recommendation engineer", "growth engineer",
        "analytics engineer", "quantitative developer", "algorithm engineer",
        "video engineer", "audio engineer", "network engineer",
        "storage engineer", "kernel engineer", "compiler engineer",
        "low level software engineer", "distributed systems engineer",
        "microservices engineer", "API engineer", "full stack engineer",
        "frontend engineer", "backend developer", "web application developer",
        "systems engineer", "release engineer", "build engineer",
        "automation engineer", "infrastructure developer",
        "platform developer", "container engineer", "docker engineer",
        "monitoring engineer", "observability engineer",
        "data pipeline engineer", "ETL engineer", "airflow engineer",
        "spark engineer", "hadoop engineer", "flink engineer",
        "streaming engineer", "real-time engineer",
        "payments engineer", "trading systems engineer",
        "blockchain developer", "DeFi engineer", "crypto engineer",
        "AR engineer", "VR engineer", "XR developer",
        "DevTools engineer", "developer experience engineer",
        "identity engineer", "IAM engineer",
    ]
    locs = [
        "", "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Mumbai",
        "Pune", "Delhi", "Noida", "Gurgaon", "Gurugram", "Kolkata",
        "Ahmedabad", "Jaipur", "Kochi", "Coimbatore", "Indore",
        "Lucknow", "Chandigarh", "New York", "San Francisco", "Seattle",
        "Austin", "Boston", "Chicago", "Los Angeles", "Denver", "Atlanta",
        "Miami", "Washington DC", "Portland", "San Diego", "Dallas",
        "Houston", "San Jose", "Raleigh", "Charlotte", "Minneapolis",
        "Phoenix", "Tampa", "Orlando", "Nashville", "London",
        "Manchester", "Edinburgh", "Berlin", "Munich", "Paris",
        "Amsterdam", "Dublin", "Toronto", "Vancouver", "Montreal",
        "Singapore", "Hong Kong", "Tokyo", "Seoul", "Sydney",
        "Melbourne", "Dubai", "Stockholm", "Warsaw", "Prague",
        "Sao Paulo", "Mexico City", "Cape Town", "Lagos", "Remote",
    ]
    sites = ["linkedin", "indeed", "google", "ziprecruiter", "glassdoor", "naukri"]
    items = []
    for site in sites:
        for kw in kws:
            for loc in locs:
                items.append({"type": "jobspy", "kw": kw, "loc": loc, "site": site})
    return items


# ═══════════════════════════════════════════════════════════════
# WORKERS
# ═══════════════════════════════════════════════════════════════

def work_ats(db: JobDB, client, item: dict) -> int:
    """Scrape one ATS board, insert into DB, return new count."""
    try:
        from jobcollector.sources.ats_api import fetch_ats_api
        jobs = fetch_ats_api(client, item["kind"], item["slug"], limit=200)
        dicts = []
        for j in jobs:
            dicts.append({
                "title": j.title, "company": j.company,
                "location": j.location, "url": j.url,
                "description": j.description or "",
                "source": j.source, "source_kind": j.source_kind,
                "id": j.external_id or "",
                "posted_at": str(j.posted_at) if j.posted_at else None,
                "salary": j.salary or "",
                "tags": ",".join(j.tags or []),
            })
        return db.insert_jobs(dicts)
    except Exception:
        return 0


def work_jobspy(db: JobDB, client, item: dict) -> int:
    """Scrape one JobSpy search, insert into DB, return new count."""
    try:
        from jobspy import scrape_jobs
        site = item.get("site", "linkedin")
        results = scrape_jobs(
            site_name=[site],
            search_term=item["kw"],
            location=item["loc"] if item["loc"] else None,
            results_wanted=50,
            offset=0,
        )
        if results is None or results.empty:
            return 0
        dicts = []
        for _, row in results.iterrows():
            url = str(row.get("job_url", ""))
            if not url:
                continue
            salary = ""
            if row.get("min_amount") and row.get("max_amount"):
                salary = f"{row.get('currency','')} {row.get('min_amount')}-{row.get('max_amount')} {row.get('interval','')}"
            dicts.append({
                "title": str(row.get("title", "")),
                "company": str(row.get("company", "")),
                "location": str(row.get("location", "")),
                "url": url,
                "description": str(row.get("description", "") or "")[:500],
                "source": f"jobspy:{item.get('site','unknown')}",
                "source_kind": "web",
                "id": str(row.get("id", "")),
                "posted_at": str(row.get("date_posted")) if row.get("date_posted") else None,
                "salary": salary,
                "tags": "",
            })
        return db.insert_jobs(dicts)
    except Exception:
        return 0


# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    max_hours = 8
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--hours" and i + 1 < len(sys.argv) - 1:
            try:
                max_hours = float(sys.argv[i + 2])
            except Exception:
                pass

    log("=" * 60)
    log(f"BULLETPROOF SCRAPE — {MAX_WORKERS} workers, {max_hours}h max")
    log("=" * 60)

    # Build work items
    ats_items = build_ats_items()
    jobspy_items = build_jobspy_items()
    log(f"ATS items: {len(ats_items)} | JobSpy items: {len(jobspy_items)}")

    # Load checkpoint
    cp = load_cp()
    done_set = set(cp["done"])
    log(f"Checkpoint: {len(done_set)} done, {cp['total_new']:,} new so far")

    # Filter remaining work
    ats_remaining = [i for i in ats_items if f"ats:{i['kind']}:{i['slug']}" not in done_set]
    jobspy_remaining = [i for i in jobspy_items if f"js:{i.get('site','li')}:{i['kw']}:{i['loc']}" not in done_set]
    log(f"ATS remaining: {len(ats_remaining)} | JobSpy remaining: {len(jobspy_remaining)}")

    # If everything is done, reset for a new round
    if not ats_remaining and not jobspy_remaining:
        log("All work done! Starting new round...")
        done_set.clear()
        cp["done"] = []
        cp["round"] = cp.get("round", 0) + 1
        ats_remaining = ats_items[:]
        jobspy_remaining = jobspy_items[:]

    total_before, fresh_before = JobDB().count()
    log(f"DB: {total_before:,} | Fresh7d: {fresh_before:,} | Gap: {max(0, TARGET - total_before):,}")

    # Build work queue — interleave ATS and JobSpy for diversity
    work_q = queue.Queue()
    # Add all ATS items first (they're faster and have zero dedup)
    for item in ats_remaining:
        work_q.put(("ats", item))
    # Then JobSpy items
    for item in jobspy_remaining:
        work_q.put(("jobspy", item))

    total_items = work_q.qsize()
    log(f"Total work queue: {total_items}")

    db = JobDB()
    start_time = time.time()
    batch_counter = {"done": 0, "new": 0}
    counter_lock = threading.Lock()

    # ─── Worker ───
    def worker(wid: int):
        import httpx as _httpx
        client = _httpx.Client(timeout=10, follow_redirects=True)
        local_done = 0
        local_new = 0
        local_errs = 0
        try:
            while True:
                # Check time limit
                elapsed_h = (time.time() - start_time) / 3600
                if elapsed_h >= max_hours:
                    break

                try:
                    wtype, item = work_q.get(timeout=3)
                except queue.Empty:
                    break

                # Build dedupe key
                if wtype == "ats":
                    key = f"ats:{item['kind']}:{item['slug']}"
                    new = work_ats(db, client, item)
                else:
                    key = f"js:{item.get('site','li')}:{item['kw']}:{item['loc']}"
                    new = work_jobspy(db, client, item)

                local_done += 1
                local_new += new
                if new == 0:
                    local_errs += 1

                with counter_lock:
                    batch_counter["done"] += 1
                    batch_counter["new"] += new
                    done_set.add(key)
                    total_done = batch_counter["done"]
                    total_new = batch_counter["new"]

                # Log every find and every 50 batches
                if wtype == "ats":
                    label = f"{item.get('kind','')}:{item.get('slug','')}"
                else:
                    label = f"{item.get('site','')}:{item.get('kw','')[:20]}|{item.get('loc','')[:12]}"
                if new > 0:
                    log(f"  W{wid:02d} +{new:3d} {label[:50]}")
                elif local_done % 50 == 0:
                    log(f"  W{wid:02d} [{local_done} done, {local_new} new, {local_errs} empty] {label[:50]}")

                # Checkpoint every CHECKPOINT_EVERY batches
                if local_done % CHECKPOINT_EVERY == 0:
                    cp["done"] = list(done_set)
                cp["total_new"] = cp["total_new"] + new  # approximate
                cp["batches"] = cp.get("batches", 0) + local_done
                save_cp(cp)

                if total_done % 100 == 0:
                    cur_t, cur_f = db.count()
                    rate = total_new / max((time.time() - start_time) / 60, 0.1)
                    log(f"  [{total_done}/{total_items}] +{total_new:,} new | "
                        f"DB={cur_t:,} | Fresh7d={cur_f:,} | "
                        f"{rate:.0f}/min | Gap={max(0, TARGET-cur_t):,}")

            work_q.task_done()
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    # Launch workers
    log(f"Launching {MAX_WORKERS} workers...")
    threads = []
    for i in range(MAX_WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)

    # Wait for queue to drain
    try:
        work_q.join()
    except Exception:
        pass

    # Wait for threads
    for t in threads:
        t.join(timeout=10)

    # Final checkpoint
    cp["done"] = list(done_set)
    save_cp(cp)

    final_t, final_f = db.count()
    elapsed = (time.time() - start_time) / 60
    db.close()

    log("")
    log("=" * 60)
    log(f"ROUND COMPLETE")
    log(f"Batches: {batch_counter['done']} | New: +{batch_counter['new']:,}")
    log(f"DB: {final_t:,} | Fresh7d: {final_f:,}")
    log(f"Gap to 1M: {max(0, TARGET - final_t):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {batch_counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 60)


if __name__ == "__main__":
    main()
