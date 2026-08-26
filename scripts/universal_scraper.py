#!/usr/bin/env python3
"""UNIVERSAL WEB SCRAPER v2 — Direct scraping of 7 job portals with Playwright.

Sites: Indeed, Google Jobs, Naukri, SimplyHired, Dice, ZipRecruiter, Glassdoor
Each worker runs its own Chromium browser with stealth.
Checkpoint saves every 500 iterations. Runs as a scheduled task.
"""
from __future__ import annotations
import hashlib, json, os, queue, sqlite3, sys, threading, time, random, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "webscrape_cp.json"
LOG_PATH = ROOT / "webscrape_log.txt"
TARGET = 1_000_000
WORKERS = 6
BATCH_SECONDS = 600  # 10 min per batch

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

    def _hash(self, url: str, title: str, company: str) -> str:
        raw = f"{url.strip().lower()}|{title.strip().lower()}|{company.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def insert(self, jobs: list[dict]) -> int:
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
                         "web_scrape", j.get("id",""), j.get("posted"),
                         j.get("salary",""), "", now, now))
                    if cur.rowcount > 0:
                        new += 1
                except: continue
            if new > 0: self.conn.commit()
        return new

    def count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def close(self):
        try: self.conn.close()
        except: pass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

# ═══════════════════════════════════════════════════════════════
# SCRAPERS
# ═══════════════════════════════════════════════════════════════

def scrape_indeed(page, kw, loc, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.indeed.com/jobs?q={q}"
        if loc: url += f"&l={quote_plus(loc)}"
        if pg > 0: url += f"&start={pg * 10}"
        url += "&sort=date"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(random.uniform(1.5, 2.5))
        cards = page.query_selector_all("div.job_seen_beacon")
        for card in cards[:20]:
            try:
                a = card.query_selector("a.jcs-JobTitle")
                if not a: continue
                title = (a.inner_text() or "").strip()
                href = a.get_attribute("href") or ""
                job_url = f"https://www.indeed.com{href}" if href.startswith("/") else href
                c = card.query_selector("span[data-testid='company-name']")
                company = (c.inner_text() or "").strip() if c else ""
                lo = card.query_selector("div[data-testid='text-location']")
                location = (lo.inner_text() or "").strip() if lo else ""
                sal = card.query_selector("div.salary-snippet-container")
                salary = (sal.inner_text() or "").strip() if sal else ""
                if title and len(job_url) > 10:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": location, "desc": "", "source": "indeed_web",
                                 "id": "", "posted": None, "salary": salary})
            except: continue
    except: pass
    return jobs

def scrape_dice(page, kw, loc, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.dice.com/jobs?q={q}&pageSize=20&page={pg+1}&sort=date"
        if loc: url += f"&location={quote_plus(loc)}"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(random.uniform(2.0, 3.0))
        cards = page.query_selector_all("div[data-testid='job-card']")
        for card in cards[:20]:
            try:
                a = card.query_selector("a[data-testid='job-search-job-detail-link']")
                title = (a.inner_text() or "").strip() if a else ""
                link = card.query_selector("a[data-testid='job-search-job-card-link']")
                href = link.get_attribute("href") or "" if link else ""
                job_url = f"https://www.dice.com{href}" if href.startswith("/") else href
                c = card.query_selector("p[data-testid='job-card-company-name']")
                company = (c.inner_text() or "").strip() if c else ""
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": loc, "desc": "", "source": "dice_web",
                                 "id": "", "posted": None, "salary": ""})
            except: continue
    except: pass
    return jobs

