#!/usr/bin/env python3
"""ULTRA V3 — Maximum speed: JobSpy + HTML scrapers + ATS APIs.
Uses JobSpy's internal APIs for LinkedIn/Indeed/Google (different from our HTML scrapers = unique jobs).
"""
import asyncio, hashlib, os, random, sqlite3, sys, time, queue, threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
import aiohttp
from bs4 import BeautifulSoup

# JobSpy is sync — run it in threads
from jobspy import scrape_jobs as js_scrape_jobs
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"
LOG_PATH = ROOT / "ultra_v3_log.txt"
CP_PATH = ROOT / "ultra_v3_cp.json"
TARGET = 1_000_000
WORKERS = 150
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
        if item is None:
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
    log(f"DB writer done. Wrote {total_written:,} total.")

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

def push(jobs):
    for j in jobs:
        try:
            _write_q.put_nowait(j)
        except queue.Full:
            pass

NOW = datetime.now(timezone.utc).isoformat()
UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
]
HDR = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
       "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = aiohttp.ClientTimeout(total=8, connect=4)


# ── JobSpy source (sync, run in thread) ──
def jobspy_search(kw, loc):
    """Run JobSpy for LinkedIn+Indeed+Google. Returns list of job dicts."""
    jobs = []
    try:
        df = js_scrape_jobs(
            sites=["linkedin", "indeed", "google"],
            search_term=kw,
            location=loc or "United States",
            results_wanted=30,
            hours_old=168,
        )
        if df is None or df.empty:
            return []
        for _, row in df.iterrows():
            url = str(row.get("job_url", "") or row.get("job_url_direct", ""))
            title = str(row.get("title", ""))
            company = str(row.get("company", ""))
            location = str(row.get("location", ""))
            site = str(row.get("site", ""))
            if not url or not title:
                continue
            jobs.append({
                "url": url, "title": title, "company": company,
                "location": location, "source": f"js:{site}", "ts": NOW,
            })
    except:
        pass
    return jobs


# ── HTML Sources (async) ──
async def scrape_sh(session, kw, loc, pg):
    jobs = []
    url = f"https://www.simplyhired.com/search?q={quote_plus(kw)}&pn={pg}"
    if loc:
        url += f"&l={quote_plus(loc)}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200: return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("h2 a"):
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or not h: continue
            if not h.startswith("http"): h = "https://www.simplyhired.com" + h
            jobs.append({"url": h, "title": t, "company": "", "location": loc, "source": "sh", "ts": NOW})
    except: pass
    return jobs

async def scrape_dc(session, kw, loc, pg):
    jobs = []
    url = f"https://www.dice.com/jobs?q={quote_plus(kw)}&start={pg*20}&pageSize=20"
    if loc: url += f"&location={quote_plus(loc)}"
    try:
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
            jobs.append({"url": h, "title": t, "company": c, "location": loc, "source": "dc", "ts": NOW})
    except: pass
    return jobs

async def scrape_talent(session, kw, loc, pg):
    jobs = []
    url = f"https://www.talent.com/jobs?k={quote_plus(kw)}&l={quote_plus(loc or '')}&p={pg+1}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200: return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("[class*='job-card'], [class*='result'], article"):
            a = card.select_one("a[href*='/job/']")
            if not a: continue
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or len(t) < 5: continue
            if not h.startswith("http"): h = "https://www.talent.com" + h
            jobs.append({"url": h, "title": t, "company": "", "location": loc, "source": "talent", "ts": NOW})
    except: pass
    return jobs

async def scrape_builtin(session, kw, pg):
    """BuiltIn — use JSON endpoint instead of HTML."""
    jobs = []
    url = f"https://builtin.com/jobs?search={quote_plus(kw)}&page={pg+1}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200: return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("[class*='JobCard'], [class*='job-card'], [data-id]"):
            a = card.select_one("a[href*='/jobs/']")
            if not a: continue
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or len(t) < 5: continue
            if not h.startswith("http"): h = "https://builtin.com" + h
            jobs.append({"url": h, "title": t, "company": "", "location": "", "source": "builtin", "ts": NOW})
    except: pass
    return jobs

