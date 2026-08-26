#!/usr/bin/env python3
"""PAGINATION SCRAPER V2 - Fixed selectors for SimplyHired, Dice, CWJobs, and more."""
from __future__ import annotations
import hashlib, json, os, queue, random, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
import httpx

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "page_scraper_cp.json"
LOG_PATH = ROOT / "page_scraper_log.txt"
TARGET = 1_000_000
WORKERS = 30
MAX_PAGES = 50
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
        self.lock = threading.Lock()
        self._seen = set()
    def load_dedup_keys(self):
        rows = self.conn.execute("SELECT dedupe_key FROM jobs").fetchall()
        for r in rows:
            self._seen.add(r[0])
        log(f"Loaded {len(self._seen):,} dedup keys")
        return len(self._seen)
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
                         "pagination", j.get("id",""), j.get("posted"),
                         j.get("salary",""), j.get("tags",""), now, now))
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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

def safe_get(client, url, retries=2, timeout=15):
    for attempt in range(retries + 1):
        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": random.choice(["en-US,en;q=0.9", "en-GB,en;q=0.9"]),
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
            resp = client.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            if resp.status_code == 200: return resp
            if resp.status_code in (403, 429):
                time.sleep(random.uniform(2, 6))
                return resp
            return resp
        except Exception:
            if attempt < retries:
                time.sleep(random.uniform(1, 3))
    return None

# ==================== SIMPLYHIRED ====================
def scrape_simplyhired(client, kw, loc, pg):
    """SimplyHired - uses h2 a for titles, /job/ for links."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.simplyhired.com/search?q={q}&pn={pg}"
        if loc: url += f"&l={quote_plus(loc)}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        # SimplyHired uses h2 a for job titles
        for a in soup.select("h2 a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or not href: continue
            if not href.startswith("http"):
                href = "https://www.simplyhired.com" + href
            # Walk up to find company info
            company = ""
            card = a.parent
            for _ in range(4):
                if card is None: break
                # Look for spans with company name
                for span in card.select("span"):
                    t = span.get_text(strip=True)
                    if t and t != title and len(t) < 60 and not t.startswith("$"):
                        company = t
                        break
                if company: break
                card = card.parent
            jobs.append({"url": href, "title": title, "company": company,
                        "location": loc, "source": "simplyhired"})
    except: pass
    return jobs

# ==================== DICE ====================
def scrape_dice(client, kw, loc, pg):
    """Dice - uses [data-testid='job-card'] and /job-detail/ links."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.dice.com/jobs?q={q}&start={pg * 20}&pageSize=20"
        if loc: url += f"&location={quote_plus(loc)}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("[data-testid='job-card']"):
            # Find link with actual text (first link is often empty)
            title = ""
            href = ""
            for a in card.select("a[href*='/job-detail/']"):
                t = a.get_text(strip=True)
                if t and len(t) > 3:
                    title = t
                    href = a.get("href", "")
                    break
            if not title or not href: continue
            if not href.startswith("http"):
                href = "https://www.dice.com" + href
            company = ""
            comp_el = card.select_one("[data-testid='job-card-company-name']")
            if comp_el: company = comp_el.get_text(strip=True)
            if not company:
                for a in card.select("a[href*='/company-profile/']"):
                    t = a.get_text(strip=True)
                    if t:
                        company = t
                        break
            jobs.append({"url": href, "title": title, "company": company,
                        "location": loc, "source": "dice"})
    except: pass
    return jobs

# ==================== CWJOBS ====================
def scrape_cwjobs(client, kw, loc, pg):
    """CWJobs/TotalJobs - UK tech jobs."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.cwjobs.co.uk/jobs/{q}?page={pg + 1}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a[href*='/job/']"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or len(title) < 5 or not href: continue
            if not href.startswith("http"):
                href = "https://www.cwjobs.co.uk" + href
            jobs.append({"url": href, "title": title, "company": "",
                        "location": loc, "source": "cwjobs"})
    except: pass
    return jobs

# ==================== WELLFOUND ====================
def scrape_wellfound(client, kw, pg):
    """Wellfound (AngelList) - startup jobs."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://wellfound.com/role/{q}?page={pg + 1}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a[href*='/startup/'], a[href*='/company/']"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or len(title) < 5: continue
            if not href.startswith("http"):
                href = "https://wellfound.com" + href
            jobs.append({"url": href, "title": title, "company": "",
                        "location": "", "source": "wellfound"})
    except: pass
    return jobs