def scrape_simplyhired(page, kw, loc, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.simplyhired.com/search?q={q}"
        if loc: url += f"&l={quote_plus(loc)}"
        if pg > 0: url += f"&pn={pg}"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(random.uniform(1.5, 2.5))
        cards = page.query_selector_all("article.SerpJob, div.SerpJob-jobCard")
        for card in cards[:20]:
            try:
                a = card.query_selector("a[data-testid='searchSerpJobTitle'], h2 a")
                title = (a.inner_text() or "").strip() if a else ""
                href = a.get_attribute("href") or "" if a else ""
                job_url = f"https://www.simplyhired.com{href}" if href.startswith("/") else href
                c = card.query_selector("span[data-testid='companyName'], span.SerpJob-company")
                company = (c.inner_text() or "").strip() if c else ""
                lo = card.query_selector("span[data-testid='searchSerpJobLocation']")
                location = (lo.inner_text() or "").strip() if lo else ""
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": location, "desc": "", "source": "simplyhired_web",
                                 "id": "", "posted": None, "salary": ""})
            except: continue
    except: pass
    return jobs

def scrape_google_jobs(page, kw, loc):
    jobs = []
    try:
        q = quote_plus(f"{kw} jobs" + (f" in {loc}" if loc else ""))
        url = f"https://www.google.com/search?q={q}&ibp=htl;jobs"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(random.uniform(2.0, 3.5))
        cards = page.query_selector_all("li.i0MbCb, div.iFjolb, div[jscontroller]")
        for card in cards[:20]:
            try:
                t = card.query_selector("div.BjJfJf, h3")
                title = (t.inner_text() or "").strip() if t else ""
                co = card.query_selector("div.vNEEBe, div.nJlQNd")
                company = (co.inner_text() or "").strip() if co else ""
                lo = card.query_selector("div.Qk80Jf, span.legV9c")
                location = (lo.inner_text() or "").strip() if lo else ""
                link = card.query_selector("a[href*='url=']")
                job_url = ""
                if link:
                    href = link.get_attribute("href") or ""
                    m = re.search(r'url=([^&]+)', href)
                    if m:
                        from urllib.parse import unquote
                        job_url = unquote(m.group(1))
                if not job_url:
                    job_url = f"https://www.google.com/search?q={quote_plus(title+' '+company)}"
                if title and company:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": location, "desc": "", "source": "google_jobs",
                                 "id": "", "posted": None, "salary": ""})
            except: continue
    except: pass
    return jobs

def scrape_naukri(page, kw, loc, pg):
    jobs = []
    try:
        q = kw.lower().replace(" ", "-")
        url = f"https://www.naukri.com/{q}-jobs-{loc.lower().replace(' ','-')}?experience=0&pageNo={pg+1}"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(random.uniform(2.0, 3.0))
        cards = page.query_selector_all("div[data-testid='job-tuple'], article.tuple, div.srp-cardListing > div")
        for card in cards[:25]:
            try:
                a = card.query_selector("a.title, a[data-entity-id]")
                title = (a.inner_text() or "").strip() if a else ""
                href = a.get_attribute("href") or "" if a else ""
                job_url = href if href.startswith("http") else f"https://www.naukri.com{href}"
                c = card.query_selector("a.subTitle, span.companyName")
                company = (c.inner_text() or "").strip() if c else ""
                lo = card.query_selector("span.location, span.subLocation")
                location = (lo.inner_text() or "").strip() if lo else ""
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": location, "desc": "", "source": "naukri_web",
                                 "id": "", "posted": None, "salary": ""})
            except: continue
    except: pass
    return jobs

def scrape_ziprecruiter(page, kw, loc, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.ziprecruiter.com/jobs-search?search={q}"
        if loc: url += f"&location={quote_plus(loc)}"
        if pg > 0: url += f"&page={pg+1}"
        url += "&days=7"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(random.uniform(2.0, 3.0))
        cards = page.query_selector_all("article.job_result, div.job_content, div.job_result_content_wrapper")
        for card in cards[:20]:
            try:
                a = card.query_selector("h2 a, a.job_link")
                title = (a.inner_text() or "").strip() if a else ""
                href = a.get_attribute("href") or "" if a else ""
                job_url = href if href.startswith("http") else f"https://www.ziprecruiter.com{href}"
                c = card.query_selector("a.t_u_j, h4, p.company_name")
                company = (c.inner_text() or "").strip() if c else ""
                lo = card.query_selector("p.location, a.location")
                location = (lo.inner_text() or "").strip() if lo else ""
                if title and job_url and len(job_url) > 15:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": location, "desc": "", "source": "ziprecruiter_web",
                                 "id": "", "posted": None, "salary": ""})
            except: continue
    except: pass
    return jobs

