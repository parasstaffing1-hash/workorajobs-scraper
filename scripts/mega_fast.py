#!/usr/bin/env python3
"""MEGA FAST — 100 workers scraping from 6 sites with massive keyword/location matrix.

Target: 1,000,000 fresh jobs in last 7 days.
Architecture: checkpoint saves every 10 iterations per worker, auto-relaunch via scheduled task.
"""
from __future__ import annotations
import json, os, queue, sqlite3, sys, threading, time, random
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "mega_cp.json"
LOG_PATH = ROOT / "mega_log.txt"
TARGET = 1_000_000
WORKERS = 100
PER_PAGE = 50

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

SITES = ["linkedin", "indeed", "google", "ziprecruiter", "glassdoor", "naukri"]

KWS = [
    "software engineer", "backend engineer", "frontend developer", "full stack developer",
    "data engineer", "devops engineer", "machine learning engineer", "product manager",
    "data scientist", "cloud engineer", "android developer", "ios developer",
    "python developer", "java developer", "react developer", "AI engineer",
    "blockchain developer", "security engineer", "QA engineer", "site reliability engineer",
    "platform engineer", "infrastructure engineer", "mobile developer", "web developer",
    "software developer", "technical lead", "engineering manager", "staff engineer",
    "principal engineer", "SDE", "SRE", "senior software engineer", "junior software engineer",
    "remote software engineer", "C++ engineer", "ruby developer", "PHP developer",
    "scala developer", "kotlin developer", "swift developer", "Vue.js developer",
    "Angular developer", "data analyst", "IT recruiter", "scrum master",
    "business analyst", "UX designer", "robotics engineer", "embedded systems engineer",
    "firmware engineer", "game developer", "graphics engineer", "database engineer",
    "fintech engineer", "solutions architect", "cloud architect", "go developer",
    "node.js developer", "django developer", "flask developer", "fastapi developer",
    "spring boot developer", ".NET developer", "laravel developer", "aws engineer",
    "azure engineer", "gcp engineer", "kubernetes engineer", "terraform engineer",
    "CI/CD engineer", "test automation engineer", "MLE", "DevOps", "golang engineer",
    "typescript developer", "javascript developer", "rust developer", "NLP engineer",
    "computer vision engineer", "IoT engineer", "devrel", "developer advocate",
    "tech lead", "penetration tester", "cloud security engineer", "MLOps engineer",
    "search engineer", "growth engineer", "payments engineer", "AR engineer", "VR engineer",
    "systems engineer", "release engineer", "build engineer", "automation engineer",
    "container engineer", "docker engineer", "monitoring engineer", "ETL engineer",
    "streaming engineer", "trading systems engineer", "network engineer",
    "storage engineer", "kernel engineer", "compiler engineer",
    "distributed systems engineer", "microservices engineer", "API engineer",
    "frontend engineer", "backend developer", "web application developer",
    "platform developer", "developer experience engineer", "quantitative developer",
    "algorithm engineer", "video engineer", "audio engineer", "identity engineer",
    "solidity developer", "web3 developer", "unity developer", "unreal developer",
    "elixir developer", "haskell developer", "perl developer", "rust systems engineer",
    "react native developer", "flutter developer", "graphql engineer",
    "linux engineer", "windows engineer", "macos engineer",
    "cybersecurity analyst", "information security engineer",
    "data platform engineer", "ML platform engineer", "analytics engineer",
    "recommendation engineer", "ad tech engineer", "search infrastructure engineer",
    "payment systems engineer", "billing engineer", "ledger engineer",
    "real-time engineer", "low-latency engineer", "high-performance computing engineer",
    "simulation engineer", "cryptography engineer", "zero trust engineer",
    "data governance engineer", "data quality engineer", "feature store engineer",
    "model serving engineer", "inference engineer", "training engineer",
    "GPU engineer", "CUDA engineer", "FPGA engineer",
    "vision engineer", "speech engineer", "audio ML engineer",
    "genomics engineer", "bioinformatics engineer", "computational biology engineer",
    "geospatial engineer", "GIS engineer", "mapping engineer",
    "AR/VR engineer", "spatial computing engineer", "mixed reality engineer",
    "quantum computing engineer", "quantum software engineer",
    "robotics perception engineer", "motion planning engineer", "autonomous systems engineer",
    "ADAS engineer", "self-driving engineer", "computer vision software engineer",
    "NLP software engineer", "conversational AI engineer", "chatbot engineer",
    "LLM engineer", "large language model engineer", "generative AI engineer",
    "prompt engineer", "AI safety engineer", "alignment engineer",
    "data ops engineer", "platform ops engineer", "developer productivity engineer",
    "internal tools engineer", "developer platform engineer",
    "site reliability manager", "production engineer", "incident commander",
    "chaos engineer", "resilience engineer", "disaster recovery engineer",
]

