#!/usr/bin/env python3
"""ULTRA SCRAPER — Maximum speed: 200 workers, batch DB inserts, zero delays.
Target: 2000+ jobs/min → 1M in <5 hours."""

import asyncio, hashlib, json, os, random, sqlite3, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
import aiohttp

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "ultra_cp.json"
LOG_PATH = ROOT / "ultra_log.txt"
TARGET = 1_000_000
CONCURRENCY = 200  # 200 simultaneous connections
BATCH_SIZE = 500   # Commit every 500 jobs (not per-request)
BATCH_INTERVAL = 3 # Or every 3 seconds
MAX_PAGES = 50

_log_lock = __import__('threading').Lock()
_db_lock = __import__('threading').Lock()
_db_conn = None
_seen = set()
_pending = []
_pending_lock = __import__('threading').Lock()
_counter = {"iters": 0, "new": 0, "batch_new": 0}
_stats = {}
_start_time = 0


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _log_lock:
        try:
            with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{ts}] {msg}\n")
        except: pass


def _get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.execute("PRAGMA synchronous=NORMAL")
        _db_conn.execute("PRAGMA cache_size=-128000")
    return _db_conn


def _hash(url, title, company):
    raw = f"{url.strip().lower()}|{title.strip().lower()}|{company.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def load_dedup():
    conn = _get_db()
    rows = conn.execute("SELECT dedupe_key FROM jobs").fetchall()
    for r in rows:
        _seen.add(r[0])
    log(f"Loaded {len(_seen):,} dedup keys")


def flush_batch():
    """Insert all pending jobs in one batch commit."""
    global _pending
    with _pending_lock:
        if not _pending:
            return 0
        batch = _pending[:]
        _pending = []

    with _db_lock:
        conn = _get_db()
        rows = []
        now = datetime.now(timezone.utc).isoformat()
        for j in batch:
            url = j.get("url", "").strip()
            title = j.get("title", "").strip()
            company = j.get("company", "").strip()
            if not url or not title:
                continue
            key = _hash(url, title, company)
            if key in _seen:
                continue
            _seen.add(key)
            rows.append((key, title, company, j.get("location", ""),
                         j.get("desc", "")[:500], url, j.get("source", ""),
                         "ultra", j.get("id", ""), j.get("posted"),
                         j.get("salary", ""), j.get("tags", ""), now, now))
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO jobs "
                "(dedupe_key,title,company,location,description,url,"
                "source,source_kind,external_id,posted_at,salary,tags,"
                "first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows)
            conn.commit()
            _counter["batch_new"] += len(rows)
            return len(rows)
    return 0


def db_count():
    with _db_lock:
        return _get_db().execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


# ── HTTP ──────────────────────────────────────────────────────
UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
HDR = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
       "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate, br"}
TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)


# ── Scrapers ──────────────────────────────────────────────────
async def sh(session, kw, loc, pg):
    jobs = []
    try:
        url = f"https://www.simplyhired.com/search?q={quote_plus(kw)}&pn={pg}"
        if loc: url += f"&l={quote_plus(loc)}"
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200: return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("h2 a"):
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or not h: continue
            if not h.startswith("http"): h = "https://www.simplyhired.com" + h
            c = ""
            p = a.parent
            for _ in range(4):
                if p is None: break
                for s in p.select("span"):
                    v = s.get_text(strip=True)
                    if v and v != t and len(v) < 60 and not v.startswith("$"):
                        c = v; break
                if c: break
                p = p.parent
            jobs.append({"url": h, "title": t, "company": c, "location": loc, "source": "sh"})
    except: pass
    return jobs


async def dc(session, kw, loc, pg):
    jobs = []
    try:
        url = f"https://www.dice.com/jobs?q={quote_plus(kw)}&start={pg*20}&pageSize=20"
        if loc: url += f"&location={quote_plus(loc)}"
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200: return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("[data-testid='job-card']"):
            t, h = "", ""
            for a in card.select("a[href*='/job-detail/']"):
                v = a.get_text(strip=True)
                if v and len(v) > 3: t = v; h = a.get("href", ""); break
            if not t or not h: continue
            if not h.startswith("http"): h = "https://www.dice.com" + h
            c = ""
            ce = card.select_one("[data-testid='job-card-company-name']")
            if ce: c = ce.get_text(strip=True)
            jobs.append({"url": h, "title": t, "company": c, "location": loc, "source": "dc"})
    except: pass
    return jobs


async def cj(session, kw, pg):
    jobs = []
    try:
        url = f"https://www.cwjobs.co.uk/jobs/{quote_plus(kw)}?page={pg+1}"
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200: return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href*='/job/']"):
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or len(t) < 5 or not h: continue
            if not h.startswith("http"): h = "https://www.cwjobs.co.uk" + h
            jobs.append({"url": h, "title": t, "company": "", "location": "", "source": "cwj"})
    except: pass
    return jobs


