#!/usr/bin/env python3
"""MEGA SCRAPER v7 — Smart source rotation, DB dedup with local cache.

Key improvements over v6:
1. Skips saturated LinkedIn/Indeed (already 434K in DB)
2. Focuses on Google, ZipRecruiter, Glassdoor, Naukri, Dice, SimplyHired
3. DB-based dedup with local cache (no OOM)
4. 50 workers with per-site rate limiting
5. 10-min batch with auto-save checkpoint
"""
from __future__ import annotations
import hashlib, json, os, queue, sqlite3, sys, threading, time, random
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "mega_cp.json"
LOG_PATH = ROOT / "mega_log.txt"
TARGET = 1_000_000
WORKERS = 25
PER_PAGE = 100
BATCH_SECONDS = 600  # 10 minutes per batch

# Rate limits per site (concurrent workers)
SITE_SEMAPHORES = {}
SITE_LIMITS = {
    "google": 8, "ziprecruiter": 8, "glassdoor": 8,
    "naukri": 10, "simplyhired": 10, "dice": 10,
    "linkedin": 2, "indeed": 2,  # SATURATED - low limits
    "indeed_direct": 3, "ats": 10,
}

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
        try:
            d = json.loads(CP_PATH.read_text("utf-8"))
            return {"done": set(d.get("done", [])),
                    "total_new": d.get("total_new", 0),
                    "iterations": d.get("iterations", 0),
                    "round": d.get("round", 0)}
        except: pass
    return {"done": set(), "total_new": 0, "iterations": 0, "round": 0}

def save_cp(cp):
    try:
        done_list = list(cp["done"])
        if len(done_list) > 300000:
            done_list = done_list[-300000:]
        CP_PATH.write_text(json.dumps({
            "done": done_list,
            "total_new": cp["total_new"],
            "iterations": cp["iterations"],
            "round": cp.get("round", 0),
        }), "utf-8")
    except: pass

class JobDB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-64000")
        self.lock = threading.Lock()

    def _hash(self, url: str, title: str, company: str) -> str:
        raw = f"{url.strip().lower()}|{title.strip().lower()}|{company.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def insert(self, jobs, seen: set):
        new = 0
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            for j in jobs:
                try:
                    url = j.get("url", "").strip()
                    title = j.get("title", "").strip()
                    company = j.get("company", "").strip()
                    if not url or not title: continue
                    key = self._hash(url, title, company)
                    if key in seen: continue
                    seen.add(key)
                    cur = self.conn.execute(
                        "INSERT OR IGNORE INTO jobs "
                        "(dedupe_key,title,company,location,description,url,"
                        "source,source_kind,external_id,posted_at,salary,tags,"
                        "first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (key, title, company, j.get("location",""),
                         j.get("desc","")[:500], url, j.get("source",""),
                         "web", j.get("id",""), j.get("posted"),
                         j.get("salary",""), "", now, now))
                    if cur.rowcount > 0:
                        new += 1
                except: continue
            if new > 0: self.conn.commit()
        return new

    def count(self):
        with self.lock:
            t = self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            f = self.conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE first_seen_at >= datetime('now','-7 days')"
            ).fetchone()[0]
            return t, f

    def close(self):
        try: self.conn.close()
        except: pass

# ═══════════════════════════════════════════════════════════════
# ITEMS
# ═══════════════════════════════════════════════════════════════

def build_ats_items():
    items = []
    cp = ROOT / "companies.yaml"
    if not cp.exists(): return items
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
                kind_map = {"greenhouse": "greenhouse", "ashby": "ashby",
                    "lever": "lever", "smartrecruiters": "smartrecruiters",
                    "workable": "workable", "breezy": "breezy",
                    "teamtailor": "teamtailor", "workday": "workday",
                    "bamboohr": "bamboohr", "yc": "yc"}
                kind = kind_map.get(section, "")
                if kind:
                    items.append(("ats", kind, slug, 0, 0))
    return items

def work_ats(client, kind, slug):
    try:
        from jobcollector.sources.ats_api import fetch_ats_api
        jobs = fetch_ats_api(client, kind, slug, limit=200)
        return [{"url": j.url, "title": j.title, "company": j.company,
                 "location": j.location, "desc": j.description or "",
                 "source": j.source, "id": j.external_id or "",
                 "posted": str(j.posted_at) if j.posted_at else None,
                 "salary": j.salary or ""} for j in jobs]
    except:
        return []

# HIGH-YIELD sites (not saturated)
PRIORITY_SITES = ["google", "ziprecruiter", "glassdoor", "naukri",
                  "simplyhired", "dice"]
# LOW-YIELD sites (saturated — still include but fewer combos)
LEGACY_SITES = ["linkedin", "indeed"]