LOCS = [
    "", "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Mumbai",
    "Pune", "Delhi", "Noida", "Gurgaon", "Gurugram", "Kolkata",
    "Ahmedabad", "Jaipur", "Kochi", "Coimbatore", "Indore",
    "Lucknow", "Chandigarh", "New York", "San Francisco", "Seattle",
    "Austin", "Boston", "Chicago", "Los Angeles", "Denver", "Atlanta",
    "Miami", "Washington DC", "Portland", "San Diego", "Dallas", "Houston",
    "San Jose", "Raleigh", "Charlotte", "Minneapolis", "Detroit",
    "Phoenix", "Tampa", "Orlando", "Nashville", "Salt Lake City",
    "London", "Manchester", "Birmingham", "Edinburgh", "Bristol",
    "Berlin", "Munich", "Hamburg", "Frankfurt", "Cologne",
    "Paris", "Lyon", "Marseille", "Amsterdam", "Rotterdam",
    "Dublin", "Cork", "Toronto", "Vancouver", "Montreal", "Ottawa",
    "Singapore", "Hong Kong", "Tokyo", "Seoul",
    "Sydney", "Melbourne", "Brisbane", "Perth",
    "Dubai", "Abu Dhabi", "Riyadh", "Stockholm", "Oslo", "Copenhagen",
    "Warsaw", "Prague", "Budapest", "Sao Paulo", "Mexico City",
    "Cape Town", "Lagos", "Remote", "Tel Aviv",
]

OFFSETS = [0, 50, 100, 150, 200, 250, 300, 350, 400, 450]


def build_items():
    items = []
    for site in SITES:
        for kw in KWS:
            for loc in LOCS:
                for off in OFFSETS:
                    items.append((site, kw, loc, off))
    return items


def scrape(site, kw, loc, offset):
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
                "title": str(r.get("title", "")), "company": str(r.get("company", "")),
                "location": str(r.get("location", "")), "url": url,
                "desc": str(r.get("description", "") or "")[:500],
                "source": f"jobspy:{site}", "id": str(r.get("id", "")),
                "posted": str(r.get("date_posted")) if r.get("date_posted") else None,
                "salary": sal,
            })
        return jobs
    except:
        return []


def main():
    max_hours = 8
    for i, a in enumerate(sys.argv[1:]):
        if a == "--hours":
            try: max_hours = float(sys.argv[i+2])
            except: pass

    log("=" * 50)
    log(f"MEGA FAST — {WORKERS} workers, {max_hours}h max")
    log("=" * 50)

    all_items = build_items()
    log(f"Total items: {len(all_items):,}")

    cp = load_cp()
    done_set = set(tuple(d) for d in cp["done"])
    remaining = [i for i in all_items if tuple(i) not in done_set]
    log(f"Done: {len(done_set):,}, Remaining: {len(remaining):,}")

    if not remaining:
        done_set.clear(); remaining = all_items[:]; cp["done"] = []

    db = DB()
    t0, f0 = db.count()
    log(f"DB: {t0:,} | Fresh7d: {f0:,} | Gap: {max(0, TARGET-t0):,}")

    random.shuffle(remaining)
    wq = queue.Queue()
    for item in remaining: wq.put(item)

    start = time.time()
    counter = {"iters": 0, "new": 0}
    c_lock = threading.Lock()

    def worker(wid):
        local_iters = 0
        while True:
            if (time.time() - start) / 3600 >= max_hours: break
            try: site, kw, loc, off = wq.get(timeout=3)
            except queue.Empty: break
            jobs = scrape(site, kw, loc, off)
            new = db.insert(jobs) if jobs else 0
            local_iters += 1
            with c_lock:
                counter["iters"] += 1
                counter["new"] += new
                done_set.add((site, kw, loc, off))
                iters = counter["iters"]
                total_new = counter["new"]
            if new > 15:
                log(f"  W{wid:03d} +{new:3d} {site}:{kw[:18]}|{loc[:10]} o={off}")
            if local_iters % 10 == 0:
                cp["done"] = list(done_set)
                cp["total_new"] = total_new
                cp["iterations"] = iters
                save_cp(cp)
            if iters % 500 == 0:
                ct, cf = db.count()
                rate = total_new / max((time.time()-start)/60, 0.1)
                log(f"  [{iters:,}] +{total_new:,} new | DB={ct:,} | Fresh7d={cf:,} | {rate:.0f}/min | Gap={max(0,TARGET-ct):,}")
            wq.task_done()

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
    log(f"COMPLETE | Iters: {counter['iters']:,} | New: +{counter['new']:,}")
    log(f"DB: {ft:,} | Fresh7d: {ff:,} | Gap: {max(0,TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 50)

if __name__ == "__main__":
    main()