async def bi(session, kw, pg):
    jobs = []
    try:
        url = f"https://builtin.com/jobs?q={quote_plus(kw)}&p={pg+1}"
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200: return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href*='/jobs/']"):
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or len(t) < 5 or not h: continue
            if not h.startswith("http"): h = "https://builtin.com" + h
            jobs.append({"url": h, "title": t, "company": "", "location": "", "source": "bin"})
    except: pass
    return jobs


async def wf(session, kw, pg):
    jobs = []
    try:
        url = f"https://wellfound.com/role/{quote_plus(kw)}?page={pg+1}"
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200: return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href*='/startup/'], a[href*='/company/']"):
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or len(t) < 5: continue
            if not h.startswith("http"): h = "https://wellfound.com" + h
            jobs.append({"url": h, "title": t, "company": "", "location": "", "source": "wf"})
    except: pass
    return jobs


async def ad(session, country, kw, pg):
    jobs = []
    try:
        url = f"https://www.adzuna.{country}/search?q={quote_plus(kw)}&pg={pg+1}"
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200: return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[data-aid='jobResultTitle'], h2 a[href*='/job/']"):
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or not h: continue
            if not h.startswith("http"): h = f"https://www.adzuna.{country}" + h
            c = ""
            p = a.parent
            if p:
                for s in p.select("span"):
                    v = s.get_text(strip=True)
                    if v and v != t and len(v) < 60: c = v; break
            jobs.append({"url": h, "title": t, "company": c, "location": "", "source": f"ad:{country[:2]}"})
    except: pass
    return jobs


# ── Keywords (88) & Locations (95) ────────────────────────────
KW = [
    "software engineer","backend engineer","frontend engineer","full stack developer",
    "data engineer","devops engineer","ML engineer","AI engineer","cloud engineer",
    "mobile developer","python developer","java developer","react developer",
    "node developer","go developer","rust developer","C++ engineer","SRE",
    "security engineer","platform engineer","systems engineer","QA engineer",
    "automation engineer","database engineer","network engineer","fintech engineer",
    "blockchain developer","game developer","graphics engineer","embedded engineer",
    "firmware engineer","DevOps","SDE","tech lead","principal engineer",
    "staff engineer","product engineer","application engineer","web developer",
    "software developer","IT engineer","data scientist","NLP engineer",
    "computer vision engineer","robotics engineer","AR engineer","VR engineer",
    "compiler engineer","kernel engineer","distributed systems engineer",
    "microservices engineer","API engineer","payments engineer",
    "mobile app developer","iOS developer","Android developer","Flutter developer",
    "React Native developer","Kotlin developer","Swift developer",
    "PHP developer","Ruby developer",".NET developer","Scala developer",
    "Elixir developer","Haskell developer","Lua developer",
    "TypeScript developer","Angular developer","Vue developer",
    "Django developer","FastAPI developer","Spring Boot developer",
    "AWS engineer","GCP engineer","Azure engineer",
    "Kubernetes engineer","Terraform engineer","Docker engineer",
    "PostgreSQL engineer","MongoDB engineer","Redis engineer",
    "golang developer","C# developer","Perl developer",
    "Hadoop engineer","Spark engineer","Kafka engineer",
    "cybersecurity engineer","penetration tester",
    "machine learning engineer","deep learning engineer",
    "GenAI engineer","LLM engineer","RAG engineer",
    "infrastructure engineer","release engineer","build engineer",
]
LOC = [
    "New York","San Francisco","Los Angeles","Chicago","Seattle",
    "Austin","Boston","Denver","Atlanta","Miami",
    "Dallas","Houston","Phoenix","Portland","San Diego",
    "Washington DC","Remote","London","Berlin","Toronto",
    "Sydney","Melbourne","Singapore","Dublin","Amsterdam",
    "Bangalore","Mumbai","Delhi","Hyderabad","Chennai",
    "Pune","Noida","Gurgaon","Kolkata","Jaipur",
    "Paris","Munich","Zurich","Stockholm","Oslo",
    "Copenhagen","Helsinki","Tokyo","Seoul","Hong Kong",
    "Sao Paulo","Mexico City","Buenos Aires","Dubai","Tel Aviv",
    "Barcelona","Milan","Rome","Lisbon","Vienna",
    "Prague","Warsaw","Budapest","Krakow","Bucharest",
    "Vancouver","Calgary","Ottawa","Edmonton","Montreal",
    "Manchester","Birmingham","Edinburgh","Bristol","Leeds",
    "Newcastle","Glasgow","Cardiff","Nottingham","Sheffield",
    "Adelaide","Perth","Brisbane","Gold Coast","Wellington",
    "Auckland","Cape Town","Johannesburg","Nairobi","Lagos",
    "Coimbatore","Thiruvananthapuram","Lucknow","Indore",
    "Ahmedabad","Surat","Nagpur","Visakhapatnam","Patna",
]

ADZ_COUNTRIES = ["com","co.uk","de","fr","ca","in","au","nl","sg","nz"]


