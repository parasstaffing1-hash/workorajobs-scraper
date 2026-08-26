#!/usr/bin/env python3
"""MEGA BATCH V2 — ALL sources running in parallel until 1M jobs.

Sources:
1. ATS APIs (14+ platforms): Greenhouse, Lever, Ashby, SmartRecruiters, etc.
2. JobSpy: LinkedIn + Indeed keyword searches with pagination
3. Free APIs: TheMuse, WorkingNomads, TCS, YC, USAJobs, Adzuna
4. Web scraping: via Playwright for anti-bot sites

Architecture:
- 12 worker threads per source type (ATS, JobSpy, FreeAPI)
- Shared SQLite writer with WAL mode
- Checkpoint saved every 10 batches
- Auto-resume on restart
- Runs until 1M fresh jobs in last 7 days
"""
from __future__ import annotations

import json
import os
import queue
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "jobs.db"
CP = ROOT / "mega_batch_v2_cp.json"
LOG = ROOT / "mega_batch_v2_log.txt"
TARGET = 1_000_000

# Thread counts per source type
ATS_WORKERS = 20
JOBSPY_WORKERS = 8  # LinkedIn rate-limits with more
FREEAPI_WORKERS = 10
BATCH_SIZE = 100

# ═══════════════════════════════════════════════════════════════
# ATS COMPANY SLUGS (from companies.yaml + mega_probe discovery)
# ═══════════════════════════════════════════════════════════════

def load_ats_slugs() -> list[tuple[str, str]]:
    """Load all ATS company slugs from companies.yaml."""
    slugs = []  # (kind, slug)
    cp = ROOT / "companies.yaml"
    if not cp.exists():
        return slugs
    
    lines = cp.read_text("utf-8").splitlines()
    current_section = None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line and not line.startswith("-"):
            current_section = line.rstrip(":").strip()
            continue
        if line.startswith("-") and current_section:
            slug = line.lstrip("-").strip()
            if slug and "|" not in slug:  # skip keyword pipes for now
                # Map section to ATS kind
                kind_map = {
                    "greenhouse": "greenhouse",
                    "ashby": "ashby",
                    "lever": "lever",
                    "smartrecruiters": "smartrecruiters",
                    "workable": "workable",
                    "breezy": "breezy",
                    "teamtailor": "teamtailor",
                    "workday": "workday",
                    "bamboohr": "bamboohr",
                    "rippling": "rippling",
                    "personio": "personio",
                    "jazzhr": "jazzhr",
                    "freshteam": "freshteam",
                    "fountain": "fountain",
                    "deel": "deel",
                    "phenom": "phenom",
                    "yc": "yc",
                }
                kind = kind_map.get(current_section, "")
                if kind:
                    slugs.append((kind, slug))
    
    # Also add mega_probe discovered slugs
    disc_file = ROOT / "discovered_slugs.json"
    if disc_file.exists():
        try:
            data = json.loads(disc_file.read_text("utf-8"))
            for entry in data:
                if isinstance(entry, dict):
                    kind = entry.get("kind", "")
                    slug = entry.get("slug", "")
                    if kind and slug and (kind, slug) not in slugs:
                        slugs.append((kind, slug))
        except Exception:
            pass
    
    return slugs