async def scrape_adzuna(session, kw, country, pg):
    jobs = []
    url = f"https://www.adzuna.{country}/search?q={quote_plus(kw)}&pg={pg+1}"
    try:
        async with session.get(url, headers={**HDR, "User-Agent": random.choice(UA)}, timeout=TIMEOUT) as r:
            if r.status != 200: return []
            html = await r.text()
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[data-aid='jobResultTitle'], h2 a[href*='/job/']"):
            t = a.get_text(strip=True)
            h = a.get("href", "")
            if not t or not h: continue
            if not h.startswith("http"): h = f"https://www.adzuna.{country}" + h
            jobs.append({"url": h, "title": t, "company": "", "location": "", "source": "adzuna", "ts": NOW})
    except: pass
    return jobs

# ── Greenhouse ATS API (JSON, no browser needed) ──
GREENHOUSE_BOARDS = [
    "reddit", "n26", "robinhood", "newrelic", "airtable", "groww", "asana",
    "affirm", "algolia", "veracode", "dashlane", "pinterest", "figma",
    "mongodb", "pagerduty", "coinbase", "webflow", "stripe", "coursera",
    "vercel", "epicgames", "netlify", "fastly", "squarespace", "starburst",
    "spacex", "duolingo", "monzo", "airbnb", "wise", "block", "databricks",
    "twitch", "sofi", "chime", "zscaler", "anthropic", "okta", "intercom",
    "waymo", "togetherai", "datadog", "discord", "brex", "lyft", "twilio",
    "neo4j", "roblox", "elastic", "instacart", "gitlab", "tripadvisor",
    "circleci", "clickhouse", "flexport", "launchdarkly", "cloudflare",
    "postman", "tcs", "mixpanel", "amplitude", "prisma", "onemedical",
    "hightouch", "ramp", "rivery", "fireworks", "abisource", "samsara",
    "toonbox", "luminar", "hopin", "messagebird", "fivetran", "synk",
    "vine", "checkr", "lever", "butterfly", "outbrain", "gusto",
]

async def scrape_greenhouse(session, board, pg):
    """Fetch jobs from a Greenhouse board's JSON API."""
    jobs = []
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200: return []
            data = await r.json(content_type=None)
        if not isinstance(data, dict):
            return []
        for j in data.get("jobs", []):
            title = j.get("title", "")
            jid = j.get("id", "")
            jurl = j.get("absolute_url", f"https://boards.greenhouse.io/{board}/jobs/{jid}")
            loc = ""
            locs = j.get("location", {})
            if isinstance(locs, dict):
                loc = locs.get("name", "")
            desc = j.get("content", "")[:500] if j.get("content") else ""
            jobs.append({
                "url": jurl, "title": title, "company": board,
                "location": loc, "source": "gh", "desc": desc,
                "id": str(jid), "ts": NOW,
            })
    except:
        pass
    return jobs

# ── Lever ATS API ──
LEVER_BOARDS = ["fiscalnote", "zoox", "gopuff"]

async def scrape_lever(session, board, pg):
    jobs = []
    url = f"https://api.lever.co/v0/postings/{board}?mode=json"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200: return []
            data = await r.json(content_type=None)
        if not isinstance(data, list): return []
        for j in data:
            title = j.get("text", "")
            jurl = j.get("hostedUrl", "")
            jid = j.get("id", "")
            loc = j.get("categories", {}).get("team", "")
            jobs.append({
                "url": jurl, "title": title, "company": board,
                "location": loc, "source": "lever", "id": str(jid), "ts": NOW,
            })
    except: pass
    return jobs