# ==================== ADZUNA API ====================
def scrape_adzuna(client, country, kw, pg):
    """Adzuna - works via HTML scraping."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.adzuna.{country}/search?q={q}&pg={pg + 1}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        # Adzuna uses h2 a for job titles
        for a in soup.select("a[data-aid='jobResultTitle'], h2 a[href*='/job/']"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or not href: continue
            if not href.startswith("http"):
                href = f"https://www.adzuna.{country}" + href
            company = ""
            card = a.parent
            if card:
                for span in card.select("span"):
                    t = span.get_text(strip=True)
                    if t and t != title and len(t) < 60:
                        company = t
                        break
            jobs.append({"url": href, "title": title, "company": company,
                        "location": "", "source": f"adzuna:{country}"})
    except: pass
    return jobs

# ==================== BUILTIN ====================
def scrape_builtin(client, kw, pg):
    """BuiltIn - US tech jobs."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://builtin.com/jobs?q={q}&p={pg + 1}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a[href*='/jobs/']"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or len(title) < 5 or not href: continue
            if not href.startswith("http"):
                href = "https://builtin.com" + href
            jobs.append({"url": href, "title": title, "company": "",
                        "location": "", "source": "builtin"})
    except: pass
    return jobs

# ==================== HIMALAYAS API ====================
def scrape_himalayas(client, kw, pg):
    """Himalayas.app - JSON API, remote jobs."""
    jobs = []
    try:
        url = f"https://himalayas.app/jobs/api?limit=50&offset={pg * 50}"
        if kw: url += f"&search={quote_plus(kw)}"
        resp = safe_get(client, url, timeout=15)
        if not resp or resp.status_code != 200: return []
        data = resp.json()
        for item in data.get("jobs", []):
            url_j = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company", {})
            company_name = company.get("name", "") if isinstance(company, dict) else str(company)
            location = item.get("location", "") or (company.get("location", "") if isinstance(company, dict) else "")
            if title and url_j:
                jobs.append({"url": url_j, "title": title, "company": company_name,
                            "location": str(location), "source": "himalayas"})
    except: pass
    return jobs

# ==================== REMOTEOK API ====================
def scrape_remoteok(client, pg):
    """RemoteOK - JSON API."""
    jobs = []
    try:
        resp = safe_get(client, "https://remoteok.com/api", timeout=15)
        if not resp or resp.status_code != 200: return []
        data = resp.json()
        start = pg * 100
        for item in data[start:start+100]:
            if not isinstance(item, dict): continue
            title = item.get("position", "")
            url_j = item.get("url", "")
            company = item.get("company", "")
            location = item.get("location", "")
            if title and url_j:
                jobs.append({"url": url_j, "title": title, "company": company,
                            "location": location, "source": "remoteok"})
    except: pass
    return jobs

# ==================== ARBEITNOW API ====================
def scrape_arbeitnow(client, pg):
    """Arbeitnow - JSON API."""
    jobs = []
    try:
        resp = safe_get(client, f"https://www.arbeitnow.com/api/job-board-api?page={pg + 1}", timeout=15)
        if not resp or resp.status_code != 200: return []
        data = resp.json()
        for item in data.get("data", []):
            title = item.get("title", "")
            url_j = item.get("url", "")
            company = item.get("company_name", "")
            location = item.get("location", "")
            if title and url_j:
                jobs.append({"url": url_j, "title": title, "company": company,
                            "location": location, "source": "arbeitnow"})
    except: pass
    return jobs

# ==================== REMOTIVE API ====================
def scrape_remotive(client, pg):
    """Remotive - JSON API."""
    jobs = []
    try:
        resp = safe_get(client, "https://remotive.com/api/remote-jobs?limit=100", timeout=15)
        if not resp or resp.status_code != 200: return []
        data = resp.json()
        for item in data.get("jobs", []):
            title = item.get("title", "")
            url_j = item.get("url", "")
            company = item.get("company_name", "")
            location = item.get("candidate_required_location", "")
            if title and url_j:
                jobs.append({"url": url_j, "title": title, "company": company,
                            "location": location, "source": "remotive"})
    except: pass
    return jobs

# ==================== WORKING NOMADS API ====================
def scrape_workingnomads(client, pg):
    """Working Nomads - JSON API."""
    jobs = []
    try:
        resp = safe_get(client, "https://www.workingnomads.com/api/exposed_jobs.json", timeout=15)
        if not resp or resp.status_code != 200: return []
        data = resp.json()
        start = pg * 25
        for item in data[start:start+25]:
            title = item.get("title", "")
            url_j = item.get("url", "")
            company = item.get("company_name", "")
            if title and url_j:
                jobs.append({"url": url_j, "title": title, "company": company,
                            "location": "Remote", "source": "workingnomads"})
    except: pass
    return jobs