KWS_CORE = [
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
    "engineer", "developer", "programmer", "architect",
    "consultant", "specialist", "analyst", "scientist",
    "intern", "lead", "manager", "director",
    "hiring now", "urgent hiring", "work from home", "remote engineer",
    "contract engineer", "freelance engineer",
    "React developer", "Vue developer", "Next.js developer",
    "Laravel developer", "Rails developer",
    "PostgreSQL developer", "MongoDB developer",
    "AWS developer", "Azure developer", "GCP developer",
    "Docker engineer", "PyTorch engineer", "TensorFlow engineer",
    "LLM developer", "RAG engineer",
    "blockchain developer", "solidity engineer",
    "game programmer", "graphics engineer",
    "quantitative engineer", "fintech engineer", "banking software",
]

# EXTRA keywords for diversity — tech stack specific
KWS_EXTRA = [
    "React", "Angular", "Vue", "Svelte", "Next.js", "Nuxt",
    "Node.js", "Express", "NestJS", "Django", "Flask", "FastAPI",
    "Spring Boot", "ASP.NET", "Laravel", "Rails",
    "Python", "Java", "JavaScript", "TypeScript", "Go", "Rust",
    "C#", "Kotlin", "Swift", "Scala", "Ruby", "PHP",
    "React Native", "Flutter", "Xamarin",
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch",
    "DynamoDB", "Cassandra", "ClickHouse", "Snowflake",
    "Docker", "Kubernetes", "Terraform", "Ansible", "Jenkins",
    "Prometheus", "Grafana", "Istio",
    "PyTorch", "TensorFlow", "JAX", "Hugging Face", "LangChain",
    "CUDA", "TensorRT", "ONNX", "OpenAI",
    "AWS Lambda", "EC2", "ECS", "EKS", "S3",
    "Azure Functions", "AKS", "GKE", "Cloud Run",
    "Kafka", "RabbitMQ", "Pulsar", "Flink", "Spark",
    "Airflow", "dbt", "Prefect",
    "Sentry", "Datadog", "Splunk", "New Relic",
    "Linux", "Windows Server", "macOS",
    "GraphQL", "REST", "gRPC", "WebSocket",
    "Terraform", "Pulumi", "CloudFormation",
    "ArgoCD", "Helm", "Skaffold",
    "Git", "GitHub", "GitLab", "Bitbucket",
    "Jira", "Confluence", "Notion",
    "Tableau", "Power BI", "Looker",
    "Salesforce", "ServiceNow", "SAP",
    "Microservices", "Event-driven", "CQRS", "DDD",
    "CI/CD", "MLOps", "DataOps", "Platform Engineering",
    "Site Reliability", "Infrastructure as Code",
    "Zero Trust", "OAuth", "JWT",
    "Mobile App", "Cross-platform", "Desktop App",
    "Machine Learning", "Deep Learning", "NLP", "Computer Vision",
    "Recommender System", "Search Engine", "Information Retrieval",
    "Data Pipeline", "ETL", "Data Warehouse",
    "Big Data", "Streaming", "Real-time",
]

KWS = list(set(KWS_CORE + KWS_EXTRA))

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
    "Zurich", "Barcelona", "Madrid", "Lisbon", "Milan", "Rome",
    "Copenhagen", "Oslo", "Helsinki", "Vienna", "Brussels",
    "Shanghai", "Beijing", "Shenzhen", "Taipei", "Bangkok", "Jakarta",
    "Manila", "Hanoi", "Kuala Lumpur",
    "Istanbul", "Cairo", "Nairobi", "Johannesburg",
    "Buenos Aires", "Bogota", "Lima", "Santiago",
]

OFFSETS_PRIORITY = [0, 100, 200, 400, 600, 800]  # More offsets for priority sites
OFFSETS_LEGACY = [0, 200, 400]  # Fewer offsets for saturated sites

def build_jobspy_items():
    items = []
    # Priority sites: more keywords, all locations, more offsets
    for site in PRIORITY_SITES:
        for kw in KWS:
            for loc in LOCS:
                for off in OFFSETS_PRIORITY:
                    items.append(("jobspy", site, kw, loc, off))
    # Legacy sites: fewer offsets to avoid saturation
    for site in LEGACY_SITES:
        for kw in KWS:
            for loc in LOCS:
                for off in OFFSETS_LEGACY:
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

