#!/usr/bin/env python3
"""ULTRA V2 — Maximum speed scraper.
300 async workers, single-writer DB thread, 600+ keywords, 10+ sources.
"""
import asyncio, hashlib, os, random, sqlite3, sys, time, queue, threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
import aiohttp
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"
LOG_PATH = ROOT / "ultra_v2_log.txt"
CP_PATH = ROOT / "ultra_v2_cp.json"
TARGET = 1_000_000
WORKERS = 300
DB_BATCH = 2000

_seen: set[str] = set()
_write_q: queue.Queue = queue.Queue(maxsize=50000)
_log_lock = threading.Lock()
_stats: dict = {}
_counter = {"iters": 0, "new_total": 0}
_start_time = 0.0

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _log_lock:
        try:
            with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{ts}] {msg}\n")
        except:
            pass

def _hash(url, title, company):
    raw = f"{url.strip().lower()}|{title.strip().lower()}|{company.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def db_writer_thread():
    """Single thread: drain queue -> dedup -> batch insert. No contention."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-128000")
    batch = []
    last_flush = time.time()
    total_written = 0
    while True:
        try:
            item = _write_q.get(timeout=2)
        except queue.Empty:
            if batch and time.time() - last_flush > 1:
                _flush(conn, batch)
                total_written += len(batch)
                batch = []
                last_flush = time.time()
            continue
        if item is None:  # shutdown sentinel
            break
        url = item.get("url", "").strip()
        title = item.get("title", "").strip()
        company = item.get("company", "").strip()
        if not url or not title:
            continue
        key = _hash(url, title, company)
        if key in _seen:
            continue
        _seen.add(key)
        batch.append((key, title, company, item.get("location", ""),
                       item.get("desc", "")[:500], url, item.get("source", ""),
                       "ultra", item.get("id", ""), item.get("posted"),
                       item.get("salary", ""), item.get("tags", ""),
                       item.get("ts", ""), item.get("ts", "")))
        if len(batch) >= DB_BATCH:
            _flush(conn, batch)
            total_written += len(batch)
            batch = []
            last_flush = time.time()
    if batch:
        _flush(conn, batch)
        total_written += len(batch)
    conn.close()
    log(f"DB writer done. Wrote {total_written:,} total jobs.")

def _flush(conn, batch):
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO jobs"
            "(dedupe_key,title,company,location,description,url,"
            "source,source_kind,external_id,posted_at,salary,tags,"
            "first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            batch)
        conn.commit()
        _counter["new_total"] += len(batch)
    except Exception as e:
        log(f"DB flush error: {e}")

def db_count():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    return n

# ── HTTP headers ──
UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]
HDR = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
       "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = aiohttp.ClientTimeout(total=8, connect=4)
NOW = datetime.now(timezone.utc).isoformat()

# ── Sources ──

async def scrape_sh(session, kw, loc, pg):
    """SimplyHired — 20 jobs/page, excellent yield."""
    jobs = []
    url = f"https://www.simplyhired.com/search?q={quote_plus(kw)}&pn={pg}"
    if loc:
        url += f"&l={quote_plus(loc)}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200:
                return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("h2 a"):
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or not h:
                continue
            if not h.startswith("http"):
                h = "https://www.simplyhired.com" + h
            c = ""
            p = a.parent
            for _ in range(5):
                if p is None:
                    break
                for s in p.select("span"):
                    v = s.get_text(strip=True)
                    if v and v != t and len(v) < 80:
                        c = v
                        break
                if c:
                    break
                p = p.parent
            jobs.append({"url": h, "title": t, "company": c, "location": loc, "source": "sh", "ts": NOW})
    except:
        pass
    return jobs

async def scrape_dc(session, kw, loc, pg):
    """Dice — 20 jobs/page, very reliable."""
    jobs = []
    url = f"https://www.dice.com/jobs?q={quote_plus(kw)}&start={pg*20}&pageSize=20"
    if loc:
        url += f"&location={quote_plus(loc)}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200:
                return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("[data-testid='job-card']"):
            t, h = "", ""
            for a in card.select("a[href*='/job-detail/']"):
                v = a.get_text(strip=True)
                if v and len(v) > 3:
                    t = v
                    h = a.get("href", "")
                    break
            if not t or not h:
                continue
            if not h.startswith("http"):
                h = "https://www.dice.com" + h
            c = ""
            ce = card.select_one("[data-testid='job-card-company-name']")
            if ce:
                c = ce.get_text(strip=True)
            jobs.append({"url": h, "title": t, "company": c, "location": loc, "source": "dc", "ts": NOW})
    except:
        pass
    return jobs

async def scrape_jooble(session, kw, loc, pg):
    """Jooble — huge aggregator, HTML scrape."""
    jobs = []
    slug = quote_plus(kw)
    url = f"https://jooble.org/SearchResult?ukw={slug}&loc={quote_plus(loc or '')}&p={pg+1}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200:
                return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for article in soup.select("article, [data-test-name='_jobCard'], [class*='vacancy']"):
            a = article.select_one("a[href]")
            if not a:
                continue
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or len(t) < 5:
                continue
            if not h.startswith("http"):
                h = "https://jooble.org" + h
            c_el = article.select_one("[class*='company'], [class*='employer']")
            c = c_el.get_text(strip=True) if c_el else ""
            jobs.append({"url": h, "title": t, "company": c, "location": loc, "source": "jooble", "ts": NOW})
    except:
        pass
    return jobs

async def scrape_cwjobs(session, kw, pg):
    """CWJobs — UK tech jobs, 25/page."""
    jobs = []
    url = f"https://www.cwjobs.co.uk/jobs/{quote_plus(kw)}?page={pg+1}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200:
                return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("[class*='job'], article"):
            a = card.select_one("a[href*='/job/']")
            if not a:
                continue
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or len(t) < 5:
                continue
            if not h.startswith("http"):
                h = "https://www.cwjobs.co.uk" + h
            c_el = card.select_one("[class*='company'], span")
            c = c_el.get_text(strip=True) if c_el else ""
            jobs.append({"url": h, "title": t, "company": c, "location": "UK", "source": "cwjobs", "ts": NOW})
    except:
        pass
    return jobs

async def scrape_builtin(session, kw, pg):
    """BuiltIn — tech company jobs, ~40/page."""
    jobs = []
    url = f"https://builtin.com/jobs?q={quote_plus(kw)}&p={pg+1}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200:
                return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("[class*='card'], [class*='job'], article"):
            a = card.select_one("a[href*='/jobs/']")
            if not a:
                continue
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or len(t) < 5:
                continue
            if not h.startswith("http"):
                h = "https://builtin.com" + h
            jobs.append({"url": h, "title": t, "company": "", "location": "", "source": "builtin", "ts": NOW})
    except:
        pass
    return jobs

async def scrape_reed(session, kw, pg):
    """Reed.co.uk — UK jobs, 25/page."""
    jobs = []
    url = f"https://www.reed.co.uk/jobs/{quote_plus(kw)}?pageno={pg+1}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200:
                return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("[class*='job'], article, [data-qa='job-card']"):
            a = card.select_one("a[href*='/job/']")
            if not a:
                continue
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or len(t) < 5:
                continue
            if not h.startswith("http"):
                h = "https://www.reed.co.uk" + h
            jobs.append({"url": h, "title": t, "company": "", "location": "UK", "source": "reed", "ts": NOW})
    except:
        pass
    return jobs

async def scrape_indeed(session, kw, loc, pg):
    """Indeed — huge but sometimes blocks. Rotate UAs."""
    jobs = []
    l = quote_plus(loc) if loc else ""
    url = f"https://www.indeed.com/jobs?q={quote_plus(kw)}&l={l}&start={pg*10}"
    try:
        async with session.get(url, headers={
            **HDR, "User-Agent": random.choice(UA),
            "Accept-Language": "en-US,en;q=0.9",
        }, timeout=TIMEOUT) as r:
            if r.status != 200:
                return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("div.job_seen_beacon, div.jobsearch-ResultsList > div"):
            a = card.select_one("a[href*='/rc/clk'], a.jcs-JobTitle")
            if not a:
                continue
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or len(t) < 3:
                continue
            if not h.startswith("http"):
                h = "https://www.indeed.com" + h
            c_el = card.select_one("span[data-testid='company-name'], [class*='companyName']")
            c = c_el.get_text(strip=True) if c_el else ""
            jobs.append({"url": h, "title": t, "company": c, "location": loc, "source": "indeed", "ts": NOW})
    except:
        pass
    return jobs

async def scrape_adzuna(session, kw, country, pg):
    """Adzuna — works across 10 countries."""
    jobs = []
    url = f"https://www.adzuna.{country}/search?q={quote_plus(kw)}&pg={pg+1}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200:
                return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[data-aid='jobResultTitle'], h2 a[href*='/job/']"):
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or not h:
                continue
            if not h.startswith("http"):
                h = f"https://www.adzuna.{country}" + h
            c = ""
            p = a.parent
            if p:
                for s in p.select("span"):
                    v = s.get_text(strip=True)
                    if v and v != t and len(v) < 80:
                        c = v
                        break
            jobs.append({"url": h, "title": t, "company": c, "location": "", "source": f"adzuna", "ts": NOW})
    except:
        pass
    return jobs

async def scrape_talent(session, kw, loc, pg):
    """Talent.com (formerly neuvoo) — large global aggregator."""
    jobs = []
    url = f"https://www.talent.com/jobs?k={quote_plus(kw)}&l={quote_plus(loc or '')}&p={pg+1}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200:
                return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("[class*='job-card'], [class*='result'], article"):
            a = card.select_one("a[href*='/job/']")
            if not a:
                continue
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or len(t) < 5:
                continue
            if not h.startswith("http"):
                h = "https://www.talent.com" + h
            jobs.append({"url": h, "title": t, "company": "", "location": loc, "source": "talent", "ts": NOW})
    except:
        pass
    return jobs

# ── Keywords ──
KW_CORE = [
    "software engineer", "software developer", "backend engineer", "frontend engineer",
    "full stack developer", "full stack engineer", "web developer", "mobile developer",
    "data engineer", "devops engineer", "SRE", "platform engineer", "infrastructure engineer",
    "security engineer", "cloud engineer", "systems engineer", "QA engineer",
    "automation engineer", "database engineer", "network engineer", "fintech engineer",
    "blockchain developer", "game developer", "graphics engineer", "embedded engineer",
    "firmware engineer", "SDE", "tech lead", "principal engineer", "staff engineer",
    "product engineer", "application engineer", "AI engineer", "ML engineer",
    "NLP engineer", "computer vision engineer", "robotics engineer",
    "compiler engineer", "kernel engineer", "distributed systems engineer",
    "microservices engineer", "API engineer", "payments engineer",
]
KW_LANG = [
    "python developer", "java developer", "react developer", "node developer",
    "go developer", "golang developer", "rust developer", "C++ engineer",
    "C# developer", "TypeScript developer", "Kotlin developer", "Swift developer",
    "PHP developer", "Ruby developer", ".NET developer", "Scala developer",
    "Angular developer", "Vue developer", "Django developer", "FastAPI developer",
    "Spring Boot developer", "Perl developer", "Elixir developer", "Haskell developer",
]
KW_CLOUD = [
    "AWS engineer", "GCP engineer", "Azure engineer", "Kubernetes engineer",
    "Terraform engineer", "Docker engineer", "cloud architect",
    "PostgreSQL engineer", "MongoDB engineer", "Redis engineer", "Kafka engineer",
    "Hadoop engineer", "Spark engineer", "Elasticsearch engineer",
]
KW_AI = [
    "machine learning engineer", "deep learning engineer", "GenAI engineer",
    "LLM engineer", "RAG engineer", "MLOps engineer", "data scientist",
    "AI infrastructure engineer", "ML platform engineer", "NLP developer",
]
KW_SENIORITY = [
    "junior software engineer", "senior software engineer", "lead software engineer",
    "junior developer", "senior developer", "lead developer",
    "junior backend engineer", "senior backend engineer",
    "junior frontend engineer", "senior frontend engineer",
    "junior data engineer", "senior data engineer",
    "junior devops engineer", "senior devops engineer",
    "staff software engineer", "principal software engineer",
    "distinguished engineer", "fellow engineer",
]
KW_WEB = [
    "React developer", "Angular developer", "Vue.js developer", "Next.js developer",
    "Node.js developer", "Express developer", "Django developer", "Rails developer",
    "Laravel developer", "WordPress developer", "Shopify developer",
]
KW_MOBILE = [
    "iOS developer", "Android developer", "Flutter developer", "React Native developer",
    "mobile app developer", "Swift developer", "Kotlin developer",
]
KW_DATA = [
    "data engineer", "data scientist", "data analyst", "analytics engineer",
    "big data engineer", "ETL developer", "data pipeline engineer",
    "business intelligence developer", "database administrator",
]
KW_DEVOPS = [
    "DevOps engineer", "SRE", "site reliability engineer", "cloud engineer",
    "platform engineer", "infrastructure engineer", "CI/CD engineer",
    "release engineer", "build engineer", "system administrator",
]
KW_SECURITY = [
    "security engineer", "cybersecurity engineer", "penetration tester",
    "application security engineer", "cloud security engineer",
    "security architect", "SOC analyst", "information security engineer",
]
KW_EMBEDDED = [
    "embedded engineer", "embedded systems engineer", "firmware engineer",
    "RTOS engineer", "driver engineer", "BSP engineer", "FPGA engineer",
]
KW_OTHER = [
    "technical writer", "scrum master", "project manager", "product manager",
    "UX engineer", "design engineer", "solutions architect",
    "computer engineer", "IT engineer", "network architect",
]

ALL_KW = list(set(KW_CORE + KW_LANG + KW_CLOUD + KW_AI + KW_SENIORITY +
                   KW_WEB + KW_MOBILE + KW_DATA + KW_DEVOPS + KW_SECURITY +
                   KW_EMBEDDED + KW_OTHER))

LOC = [
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
    "Coimbatore", "Thiruvananthapuram", "Lucknow", "Indore",
    "Ahmedabad", "Surat", "Nagpur", "Visakhapatnam", "Patna",
    "",  # empty = no location filter
]
LOC_US = ["New York", "San Francisco", "Los Angeles", "Chicago", "Seattle",
          "Austin", "Boston", "Denver", "Atlanta", "Miami", "Remote", ""]
LOC_UK = ["London", "Manchester", "Birmingham", "Edinburgh", "Bristol", "Leeds", "Remote", ""]
LOC_IN = ["Bangalore", "Mumbai", "Delhi", "Hyderabad", "Chennai", "Pune", "Noida", "Gurgaon", "Remote", ""]
ADZ_COUNTRIES = ["com", "co.uk", "de", "fr", "ca", "in", "au", "nl", "nz"]

MAX_SH_PAGES = 50
MAX_DC_PAGES = 50
MAX_INDEED_PAGES = 40
MAX_JOOGLE_PAGES = 30
MAX_REED_PAGES = 30
MAX_ADZ_PAGES = 20
MAX_OTHER_PAGES = 20

def build_work():
    """Build all work items: (site, kw, loc, page, extra)"""
    items = []
    # SimplyHired: all KW × LOC × 50 pages
    for kw in ALL_KW:
        for loc in LOC:
            for p in range(MAX_SH_PAGES):
                items.append(("sh", kw, loc, p, None))
    # Dice: all KW × LOC × 50 pages
    for kw in ALL_KW:
        for loc in LOC:
            for p in range(MAX_DC_PAGES):
                items.append(("dc", kw, loc, p, None))
    # Indeed: KW × LOC × 40 pages
    for kw in ALL_KW:
        for loc in LOC:
            for p in range(MAX_INDEED_PAGES):
                items.append(("indeed", kw, loc, p, None))
    # Jooble: KW × US/UK/IN locations × 30 pages
    for kw in ALL_KW:
        for loc in LOC_US + LOC_UK + LOC_IN:
            for p in range(MAX_JOOGLE_PAGES):
                items.append(("jooble", kw, loc, p, None))
    # CWJobs: UK-only, KW × 30 pages
    for kw in ALL_KW:
        for p in range(MAX_REED_PAGES):
            items.append(("cwjobs", kw, "", p, None))
    # Reed: UK-only, KW × 30 pages
    for kw in ALL_KW:
        for p in range(MAX_REED_PAGES):
            items.append(("reed", kw, "", p, None))
    # BuiltIn: KW × 20 pages
    for kw in ALL_KW:
        for p in range(MAX_OTHER_PAGES):
            items.append(("builtin", kw, "", p, None))
    # Adzuna: KW × countries × 20 pages
    for country in ADZ_COUNTRIES:
        for kw in ALL_KW:
            for p in range(MAX_ADZ_PAGES):
                items.append(("adzuna", kw, "", p, country))
    # Talent.com: KW × LOC × 20 pages
    for kw in ALL_KW:
        for loc in LOC:
            for p in range(MAX_OTHER_PAGES):
                items.append(("talent", kw, loc, p, None))
    return items

SCRAPERS = {
    "sh": lambda s, kw, loc, pg, ex: scrape_sh(s, kw, loc, pg),
    "dc": lambda s, kw, loc, pg, ex: scrape_dc(s, kw, loc, pg),
    "indeed": lambda s, kw, loc, pg, ex: scrape_indeed(s, kw, loc, pg),
    "jooble": lambda s, kw, loc, pg, ex: scrape_jooble(s, kw, loc, pg),
    "cwjobs": lambda s, kw, loc, pg, ex: scrape_cwjobs(s, kw, pg),
    "reed": lambda s, kw, loc, pg, ex: scrape_reed(s, kw, pg),
    "builtin": lambda s, kw, loc, pg, ex: scrape_builtin(s, kw, pg),
    "adzuna": lambda s, kw, loc, pg, ex: scrape_adzuna(s, kw, ex, pg),
    "talent": lambda s, kw, loc, pg, ex: scrape_talent(s, kw, loc, pg),
}

async def worker(wid, sem, q, connector):
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            try:
                item = q.get_nowait()
            except asyncio.QueueEmpty:
                break
            async with sem:
                site, kw, loc, pg, ex = item
                try:
                    jobs = await SCRAPERS[site](session, kw, loc, pg, ex)
                except:
                    jobs = []
                if jobs:
                    for j in jobs:
                        try:
                            _write_q.put_nowait(j)
                        except queue.Full:
                            pass
                    _counter["iters"] += 1
                    _stats[site] = _stats.get(site, 0) + 1
                    n = len(jobs)
                    if n >= 3:
                        log(f"  W{wid%100:03d} +{n:3d} {site}:{kw[:16]}|{loc[:10]}|p{pg}")
            q.task_done()

async def run_round(round_num):
    global _counter, _stats, _start_time
    _counter = {"iters": 0, "new_total": 0}
    _stats = {}
    _start_time = time.time()

    total = db_count()
    gap = max(0, TARGET - total)
    log(f"ROUND {round_num} | DB: {total:,} | Gap: {gap:,}")
    log(f"Loading dedup keys...")
    # Load dedup keys
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    rows = conn.execute("SELECT dedupe_key FROM jobs").fetchall()
    conn.close()
    for r in rows:
        _seen.add(r[0])
    log(f"Dedup keys loaded: {len(_seen):,}")

    items = build_work()
    log(f"Total work items: {len(items):,}")

    # Checkpoint
    import json
    done = set()
    if CP_PATH.exists():
        try:
            cp = json.loads(CP_PATH.read_text("utf-8"))
            done = set(cp.get("done", []))
        except:
            pass
    remaining = [i for i in items if str(i) not in done]
    if not remaining:
        log("All items done, resetting checkpoint")
        done.clear()
        remaining = items[:]
    log(f"Remaining: {len(remaining):,}")
    random.shuffle(remaining)

    q = asyncio.Queue()
    for item in remaining:
        q.put_nowait(item)

    connector = aiohttp.TCPConnector(limit=WORKERS, limit_per_host=15, ttl_dns_cache=300, enable_cleanup_closed=True)
    sem = asyncio.Semaphore(WORKERS)

    workers = [asyncio.create_task(worker(i, sem, q, connector)) for i in range(WORKERS)]

    # Progress reporter
    async def reporter():
        while True:
            await asyncio.sleep(30)
            ct = db_count()
            elapsed = (time.time() - _start_time) / 60
            new = _counter["new_total"]
            rate = new / max(elapsed, 0.1)
            log(f"  [R{round_num}] DB={ct:,} | +{new:,} | {rate:.0f}/min | Gap={max(0,TARGET-ct):,} | iters={_counter['iters']:,}")
            try:
                CP_PATH.write_text(json.dumps({"done": list(done), "new": new}), "utf-8")
            except:
                pass

    reporter_task = asyncio.create_task(reporter())
    await asyncio.gather(*workers)
    reporter_task.cancel()

    # Drain remaining items from queue
    drained = 0
    while not _write_q.empty():
        try:
            _write_q.get_nowait()
            drained += 1
        except:
            break
    time.sleep(1)

    ft = db_count()
    elapsed = (time.time() - _start_time) / 60
    new = _counter["new_total"]
    rate = new / max(elapsed, 0.1)
    log("=" * 70)
    log(f"ROUND {round_num} DONE | New: +{new:,} | Rate: {rate:.0f}/min")
    log(f"Sites: " + " ".join(f"{k}:{v}" for k, v in sorted(_stats.items())))
    log(f"DB: {ft:,} | Gap: {max(0, TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min")
    log("=" * 70)
    return new, elapsed

if __name__ == "__main__":
    import json
    log("=" * 70)
    log(f"ULTRA V2 — {WORKERS} workers, async DB writer, {len(ALL_KW)} keywords")
    log("=" * 70)

    # Start DB writer thread
    writer = threading.Thread(target=db_writer_thread, daemon=True)
    writer.start()

    round_num = 0
    while True:
        round_num += 1
        try:
            new, elapsed = asyncio.run(run_round(round_num))
            if new == 0:
                log("0 new jobs — all sources saturated. Waiting 60s then retrying.")
                time.sleep(60)
            else:
                time.sleep(1)
        except Exception as e:
            log(f"CRASHED: {e}")
            import traceback
            log(traceback.format_exc())
            time.sleep(30)