def scrape_glassdoor(page, kw, loc, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.glassdoor.com/Job/jobs.htm?sc.keyword={q}"
        if loc: url += f"&locT=&locId=&locKeyword={quote_plus(loc)}"
        if pg > 0: url += f"&p={pg+1}"
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(random.uniform(2.0, 3.5))
        cards = page.query_selector_all("li.JobsList_jobListItem__wjTH, li[data-test='jobListing'], div.jobListing")
        for card in cards[:20]:
            try:
                a = card.query_selector("a[data-test='job-link'], a.jobTitle, a[href*='/job-listing/']")
                title = (a.inner_text() or "").strip() if a else ""
                href = a.get_attribute("href") or "" if a else ""
                job_url = f"https://www.glassdoor.com{href}" if href.startswith("/") else href
                c = card.query_selector("span.EmployerProfile_compactEmployerName__LE242, a.employerName")
                company = (c.inner_text() or "").strip() if c else ""
                lo = card.query_selector("div[data-test='emp-location'], span.JobCard_location__rCz3x")
                location = (lo.inner_text() or "").strip() if lo else ""
                if title and job_url and len(job_url) > 15:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": location, "desc": "", "source": "glassdoor_web",
                                 "id": "", "posted": None, "salary": ""})
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
    for kw in KWS:
        for loc in LOCS:
            items.append(("google", kw, loc, 0))
            for pg in range(8):   # Indeed 8 pages
                items.append(("indeed", kw, loc, pg))
            for pg in range(5):   # Naukri 5 pages
                items.append(("naukri", kw, loc, pg))
            for pg in range(5):   # SimplyHired 5 pages
                items.append(("simplyhired", kw, loc, pg))
            for pg in range(8):   # Dice 8 pages
                items.append(("dice", kw, loc, pg))
            for pg in range(3):   # ZipRecruiter 3 pages
                items.append(("ziprecruiter", kw, loc, pg))
            for pg in range(3):   # Glassdoor 3 pages
                items.append(("glassdoor", kw, loc, pg))
    return items

SCRAPERS = {
    "google": lambda page, kw, loc, pg: scrape_google_jobs(page, kw, loc),
    "indeed": scrape_indeed,
    "naukri": scrape_naukri,
    "simplyhired": scrape_simplyhired,
    "dice": scrape_dice,
    "ziprecruiter": scrape_ziprecruiter,
    "glassdoor": scrape_glassdoor,
}

def main():
    reset = "--reset" in sys.argv

    log("=" * 60)
    log(f"UNIVERSAL WEB SCRAPER v2 — {WORKERS} workers, 7 sites")
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

    # Load dedup keys
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
        from playwright.sync_api import sync_playwright
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                           "--disable-gpu", "--disable-extensions",
                           "--disable-background-networking"])
                context = browser.new_context(
                    user_agent=UA,
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                )
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                """)
                page = context.new_page()

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
                        time.sleep(random.uniform(0.3, 1.5))
                        fn = SCRAPERS.get(site)
                        jobs = fn(page, kw, loc, pg) if fn else []
                    except Exception:
                        jobs = []
                        try: page = context.new_page()
                        except: pass

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

                browser.close()
        except Exception as e:
            log(f"  W{wid:02d} FATAL: {e}")

    log(f"Launching {WORKERS} browser workers on {len(remaining):,} items...")
    threads = []
    for i in range(WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.5)

    try: wq.join()
    except: pass
    stop_event.set()

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