# ── Keywords ──
KW = [
    "software engineer","software developer","backend engineer","frontend engineer",
    "full stack developer","full stack engineer","web developer","mobile developer",
    "data engineer","devops engineer","SRE","platform engineer","infrastructure engineer",
    "security engineer","cloud engineer","systems engineer","QA engineer",
    "python developer","java developer","react developer","node developer",
    "go developer","golang developer","rust developer","C++ engineer","C# developer",
    "TypeScript developer","Kotlin developer","Swift developer","PHP developer",
    "Ruby developer",".NET developer","Scala developer","Angular developer",
    "Vue developer","Django developer","FastAPI developer","Spring Boot developer",
    "AWS engineer","GCP engineer","Azure engineer","Kubernetes engineer",
    "Terraform engineer","Docker engineer","ML engineer","AI engineer",
    "data scientist","machine learning engineer","deep learning engineer",
    "GenAI engineer","LLM engineer","tech lead","principal engineer",
    "staff engineer","senior software engineer","junior software engineer",
    "embedded engineer","firmware engineer","game developer","graphics engineer",
    "blockchain developer","DevOps","automation engineer","database engineer",
    "fintech engineer","payments engineer","API engineer","microservices engineer",
    "distributed systems engineer","cybersecurity engineer","penetration tester",
    "iOS developer","Android developer","Flutter developer","React Native developer",
    "Hadoop engineer","Spark engineer","Kafka engineer","Redis engineer",
    "PostgreSQL engineer","MongoDB engineer","Elasticsearch engineer",
    "Perl developer","Elixir developer","Haskell developer",
    "data analyst","analytics engineer","big data engineer",
    "site reliability engineer","cloud architect","solutions architect",
    "technical writer","scrum master","product manager",
    "UX engineer","design engineer","network engineer",
    "information security engineer","cloud security engineer",
    "Kubernetes administrator","Terraform developer","Ansible engineer",
    "CI/CD engineer","release engineer","build engineer",
    "systems programmer","kernel engineer","compiler engineer",
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
    "Sao Paulo","Mexico City","Dubai","Tel Aviv",
    "Barcelona","Milan","Rome","Lisbon","Vienna",
    "Prague","Warsaw","Budapest","Krakow","Bucharest",
    "Vancouver","Calgary","Ottawa","Edmonton","Montreal",
    "Manchester","Birmingham","Edinburgh","Bristol","Leeds",
    "Adelaide","Perth","Brisbane","Wellington","Auckland",
    "Cape Town","Johannesburg","Nairobi","Lagos",
    "",
]

ADZ_COUNTRIES = ["com", "co.uk", "de", "fr", "ca", "in", "au"]


def build_work():
    """Build work items: (type, kw, loc, page, extra)"""
    items = []
    # JobSpy: KW × LOC (each call = LinkedIn+Indeed+Google = ~40-60 results)
    for kw in KW:
        for loc in LOC:
            items.append(("jobspy", kw, loc, 0, None))
    # SimplyHired: KW × LOC × 30 pages
    for kw in KW:
        for loc in LOC:
            for p in range(30):
                items.append(("sh", kw, loc, p, None))
    # Dice: KW × LOC × 30 pages
    for kw in KW:
        for loc in LOC:
            for p in range(30):
                items.append(("dc", kw, loc, p, None))
    # Talent.com: KW × LOC × 15 pages
    for kw in KW:
        for loc in LOC:
            for p in range(15):
                items.append(("talent", kw, loc, p, None))
    # Greenhouse: ALL boards (each = 20-500 jobs)
    for board in GREENHOUSE_BOARDS:
        items.append(("gh", board, "", 0, None))
    # Lever
    for board in LEVER_BOARDS:
        items.append(("lever", board, "", 0, None))
    # Adzuna: KW × countries × 15 pages
    for country in ADZ_COUNTRIES:
        for kw in KW:
            for p in range(15):
                items.append(("adzuna", kw, "", p, country))
    # BuiltIn: KW × 15 pages
    for kw in KW:
        for p in range(15):
            items.append(("builtin", kw, "", p, None))
    return items


async def worker(wid, sem, q, connector):
    """HTML scraper worker."""
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            try:
                item = q.get_nowait()
            except asyncio.QueueEmpty:
                break
            stype = item[0]
            if stype == "jobspy":
                # JobSpy is sync — run in thread
                q.task_done()
                continue  # handled by jobspy_worker
            async with sem:
                try:
                    if stype == "sh":
                        jobs = await scrape_sh(session, item[1], item[2], item[3])
                    elif stype == "dc":
                        jobs = await scrape_dc(session, item[1], item[2], item[3])
                    elif stype == "talent":
                        jobs = await scrape_talent(session, item[1], item[2], item[3])
                    elif stype == "gh":
                        jobs = await scrape_greenhouse(session, item[1], item[3])
                    elif stype == "lever":
                        jobs = await scrape_lever(session, item[1], item[3])
                    elif stype == "adzuna":
                        jobs = await scrape_adzuna(session, item[1], item[4], item[3])
                    elif stype == "builtin":
                        jobs = await scrape_builtin(session, item[1], item[3])
                    else:
                        jobs = []
                except:
                    jobs = []
                if jobs:
                    push(jobs)
                    _counter["iters"] += 1
                    _stats[stype] = _stats.get(stype, 0) + 1
                    if len(jobs) >= 2:
                        log(f"  W{wid%100:03d} +{len(jobs):3d} {stype}:{item[1][:20]}")
            q.task_done()