KEYWORDS = [
    "software engineer", "backend engineer", "frontend engineer", "full stack developer",
    "data engineer", "devops engineer", "ML engineer", "AI engineer", "cloud engineer",
    "mobile developer", "python developer", "java developer", "react developer",
    "node developer", "go developer", "rust developer", "C++ engineer", "SRE",
    "security engineer", "platform engineer", "systems engineer", "QA engineer",
    "automation engineer", "database engineer", "network engineer", "fintech engineer",
    "blockchain developer", "game developer", "graphics engineer", "embedded engineer",
    "firmware engineer", "DevOps", "SDE", "tech lead", "principal engineer",
    "staff engineer", "product engineer", "application engineer", "web developer",
    "software developer", "IT engineer", "data scientist", "NLP engineer",
    "computer vision engineer", "robotics engineer", "AR engineer", "VR engineer",
    "compiler engineer", "kernel engineer", "distributed systems engineer",
    "microservices engineer", "API engineer", "payments engineer",
    "mobile app developer", "iOS developer", "Android developer", "Flutter developer",
    "React Native developer", "Kotlin developer", "Swift developer",
    "PHP developer", "Ruby developer", ".NET developer", "Scala developer",
    "Elixir developer", "Haskell developer", "Lua developer",
]

LOCATIONS = [
    "New York", "San Francisco", "Los Angeles", "Chicago", "Seattle",
    "Austin", "Boston", "Denver", "Atlanta", "Miami",
    "Dallas", "Houston", "Phoenix", "Portland", "San Diego",
    "Washington DC", "Remote", "London", "Berlin", "Toronto",
    "Sydney", "Melbourne", "Singapore", "Dublin", "Amsterdam",
    "Bangalore", "Mumbai", "Delhi", "Hyderabad", "Chennai",
    "Pune", "Noida", "Gurgaon", "Kolkata", "Jaipur",
    "Paris", "Munich", "Zurich", "Stockholm", "Oslo",
    "Copenhagen", "Helsinki", "Tokyo", "Seoul", "Hong Kong",
    "Sao Paulo", "Mexico City", "Buenos Aires", "Dubai", "Tel Aviv",
    "Barcelona", "Milan", "Rome", "Lisbon", "Vienna",
    "Prague", "Warsaw", "Budapest", "Krakow", "Bucharest",
    "Vancouver", "Calgary", "Ottawa", "Edmonton", "Montreal",
    "Manchester", "Birmingham", "Edinburgh", "Bristol", "Leeds",
    "Newcastle", "Glasgow", "Cardiff", "Nottingham", "Sheffield",
    "Adelaide", "Perth", "Brisbane", "Gold Coast", "Wellington",
    "Auckland", "Cape Town", "Johannesburg", "Nairobi", "Lagos",
    "Chennai", "Coimbatore", "Thiruvananthapuram", "Lucknow", "Indore",
    "Ahmedabad", "Surat", "Nagpur", "Visakhapatnam", "Patna",
]

def build_work_items():
    items = []
    # SimplyHired - 62 keywords × 86 locations × 50 pages
    for kw in KEYWORDS:
        for loc in LOCATIONS:
            for pg in range(MAX_PAGES):
                items.append(("simplyhired", kw, loc, pg))
    # Dice - same
    for kw in KEYWORDS:
        for loc in LOCATIONS:
            for pg in range(MAX_PAGES):
                items.append(("dice", kw, loc, pg))
    # CWJobs - keywords × 50 pages (no location needed)
    for kw in KEYWORDS:
        for pg in range(30):
            items.append(("cwjobs", kw, "", pg))
    # Wellfound - keywords × 30 pages
    for kw in KEYWORDS[:30]:
        for pg in range(30):
            items.append(("wellfound", kw, "", pg))
    # Adzuna - 10 countries × 30 keywords × 20 pages
    for country in ["com", "co.uk", "de", "fr", "ca", "in", "au", "nl", "sg", "nz"]:
        for kw in KEYWORDS[:30]:
            for pg in range(20):
                items.append((f"adzuna_{country}", kw, "", pg))
    # BuiltIn - keywords × 20 pages
    for kw in KEYWORDS[:30]:
        for pg in range(20):
            items.append(("builtin", kw, "", pg))
    # Himalayas API - keywords × 20 pages
    for kw in KEYWORDS[:30]:
        for pg in range(20):
            items.append(("himalayas", kw, "", pg))
    # RemoteOK API - 20 pages
    for pg in range(20):
        items.append(("remoteok", "", "", pg))
    # Arbeitnow API - 20 pages
    for pg in range(20):
        items.append(("arbeitnow", "", "", pg))
    # Remotive API - 10 pages
    for pg in range(10):
        items.append(("remotive", "", "", pg))
    # Working Nomads API - 10 pages
    for pg in range(10):
        items.append(("workingnomads", "", "", pg))
    return items

