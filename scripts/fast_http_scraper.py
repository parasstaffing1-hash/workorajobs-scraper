#!/usr/bin/env python3
"""FAST HTTP SCRAPER v3 — With UA rotation, retry, and rate-limit backoff.

Sources (all free, no auth):
- Dice (HTML): 20 jobs per search, ~0.3s
- SimplyHired (HTML): 20 jobs per search, ~0.5s
- Remotive (JSON API): ~500 remote jobs
- Arbeitnow (JSON API): ~1000+ remote jobs
- RemoteOK (JSON API): ~500+ remote jobs
"""
from __future__ import annotations
import hashlib, json, os, queue, sqlite3, sys, threading, time, random, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "fasthttp_cp.json"
LOG_PATH = ROOT / "fasthttp_log.txt"
TARGET = 1_000_000
WORKERS = 30
BATCH_SECONDS = 3600  # 1 hour per batch

_lock = threading.Lock()
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        try:
            with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{ts}] {msg}\n")
        except: pass

class JobDB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-64000")
        self.lock = threading.Lock()
        self._seen = set()

    def _hash(self, url, title, company):
        raw = f"{url.strip().lower()}|{title.strip().lower()}|{company.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def insert(self, jobs):
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
                    if key in self._seen: continue
                    self._seen.add(key)
                    cur = self.conn.execute(
                        "INSERT OR IGNORE INTO jobs "
                        "(dedupe_key,title,company,location,description,url,"
                        "source,source_kind,external_id,posted_at,salary,tags,"
                        "first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (key, title, company, j.get("location",""),
                         j.get("desc","")[:500], url, j.get("source",""),
                         "fast_http", j.get("id",""), j.get("posted"),
                         j.get("salary",""), "", now, now))
                    if cur.rowcount > 0: new += 1
                except: continue
            if new > 0: self.conn.commit()
        return new

    def count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    def close(self):
        try: self.conn.close()
        except: pass

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

def safe_get(client, url, retries=2):
    """GET with retry, UA rotation, and timeout protection."""
    for attempt in range(retries + 1):
        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
            resp = client.get(url, headers=headers, timeout=12, follow_redirects=True)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 403:
                time.sleep(random.uniform(3, 8))  # backoff on block
                continue
            if resp.status_code == 429:
                time.sleep(random.uniform(10, 20))  # rate limited
                continue
            return resp
        except Exception:
            if attempt < retries:
                time.sleep(random.uniform(1, 3))
    return None

# ═══════════════════════════════════════════════════════════════
# JSON API SCRAPERS — ultra fast
# ═══════════════════════════════════════════════════════════════

def scrape_remotive(client, page_num=0):
    jobs = []
    try:
        resp = safe_get(client, "https://remotive.com/api/remote-jobs?category=software-dev&limit=100")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("jobs", []):
            url = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company_name", "")
            location = item.get("candidate_required_location", "")
            salary = item.get("salary", "")
            desc = (item.get("description", "") or "")[:500]
            posted = item.get("publication_date", "")
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location, "desc": desc, "source": "remotive_api",
                             "id": str(item.get("id", "")), "posted": posted, "salary": salary})
    except: pass
    return jobs

def scrape_arbeitnow(client, page_num=0):
    jobs = []
    try:
        resp = safe_get(client, f"https://www.arbeitnow.com/api/job-board-api?page={page_num+1}")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("data", []):
            url = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company_name", "")
            location = item.get("location", "")
            remote = item.get("remote", False)
            desc = (item.get("description", "") or "")[:500]
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location or ("Remote" if remote else ""),
                             "desc": desc, "source": "arbeitnow_api",
                             "id": str(item.get("id", "")), "posted": item.get("created_at", ""),
                             "salary": ""})
    except: pass
    return jobs

def scrape_remoteok(client, page_num=0):
    jobs = []
    try:
        resp = safe_get(client, "https://remoteok.com/api")
        if not resp or resp.status_code != 200: return []
        data = resp.json()
        for item in data[1:] if len(data) > 1 else []:
            url = f"https://remoteok.com/remote-jobs/{item.get('slug', '')}"
            title = item.get("position", "")
            company = item.get("company", "")
            location = item.get("location", "Remote")
            salary_min = item.get("salary_min", "")
            salary_max = item.get("salary_max", "")
            salary = f"${salary_min}-${salary_max}" if salary_min and salary_max else ""
            desc = (item.get("description", "") or "")[:500]
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location, "desc": desc, "source": "remoteok_api",
                             "id": str(item.get("id", "")), "posted": item.get("date", ""),
                             "salary": salary})
    except: pass
    return jobs