def jobspy_worker(sem_items):
    """Run JobSpy searches in a thread pool (JobSpy is sync)."""
    from concurrent.futures import ThreadPoolExecutor
    def _run_one(kw, loc):
        try:
            return jobspy_search(kw, loc)
        except:
            return []

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {}
        for kw, loc in sem_items:
            f = pool.submit(_run_one, kw, loc)
            futures[f] = (kw, loc)

        done_count = 0
        for f in __import__('concurrent.futures').as_completed(futures):
            kw, loc = futures[f]
            try:
                jobs = f.result(timeout=30)
            except:
                jobs = []
            if jobs:
                push(jobs)
                _counter["iters"] += 1
                _stats["jobspy"] = _stats.get("jobspy", 0) + 1
                if len(jobs) >= 3:
                    log(f"  JS +{len(jobs):3d} jobspy:{kw[:20]}|{loc[:10]}")
            done_count += 1
            if done_count % 100 == 0:
                log(f"  JobSpy progress: {done_count}/{len(sem_items)}")


async def run_round(round_num):
    import json
    global _counter, _stats, _start_time
    _counter = {"iters": 0, "new_total": 0}
    _stats = {}
    _start_time = time.time()

    total = db_count()
    gap = max(0, TARGET - total)
    log(f"ROUND {round_num} | DB: {total:,} | Gap: {gap:,}")
    log("Loading dedup keys...")
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    rows = conn.execute("SELECT dedupe_key FROM jobs").fetchall()
    conn.close()
    for r in rows:
        _seen.add(r[0])
    log(f"Dedup: {len(_seen):,}")

    items = build_work()
    log(f"Total work items: {len(items):,}")

    # Checkpoint
    done = set()
    if CP_PATH.exists():
        try:
            cp = json.loads(CP_PATH.read_text("utf-8"))
            done = set(cp.get("done", []))
        except: pass
    remaining = [i for i in items if str(i) not in done]
    if not remaining:
        log("All done, resetting")
        done.clear()
        remaining = items[:]
    log(f"Remaining: {len(remaining):,}")
    random.shuffle(remaining)

    # Split: jobspy items vs async items
    jobspy_items = [(i[1], i[2]) for i in remaining if i[0] == "jobspy"]
    async_items = [i for i in remaining if i[0] != "jobspy"]

    log(f"JobSpy items: {len(jobspy_items):,} | Async items: {len(async_items):,}")

    # Queue for async workers
    q = asyncio.Queue()
    for item in async_items:
        q.put_nowait(item)

    connector = aiohttp.TCPConnector(limit=WORKERS, limit_per_host=10, ttl_dns_cache=300)
    sem = asyncio.Semaphore(WORKERS)

    # Start JobSpy in thread
    loop = asyncio.get_event_loop()
    js_task = loop.run_in_executor(None, jobspy_worker, jobspy_items)

    # Start HTML/ATS workers
    workers = [asyncio.create_task(worker(i, sem, q, connector)) for i in range(WORKERS)]

    # Reporter
    async def reporter():
        import json
        while True:
            await asyncio.sleep(30)
            ct = db_count()
            elapsed = (time.time() - _start_time) / 60
            new = _counter["new_total"]
            rate = new / max(elapsed, 0.1)
            log(f"  [R{round_num}] DB={ct:,} | +{new:,} | {rate:.0f}/min | Gap={max(0,TARGET-ct):,} | iters={_counter['iters']:,}")
            try:
                CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
            except: pass
    reporter_task = asyncio.create_task(reporter())

    await asyncio.gather(*workers)
    # Wait for JobSpy to finish
    try:
        await js_task
    except: pass
    reporter_task.cancel()

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
    log(f"ULTRA V3 — {WORKERS} HTML workers + JobSpy thread pool + ATS APIs")
    log("=" * 70)

    writer = threading.Thread(target=db_writer_thread, daemon=True)
    writer.start()

    round_num = 0
    while True:
        round_num += 1
        try:
            new, elapsed = asyncio.run(run_round(round_num))
            if new == 0:
                log("0 new — waiting 60s")
                time.sleep(60)
            else:
                time.sleep(1)
        except Exception as e:
            log(f"CRASHED: {e}")
            import traceback
            log(traceback.format_exc())
            time.sleep(30)
