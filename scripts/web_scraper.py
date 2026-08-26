#!/usr/bin/env python3
"""WEB SCRAPER — Playwright browser automation for job scraping.

Scrapes jobs from:
1. Indeed (paginated search results)
2. LinkedIn (guest job search)
3. Google Jobs (search results)
4. Naukri (Indian job board)
5. Glassdoor (job listings)

Dedup system: SHA256(url + title + company) hash stored in seen_set.
Verification: only INSERT jobs that pass dedup check.

Uses playwright-stealth for anti-detection.
"""
from __future__ import annotations
import hashlib, json, os, queue, re, sqlite3, sys, threading, time, random
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "web_cp.json"
LOG_PATH = ROOT / "web_log.txt"
TARGET = 1_000_000
WORKERS = 20  # browser contexts (fewer than API workers — each uses RAM)

_lock = threading.Lock()
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        try:
            with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{ts}] {msg}\n")
        except: pass

# ═══════════════════════════════════════════════════════════════
# CHECKPOINT
# ═══════════════════════════════════════════════════════════════

def load_cp():
    if CP_PATH.exists():
        try: return json.loads(CP_PATH.read_text("utf-8"))
        except: pass
    return {"done": [], "total_new": 0, "iterations": 0}

def save_cp(cp):
    try:
        CP_PATH.write_text(json.dumps({
            "done": cp["done"][-50000:],
            "total_new": cp["total_new"],
            "iterations": cp["iterations"],
        }), "utf-8")
    except: pass

# ═══════════════════════════════════════════════════════════════
# DATABASE with dedup verification
# ═══════════════════════════════════════════════════════════════

class JobDB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-128000")
        # Load existing dedup keys into memory for fast checking
        self.seen = set()
        rows = self.conn.execute("SELECT dedupe_key FROM jobs").fetchall()
        for r in rows:
            self.seen.add(r[0])
        log(f"Loaded {len(self.seen):,} existing dedup keys")
        self.lock = threading.Lock()

    def _hash(self, url: str, title: str, company: str) -> str:
        """Create dedup hash from url + title + company."""
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
                    if not url or not title:
                        continue
                    # Verify: create dedup key
                    key = self._hash(url, title, company)
                    # Skip if already seen
                    if key in self.seen:
                        continue
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
                        self.seen.add(key)
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
# WORK ITEMS
# ═══════════════════════════════════════════════════════════════

KWS = [
    "software engineer", "backend engineer", "frontend developer",
    "full stack developer", "data engineer", "devops engineer",
    "machine learning engineer", "product manager", "data scientist",
    "cloud engineer", "python developer", "java developer",
    "react developer", "AI engineer", "security engineer",
    "QA engineer", "SRE", "platform engineer", "mobile developer",
    "web developer", "software developer", "technical lead",
    "staff engineer", "principal engineer", "SDE",
    "senior software engineer", "C++ engineer", "ruby developer",
    "PHP developer", "kotlin developer", "swift developer",
    "Angular developer", "data analyst", "IT recruiter",
    "scrum master", "business analyst", "UX designer",
    "robotics engineer", "embedded systems engineer", "firmware engineer",
    "database engineer", "solutions architect", "cloud architect",
    "go developer", "node.js developer", "django developer",
    ".NET developer", "aws engineer", "azure engineer",
    "kubernetes engineer", "terraform engineer", "MLE", "DevOps",
    "typescript developer", "rust developer", "NLP engineer",
    "computer vision engineer", "devrel", "tech lead",
    "MLOps engineer", "payments engineer", "network engineer",
    "LLM engineer", "generative AI engineer",
    "systems engineer", "automation engineer", "platform developer",
    "data platform engineer", "ML platform engineer",
    "linux engineer", "cybersecurity analyst", "video engineer",
    "storage engineer", "streaming engineer", "microservices engineer",
    "backend developer", "frontend engineer", "API engineer",
    "distributed systems engineer", "container engineer",
    "developer experience engineer", "quantitative developer",
    "identity engineer", "solidity developer", "web3 developer",
    "unity developer", "react native developer", "flutter developer",
    "kernel engineer", "compiler engineer", "game developer",
    "graphics engineer", "blockchain developer",
]

LOCS = [
    "", "Bangalore", "Hyderabad", "Chennai", "Mumbai", "Pune",
    "Delhi", "Noida", "Gurgaon", "New York", "San Francisco",
    "Seattle", "Austin", "Boston", "Chicago", "Los Angeles",
    "Denver", "Atlanta", "Miami", "London", "Berlin",
    "Toronto", "Vancouver", "Singapore", "Sydney", "Dubai",
    "Remote", "Tel Aviv",
]

