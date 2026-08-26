#!/usr/bin/env python3
"""ULTRA SCRAPE v2 — maximum yield per iteration.

Key optimization: results_wanted=100 (was 50) = 2x more jobs per search.
Strategy:
1. ATS APIs first (100-400 unique jobs per company, ZERO dedup, 1-3s each)
2. JobSpy with results_wanted=100 (doubles yield)
3. 100 workers, checkpoint every 10 iterations
"""
from __future__ import annotations
import json, os, queue, sqlite3, sys, threading, time, random
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "ultra_cp.json"
LOG_PATH = ROOT / "ultra_log.txt"
TARGET = 1_000_000
WORKERS = 100
PER_PAGE = 100  # doubled from 50!

_lock = threading.Lock()
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        try:
            with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{ts}] {msg}\n")
        except: pass

def load_cp():
    if CP_PATH.exists():
        try: return json.loads(CP_PATH.read_text("utf-8"))
        except: pass
    return {"done": [], "total_new": 0, "iterations": 0}

def save_cp(cp):
    try:
        CP_PATH.write_text(json.dumps({
            "done": cp["done"][-100000:],
            "total_new": cp["total_new"],
            "iterations": cp["iterations"],
        }), "utf-8")
    except: pass

class DB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-128000")
        self.lock = threading.Lock()

    def insert(self, jobs):
        new = 0
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            for j in jobs:
                try:
                    cur = self.conn.execute(
                        "INSERT OR IGNORE INTO jobs "
                        "(dedupe_key,title,company,location,description,url,"
                        "source,source_kind,external_id,posted_at,salary,tags,"
                        "first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (j["url"] or j.get("id",""), j["title"], j.get("company",""),
                         j.get("location",""), j.get("desc","")[:500], j["url"],
                         j["source"], "web", j.get("id",""),
                         j.get("posted"), j.get("salary",""), "",
                         now, now))
                    if cur.rowcount > 0: new += 1
                except: continue
            if new > 0: self.conn.commit()
        return new

    def count(self):
        with self.lock:
            t = self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            f = self.conn.execute("SELECT COUNT(*) FROM jobs WHERE first_seen_at >= datetime('now','-7 days')").fetchone()[0]
            return t, f

    def close(self):
        try: self.conn.close()
        except: pass

# ═══════════════════════════════════════════════════════════════
# ATS ITEMS — highest yield, zero dedup between companies
# ═══════════════════════════════════════════════════════════════

def build_ats_items():
    items = []
    cp = ROOT / "companies.yaml"
    if not cp.exists():
        return items
    section = None
    for line in cp.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
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
                    items.append(("ats", kind, slug, 0))
    return items

def work_ats(client, kind, slug):
    try:
        from jobcollector.sources.ats_api import fetch_ats_api
        jobs = fetch_ats_api(client, kind, slug, limit=200)
        dicts = []
        for j in jobs:
            dicts.append({
                "url": j.url, "title": j.title,
                "company": j.company, "location": j.location,
                "desc": j.description or "", "source": j.source,
                "id": j.external_id or "",
                "posted": str(j.posted_at) if j.posted_at else None,
                "salary": j.salary or "",
            })
        return dicts
    except:
        return []

# ═══════════════════════════════════════════════════════════════
# JOBSPY — maximum yield per search
# ═══════════════════════════════════════════════════════════════

SITES = ["linkedin", "indeed", "google", "ziprecruiter", "glassdoor", "naukri"]

