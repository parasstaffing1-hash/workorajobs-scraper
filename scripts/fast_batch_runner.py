#!/usr/bin/env python3
"""FAST PARALLEL BATCH RUNNER — 20 workers scraping simultaneously.

Architecture:
- 20 worker threads each scrape independently via JobSpy
- Shared search queue (thread-safe) with 4500+ keyword×location combos
- Single writer thread handles SQLite (WAL mode for fast reads)
- Checkpoint saved every 50 batches
- Each worker scrapes 50 jobs per search call (fast), stores only new ones
- Target: 1M unique jobs in last 7 days

Runs for up to 55 minutes (fits in 600s timeout), saves checkpoint, exits.
Call repeatedly until done.
"""
from __future__ import annotations

import json
import queue
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP = ROOT / "fast_batch_cp.json"
LOG = ROOT / "fast_batch_log.txt"
TARGET = 1_000_000
MAX_RUNTIME_SEC = 54 * 60  # 54 minutes (safe under 600s timeout)
WORKERS = 8  # 8 threads avoids LinkedIn 429
BATCH_SIZE = 50  # jobs per JobSpy call

# ═══════════════════════════════════════════════════════════════
# KEYWORD × LOCATION MATRIX (4500+ combos)
# ═══════════════════════════════════════════════════════════════

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
    "database engineer", "storage engineer",
    "compiler engineer", "fintech engineer",
    "payments engineer", "enterprise architect",
    "solutions engineer", "sales engineer",
    "systems engineer", "cloud architect",
    "security architect", "ML platform engineer",
    "data platform engineer", "backend developer",
    "frontend engineer", "full stack engineer",
    "web application developer", "API engineer",
    "microservices engineer", "distributed systems engineer",
    "platform developer", "infrastructure developer",
    "cloud native engineer", "container engineer",
    "kubernetes engineer", "terraform engineer",
    "ansible engineer", "CI/CD engineer",
    "release engineer", "build engineer",
    "automation engineer", "test automation engineer",
    "SDET", "software development engineer",
    "SDE", "staff software engineer",
    "principal software engineer", "senior staff engineer",
    "engineering director", "VP engineering",
    "technical program manager", "project manager",
    "product owner", "Agile coach",
    "release manager", "DevOps lead",
    "SRE lead", "platform lead",
    "infrastructure lead", "go developer",
    "golang engineer", "node.js developer",
    "express developer", "nestjs developer",
    "django developer", "flask developer",
    "fastapi developer", "spring boot developer",
    "ruby on rails developer", ".NET developer",
    "ASP.NET developer", "laravel developer",
    "aws engineer", "azure engineer",
    "gcp engineer", "serverless engineer",
    "lambda engineer", "microservices developer",
    "event-driven architect", "message queue engineer",
    "kafka engineer", "redis engineer",
    "elasticsearch engineer", "solr engineer",
    "machine learning ops", "MLOps engineer",
    "data pipeline engineer", "ETL engineer",
    "airflow engineer", "spark engineer",
    "hadoop engineer", "flink engineer",
    "streaming engineer", "real-time engineer",
    "low-latency engineer", "high-frequency trader",
    "quantitative developer", "algorithm engineer",
    "computer vision engineer", "NLP engineer",
    "speech engineer", "recommendation engineer",
    "growth engineer", "experimentation engineer",
    "feature engineer", "analytics engineer",
    "business intelligence engineer", "tableau developer",
    "power BI developer", "reporting engineer",
    "compliance engineer", "regulatory engineer",
    "fintech developer", "banking software engineer",
    "trading systems engineer", "risk engineer",
    "payment systems engineer", "blockchain developer",
    "smart contract developer", "Web3 developer",
    "DeFi engineer", "crypto engineer",
    "game engine developer", "Unity developer",
    "Unreal developer", "graphics programmer",
    "shader engineer", "rendering engineer",
    "simulation engineer", "physics engineer",
    "robotics software engineer", "autonomous systems engineer",
    "self-driving engineer", "ADAS engineer",
    "IoT engineer", "edge computing engineer",
    "AR engineer", "VR engineer",
    "XR developer", "spatial computing engineer",
    "DevTools engineer", "developer experience engineer",
    "internal tools engineer", "platform engineer",
    "SRE", "reliability engineer",
    "incident response engineer", "chaos engineer",
    "observability engineer", "monitoring engineer",
    "CI/CD developer", "GitOps engineer",
    "infrastructure as code engineer", "cloud security engineer",
    "identity engineer", "IAM engineer",
    "penetration tester", "vulnerability engineer",
    "SOC engineer", "threat detection engineer",
    "malware engineer", "forensics engineer",
]