PAGES = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]


def build_work_items():
    """Build (source, keyword, location, page) tuples."""
    items = []
    for kw in KWS:
        for loc in LOCS:
            for page in PAGES:
                items.append(("indeed", kw, loc, page))
                items.append(("google", kw, loc, page))
    for kw in KWS:
        for loc in LOCS:
            items.append(("linkedin", kw, loc, 0))
    return items

# ═══════════════════════════════════════════════════════════════
# SCRAPERS — one per source
# ═══════════════════════════════════════════════════════════════

def scrape_indeed(page, kw: str, loc: str, pg: int) -> list[dict]:
    """Scrape Indeed search results using Playwright."""
    try:
        search_url = f"https://www.indeed.com/jobs?q={kw.replace(' ', '+')}"
        if loc:
            search_url += f"&l={loc.replace(' ', '+')}"
        if pg > 0:
            search_url += f"&start={pg}"

        page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(1.5)

        jobs = []
        cards = page.query_selector_all("div.job_seen_beacon") or page.query_selector_all("div.jobsearch-ResultsList > div")
        for card in cards[:25]:
            try:
                a_el = card.query_selector("a.jcs-JobTitle")
                if not a_el:
                    a_el = card.query_selector("a[href*='/rc/clk']")
                if not a_el:
                    continue
                title = (a_el.inner_text() or "").strip()
                href = a_el.get_attribute("href") or ""
                url = f"https://www.indeed.com{href}" if href.startswith("/") else href

                comp_el = card.query_selector("span[data-testid='company-name']")
                if not comp_el:
                    comp_el = card.query_selector("span.companyName")
                company = (comp_el.inner_text() or "").strip() if comp_el else ""

                loc_el = card.query_selector("div[data-testid='text-location']")
                if not loc_el:
                    loc_el = card.query_selector("div.companyLocation")
                location = (loc_el.inner_text() or "").strip() if loc_el else ""

                desc_el = card.query_selector("div.job-snippet") or card.query_selector("table.jobCardShelfContainer")
                desc = (desc_el.inner_text() or "").strip()[:500] if desc_el else ""

                if title and url:
                    jobs.append({
                        "url": url, "title": title, "company": company,
                        "location": location, "desc": desc,
                        "source": f"web:indeed:{kw[:20]}", "id": "",
                        "posted": None, "salary": "",
                    })
            except:
                continue
        return jobs
    except:
        return []


def scrape_linkedin(page, kw: str, loc: str, pg: int) -> list[dict]:
    """Scrape LinkedIn guest job search."""
    try:
        start = pg * 25
        search_url = f"https://www.linkedin.com/jobs/search/?keywords={kw.replace(' ', '%20')}"
        if loc:
            search_url += f"&location={loc.replace(' ', '%20')}"
        search_url += f"&start={start}"

        page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(2)

        jobs = []
        cards = page.query_selector_all("li.artdeco-card") or page.query_selector_all("li.jobs-search-results__list-item")
        for card in cards[:25]:
            try:
                a_el = card.query_selector("a.base-card__full-link") or card.query_selector("a[href*='/jobs/view/']")
                if not a_el:
                    continue
                title_el = card.query_selector("h3.base-search-card__title") or card.query_selector("h3")
                title = (title_el.inner_text() or "").strip() if title_el else ""
                url = a_el.get_attribute("href") or ""
                if "?" in url:
                    url = url.split("?")[0]

                comp_el = card.query_selector("h4.base-search-card__subtitle") or card.query_selector("span.hidden-nested-link")
                company = (comp_el.inner_text() or "").strip() if comp_el else ""

                loc_el = card.query_selector("span.job-search-card__location")
                location = (loc_el.inner_text() or "").strip() if loc_el else ""

                if title and url:
                    jobs.append({
                        "url": url, "title": title, "company": company,
                        "location": location, "desc": "",
                        "source": f"web:linkedin:{kw[:20]}", "id": "",
                        "posted": None, "salary": "",
                    })
            except:
                continue
        return jobs
    except:
        return []