# Broad keywords that return many results
KWS = [
    "software engineer", "backend engineer", "frontend developer", "full stack developer",
    "data engineer", "devops engineer", "machine learning engineer", "product manager",
    "data scientist", "cloud engineer", "android developer", "ios developer",
    "python developer", "java developer", "react developer", "AI engineer",
    "security engineer", "QA engineer", "SRE", "platform engineer",
    "infrastructure engineer", "mobile developer", "web developer",
    "software developer", "technical lead", "engineering manager",
    "staff engineer", "principal engineer", "SDE",
    "senior software engineer", "junior software engineer",
    "C++ engineer", "ruby developer", "PHP developer",
    "kotlin developer", "swift developer", "Angular developer",
    "data analyst", "IT recruiter", "scrum master", "business analyst",
    "UX designer", "robotics engineer", "embedded systems engineer",
    "firmware engineer", "game developer", "database engineer",
    "solutions architect", "cloud architect", "go developer",
    "node.js developer", "django developer", "flask developer",
    "fastapi developer", "spring boot developer", ".NET developer",
    "aws engineer", "azure engineer", "gcp engineer",
    "kubernetes engineer", "terraform engineer", "CI/CD engineer",
    "MLE", "DevOps", "golang engineer", "typescript developer",
    "rust developer", "NLP engineer", "computer vision engineer",
    "devrel", "tech lead", "MLOps engineer", "payments engineer",
    "network engineer", "container engineer", "LLM engineer",
    "generative AI engineer", "prompt engineer",
    "systems engineer", "build engineer", "automation engineer",
    "distributed systems engineer", "microservices engineer",
    "backend developer", "frontend engineer", "platform developer",
    "data platform engineer", "ML platform engineer", "analytics engineer",
    "identity engineer", "solidity developer", "web3 developer",
    "unity developer", "react native developer", "flutter developer",
    "linux engineer", "cybersecurity analyst", "video engineer",
    "kernel engineer", "storage engineer", "streaming engineer",
]

LOCS = [
    "", "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Mumbai", "Pune",
    "Delhi", "Noida", "Gurgaon", "Gurugram", "Kolkata", "Ahmedabad",
    "Jaipur", "Kochi", "Coimbatore", "Indore", "Lucknow", "Chandigarh",
    "New York", "San Francisco", "Seattle", "Austin", "Boston", "Chicago",
    "Los Angeles", "Denver", "Atlanta", "Miami", "Washington DC", "Portland",
    "San Diego", "Dallas", "Houston", "San Jose", "Raleigh", "Charlotte",
    "Minneapolis", "Detroit", "Phoenix", "Tampa", "Orlando", "Nashville",
    "London", "Manchester", "Edinburgh", "Berlin", "Munich", "Paris",
    "Amsterdam", "Dublin", "Toronto", "Vancouver", "Montreal",
    "Singapore", "Hong Kong", "Tokyo", "Seoul", "Sydney", "Melbourne",
    "Dubai", "Stockholm", "Warsaw", "Prague", "Sao Paulo", "Mexico City",
    "Cape Town", "Lagos", "Remote", "Tel Aviv",
]

OFFSETS = [0, 100, 200, 300, 400]

def build_jobspy_items():
    items = []
    for site in SITES:
        for kw in KWS:
            for loc in LOCS:
                for off in OFFSETS:
                    items.append(("jobspy", site, kw, loc, off))
    return items