def load_jobspy_searches() -> list[tuple[str, str]]:
    """Load keyword x location combos for JobSpy."""
    keywords = [
        "software engineer", "backend engineer", "frontend developer",
        "full stack developer", "data engineer", "devops engineer",
        "machine learning engineer", "product manager", "data scientist",
        "cloud engineer", "android developer", "ios developer",
        "python developer", "java developer", "react developer",
        "AI engineer", "blockchain developer", "security engineer",
        "QA engineer", "site reliability engineer", "platform engineer",
        "infrastructure engineer", "mobile developer", "web developer",
        "software developer", "system administrator", "database administrator",
        "network engineer", "solutions architect", "technical lead",
        "engineering manager", "staff engineer", "principal engineer",
        "senior software engineer", "junior software engineer",
        "remote software engineer", "C++ engineer", "ruby developer",
        "PHP developer", "scala developer", "kotlin developer", "swift developer",
        "Vue.js developer", "Angular developer", "data analyst",
        "IT recruiter", "talent acquisition", "scrum master",
        "business analyst", "UX designer", "UI developer",
        "robotics engineer", "embedded systems engineer", "firmware engineer",
        "game developer", "graphics engineer", "video engineer",
        "database engineer", "storage engineer", "compiler engineer",
        "fintech engineer", "payments engineer", "enterprise architect",
        "solutions engineer", "sales engineer", "systems engineer",
        "cloud architect", "security architect", "ML platform engineer",
        "data platform engineer", "backend developer", "frontend engineer",
        "full stack engineer", "web application developer", "API engineer",
        "microservices engineer", "distributed systems engineer",
        "go developer", "golang engineer", "node.js developer",
        "django developer", "flask developer", "fastapi developer",
        "spring boot developer", "ruby on rails developer", ".NET developer",
        "ASP.NET developer", "laravel developer",
        "aws engineer", "azure engineer", "gcp engineer",
        "serverless engineer", "kubernetes engineer", "terraform engineer",
        "CI/CD engineer", "release engineer", "build engineer",
        "automation engineer", "test automation engineer", "SDET",
        "SDE", "MLE", "DevOps", "SRE",
        "technical program manager", "project manager",
        "growth engineer", "analytics engineer",
        "quantitative developer", "algorithm engineer",
        "computer vision engineer", "NLP engineer",
        "IoT engineer", "edge computing engineer",
        "DevTools engineer", "cloud security engineer",
        "penetration tester", "SOC engineer",
        "hiring", "recruitment", "talent",
        "devrel", "developer advocate",
        "cto", "vp engineering", "engineering director",
        "lead developer", "tech lead", "principal",
        "staff+", "distinguished engineer",
        "open source", "linux kernel", "compiler",
        "llvm", "gcc", "rust developer",
        "elixir developer", "haskell developer",
        "clojure developer", "erlang developer",
        "perl developer", "typescript developer",
        "javascript developer", "css developer",
        "html developer", "web3 developer",
        "solidity developer", "smart contract",
        "defi", "nft", "dao",
        "unity developer", "unreal developer",
        "3d artist", "animation engineer",
        "audio engineer", "sound engineer",
        "networking engineer", "protocol engineer",
        "storage engineer", "file systems",
        "kernel engineer", "os engineer",
        "virtualization engineer", "hypervisor",
        "container engineer", "docker engineer",
        "monitoring engineer", "observability",
        "chaos engineering", "incident response",
        "disaster recovery", "backup engineer",
        "data warehousing", "etl developer",
        "bi developer", "reporting engineer",
        "etl engineer", "data migration",
        "data governance", "data quality",
        "mlops engineer", "ml infrastructure",
        "feature store", "model serving",
        "inference engineer", "training engineer",
        "reinforcement learning", "recommendation systems",
        "search engineer", "ranking engineer",
        "personalization engineer", "ads engineer",
        "growth hacking", "ab testing",
        "experimentation platform", "feature flag",
        "canary deployment", "blue green deployment",
    ]
    
    locations = [
        "", "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Mumbai", "Pune",
        "Delhi", "Noida", "Gurgaon", "Gurugram", "Kolkata", "Ahmedabad",
        "Jaipur", "Kochi", "Coimbatore", "Indore", "Lucknow", "Chandigarh",
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
        "Cape Town", "Lagos", "Remote",
    ]
    
    return [(kw, loc) for kw in keywords for loc in locations]


# ═══════════════════════════════════════════════════════════════
# LOGGING & CHECKPOINT
# ═══════════════════════════════════════════════════════════════

_log_lock = threading.Lock()

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
    return {
        "ats_done": [],
        "jobspy_done": [],
        "ats_idx": 0,
        "jobspy_idx": 0,
        "total_new": 0,
        "batches": 0,
    }


def save_cp(cp):
    try:
        CP.write_text(json.dumps({
            "ats_done": cp["ats_done"][-10000:],
            "jobspy_done": cp["jobspy_done"][-10000:],
            "ats_idx": cp["ats_idx"],
            "jobspy_idx": cp["jobspy_idx"],
            "total_new": cp["total_new"],
            "batches": cp["batches"],
        }, indent=1), "utf-8")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# DATABASE WRITER (single thread, WAL mode)
