#!/usr/bin/env python3
"""FAST ASYNC SCRAPER — aiohttp with 100+ concurrent connections.
Target: 1000+ new jobs/min to hit 1M in <5 hours."""

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
CP_PATH = ROOT / "fast_async_cp.json"
LOG_PATH = ROOT / "fast_async_log.txt"
TARGET = 1_000_000
CONCURRENCY = 100  # 100 simultaneous connections
MAX_PAGES = 50

_lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None
_log_lock = __import__('threading').Lock()

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _log_lock:
        try:
            with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{ts}] {msg}\n")
        except: pass


# ── DB (thread-safe for async) ────────────────────────────────
class JobDB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        self.lock = __import__('threading').Lock()
        self._seen = set()
        self._new_count = 0

    def load_dedup(self):
        rows = self.conn.execute("SELECT dedupe_key FROM jobs").fetchall()
        for r in rows:
            self._seen.add(r[0])
        log(f"Loaded {len(self._seen):,} dedup keys ({sys.getsizeof(self._seen)//1024//1024}MB)")
        return len(self._seen)

    def _hash(self, url, title, company):
        raw = f"{url.strip().lower()}|{title.strip().lower()}|{company.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def insert_batch(self, jobs):
        new = 0
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        with self.lock:
            for j in jobs:
                url = j.get("url", "").strip()
                title = j.get("title", "").strip()
                company = j.get("company", "").strip()
                if not url or not title: continue
                key = self._hash(url, title, company)
                if key in self._seen: continue
                self._seen.add(key)
                rows.append((key, title, company, j.get("location", ""),
                             j.get("desc", "")[:500], url, j.get("source", ""),
                             "async", j.get("id", ""), j.get("posted"),
                             j.get("salary", ""), j.get("tags", ""), now, now))
            if rows:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO jobs "
                    "(dedupe_key,title,company,location,description,url,"
                    "source,source_kind,external_id,posted_at,salary,tags,"
                    "first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows)
                self.conn.commit()
                new = len(rows)
                self._new_count += new
        return new

    def count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def close(self):
        try: self.conn.close()
        except: pass


# ── HTTP helpers ──────────────────────────────────────────────
UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