def scrape_jobspy(site, kw, loc, offset):
    try:
        from jobspy import scrape_jobs
        df = scrape_jobs(site_name=[site], search_term=kw,
                         location=loc if loc else None,
                         results_wanted=PER_PAGE, offset=offset)
        if df is None or df.empty: return []
        jobs = []
        for _, r in df.iterrows():
            url = str(r.get("job_url", ""))
            if not url: continue
            sal = ""
            if r.get("min_amount") and r.get("max_amount"):
                sal = f"{r.get('currency','')} {r.get('min_amount')}-{r.get('max_amount')} {r.get('interval','')}"
            jobs.append({
                "url": url, "title": str(r.get("title", "")),
                "company": str(r.get("company", "")),
                "location": str(r.get("location", "")),
                "desc": str(r.get("description", "") or "")[:500],
                "source": f"jobspy:{site}", "id": str(r.get("id", "")),
                "posted": str(r.get("date_posted")) if r.get("date_posted") else None,
                "salary": sal,
            })
        return jobs
    except:
        return []

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    max_hours = 8
    for i, a in enumerate(sys.argv[1:]):
        if a == "--hours":
            try: max_hours = float(sys.argv[i+2])
            except: pass

    log("=" * 50)
    log(f"ULTRA SCRAPE v2 — {WORKERS} workers, results_wanted={PER_PAGE}, {max_hours}h max")
    log("=" * 50)

    ats_items = build_ats_items()
    jobspy_items = build_jobspy_items()
    all_items = ats_items + jobspy_items
    log(f"ATS: {len(ats_items):,} | JobSpy: {len(jobspy_items):,} | Total: {len(all_items):,}")

    cp = load_cp()
    done_set = set(tuple(d) for d in cp["done"])
    remaining = [i for i in all_items if tuple(i) not in done_set]
    log(f"Done: {len(done_set):,}, Remaining: {len(remaining):,}")

    if not remaining:
        done_set.clear(); remaining = all_items[:]; cp["done"] = []

    db = DB()
    t0, f0 = db.count()
    log(f"DB: {t0:,} | Fresh7d: {f0:,} | Gap: {max(0, TARGET-t0):,}")

    # ATS items first (highest yield), then shuffle JobSpy
    random.shuffle(remaining)
    wq = queue.Queue()
    for item in remaining: wq.put(item)

    start = time.time()
    counter = {"iters": 0, "new": 0}
    c_lock = threading.Lock()
    ats_count = [0]
    js_count = [0]

    def worker(wid):
        import httpx
        client = httpx.Client(timeout=15, follow_redirects=True)
        local_iters = 0
        try:
            while True:
                if (time.time() - start) / 3600 >= max_hours: break
                try: item = wq.get(timeout=3)
                except queue.Empty: break

                itype = item[0]
                if itype == "ats":
                    kind, slug = item[1], item[2]
                    jobs = work_ats(client, kind, slug)
                    key = f"ats:{kind}:{slug}"
                    with c_lock: ats_count[0] += 1
                else:
                    site, kw, loc, off = item[1], item[2], item[3], item[4]
                    jobs = scrape_jobspy(site, kw, loc, off)
                    key = f"js:{site}:{kw}:{loc}:{off}"
                    with c_lock: js_count[0] += 1

                new = db.insert(jobs) if jobs else 0
                local_iters += 1
                with c_lock:
                    counter["iters"] += 1
                    counter["new"] += new
                    done_set.add(key)
                    iters = counter["iters"]
                    total_new = counter["new"]

                if new > 20:
                    label = f"{kind}:{slug}" if itype == "ats" else f"{site}:{kw[:18]}|{loc[:8]} o={off}"
                    log(f"  W{wid:03d} +{new:3d} {label}")

                if local_iters % 10 == 0:
                    cp["done"] = list(done_set)
                    cp["total_new"] = total_new
                    cp["iterations"] = iters
                    save_cp(cp)

                if iters % 500 == 0:
                    ct, cf = db.count()
                    rate = total_new / max((time.time()-start)/60, 0.1)
                    log(f"  [{iters:,}] ATS:{ats_count[0]:,} JS:{js_count[0]:,} | +{total_new:,} | DB={ct:,} | Fresh7d={cf:,} | {rate:.0f}/min | Gap={max(0,TARGET-ct):,}")

                wq.task_done()
        finally:
            try: client.close()
            except: pass

    log(f"Launching {WORKERS} workers on {len(remaining):,} items...")
    threads = []
    for i in range(min(WORKERS, len(remaining))):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start(); threads.append(t)

    try: wq.join()
    except: pass
    for t in threads: t.join(timeout=10)

    cp["done"] = list(done_set)
    save_cp(cp)

    ft, ff = db.count()
    elapsed = (time.time()-start)/60
    db.close()

    log("=" * 50)
    log(f"COMPLETE | ATS:{ats_count[0]:,} JS:{js_count[0]:,} | New: +{counter['new']:,}")
    log(f"DB: {ft:,} | Fresh7d: {ff:,} | Gap: {max(0,TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 50)

if __name__ == "__main__":
    main()