# ═══════════════════════════════════════════════════════════════

class DBWriter:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-64000")
        self.total_new = 0
        self.lock = threading.Lock()
    
    def add_jobs(self, jobs: list) -> int:
        new = 0
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            for j in jobs:
                try:
                    cur = self.conn.execute(
                        "INSERT OR IGNORE INTO jobs "
                        "(dedupe_key,title,company,location,description,url,source,source_kind,"
                        "external_id,posted_at,salary,tags,first_seen_at,last_seen_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            getattr(j, "url", "") or getattr(j, "external_id", ""),
                            getattr(j, "title", ""),
                            getattr(j, "company", ""),
                            getattr(j, "location", ""),
                            (getattr(j, "description", "") or "")[:500],
                            getattr(j, "url", ""),
                            getattr(j, "source", ""),
                            getattr(j, "source_kind", ""),
                            getattr(j, "external_id", ""),
                            str(getattr(j, "posted_at", "")) if getattr(j, "posted_at", None) else None,
                            getattr(j, "salary", ""),
                            ",".join(getattr(j, "tags", []) or []),
                            now,
                            now,
                        )
                    )
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
            f7 = self.conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE first_seen_at >= datetime('now','-7 days')"
            ).fetchone()[0]
            return t, f7
        except Exception:
            return 0, 0
    
    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# ATS WORKER
# ═══════════════════════════════════════════════════════════════

def scrape_ats_batch(kind: str, slug: str) -> list:
    """Scrape one ATS board. Returns list of Job objects."""
    try:
        import httpx
        from jobcollector.sources.ats_api import fetch_ats_api
        
        client = httpx.Client(timeout=10, follow_redirects=True)
        try:
            jobs = fetch_ats_api(client, kind, slug, limit=200)
            return jobs
        except Exception:
            return []
        finally:
            client.close()
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# JOBSPY WORKER
# ═══════════════════════════════════════════════════════════════