def run_round(db, round_num):
    """Run one round of scraping. Returns (new_count, elapsed_min)."""
    all_items = build_work_items()

    # Load checkpoint
    done = set()
    if CP_PATH.exists():
        try:
            cp = json.loads(CP_PATH.read_text("utf-8"))
            done = set(cp.get("done", []))
        except: pass

    remaining = [i for i in all_items if str(i) not in done]
    if not remaining:
        log(f"All {len(all_items)} work items done. Resetting for round {round_num+1}.")
        done.clear()
        remaining = all_items[:]

    random.shuffle(remaining)
    work_queue = queue.Queue()
    for item in remaining:
        work_queue.put(item)

    start = time.time()
    counter = {"iters": 0, "new": 0}
    c_lock = threading.Lock()
    stats = {}
    stop_event = threading.Event()

    def worker(wid):
        client = httpx.Client(timeout=20, follow_redirects=True,
                              limits=httpx.Limits(max_connections=5, max_keepalive_connections=3))
        try:
            while not stop_event.is_set():
                try:
                    item = work_queue.get(timeout=5)
                except queue.Empty:
                    break
                site, kw, loc, pg = item
                jobs = []
                try:
                    time.sleep(random.uniform(0.05, 0.3))
                    if site == "simplyhired":
                        jobs = scrape_simplyhired(client, kw, loc, pg)
                    elif site == "dice":
                        jobs = scrape_dice(client, kw, loc, pg)
                    elif site == "cwjobs":
                        jobs = scrape_cwjobs(client, kw, loc, pg)
                    elif site == "wellfound":
                        jobs = scrape_wellfound(client, kw, pg)
                    elif site.startswith("adzuna_"):
                        country = site.replace("adzuna_", "")
                        jobs = scrape_adzuna(client, country, kw, pg)
                    elif site == "builtin":
                        jobs = scrape_builtin(client, kw, pg)
                    elif site == "himalayas":
                        jobs = scrape_himalayas(client, kw, pg)
                    elif site == "remoteok":
                        jobs = scrape_remoteok(client, pg)
                    elif site == "arbeitnow":
                        jobs = scrape_arbeitnow(client, pg)
                    elif site == "remotive":
                        jobs = scrape_remotive(client, pg)
                    elif site == "workingnomads":
                        jobs = scrape_workingnomads(client, pg)
                except:
                    jobs = []

                new = db.insert(jobs) if jobs else 0
                with c_lock:
                    counter["iters"] += 1
                    counter["new"] += new
                    done.add(str(item))
                    stats[site] = stats.get(site, 0) + 1

                if new > 0:
                    log(f"  W{wid:02d} +{new:3d} {site}:{kw[:15]}|{loc[:10]}|p{pg}")

                if counter["iters"] % 200 == 0:
                    ct = db.count()
                    rate = counter["new"] / max((time.time()-start)/60, 0.1)
                    log(f"  [R{round_num}] [{counter['iters']:,}] +{counter['new']:,} | DB={ct:,} | {rate:.0f}/min")
                    try:
                        CP_PATH.write_text(json.dumps({"done": list(done), "total_new": counter["new"],
                                                         "iters": counter["iters"], "round": round_num}), "utf-8")
                    except: pass
        except Exception as e:
            log(f"  W{wid:02d} FATAL: {e}")
        finally:
            try: client.close()
            except: pass

    log(f"[Round {round_num}] Launching {WORKERS} workers | {len(remaining):,} items")
    threads = []
    for i in range(WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.05)

    for t in threads:
        t.join(timeout=7200)

    stop_event.set()
    time.sleep(2)

    ft = db.count()
    elapsed = (time.time() - start) / 60

    log("=" * 70)
    log(f"ROUND {round_num} COMPLETE | New: +{counter['new']:,}")
    log(f"DB: {ft:,} | Gap: {max(0, TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 70)
    return counter["new"], elapsed


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    log("=" * 70)
    log("PAGINATION SCRAPER V2 - Infinite loop, 30 workers, 11 sources")
    log("=" * 70)

    db = JobDB()
    total = db.count()
    log(f"DB: {total:,} | Gap: {max(0, TARGET-total):,}")
    db.load_dedup_keys()

    if reset and CP_PATH.exists():
        try: CP_PATH.unlink()
        except: pass

    round_num = 0
    while True:
        round_num += 1
        ct = db.count()
        if ct >= TARGET:
            log(f"TARGET REACHED! DB={ct:,} >= {TARGET:,}")
            break
        try:
            new, elapsed = run_round(db, round_num)
            if new == 0:
                log("0 new jobs in round — waiting 60s before retry")
                time.sleep(60)
            else:
                time.sleep(5)
        except Exception as e:
            log(f"ROUND {round_num} CRASHED: {e}")
            time.sleep(30)

    db.close()
    log("DONE")