# ═══════════════════════════════════════════════════════════════
# HTML SCRAPERS — with retry and backoff
# ═══════════════════════════════════════════════════════════════

def scrape_dice(client, kw, loc, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.dice.com/jobs?q={q}&pageSize=20&page={pg+1}&sort=date"
        if loc: url += f"&location={quote_plus(loc)}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string)
                items = data if isinstance(data, list) else data.get("itemListElement", [])
                for item in items[:20]:
                    je = item.get("item", item) if isinstance(item, dict) else {}
                    title = je.get("name", "")
                    job_url = je.get("url", "")
                    comp = je.get("hiringOrganization", {})
                    company = comp.get("name", "") if isinstance(comp, dict) else ""
                    loc_obj = je.get("jobLocation", {})
                    location = ""
                    if isinstance(loc_obj, dict):
                        addr = loc_obj.get("address", {})
                        location = f"{addr.get('addressLocality', '')} {addr.get('addressRegion', '')}".strip()
                    if title and job_url:
                        jobs.append({"url": job_url, "title": title, "company": company,
                                     "location": location, "desc": "", "source": "dice_http",
                                     "id": "", "posted": None, "salary": ""})
            except: continue
        if not jobs:
            for card in soup.select("div[data-testid='job-card']")[:20]:
                try:
                    a = card.select_one("a[data-testid='job-search-job-detail-link']")
                    title = (a.get_text(strip=True) or "").strip() if a else ""
                    link = card.select_one("a[data-testid='job-search-job-card-link']")
                    href = link.get("href", "") if link else ""
                    job_url = f"https://www.dice.com{href}" if href.startswith("/") else href
                    c = card.select_one("p[data-testid='job-card-company-name']")
                    company = (c.get_text(strip=True) or "").strip() if c else ""
                    if title and job_url:
                        jobs.append({"url": job_url, "title": title, "company": company,
                                     "location": loc, "desc": "", "source": "dice_http",
                                     "id": "", "posted": None, "salary": ""})
                except: continue
    except: pass
    return jobs

def scrape_simplyhired(client, kw, loc, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.simplyhired.com/search?q={q}"
        if loc: url += f"&l={quote_plus(loc)}"
        if pg > 0: url += f"&pn={pg}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        h2s = soup.find_all("h2", class_=lambda c: c and "css-8rdtm5" in str(c))
        for h2 in h2s[:20]:
            try:
                title = (h2.get_text(strip=True) or "").strip()
                if not title: continue
                container = h2.parent
                for _ in range(5):
                    if container is None: break
                    spans = container.find_all("span")
                    if len(spans) >= 2: break
                    container = container.parent
                job_url = ""
                if container:
                    a = container.find("a", href=lambda h: h and "/job/" in str(h))
                    if a:
                        href = a.get("href", "")
                        job_url = f"https://www.simplyhired.com{href}" if href.startswith("/") else href
                if not job_url: continue
                company = ""
                location = ""
                salary = ""
                if container:
                    for s in container.find_all("span"):
                        cls = " ".join(s.get("class", []))
                        txt = (s.get_text(strip=True) or "").strip()
                        if not txt: continue
                        if "css-lvyu5j" in cls: company = txt
                        elif "css-1t92pv" in cls: location = txt
                        elif "css-h61onv" in cls: salary = txt
                jobs.append({"url": job_url, "title": title, "company": company,
                             "location": location, "desc": "", "source": "simplyhired_http",
                             "id": "", "posted": None, "salary": salary})
            except: continue
    except: pass
    return jobs

# ═══════════════════════════════════════════════════════════════
# WORK ITEMS
# ═══════════════════════════════════════════════════════════════

KWS = [
    "software engineer", "backend engineer", "frontend developer",
    "full stack developer", "data engineer", "devops engineer",
    "machine learning engineer", "data scientist", "cloud engineer",
    "python developer", "java developer", "react developer",
    "AI engineer", "security engineer", "QA engineer", "SRE",
    "platform engineer", "infrastructure engineer", "mobile developer",
    "web developer", "software developer", "technical lead",
    "engineering manager", "staff engineer", "SDE", "SWE",
    "senior software engineer", "junior software engineer",
    "C++ engineer", "ruby developer", "PHP developer",
    "kotlin developer", "swift developer", "Angular developer",
    "data analyst", "business analyst", "UX designer",
    "embedded systems engineer", "firmware engineer",
    "game developer", "database engineer", "cloud architect",
    "go developer", "node.js developer", "django developer",
    "fastapi developer", "spring boot developer", ".NET developer",
    "aws engineer", "azure engineer", "gcp engineer",
    "kubernetes engineer", "terraform engineer",
    "typescript developer", "rust developer",
    "computer vision engineer", "NLP engineer", "MLOps",
    "LLM engineer", "generative AI",
    "distributed systems", "blockchain developer",
    "React", "Angular", "Vue.js", "Next.js",
    "Node.js", "Django", "Flask", "FastAPI", "Spring Boot",
    "Python", "Java", "JavaScript", "TypeScript", "Go", "Rust",
    "C#", "Kotlin", "Swift", "Scala",
    "PostgreSQL", "MySQL", "MongoDB", "Redis",
    "Docker", "Kubernetes", "Terraform", "Jenkins",
    "PyTorch", "TensorFlow", "AWS", "Azure", "GCP",
    "Kafka", "Spark", "Airflow",
    "Machine Learning", "Deep Learning",
    "hiring now", "urgent hiring", "work from home", "remote",
    "contract", "intern",
    "Lead Engineer", "Principal Engineer", "Director of Engineering",
    "product manager", "scrum master",
    "iOS developer", "Android developer",
    "React Native", "Flutter",
    "Laravel developer", "Ruby on Rails",
    "Salesforce developer", "SAP",
    "Linux", "GraphQL", "CI/CD",
    "site reliability", "automation engineer",
]

LOCS = [
    "", "New York", "San Francisco", "Seattle", "Austin", "Boston", "Chicago",
    "Los Angeles", "Denver", "Atlanta", "Miami", "Washington DC", "Portland",
    "San Diego", "Dallas", "Houston", "San Jose", "Raleigh", "Charlotte",
    "Minneapolis", "Detroit", "Phoenix", "Tampa", "Nashville",
    "London", "Manchester", "Edinburgh", "Berlin", "Munich", "Paris",
    "Amsterdam", "Dublin", "Toronto", "Vancouver", "Montreal",
    "Singapore", "Hong Kong", "Tokyo", "Seoul", "Sydney", "Melbourne",
    "Dubai", "Remote", "Tel Aviv",
    "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Mumbai", "Pune",
    "Delhi", "Noida", "Gurgaon", "Kolkata", "Ahmedabad",
    "Jaipur", "Kochi", "Coimbatore", "Indore", "Lucknow", "Chandigarh",
    "Shanghai", "Beijing", "Shenzhen", "Taipei", "Bangkok",
    "Zurich", "Barcelona", "Madrid", "Lisbon", "Milan",
    "Copenhagen", "Oslo", "Helsinki", "Vienna",
    "Warsaw", "Prague", "Sao Paulo", "Mexico City",
    "Stockholm", "Jakarta", "Manila",
]

def build_items():
    items = []
    items.append(("remotive", "", "", 0))
    for pg in range(10):
        items.append(("arbeitnow", "", "", pg))
    items.append(("remoteok", "", "", 0))
    for kw in KWS:
        for loc in LOCS:
            for pg in range(8):
                items.append(("dice", kw, loc, pg))
            for pg in range(5):
                items.append(("simplyhired", kw, loc, pg))
    return items

SCRAPERS = {
    "dice": lambda client, kw, loc, pg: scrape_dice(client, kw, loc, pg),
    "simplyhired": lambda client, kw, loc, pg: scrape_simplyhired(client, kw, loc, pg),
    "remotive": lambda client, kw, loc, pg: scrape_remotive(client, pg),
    "arbeitnow": lambda client, kw, loc, pg: scrape_arbeitnow(client, pg),
    "remoteok": lambda client, kw, loc, pg: scrape_remoteok(client, pg),
}

def main():
    reset = "--reset" in sys.argv

    log("=" * 60)
    log(f"FAST HTTP SCRAPER v3 — {WORKERS} workers, 5 sources, retry+backoff")
    log("=" * 60)

    all_items = build_items()
    log(f"Total work items: {len(all_items):,}")

    done = set()
    if not reset and CP_PATH.exists():
        try:
            cp = json.loads(CP_PATH.read_text("utf-8"))
            done = set(cp.get("done", []))
        except: pass
    remaining = [i for i in all_items if str(i) not in done]
    log(f"Done: {len(done):,} | Remaining: {len(remaining):,}")

    if not remaining:
        log("All items exhausted! Resetting...")
        done.clear()
        remaining = all_items[:]

    db = JobDB()
    t0 = db.count()
    log(f"DB: {t0:,} | Gap: {max(0, TARGET-t0):,}")

    rows = db.conn.execute("SELECT dedupe_key FROM jobs").fetchall()
    for r in rows:
        db._seen.add(r[0])
    log(f"Loaded {len(db._seen):,} dedup keys")

    random.shuffle(remaining)
    wq = queue.Queue()
    for item in remaining:
        wq.put(item)

    start = time.time()
    counter = {"iters": 0, "new": 0}
    c_lock = threading.Lock()
    stats = {}
    stop_event = threading.Event()

    def worker(wid):
        import httpx
        client = httpx.Client(timeout=20, follow_redirects=True,
                              limits=httpx.Limits(max_connections=3, max_keepalive_connections=2))
        try:
            while not stop_event.is_set():
                elapsed = time.time() - start
                if elapsed >= BATCH_SECONDS:
                    break
                try:
                    item = wq.get(timeout=3)
                except queue.Empty:
                    break

                site, kw, loc, pg = item
                try:
                    # Random delay between 0.2-1.0s to avoid rate limiting
                    time.sleep(random.uniform(0.2, 1.0))
                    fn = SCRAPERS.get(site)
                    jobs = fn(client, kw, loc, pg) if fn else []
                except:
                    jobs = []

                new = db.insert(jobs) if jobs else 0

                with c_lock:
                    counter["iters"] += 1
                    counter["new"] += new
                    done.add(str(item))
                    stats[site] = stats.get(site, 0) + 1
                    iters = counter["iters"]
                    total_new = counter["new"]

                if new > 2:
                    log(f"  W{wid:02d} +{new:3d} {site}:{kw[:20]}|{loc[:12]}|p{pg}")

                if iters % 200 == 0:
                    ct = db.count()
                    rate = total_new / max((time.time()-start)/60, 0.1)
                    log(f"  [{iters:,}] +{total_new:,} | DB={ct:,} | "
                        f"{rate:.0f}/min | Gap={max(0,TARGET-ct):,} | "
                        + " ".join(f"{k}:{v}" for k,v in sorted(stats.items())))

                if iters % 500 == 0:
                    try:
                        CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
                    except: pass

                wq.task_done()
        except Exception as e:
            log(f"  W{wid:02d} FATAL: {e}")
        finally:
            try: client.close()
            except: pass

    log(f"Launching {WORKERS} http workers on {len(remaining):,} items...")
    threads = []
    for i in range(WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)

    # Heartbeat thread — saves checkpoint and logs progress
    def heartbeat():
        while not stop_event.is_set():
            time.sleep(60)
            try:
                ct = db.count()
                elapsed_min = (time.time() - start) / 60
                remaining_q = wq.qsize()
                log(f"  [HEARTBEAT] DB={ct:,} | iters={counter['iters']:,} new={counter['new']:,} | queue={remaining_q:,} | {elapsed_min:.0f}min elapsed")
                CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
            except: pass
    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()

    # Wait for all items to be processed or batch timeout
    while time.time() - start < BATCH_SECONDS:
        time.sleep(5)
        if wq.empty():
            log("Queue empty — all items processed!")
            break
    stop_event.set()
    log("Stop signal sent, waiting for workers to finish...")
    for t in threads:
        t.join(timeout=20)

    try:
        CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
    except: pass

    ft = db.count()
    elapsed = (time.time() - start) / 60
    db.close()

    log("=" * 60)
    log(f"BATCH COMPLETE | New: +{counter['new']:,}")
    log(f"Sites: " + " ".join(f"{k}:{v}" for k,v in sorted(stats.items())))
    log(f"DB: {ft:,} | Gap: {max(0, TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 60)

if __name__ == "__main__":
    main()
