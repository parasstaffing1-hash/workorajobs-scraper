#!/usr/bin/env python3
"""
Gap Filler — targets untapped job sources to find unique jobs not in the DB.
Focus: Indeed country variants, Naukri, more keyword combos, smaller markets.
"""
import hashlib, os, queue, random, sqlite3, sys, time, threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"
LOG = ROOT / "gap_log.txt"
CP = ROOT / "gap_cp.json"
NOW = datetime.now(timezone.utc).isoformat()

_seen: set[str] = set()
_lock = threading.Lock()
_stats = {}
_counter = 0
_q: queue.Queue = queue.Queue(maxsize=100000)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

def _hash(url, title, company):
    raw = f"{url.strip().lower()}|{title.strip().lower()}|{company.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def load_seen():
    global _seen
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        rows = conn.execute("SELECT dedupe_key FROM jobs").fetchall()
        _seen = {r[0] for r in rows}
        conn.close()
        log(f"Loaded {len(_seen):,} dedup keys")
    except Exception as e:
        log(f"Error loading dedup: {e}")

def flush(jobs):
    if not jobs:
        return 0
    new = []
    with _lock:
        for j in jobs:
            h = _hash(j["url"], j["title"], j["company"])
            if h not in _seen:
                _seen.add(h)
                new.append((h, j.get("title",""), j.get("company",""), j.get("location",""),
                           j.get("url",""), j.get("description","")[:2000] if j.get("description") else "",
                           j.get("tags",""), j.get("source",""), j.get("source_kind",""),
                           j.get("id",""), j.get("salary",""), j.get("posted_at",""),
                           NOW, NOW, 1))
    if not new:
        return 0
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executemany("""INSERT OR IGNORE INTO jobs
            (dedupe_key,title,company,location,url,description,tags,source,
             source_kind,external_id,salary,posted_at,first_seen_at,last_seen_at,is_active)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", new)
        conn.commit()
        conn.close()
    except:
        pass
    return len(new)

# ═══════════════════════════════════════════════════════════════
# INDEED COUNTRY VARIANTS (each domain has unique listings)
# ═══════════════════════════════════════════════════════════════
INDEED_DOMAINS = [
    ("in", "com"), ("co.uk", None), ("de", None), ("fr", None),
    ("ca", None), ("au", None), ("nl", None), ("sg", None),
    ("nz", None), ("za", None), ("ie", None), ("ph", None),
    ("ae", None), ("sa", None), ("br", None), ("mx", None),
    ("it", "it"), ("es", None), ("pl", None), ("se", None),
    ("no", "no"), ("dk", "dk"), ("fi", None), ("be", "be"),
    ("at", "at"), ("ch", "ch"), ("pt", "pt"), ("hk", "com.hk"),
    ("kr", "co.kr"), ("tw", "com.tw"),
]

KW = [
    "software engineer", "software developer", "backend developer", "frontend developer",
    "full stack developer", "data engineer", "devops engineer", "SRE",
    "python developer", "java developer", "react developer", "node developer",
    "go developer", "rust developer", "C++ developer", "mobile developer",
    "ML engineer", "AI engineer", "data scientist", "cloud engineer",
    "platform engineer", "security engineer", "systems engineer",
    "senior software engineer", "junior software developer", "tech lead",
    "principal engineer", "staff engineer", "Android developer", "iOS developer",
    "embedded engineer", "database engineer", "network engineer",
    "fintech engineer", "blockchain developer", "game developer",
    "TypeScript developer", "PHP developer", "Ruby developer",
    "Kotlin developer", "Swift developer", "Scala developer",
    "Angular developer", "Vue developer", "Django developer",
    "FastAPI developer", "Spring developer", "AWS developer",
    "Kubernetes engineer", "Terraform engineer", "Docker engineer",
    "LLM engineer", "GenAI engineer", "computer vision engineer",
    "automation engineer", "build engineer", "release engineer",
]

LOCATIONS = [
    "", "remote", "New York", "San Francisco", "London", "Berlin",
    "Toronto", "Sydney", "Singapore", "Bangalore", "Mumbai",
    "Delhi", "Hyderabad", "Chennai", "Pune", "Dublin",
    "Amsterdam", "Paris", "Tokyo", "Seoul", "Hong Kong",
    "Austin", "Seattle", "Chicago", "Boston", "Denver",
    "Los Angeles", "Atlanta", "Miami", "Dallas", "Portland",
    "Washington DC", "San Diego", "Phoenix", "Houston",
    "Melbourne", "Brisbane", "Dubai", "Tel Aviv", "Barcelona",
    "Stockholm", "Oslo", "Copenhagen", "Zurich", "Munich",
    "Milan", "Lisbon", "Vienna", "Prague", "Warsaw",
    "Vancouver", "Montreal", "Calgary", "Edinburgh", "Manchester",
    "Cape Town", "Nairobi", "Lagos", "Mexico City", "Sao Paulo",
    "Noida", "Gurgaon", "Kolkata", "Jaipur", "Ahmedabad",
    "Rajkot", "Thiruvananthapuram", "Kochi", "Coimbatore", "Indore",
]

# Indeed by country
def scrape_indeed_country(session, keyword, location, page, cc, tld):
    jobs = []
    domain = tld or cc
    url = f"https://{cc}.indeed.com/jobs?q={quote_plus(keyword)}&l={quote_plus(location)}&start={page*10}"
    try:
        r = session.get(url, timeout=12, allow_redirects=True)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, 'html.parser')
        cards = soup.select('div.job_seen_beacon') or soup.select('td.resultContent') or soup.select('div.jobsearch-ResultsList > div')
        for card in cards[:25]:
            title_el = card.select_one('h2.jobTitle a') or card.select_one('a.jcs-JobTitle') or card.select_one('a[data-jk]')
            company_el = card.select_one('span[data-testid="company-name"]') or card.select_one('span.companyName') or card.select_one('span.company')
            loc_el = card.select_one('div[data-testid="text-location"]') or card.select_one('div.companyLocation')
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            if not link.startswith("http"):
                link = f"https://{cc}.indeed.com{link}"
            company = company_el.get_text(strip=True) if company_el else ""
            loc = loc_el.get_text(strip=True) if loc_el else location
            jid = link.split("jk=")[-1].split("&")[0] if "jk=" in link else hashlib.md5(link.encode()).hexdigest()[:16]
            jobs.append({"url": link, "title": title, "company": company, "location": loc,
                        "source": f"indeed_{cc}", "id": jid, "desc": "", "ts": NOW})
    except:
        pass
    return jobs

# Naukri.com (India's #1 job board)
def scrape_naukri(session, keyword, location, page):
    jobs = []
    url = f"https://www.naukri.com/{quote_plus(keyword.lower().replace(' ', '-'))}-jobs-in-{quote_plus(location.lower().replace(' ', '-'))}?page={page+1}"
    try:
        headers = {**HEADERS, "Referer": "https://www.naukri.com/", "Accept-Language": "en-US,en;q=0.9"}
        r = session.get(url, headers=headers, timeout=15, allow_redirects=True)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, 'html.parser')
        cards = soup.select('div.srp-cardlist') or soup.select('article') or soup.select('div.ript')
        for card in cards[:25]:
            title_el = card.select_one('a.title') or card.select_one('a.ellipsis') or card.select_one('a[data-title]')
            company_el = card.select_one('a.subTitle') or card.select_one('span.companyName')
            loc_el = card.select_one('span.locWdth') or card.select_one('span.location')
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            if not link.startswith("http"):
                link = "https://www.naukri.com" + link
            company = company_el.get_text(strip=True) if company_el else ""
            loc = loc_el.get_text(strip=True) if loc_el else location
            jid = hashlib.md5(f"{title}{company}".encode()).hexdigest()[:16]
            jobs.append({"url": link, "title": title, "company": company, "location": loc,
                        "source": "naukri", "id": jid, "desc": "", "ts": NOW})
    except:
        pass
    return jobs

# Remotive (remote jobs API - free, no auth)
def scrape_remotive(session, category, page):
    jobs = []
    url = f"https://remotive.com/api/remote-jobs?category={quote_plus(category)}&page={page}"
    try:
        r = session.get(url, timeout=12)
        if r.status_code != 200:
            return []
        data = r.json()
        for j in data.get("jobs", [])[:20]:
            jobs.append({
                "url": j.get("url", ""),
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("candidate_required_location", ""),
                "source": "remotive",
                "id": str(j.get("id", "")),
                "desc": j.get("description", "")[:500],
                "tags": ",".join(j.get("tags", [])),
                "ts": NOW,
            })
    except:
        pass
    return jobs

# Himalayas (free API)
def scrape_himalayas(session, category, page):
    jobs = []
    url = f"https://himalayas.app/jobs/api?limit=20&offset={page*20}"
    if category:
        url += f"&category={quote_plus(category)}"
    try:
        r = session.get(url, timeout=12)
        if r.status_code != 200:
            return []
        data = r.json()
        for j in data.get("jobs", []):
            jobs.append({
                "url": f"https://himalayas.app/jobs/{j.get('slug', '')}",
                "title": j.get("title", ""),
                "company": j.get("companyName", ""),
                "location": j.get("location", ""),
                "source": "himalayas",
                "id": str(j.get("id", "")),
                "desc": j.get("description", "")[:500],
                "tags": ",".join(j.get("tags", [])),
                "ts": NOW,
            })
    except:
        pass
    return jobs

# Arbeitnow (free API)
def scrape_arbeitnow(session, page):
    jobs = []
    url = f"https://www.arbeitnow.com/api/job-board-api?page={page}"
    try:
        r = session.get(url, timeout=12)
        if r.status_code != 200:
            return []
        data = r.json()
        for j in data.get("data", [])[:20]:
            jobs.append({
                "url": j.get("url", ""),
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("location", ""),
                "source": "arbeitnow",
                "id": str(j.get("id", "")),
                "desc": j.get("description", "")[:500],
                "tags": ",".join(j.get("tags", [])),
                "ts": NOW,
            })
    except:
        pass
    return jobs

# Google Jobs via JobSpy (just google source)
def scrape_google_jobs(kw, loc):
    jobs = []
    try:
        from jobspy import scrape_jobs as js_scrape
        result = js_scrape(site_name=["google"], search_term=kw, location=loc, results_wanted=30)
        if hasattr(result, 'jobs'):
            for j in result.jobs:
                jobs.append({
                    "url": j.url or "", "title": j.title or "", "company": j.company or "",
                    "location": j.location or loc, "source": "google_jobs",
                    "id": str(j.id or ""), "desc": (j.description or "")[:500],
                    "tags": ",".join(j.tags or []), "ts": NOW,
                })
    except:
        pass
    return jobs

# ZipRecruiter via web scraping
def scrape_ziprecruiter(session, keyword, location, page):
    jobs = []
    url = f"https://www.ziprecruiter.com/jobs-search?search={quote_plus(keyword)}&location={quote_plus(location)}&page={page}"
    try:
        r = session.get(url, timeout=12, allow_redirects=True)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, 'html.parser')
        cards = soup.select('article.job_result') or soup.select('div.job_content')
        for card in cards[:20]:
            title_el = card.select_one('h2 a') or card.select_one('a.job_link')
            company_el = card.select_one('a.t_org_link') or card.select_one('p.company_name')
            loc_el = card.select_one('a.t_location') or card.select_one('p.location')
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            company = company_el.get_text(strip=True) if company_el else ""
            loc = loc_el.get_text(strip=True) if loc_el else location
            jid = hashlib.md5(f"{title}{company}".encode()).hexdigest()[:16]
            jobs.append({"url": link, "title": title, "company": company, "location": loc,
                        "source": "ziprecruiter", "id": jid, "desc": "", "ts": NOW})
    except:
        pass
    return jobs

# Jooble API (free, no auth key needed)
def scrape_jooble(session, keyword, location, page):
    jobs = []
    url = "https://jooble.org/api/"
    try:
        payload = {"keywords": keyword, "location": location, "page": str(page)}
        headers = {"Content-Type": "application/json"}
        r = session.post(url, json=payload, headers=headers, timeout=12)
        if r.status_code != 200:
            return []
        data = r.json()
        for j in data.get("jobs", [])[:20]:
            title = j.get("title", "")
            link = j.get("link", "") or j.get("url", "")
            company = j.get("company", "")
            loc = j.get("location", "") or location
            jid = j.get("id", hashlib.md5(f"{title}{company}".encode()).hexdigest()[:16])
            jobs.append({"url": link, "title": title, "company": company, "location": loc,
                        "source": "jooble", "id": str(jid), "desc": j.get("snippet", "")[:500], "ts": NOW})
    except:
        pass
    return jobs

# Reed.co.uk (UK jobs API - free)
def scrape_reed(session, keyword, location, page):
    jobs = []
    url = f"https://www.reed.co.uk/api/1.0/search?keywords={quote_plus(keyword)}&locationName={quote_plus(location)}&resultsToTake=25&startFrom={page*25}"
    try:
        r = session.get(url, timeout=12)
        if r.status_code != 200:
            return []
        data = r.json()
        for j in data.get("results", [])[:25]:
            jobs.append({
                "url": f"https://www.reed.co.uk/job/{j.get('jobId', '')}",
                "title": j.get("jobTitle", ""),
                "company": j.get("employerName", ""),
                "location": j.get("locationName", "") or location,
                "source": "reed",
                "id": str(j.get("jobId", "")),
                "desc": j.get("jobDescription", "")[:500],
                "salary": j.get("minimumSalary") or j.get("maximumSalary") or "",
                "ts": NOW,
            })
    except:
        pass
    return jobs


# ═══════════════════════════════════════════════════════════════
# BUILD WORK ITEMS
# ═══════════════════════════════════════════════════════════════
def build_work():
    items = []

    # Indeed country variants: KW × countries × 10 pages
    for kw in KW[:30]:  # Top 30 keywords
        for cc, tld in INDEED_DOMAINS:
            for p in range(10):
                items.append(("indeed", kw, "", p, (cc, tld)))

    # Naukri: KW × Indian cities × 5 pages
    india_cities = ["Bangalore", "Mumbai", "Delhi", "Hyderabad", "Chennai", "Pune",
                    "Noida", "Gurgaon", "Kolkata", "Jaipur", "Ahmedabad", "Kochi",
                    "Coimbatore", "Indore", "Thiruvananthapuram", "Remote"]
    for kw in KW[:40]:
        for loc in india_cities:
            for p in range(5):
                items.append(("naukri", kw, loc, p, None))

    # Remotive: categories × 10 pages
    remotive_cats = ["software-dev", "design", "product", "customer-support",
                     "marketing", "data", "devops", "qa", "business"]
    for cat in remotive_cats:
        for p in range(10):
            items.append(("remotive", cat, "", p, None))

    # Himalayas: categories × 10 pages
    himalaya_cats = ["engineering", "design", "product", "marketing", "data", "devops", "qa"]
    for cat in himalaya_cats:
        for p in range(10):
            items.append(("himalayas", cat, "", p, None))

    # Arbeitnow: 30 pages
    for p in range(30):
        items.append(("arbeitnow", "", "", p, None))

    # Google Jobs: KW × LOC (via JobSpy, 30 results each)
    for kw in KW[:20]:
        for loc in LOCATIONS[:20]:
            items.append(("google", kw, loc, 0, None))

    # ZipRecruiter: KW × 10 pages
    for kw in KW[:20]:
        for p in range(10):
            items.append(("ziprecruiter", kw, "Remote", p, None))

    # Jooble: KW × 10 pages
    for kw in KW[:30]:
        for p in range(10):
            items.append(("jooble", kw, "", p, None))

    # Reed: KW × UK × 10 pages
    for kw in KW[:20]:
        for p in range(10):
            items.append(("reed", kw, "London", p, None))

    random.shuffle(items)
    return items


# ═══════════════════════════════════════════════════════════════
# WORKER
# ═══════════════════════════════════════════════════════════════
def worker(wid):
    global _counter
    session = requests.Session()
    session.headers.update(HEADERS)
    adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=2)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    while True:
        try:
            item = _q.get_nowait()
        except queue.Empty:
            break

        stype = item[0]
        kw, loc, page = item[1], item[2], item[3]

        try:
            if stype == "indeed":
                cc, tld = item[4]
                jobs = scrape_indeed_country(session, kw, loc, page, cc, tld)
            elif stype == "naukri":
                jobs = scrape_naukri(session, kw, loc, page)
            elif stype == "remotive":
                jobs = scrape_remotive(session, kw, page)
            elif stype == "himalayas":
                jobs = scrape_himalayas(session, kw, page)
            elif stype == "arbeitnow":
                jobs = scrape_arbeitnow(session, page)
            elif stype == "google":
                jobs = scrape_google_jobs(kw, loc)
            elif stype == "ziprecruiter":
                jobs = scrape_ziprecruiter(session, kw, loc, page)
            elif stype == "jooble":
                jobs = scrape_jooble(session, kw, loc, page)
            elif stype == "reed":
                jobs = scrape_reed(session, kw, loc, page)
            else:
                jobs = []
        except:
            jobs = []

        if jobs:
            n = flush(jobs)
            _counter += 1
            _stats[stype] = _stats.get(stype, 0) + 1
            if n > 0 and wid < 10:
                log(f"  W{wid:03d} +{n:3d} {stype}:{kw[:20] or loc[:20]}")

        time.sleep(0.3)  # Be polite to servers


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    global _counter
    import json

    log("=" * 60)
    log("GAP FILLER — Untapped sources for unique jobs")
    log("=" * 60)

    load_seen()

    start_count = len(_seen)
    log(f"Starting with {start_count:,} existing jobs")

    # Build work items
    items = build_work()
    log(f"Work items: {len(items):,}")

    # Load checkpoint
    done = set()
    if CP.exists():
        try:
            done = set(json.loads(CP.read_text()))
            log(f"Resuming from {len(done)} done items")
        except:
            pass

    # Add to queue
    added = 0
    for i, item in enumerate(items):
        key = f"{item[0]}:{item[1]}:{item[2]}:{item[3]}"
        if key not in done:
            _q.put(item)
            added += 1
    log(f"Queue: {added} items to process")

    # Start workers
    WORKERS = 50
    threads = []
    for i in range(WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)

    # Progress reporter
    done_count = 0
    new_count = 0
    start_time = time.time()
    last_save = time.time()

    # Wait for queue to empty (workers will stop when queue is empty)
    while not _q.empty():
        time.sleep(5)
        elapsed = time.time() - start_time
        current = len(_seen)
        rate = (current - start_count) / (elapsed / 60) if elapsed > 0 else 0

        if int(elapsed) % 30 < 5:
            log(f"STATUS | Queue: {_q.qsize()} | New: {current - start_count:,} | Rate: {rate:.0f}/min | Total: {current:,}")

        # Save checkpoint every 60 seconds
        if time.time() - last_save > 60:
            done_snapshot = list(done)
            CP.write_text(json.dumps(done_snapshot[-5000:]))  # Keep last 5000
            last_save = time.time()

    # Wait for threads
    for t in threads:
        t.join(timeout=10)

    elapsed = time.time() - start_time
    final = len(_seen)
    log("=" * 60)
    log(f"DONE | New: {final - start_count:,} | Total: {final:,} | Time: {elapsed/60:.1f}min")
    log(f"Source breakdown: {_stats}")
    log("=" * 60)

    # Final save
    CP.write_text(json.dumps(list(done)[-5000:]))


if __name__ == "__main__":
    main()