def build_all_items():
    items = build_ats_items()
    jobspy = build_jobspy_items()
    return items + jobspy

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    max_hours = 8
    reset = "--reset" in sys.argv
    for i, a in enumerate(sys.argv[1:]):
        if a == "--hours":
            try: max_hours = float(sys.argv[i+2])
            except: pass

    log("=" * 60)
    log(f"MEGA SCRAPER v7 — {WORKERS} workers, smart source rotation")
    log("=" * 60)

    all_items = build_all_items()
    log(f"Total work items: {len(all_items):,}")

    if reset:
        cp = {"done": set(), "total_new": 0, "iterations": 0, "round": 0}
    else:
        cp = load_cp()

    cp["round"] = cp.get("round", 0) + 1
    done_set = cp["done"]
    remaining = [i for i in all_items if str(i) not in done_set]
    log(f"Round {cp['round']}: Done={len(done_set):,} | Remaining={len(remaining):,}")

    if not remaining:
        log("All items exhausted! Resetting with fresh offsets...")
        done_set.clear()
        remaining = all_items[:]

    db = JobDB()
    t0, f0 = db.count()
    log(f"DB: {t0:,} | Fresh7d: {f0:,} | Gap: {max(0, TARGET-t0):,}")

    # Load ALL dedup keys into memory for fast set membership
    seen = set()
    rows = db.conn.execute("SELECT dedupe_key FROM jobs").fetchall()
    for r in rows:
        seen.add(r[0])
    log(f"Loaded {len(seen):,} dedup keys into memory ({len(seen)*32//1024//1024}MB)")

    random.shuffle(remaining)
    wq = queue.Queue()
    for item in remaining:
        wq.put(item)

    for site_name, limit in SITE_LIMITS.items():
        SITE_SEMAPHORES[site_name] = threading.BoundedSemaphore(limit)

    start = time.time()
    counter = {"iters": 0, "new": 0}
    c_lock = threading.Lock()
    stats = {"ats": 0, "jobspy": 0}
    site_stats = {}
    stop_event = threading.Event()

    def worker(wid):
        import httpx
        client = httpx.Client(timeout=20, follow_redirects=True)
        try:
            while not stop_event.is_set():
                elapsed = time.time() - start
                if elapsed >= min(max_hours * 3600, BATCH_SECONDS):
                    break
                try:
                    item = wq.get(timeout=3)
                except queue.Empty:
                    break

                itype = item[0]
                site_key = item[1] if itype == "jobspy" else "ats"

                sema = SITE_SEMAPHORES.get(site_key)
                try:
                    if sema: sema.acquire(timeout=30)
                    try:
                        if itype == "ats":
                            jobs = work_ats(client, item[1], item[2])
                            with c_lock: stats["ats"] += 1
                        elif itype == "jobspy":
                            jobs = scrape_jobspy(item[1], item[2], item[3], item[4])
                            with c_lock: stats["jobspy"] += 1
                            with c_lock:
                                site_stats[item[1]] = site_stats.get(item[1], 0) + 1
                        else:
                            wq.task_done(); continue
                    finally:
                        if sema: sema.release()
                except:
                    jobs = []

                new = db.insert(jobs, seen) if jobs else 0
                with c_lock:
                    counter["iters"] += 1
                    counter["new"] += new
                    done_set.add(str(item))
                    iters = counter["iters"]
                    total_new = counter["new"]

                if new > 5:
                    log(f"  W{wid:02d} +{new:3d} {itype}:{site_key}:{item[2][:20] if len(item)>2 else ''}")

                # Progress report every 200 iterations
                if iters % 200 == 0:
                    ct, cf = db.count()
                    rate = total_new / max((time.time()-start)/60, 0.1)
                    elapsed_min = (time.time()-start)/60
                    eta_min = max(0, (TARGET - ct)) / max(rate, 1)
                    site_str = " ".join(f"{k}:{v}" for k,v in sorted(site_stats.items()))
                    log(f"  [{iters:,}] +{total_new:,} new | DB={ct:,} | "
                        f"{rate:.0f}/min | ETA={eta_min:.0f}min | "
                        f"Gap={max(0,TARGET-ct):,} | {site_str}")

                time.sleep(random.uniform(0.2, 1.0))
                wq.task_done()
        except Exception as e:
            log(f"  W{wid:02d} ERROR: {e}")
        finally:
            try: client.close()
            except: pass

    log(f"Launching {WORKERS} workers on {len(remaining):,} items...")
    threads = []
    for i in range(min(WORKERS, len(remaining))):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)

    # Checkpoint saver thread
    def checkpoint_saver():
        while not stop_event.is_set():
            time.sleep(20)
            try:
                cp["done"] = done_set
                cp["total_new"] = counter["new"]
                cp["iterations"] = counter["iters"]
                save_cp(cp)
            except: pass

    saver = threading.Thread(target=checkpoint_saver, daemon=True)
    saver.start()

    try: wq.join()
    except: pass
    stop_event.set()
    for t in threads:
        t.join(timeout=5)

    # Final save
    cp["done"] = done_set
    save_cp(cp)

    ft, ff = db.count()
    elapsed = (time.time() - start) / 60
    db.close()

    log("=" * 60)
    log(f"BATCH COMPLETE | Round {cp['round']}")
    log(f"ATS:{stats['ats']:,} JobSpy:{stats['jobspy']:,} | New: +{counter['new']:,}")
    site_str = " ".join(f"{k}:{v}" for k,v in sorted(site_stats.items()))
    log(f"Sites: {site_str}")
    log(f"DB: {ft:,} | Fresh7d: {ff:,} | Gap: {max(0, TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 60)

if __name__ == "__main__":
    main()