LOCATIONS = [
    "", "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Mumbai", "Pune",
    "Delhi", "Noida", "Gurgaon", "Gurugram", "Kolkata", "Ahmedabad",
    "Jaipur", "Kochi", "Coimbatore", "Thiruvananthapuram", "Indore",
    "Lucknow", "Chandigarh", "Bhopal", "Nagpur", "Visakhapatnam",
    "New York", "San Francisco", "Seattle", "Austin", "Boston",
    "Chicago", "Los Angeles", "Denver", "Atlanta", "Miami",
    "Washington DC", "Portland", "San Diego", "Dallas", "Houston",
    "San Jose", "Raleigh", "Charlotte", "Minneapolis", "Detroit",
    "Phoenix", "Tampa", "Orlando", "Nashville", "Salt Lake City",
    "London", "Manchester", "Birmingham", "Edinburgh", "Bristol",
    "Berlin", "Munich", "Hamburg", "Frankfurt", "Cologne",
    "Paris", "Lyon", "Marseille", "Toulouse",
    "Amsterdam", "Rotterdam", "The Hague",
    "Dublin", "Cork",
    "Toronto", "Vancouver", "Montreal", "Ottawa", "Calgary",
    "Singapore", "Hong Kong", "Tokyo", "Seoul",
    "Sydney", "Melbourne", "Brisbane", "Perth",
    "Dubai", "Abu Dhabi", "Riyadh",
    "Stockholm", "Oslo", "Copenhagen", "Helsinki",
    "Warsaw", "Prague", "Budapest", "Bucharest",
    "Sao Paulo", "Buenos Aires", "Mexico City", "Bogota",
    "Cape Town", "Lagos",
    "Mumbai", "Remote",
]

# ═══════════════════════════════════════════════════════════════
# LOGGING & CHECKPOINT
# ═══════════════════════════════════════════════════════════════

_log_lock = threading.Lock()
_write_lock = threading.Lock()

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    with _log_lock:
        print(line, flush=True)
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
        except Exception:
            pass


def load_cp():
    if CP.exists():
        try:
            return json.loads(CP.read_text("utf-8"))
        except Exception:
            pass
    return {"done": [], "idx": 0, "total_new": 0, "batches": 0}


def save_cp(cp):
    try:
        CP.write_text(json.dumps({
            "done": cp["done"][-5000:],  # keep last 5000
            "idx": cp["idx"],
            "total_new": cp["total_new"],
            "batches": cp["batches"],
        }), "utf-8")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# DATABASE WRITER (single thread)
# ═══════════════════════════════════════════════════════════════

class DBWriter:
    """Thread-safe batch writer to SQLite."""
    
    def __init__(self):
        self.conn = sqlite3.connect(str(DB), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        self.buffer = []
        self.total_new = 0
        self.lock = threading.Lock()
    
    def add_jobs(self, jobs: list[dict]) -> int:
        new = 0
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            for j in jobs:
                if not j.get("title") or not j.get("url"):
                    continue
                try:
                    cur = self.conn.execute(
                        "INSERT OR IGNORE INTO jobs (dedupe_key,title,company,location,description,url,source,source_kind,external_id,posted_at,salary,tags,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (j["url"] or j.get("id", ""), j["title"], j.get("company", ""),
                         j.get("location", ""), j.get("description", ""), j["url"],
                         j["source"], "web", j.get("id", ""),
                         j.get("posted_at"), j.get("salary", ""), "", now, now))
                    if cur.rowcount > 0:
                        new += 1
                except Exception:
                    continue
            if new > 0:
                self.conn.commit()
            self.total_new += new
            return new
    
    def count(self) -> tuple[int, int]:
        try:
            t = self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            f7 = self.conn.execute("SELECT COUNT(*) FROM jobs WHERE posted_at >= date('now', '-7 days')").fetchone()[0]
            return t, f7
        except Exception:
            return 0, 0
    
    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# WORKER: Scrape one search via JobSpy
# ═══════════════════════════════════════════════════════════════