def scrape_google(page, kw: str, loc: str, pg: int) -> list[dict]:
    """Scrape Google Jobs search results."""
    try:
        q = kw
        if loc:
            q += f" in {loc}"
        search_url = f"https://www.google.com/search?q={q.replace(' ', '+')}+jobs&ibp=htl;jobs&start={pg}"

        page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(2)

        jobs = []
        cards = page.query_selector_all("div.iFjolb") or page.query_selector_all("li.iFjolb") or page.query_selector_all("div[jscontroller]")
        for card in cards[:15]:
            try:
                a_el = card.query_selector("a") or card.query_selector("h3")
                if not a_el:
                    continue
                title = (a_el.inner_text() or "").strip()
                href = a_el.get_attribute("href") or ""

                # Extract company from nearby elements
                texts = card.inner_text().split("\n")
                company = ""
                location = ""
                for t in texts[1:5]:
                    t = t.strip()
                    if not t or t == title:
                        continue
                    if not company:
                        company = t
                    elif not location:
                        location = t
                    break

                if title and (href or len(title) > 3):
                    url = href if href.startswith("http") else f"https://www.google.com{href}"
                    jobs.append({
                        "url": url, "title": title, "company": company,
                        "location": location or loc, "desc": "",
                        "source": f"web:google:{kw[:20]}", "id": "",
                        "posted": None, "salary": "",
                    })
            except:
                continue
        return jobs
    except:
        return []

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

SCRAPERS = {
    "indeed": scrape_indeed,
    "linkedin": scrape_linkedin,
    "google": scrape_google,
}

def main():
    max_hours = 8
    for i, a in enumerate(sys.argv[1:]):
        if a == "--hours":
            try: max_hours = float(sys.argv[i+2])
            except: pass

    log("=" * 50)
    log(f"WEB SCRAPER — {WORKERS} browser contexts, {max_hours}h max")
    log("=" * 50)

    all_items = build_work_items()
    log(f"Total work items: {len(all_items):,}")

    cp = load_cp()
    done_set = set(tuple(d) for d in cp["done"])
    remaining = [i for i in all_items if tuple(i) not in done_set]
    log(f"Done: {len(done_set):,}, Remaining: {len(remaining):,}")

    if not remaining:
        done_set.clear(); remaining = all_items[:]; cp["done"] = []

    random.shuffle(remaining)
    db = JobDB()
    t0, f0 = db.count()
    log(f"DB: {t0:,} | Fresh7d: {f0:,} | Gap: {max(0, TARGET-t0):,}")

    wq = queue.Queue()
    for item in remaining: wq.put(item)

    start = time.time()
    counter = {"iters": 0, "new": 0}
    c_lock = threading.Lock()

    def worker(wid):
        from playwright.sync_api import sync_playwright
        pw = None
        browser = None
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True, args=[
                "--no-sandbox", "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage", "--disable-gpu",
            ])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            local_iters = 0
            while True:
                if (time.time() - start) / 3600 >= max_hours: break
                try: item = wq.get(timeout=3)
                except queue.Empty: break

                source, kw, loc, pg = item
                scraper = SCRAPERS.get(source)
                if not scraper:
                    wq.task_done()
                    continue

                try:
                    jobs = scraper(page, kw, loc, pg)
                    new = db.insert(jobs) if jobs else 0
                except:
                    new = 0

                local_iters += 1
                with c_lock:
                    counter["iters"] += 1
                    counter["new"] += new
                    done_set.add(tuple(item))
                    iters = counter["iters"]
                    total_new = counter["new"]

                if new > 5:
                    log(f"  W{wid:02d} +{new:3d} {source}:{kw[:18]}|{loc[:8]} pg={pg}")

                if local_iters % 10 == 0:
                    cp["done"] = list(done_set)
                    cp["total_new"] = total_new
                    cp["iterations"] = iters
                    save_cp(cp)

                if iters % 200 == 0:
                    ct, cf = db.count()
                    rate = total_new / max((time.time()-start)/60, 0.1)
                    log(f"  [{iters:,}] +{total_new:,} new | DB={ct:,} | Fresh7d={cf:,} | {rate:.0f}/min | Gap={max(0,TARGET-ct):,}")

                wq.task_done()

        except Exception as e:
            log(f"  W{wid:02d} FATAL: {e}")
        finally:
            try: browser.close()
            except: pass
            try: pw.stop()
            except: pass

    log(f"Launching {WORKERS} browser workers on {len(remaining):,} items...")
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
    log(f"COMPLETE | Iterations: {counter['iters']:,} | New: +{counter['new']:,}")
    log(f"DB: {ft:,} | Fresh7d: {ff:,} | Gap: {max(0,TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 50)

if __name__ == "__main__":
    main()