def scrape_jobspy_batch(kw: str, loc: str) -> list:
    """Scrape one keyword+location via JobSpy. Returns list of Job-like dicts."""
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
            
            # Create a simple object that has the same attributes as Job
            class JobObj:
                pass
            
            j = JobObj()
            j.title = str(row.get("title", ""))
            j.company = str(row.get("company", ""))
            j.location = str(row.get("location", ""))
            j.url = job_url
            j.description = str(row.get("description", "") or "")[:500]
            j.source = f"jobspy:{row.get('site', 'unknown')}"
            j.source_kind = "web"
            j.external_id = str(row.get("id", ""))
            j.posted_at = row.get("date_posted")
            j.salary = salary
            j.tags = []
            jobs.append(j)
        
        return jobs
    except Exception as e:
        log(f"  ERR JobSpy {kw}|{loc}: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
# FREE API WORKER
# ═══════════════════════════════════════════════════════════════

def scrape_free_apis() -> list:
    """Scrape all free APIs (TheMuse, WorkingNomads, TCS, YC, etc.)."""
    all_jobs = []
    
    try:
        import httpx
        from jobcollector.sources.ats_api import fetch_ats_api
        
        client = httpx.Client(timeout=15, follow_redirects=True)
        try:
            # TheMuse
            try:
                jobs = fetch_ats_api(client, "themuse", "", limit=200)
                all_jobs.extend(jobs)
                log(f"  [FreeAPI] TheMuse: +{len(jobs)}")
            except Exception as e:
                log(f"  [FreeAPI] TheMuse ERR: {e}")
            
            # WorkingNomads
            try:
                jobs = fetch_ats_api(client, "workingnomads", "", limit=200)
                all_jobs.extend(jobs)
                log(f"  [FreeAPI] WorkingNomads: +{len(jobs)}")
            except Exception as e:
                log(f"  [FreeAPI] WorkingNomads ERR: {e}")
            
            # TCS
            try:
                jobs = fetch_ats_api(client, "tcs", "tcs", limit=200)
                all_jobs.extend(jobs)
                log(f"  [FreeAPI] TCS: +{len(jobs)}")
            except Exception as e:
                log(f"  [FreeAPI] TCS ERR: {e}")
            
            # Rise
            try:
                jobs = fetch_ats_api(client, "rise", "", limit=200)
                all_jobs.extend(jobs)
                log(f"  [FreeAPI] Rise: +{len(jobs)}")
            except Exception as e:
                log(f"  [FreeAPI] Rise ERR: {e}")
        finally:
            client.close()
    except Exception as e:
        log(f"  [FreeAPI] ERR: {e}")
    
    return all_jobs


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    log("=" * 70)
    log("MEGA BATCH V2 — ALL SOURCES PARALLEL")
    log("=" * 70)
    
    # Load all work items
    ats_slugs = load_ats_slugs()
    jobspy_searches = load_jobspy_searches()
    
    log(f"ATS slugs: {len(ats_slugs)}")
    log(f"JobSpy searches: {len(jobspy_searches)}")
    
    # Load checkpoint
    cp = load_cp()
    ats_done_set = set(tuple(d) for d in cp["ats_done"])
    jobspy_done_set = set(tuple(d) for d in cp["jobspy_done"])
    
    ats_remaining = [(k, s) for k, s in ats_slugs if (k, s) not in ats_done_set]
    jobspy_remaining = [(k, l) for k, l in jobspy_searches if (k, l) not in jobspy_done_set]
    
    log(f"ATS: {len(ats_done_set)} done, {len(ats_remaining)} remaining")
    log(f"JobSpy: {len(jobspy_done_set)} done, {len(jobspy_remaining)} remaining")
    
    if not ats_remaining and not jobspy_remaining:
        log("All work done! Resetting...")
        ats_done_set.clear()
        jobspy_done_set.clear()
        ats_remaining = ats_slugs[:]
        jobspy_remaining = jobspy_searches[:]
        cp["ats_done"] = []
        cp["jobspy_done"] = []
    
    total_before, fresh7_before = DBWriter().count()
    log(f"DB: {total_before:,} | Fresh7d: {fresh7_before:,} | Gap: {max(0, TARGET-total_before):,}")
    
    db = DBWriter()
    start = time.time()
    
    # Create work queues
    ats_q = queue.Queue()
    for item in ats_remaining:
        ats_q.put(item)
    
    jobspy_q = queue.Queue()
    for item in jobspy_remaining:
        jobspy_q.put(item)
    
    # Counters
    counters = {
        "ats_done": 0, "ats_new": 0,
        "jobspy_done": 0, "jobspy_new": 0,
        "freeapi_new": 0,
        "lock": threading.Lock(),
    }
    
    # ─── ATS Workers ───
    def ats_worker(wid):
        while True:
            try:
                kind, slug = ats_q.get(timeout=3)
            except queue.Empty:
                break
            jobs = scrape_ats_batch(kind, slug)
            if jobs:
                new = db.add_jobs(jobs)
                with counters["lock"]:
                    counters["ats_done"] += 1
                    counters["ats_new"] += new
                    d = counters["ats_done"]
                    n = counters["ats_new"]
                if new > 0:
                    log(f"  A{wid:02d} +{new:3d} {kind}:{slug} ({len(jobs)} raw)")
                if d % 100 == 0:
                    log(f"  [ATS {d}/{len(ats_remaining)}] +{n:,} new")
            with counters["lock"]:
                pass
            ats_q.task_done()
    
    # ─── JobSpy Workers ───
    def jobspy_worker(wid):
        while True:
            try:
                kw, loc = jobspy_q.get(timeout=3)
            except queue.Empty:
                break
            jobs = scrape_jobspy_batch(kw, loc)
            if jobs:
                new = db.add_jobs(jobs)
                with counters["lock"]:
                    counters["jobspy_done"] += 1
                    counters["jobspy_new"] += new
                    d = counters["jobspy_done"]
                    n = counters["jobspy_new"]
                if new > 0:
                    log(f"  J{wid:02d} +{new:3d} {kw[:25]:25s}|{loc[:15]:15s}")
                if d % 100 == 0:
                    log(f"  [JobSpy {d}/{len(jobspy_remaining)}] +{n:,} new")
            jobspy_q.task_done()
    
    # ─── Free API Worker ───
    def freeapi_worker():
        while True:
            jobs = scrape_free_apis()
            if jobs:
                new = db.add_jobs(jobs)
                with counters["lock"]:
                    counters["freeapi_new"] += new
                log(f"  [FreeAPI] +{new} new (total +{counters['freeapi_new']})")
            time.sleep(300)  # Re-run free APIs every 5 minutes
    
    # ─── Checkpoint Saver ───
    stop_flag = threading.Event()
    def checkpoint_saver():
        while not stop_flag.is_set():
            time.sleep(30)
            try:
                with counters["lock"]:
                    c_ats = counters["ats_done"]
                    c_ats_new = counters["ats_new"]
                    c_js = counters["jobspy_done"]
                    c_js_new = counters["jobspy_new"]
                    c_free = counters["freeapi_new"]
                
                cp["ats_done"] = list(ats_done_set)
                cp["jobspy_done"] = list(jobspy_done_set)
                cp["total_new"] = c_ats_new + c_js_new + c_free
                cp["batches"] = c_ats + c_js
                save_cp(cp)
                
                current_total, current_f7 = db.count()
                elapsed = (time.time() - start) / 60
                total_new = c_ats_new + c_js_new + c_free
                rate = total_new / max(elapsed, 0.1)
                log(f"  [CKPT] ATS {c_ats}/{len(ats_remaining)} +{c_ats_new} | "
                    f"JS {c_js}/{len(jobspy_remaining)} +{c_js_new} | "
                    f"Free +{c_free} | DB={current_total:,} | "
                    f"Fresh7d={current_f7:,} | {rate:.0f}/min")
            except Exception:
                pass
    
    # Launch all workers
    log(f"Launching ATS={ATS_WORKERS}, JobSpy={JOBSPY_WORKERS}, FreeAPI={FREEAPI_WORKERS}")
    
    threads = []
    
    # ATS workers
    for i in range(ATS_WORKERS):
        t = threading.Thread(target=ats_worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)
    
    # JobSpy workers
    for i in range(JOBSPY_WORKERS):
        t = threading.Thread(target=jobspy_worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)
    
    # Free API worker
    t = threading.Thread(target=freeapi_worker, daemon=True)
    t.start()
    threads.append(t)
    
    # Checkpoint saver
    t = threading.Thread(target=checkpoint_saver, daemon=True)
    t.start()
    threads.append(t)
    
    # Wait for all queues to drain
    try:
        ats_q.join()
    except Exception:
        pass
    try:
        jobspy_q.join()
    except Exception:
        pass
    
    # Wait for threads
    for t in threads:
        t.join(timeout=10)
    
    stop_flag.set()
    
    # Final checkpoint
    with counters["lock"]:
        final_new = counters["ats_new"] + counters["jobspy_new"] + counters["freeapi_new"]
    
    cp["ats_done"] = list(ats_done_set)
    cp["jobspy_done"] = list(jobspy_done_set)
    cp["total_new"] = final_new
    save_cp(cp)
    
    final_total, final_f7 = db.count()
    elapsed = (time.time() - start) / 60
    db.close()
    
    log("")
    log("=" * 70)
    log(f"MEGA BATCH V2 COMPLETE")
    log(f"ATS: {counters['ats_done']}/{len(ats_remaining)} | +{counters['ats_new']:,}")
    log(f"JobSpy: {counters['jobspy_done']}/{len(jobspy_remaining)} | +{counters['jobspy_new']:,}")
    log(f"FreeAPI: +{counters['freeapi_new']:,}")
    log(f"Total new: +{final_new:,}")
    log(f"DB: {final_total:,} | Fresh7d: {final_f7:,}")
    log(f"Gap to 1M: {max(0, TARGET-final_total):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {final_new/max(elapsed,0.1):.0f}/min")
    log("=" * 70)


if __name__ == "__main__":
    main()