def scrape_search(kw: str, loc: str) -> list[dict]:
    """Scrape one keyword+location via JobSpy. Returns list of job dicts."""
    try:
        from jobspy import scrape_jobs
        results = scrape_jobs(
            site_name=["linkedin", "indeed"],
            search_term=kw,
            location=loc if loc else None,
            results_wanted=BATCH_SIZE,
            offset=0,
        )
        if results is None or results.empty:
            return []
        
        jobs = []
        for _, row in results.iterrows():
            job_url = str(row.get("job_url", ""))
            if not job_url:
                continue
            
            salary = ""
            if row.get("min_amount") and row.get("max_amount"):
                salary = f"{row.get('currency', '')} {row.get('min_amount')}-{row.get('max_amount')} {row.get('interval', '')}"
            
            jobs.append({
                "title": str(row.get("title", "")),
                "company": str(row.get("company", "")),
                "location": str(row.get("location", "")),
                "url": job_url,
                "posted_at": str(row.get("date_posted")) if row.get("date_posted") else None,
                "id": str(row.get("id", "")),
                "source": f"jobspy:{row.get('site', 'unknown')}",
                "description": str(row.get("description", "") or "")[:500],
                "salary": salary,
            })
        return jobs
    except Exception as e:
        log(f"  ERR {kw}|{loc}: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    log("=" * 60)
    log(f"FAST PARALLEL BATCH RUNNER — {WORKERS} workers")
    log("=" * 60)
    
    # Build search queue
    searches = []
    for kw in KEYWORDS:
        for loc in LOCATIONS:
            searches.append((kw, loc))
    
    log(f"Total searches: {len(searches)}")
    
    # Load checkpoint
    cp = load_cp()
    done_set = set(tuple(d) for d in cp["done"])
    remaining = [(kw, loc) for kw, loc in searches if (kw, loc) not in done_set]
    log(f"Done: {len(done_set)}, Remaining: {len(remaining)}")
    
    if not remaining:
        log("All searches done! Resetting...")
        done_set.clear()
        remaining = searches[:]
        cp["done"] = []
        cp["idx"] = 0
    
    total_before, fresh7_before = DBWriter().count()
    log(f"DB: {total_before:,} | Fresh7d: {fresh7_before:,} | Gap: {max(0, TARGET-total_before):,}")
    
    db = DBWriter()
    start = time.time()
    batch_count = cp["batches"]
    total_new = cp["total_new"]
    
    # Create work queue
    work_q = queue.Queue()
    for kw, loc in remaining:
        work_q.put((kw, loc))
    
    total_searches = work_q.qsize()
    batch_counter = {"done": 0, "new": 0, "lock": threading.Lock()}
    
    def worker(worker_id: int):
        """Worker thread: pull from queue, scrape, write to DB."""
        while True:
            try:
                kw, loc = work_q.get(timeout=2)
            except queue.Empty:
                break
            
            # Check time limit
            if time.time() - start > MAX_RUNTIME_SEC:
                break
            
            jobs = scrape_search(kw, loc)
            if jobs:
                new = db.add_jobs(jobs)
                with batch_counter["lock"]:
                    batch_counter["done"] += 1
                    batch_counter["new"] += new
                    done = batch_counter["done"]
                    total = batch_counter["new"]
                
                if new > 0:
                    log(f"  W{worker_id:02d} +{new:3d} {kw[:25]:25s}|{loc[:15]:15s} ({len(jobs)} scraped)")
                
                if done % 50 == 0:
                    current_total, current_f7 = db.count()
                    elapsed = (time.time() - start) / 60
                    rate = total / elapsed if elapsed > 0 else 0
                    log(f"  [{done}/{total_searches}] +{total:,} new | DB: {current_total:,} | Fresh7d: {current_f7:,} | {rate:.0f}/min | Gap: {max(0, TARGET-current_total):,}")
            
            # Mark done
            with _write_lock:
                done_set.add((kw, loc))
            
            work_q.task_done()
    
    # Checkpoint saver thread (saves every 30s, survives kill)
    stop_flag = threading.Event()
    def checkpoint_saver():
        while not stop_flag.is_set():
            time.sleep(30)
            try:
                with batch_counter["lock"]:
                    c_new = batch_counter["new"]
                    c_done = batch_counter["done"]
                cp["done"] = list(done_set)
                cp["total_new"] = total_new + c_new
                cp["batches"] = batch_count + c_done
                save_cp(cp)
                current_total, _ = db.count()
                log(f"  [CKPT] saved | searches={len(done_set)}/{total_searches} | new={c_new:,} | DB={current_total:,}")
            except Exception:
                pass
    ckpt_thread = threading.Thread(target=checkpoint_saver, daemon=True)
    ckpt_thread.start()
    
    # Launch workers
    log(f"Launching {WORKERS} workers on {total_searches} searches...")
    threads = []
    for i in range(WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)
    
    # Wait for completion or timeout
    try:
        work_q.join()
    except Exception:
        pass
    
    # Wait for all threads
    for t in threads:
        t.join(timeout=5)
    
    # Final checkpoint
    stop_flag.set()
    with batch_counter["lock"]:
        c_new = batch_counter["new"]
        c_done = batch_counter["done"]
    cp["done"] = list(done_set)
    cp["total_new"] = total_new + c_new
    cp["batches"] = batch_count + c_done
    save_cp(cp)
    
    final_total, final_f7 = db.count()
    elapsed = (time.time() - start) / 60
    db.close()
    
    log("")
    log("=" * 60)
    log(f"BATCH RUN COMPLETE")
    log(f"Searches: {batch_counter['done']}/{total_searches} | New: +{batch_counter['new']:,}")
    log(f"DB: {final_total:,} | Fresh7d: {final_f7:,} | Gap: {max(0, TARGET-final_total):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {batch_counter['new']/max(elapsed,0.1):.0f}/min")
    log(f"Remaining: {work_q.qsize()} searches")
    log("=" * 60)


if __name__ == "__main__":
    main()