def build_items():
    items = []
    # SimplyHired: 88×95×50 = 418K
    for k in KW:
        for l in LOC:
            for p in range(MAX_PAGES):
                items.append(("sh", k, l, p))
    # Dice: 88×95×50 = 418K
    for k in KW:
        for l in LOC:
            for p in range(MAX_PAGES):
                items.append(("dc", k, l, p))
    # CWJobs: 88×30 = 2.6K
    for k in KW:
        for p in range(30):
            items.append(("cwj", k, "", p))
    # Wellfound: 30×30 = 900
    for k in KW[:30]:
        for p in range(30):
            items.append(("wf", k, "", p))
    # BuiltIn: 30×20 = 600
    for k in KW[:30]:
        for p in range(20):
            items.append(("bi", k, "", p))
    # Adzuna: 10×30×20 = 6K
    for c in ADZ_COUNTRIES:
        for k in KW[:30]:
            for p in range(20):
                items.append(("ad", k, "", p, c))
    return items


# ── Worker (zero-delay, batch accumulate) ─────────────────────
SCRAPERS = {"sh": sh, "dc": dc, "cwj": cj, "bi": bi, "wf": wf, "ad": ad}

async def worker(wid, sem, q, connector):
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            try:
                item = q.get_nowait()
            except asyncio.QueueEmpty:
                break
            async with sem:
                site = item[0]
                try:
                    if site == "ad":
                        jobs = await ad(session, item[4], item[1], item[3])
                    else:
                        jobs = await SCRAPERS[site](session, item[1], item[2], item[3])
                except:
                    jobs = []

                if jobs:
                    with _pending_lock:
                        _pending.extend(jobs)
                    _counter["iters"] += 1
                    _stats[site] = _stats.get(site, 0) + 1

                    if len(jobs) > 3:
                        log(f"  W{wid:03d} +{len(jobs):3d} {site}:{item[1][:16]}|{item[2][:10]}|p{item[3]}")
            q.task_done()


# ── Main ──────────────────────────────────────────────────────
async def run_round(round_num):
    global _counter, _stats, _start_time, _pending
    _counter = {"iters": 0, "new": 0, "batch_new": 0}
    _stats = {}
    _pending = []
    _start_time = time.time()

    total = db_count()
    log(f"DB: {total:,} | Gap: {max(0, TARGET-total):,}")
    load_dedup()

    items = build_items()
    log(f"Total items: {len(items):,}")

    # Checkpoint
    done = set()
    if CP_PATH.exists():
        try:
            cp = json.loads(CP_PATH.read_text("utf-8"))
            done = set(cp.get("done", []))
        except: pass
    remaining = [i for i in items if str(i) not in done]
    if not remaining:
        log("All done, resetting...")
        done.clear()
        remaining = items[:]
    log(f"Remaining: {len(remaining):,}")
    random.shuffle(remaining)

    q = asyncio.Queue()
    for item in remaining:
        q.put_nowait(item)

    connector = aiohttp.TCPConnector(limit=CONCURRENCY, limit_per_host=20, ttl_dns_cache=300)
    sem = asyncio.Semaphore(CONCURRENCY)

    # Workers
    workers = []
    for i in range(CONCURRENCY):
        w = asyncio.create_task(worker(i, sem, q, connector))
        workers.append(w)

    # Batch flusher
    async def flusher():
        while True:
            await asyncio.sleep(BATCH_INTERVAL)
            n = await asyncio.to_thread(flush_batch)
            _counter["new"] = _counter["batch_new"]
            ct = db_count()
            elapsed = (time.time() - _start_time) / 60
            rate = _counter["new"] / max(elapsed, 0.1)
            log(f"  [R{round_num}] DB={ct:,} | +{_counter['new']:,} | {rate:.0f}/min | Gap={max(0,TARGET-ct):,} | iters={_counter['iters']:,}")
            try:
                CP_PATH.write_text(json.dumps({"done": list(done), "new": _counter["new"]}), "utf-8")
            except: pass
    flusher_task = asyncio.create_task(flusher())

    await asyncio.gather(*workers)
    flusher_task.cancel()

    # Final flush
    n = await asyncio.to_thread(flush_batch)
    _counter["new"] = _counter["batch_new"]

    ft = db_count()
    elapsed = (time.time() - _start_time) / 60
    log("=" * 70)
    log(f"ROUND {round_num} DONE | New: +{_counter['new']:,}")
    log(f"Sites: " + " ".join(f"{k}:{v}" for k,v in sorted(_stats.items())))
    log(f"DB: {ft:,} | Gap: {max(0,TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {_counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 70)
    return _counter["new"], elapsed


if __name__ == "__main__":
    log("=" * 70)
    log("ULTRA SCRAPER — 200 workers, batch inserts, zero delays")
    log("=" * 70)

    round_num = 0
    while True:
        round_num += 1
        log(f"\nROUND {round_num}")
        try:
            new, elapsed = asyncio.run(run_round(round_num))
            if new == 0:
                log("0 new — waiting 30s")
                time.sleep(30)
            else:
                time.sleep(2)
        except Exception as e:
            log(f"CRASHED: {e}")
            import traceback
            log(traceback.format_exc())
            time.sleep(30)