# ── Scrapers (async) ──────────────────────────────────────────
async def scrape_simplyhired(session, kw, loc, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.simplyhired.com/search?q={q}&pn={pg}"
        if loc: url += f"&l={quote_plus(loc)}"
        headers = {**HEADERS, "User-Agent": random.choice(UA)}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status != 200: return []
            html = await resp.text()
        if not BeautifulSoup: return []
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("h2 a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or not href: continue
            if not href.startswith("http"):
                href = "https://www.simplyhired.com" + href
            company = ""
            card = a.parent
            for _ in range(4):
                if card is None: break
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


async def scrape_dice(session, kw, loc, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.dice.com/jobs?q={q}&start={pg * 20}&pageSize=20"
        if loc: url += f"&location={quote_plus(loc)}"
        headers = {**HEADERS, "User-Agent": random.choice(UA)}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status != 200: return []
            html = await resp.text()
        if not BeautifulSoup: return []
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select("[data-testid='job-card']"):
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


async def scrape_cwjobs(session, kw, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.cwjobs.co.uk/jobs/{q}?page={pg + 1}"
        headers = {**HEADERS, "User-Agent": random.choice(UA)}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status != 200: return []
            html = await resp.text()
        if not BeautifulSoup: return []
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href*='/job/']"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or len(title) < 5 or not href: continue
            if not href.startswith("http"):
                href = "https://www.cwjobs.co.uk" + href
            jobs.append({"url": href, "title": title, "company": "",
                        "location": "", "source": "cwjobs"})
    except: pass
    return jobs


async def scrape_builtin(session, kw, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://builtin.com/jobs?q={q}&p={pg + 1}"
        headers = {**HEADERS, "User-Agent": random.choice(UA)}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status != 200: return []
            html = await resp.text()
        if not BeautifulSoup: return []
        soup = BeautifulSoup(html, "html.parser")
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


async def scrape_adzuna(session, country, kw, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.adzuna.{country}/search?q={q}&pg={pg + 1}"
        headers = {**HEADERS, "User-Agent": random.choice(UA)}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status != 200: return []
            html = await resp.text()
        if not BeautifulSoup: return []
        soup = BeautifulSoup(html, "html.parser")
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


async def scrape_wellfound(session, kw, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://wellfound.com/role/{q}?page={pg + 1}"
        headers = {**HEADERS, "User-Agent": random.choice(UA)}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status != 200: return []
            html = await resp.text()
        if not BeautifulSoup: return []
        soup = BeautifulSoup(html, "html.parser")
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


async def scrape_api_source(session, name, url, parse_fn, pg):
    """Generic async API scraper."""
    try:
        headers = {**HEADERS, "User-Agent": random.choice(UA)}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200: return []
            data = await resp.json()
        return parse_fn(data, pg)
    except: return []


# ── Keywords & Locations ──────────────────────────────────────
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
    # Extra tech combos
    "TypeScript developer", "Angular developer", "Vue developer",
    "Django developer", "FastAPI developer", "Spring Boot developer",
    "AWS engineer", "GCP engineer", "Azure engineer",
    "Kubernetes engineer", "Terraform engineer", "Docker engineer",
    "PostgreSQL engineer", "MongoDB engineer", "Redis engineer",
    "golang developer", "C# developer", "Perl developer",
    "Scala engineer", "Hadoop engineer", "Spark engineer",
    "Kafka engineer", "RabbitMQ engineer",
    "cybersecurity engineer", "penetration tester",
    "machine learning engineer", "deep learning engineer",
    "GenAI engineer", "LLM engineer", "RAG engineer",
    "site reliability", "infrastructure engineer",
    "release engineer", "build engineer",
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
    "Coimbatore", "Thiruvananthapuram", "Lucknow", "Indore",
    "Ahmedabad", "Surat", "Nagpur", "Visakhapatnam", "Patna",
]


# ── Work item builder ─────────────────────────────────────────
def build_work_items():
    items = []
    # SimplyHired — 88 keywords × 95 locations × 50 pages
    for kw in KEYWORDS:
        for loc in LOCATIONS:
            for pg in range(MAX_PAGES):
                items.append(("simplyhired", kw, loc, pg))
    # Dice — same
    for kw in KEYWORDS:
        for loc in LOCATIONS:
            for pg in range(MAX_PAGES):
                items.append(("dice", kw, loc, pg))
    # CWJobs — keywords × 30 pages
    for kw in KEYWORDS:
        for pg in range(30):
            items.append(("cwjobs", kw, "", pg))
    # Wellfound — top 30 keywords × 30 pages
    for kw in KEYWORDS[:30]:
        for pg in range(30):
            items.append(("wellfound", kw, "", pg))
    # BuiltIn — top 30 keywords × 20 pages
    for kw in KEYWORDS[:30]:
        for pg in range(20):
            items.append(("builtin", kw, "", pg))
    # Adzuna — 10 countries × 30 keywords × 20 pages
    for country in ["com", "co.uk", "de", "fr", "ca", "in", "au", "nl", "sg", "nz"]:
        for kw in KEYWORDS[:30]:
            for pg in range(20):
                items.append((f"adzuna_{country}", kw, "", pg))
    return items


# ── Async worker ──────────────────────────────────────────────
async def worker(wid, sem, queue, db, stats, counter):
    connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            async with sem:
                site, kw, loc, pg = item
                jobs = []
                try:
                    await asyncio.sleep(random.uniform(0.01, 0.08))  # Minimal delay
                    if site == "simplyhired":
                        jobs = await scrape_simplyhired(session, kw, loc, pg)
                    elif site == "dice":
                        jobs = await scrape_dice(session, kw, loc, pg)
                    elif site == "cwjobs":
                        jobs = await scrape_cwjobs(session, kw, pg)
                    elif site == "wellfound":
                        jobs = await scrape_wellfound(session, kw, pg)
                    elif site == "builtin":
                        jobs = await scrape_builtin(session, kw, pg)
                    elif site.startswith("adzuna_"):
                        country = site.replace("adzuna_", "")
                        jobs = await scrape_adzuna(session, country, kw, pg)
                except: pass

                new = 0
                if jobs:
                    new = await asyncio.to_thread(db.insert_batch, jobs)

                counter["iters"] += 1
                counter["new"] += new
                stats[site] = stats.get(site, 0) + 1

                if new > 0:
                    log(f"  W{wid:03d} +{new:3d} {site}:{kw[:18]}|{loc[:12]}|p{pg}")

            queue.task_done()


# ── Main loop ─────────────────────────────────────────────────
async def main():
    log("=" * 70)
    log("FAST ASYNC SCRAPER — 100 concurrent connections, aiohttp")
    log("=" * 70)

    db = await asyncio.to_thread(JobDB)
    total = await asyncio.to_thread(db.count)
    log(f"DB: {total:,} | Gap: {max(0, TARGET-total):,}")
    await asyncio.to_thread(db.load_dedup)

    all_items = build_work_items()
    log(f"Total work items: {len(all_items):,}")

    # Load checkpoint
    done = set()
    if CP_PATH.exists():
        try:
            cp = json.loads(CP_PATH.read_text("utf-8"))
            done = set(cp.get("done", []))
        except: pass

    remaining = [i for i in all_items if str(i) not in done]
    if not remaining:
        log("All done, resetting...")
        done.clear()
        remaining = all_items[:]

    log(f"Remaining: {len(remaining):,}")
    random.shuffle(remaining)

    # Put into async queue
    q = asyncio.Queue()
    for item in remaining:
        q.put_nowait(item)

    start = time.time()
    sem = asyncio.Semaphore(CONCURRENCY)
    counter = {"iters": 0, "new": 0}
    stats = {}

    # Launch workers
    workers = []
    for i in range(CONCURRENCY):
        w = asyncio.create_task(worker(i, sem, q, db, stats, counter))
        workers.append(w)

    # Progress reporter
    async def reporter():
        while True:
            await asyncio.sleep(120)
            ct = await asyncio.to_thread(db.count)
            elapsed = (time.time() - start) / 60
            rate = counter["new"] / max(elapsed, 0.1)
            log(f"  [HB] DB={ct:,} | iters={counter['iters']:,} new={counter['new']:,} | {elapsed:.0f}min | {rate:.0f}/min | Gap={max(0,TARGET-ct):,}")
            try:
                CP_PATH.write_text(json.dumps({"done": list(done), "total_new": counter["new"],
                                                 "iters": counter["iters"]}), "utf-8")
            except: pass

    reporter_task = asyncio.create_task(reporter())

    # Wait for all workers
    await asyncio.gather(*workers)
    reporter_task.cancel()

    ft = await asyncio.to_thread(db.count)
    elapsed = (time.time() - start) / 60

    log("=" * 70)
    log(f"COMPLETE | New: +{counter['new']:,}")
    log(f"Sites: " + " ".join(f"{k}:{v}" for k, v in sorted(stats.items())))
    log(f"DB: {ft:,} | Gap: {max(0, TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 70)

    # Save checkpoint
    try:
        CP_PATH.write_text(json.dumps({"done": list(done), "total_new": counter["new"],
                                         "iters": counter["iters"]}), "utf-8")
    except: pass

    db.close()
    return counter["new"], elapsed


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    if reset and CP_PATH.exists():
        try: CP_PATH.unlink()
        except: pass

    # Infinite loop
    round_num = 0
    while True:
        round_num += 1
        log(f"\n{'='*70}")
        log(f"ROUND {round_num} START")
        log(f"{'='*70}")
        try:
            new, elapsed = asyncio.run(main())
            if new == 0:
                log("0 new — waiting 30s before retry")
                time.sleep(30)
            else:
                time.sleep(2)
        except Exception as e:
            log(f"CRASHED: {e}")
            import traceback
            log(traceback.format_exc())
            time.sleep(30)
