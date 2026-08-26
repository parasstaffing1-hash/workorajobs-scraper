#!/usr/bin/env python3
"""ATS Board Hunter V3 — Probe 50K+ company slugs on Greenhouse/Lever/Ashby/SmartRecruiters APIs.
Each valid board gives 5-500 unique jobs NOT on any job board.
Target: find 5000+ boards, scrape them all = 100K-500K unique jobs."""
import asyncio, hashlib, json, os, queue, random, sqlite3, string, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path
import aiohttp

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"
LOG = ROOT / "ats_hunter_v3_log.txt"
CP = ROOT / "ats_hunter_v3_cp.json"
NOW = datetime.now(timezone.utc).isoformat()

_seen: set[str] = set()
_write_q: queue.Queue = queue.Queue(maxsize=100000)
_lock = threading.Lock()
_log_lock = threading.Lock()
_counter = {"new": 0, "boards": 0, "probed": 0}

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _log_lock:
        try:
            with open(LOG, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{ts}] {msg}\n")
        except: pass

def _hash(url, title, company):
    raw = f"{url.strip().lower()}|{title.strip().lower()}|{company.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def db_count():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    return n

def db_writer():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-128000")
    batch = []
    total = 0
    while True:
        try:
            item = _write_q.get(timeout=3)
        except queue.Empty:
            if batch:
                _flush(conn, batch)
                total += len(batch)
                batch = []
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
                       "ats_hunter", item.get("id", ""), item.get("posted"),
                       "", "", NOW, NOW))
        if len(batch) >= 2000:
            _flush(conn, batch)
            total += len(batch)
            batch = []
    if batch:
        _flush(conn, batch)
        total += len(batch)
    conn.close()
    log(f"DB writer done. Wrote {total:,} jobs.")

def _flush(conn, batch):
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO jobs"
            "(dedupe_key,title,company,location,description,url,"
            "source,source_kind,external_id,posted_at,salary,tags,"
            "first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            batch)
        conn.commit()
        _counter["new"] += len(batch)
    except Exception as e:
        log(f"DB error: {e}")


# ── Slug generation ──

# Common company name patterns for ATS boards
TECH_COMPANIES = [
    "google", "microsoft", "apple", "amazon", "meta", "facebook", "netflix",
    "tesla", "spacex", "nvidia", "intel", "amd", "qualcomm", "broadcom",
    "salesforce", "oracle", "adobe", "vmware", "cisco", "juniper",
    "uber", "lyft", "airbnb", "spotify", "twitter", "snap", "pinterest",
    "linkedin", "tiktok", "bytedance", "shopify", "stripe", "square",
    "block", "plaid", "coinbase", "kraken", "binance", "figma", "canva",
    "notion", "slack", "dropbox", "zoom", "twilio", "datadog", "snowflake",
    "databricks", "mongodb", "redis", "elastic", "confluent", "hashicorp",
    "gitlab", "github", "atlassian", "jira", "asana", "monday",
    "linear", "vercel", "netlify", "cloudflare", "fastly", "akamai",
    "anthropic", "openai", "deepmind", "scaleai", "palantir", "crowdstrike",
    "paloalto", "zscaler", "okta", "auth0", "1password", "bitwarden",
    "docker", "kubernetes", "rancher", "pivotal", "redhat", "suse",
    "ibm", "hp", "dell", "lenovo", "xiaomi", "samsung", "sony",
    "airtable", "smartsheet", "tableau", "looker", "dbt", "fivetran",
    "segment", "amplitude", "mixpanel", "heap", "fullstory",
    "rippling", "gusto", "bamboo", "workday", "adp", "paylocity",
    "lever", "greenhouse", "ashby", "smartrecruiters", "workable",
    "brex", "ramp", "divvy", "navan", "tripactions",
    "instacart", "doordash", "grubhub", "ubereats", "postmates",
    "robinhood", "sofi", "chime", "nubank", "revolut", "wise",
    "coinbase", "circle", "polygon", "avalanche", "solana",
    "waymo", "cruise", "argo", "zoox", "nuro",
    "zoom", "webex", "teams", "meet", "hopin", "runthe",
    "unity", "unreal", "epicgames", "roblox", "activision", "blizzard",
    "rockstargames", "riotgames", "ea", "valve", "nintendo",
    "bytedance", "tencent", "alibaba", "baidu", "jd", "meituan",
    "coupang", "grab", "gojek", "rappi", "mercadolibre",
    "samsung", "lg", "sk", "kt", "naver",
    "infosys", "wipro", "tcs", "hcltech", "techmahindra", "mphasis",
    "cognizant", "accenture", "capgemini", "dxc", "cgi",
    "jpmorgan", "goldmansachs", "morganstanley", "citibank", "hsbc",
    "barclays", "ubs", "deutschebank", "nomura", "mitsubishi",
    "visa", "mastercard", "amex", "paypal", "adyen", "checkout",
    "snowflake", "teradata", "cloudera", " Hortonworks",
    "unity", "autodesk", "ansys", "mathworks", "matlab",
    "crowdstrike", "sentinelone", "carbonblack", "tanium",
    "norton", "mcafee", "kaspersky", "eset", "bitdefender",
    "sony", "panasonic", "toshiba", "hitachi", "fujitsu",
]

# Generate slugs from common patterns
COMMON_WORDS = [
    "tech", "data", "cloud", "ai", "ml", "digital", "cyber", "block",
    "stack", "code", "dev", "net", "bit", "byte", "pixel", "wave",
    "flow", "sync", "hub", "lab", "labs", "io", "ai", "ly",
    "up", "go", "do", "try", "get", "set", "run", "fly",
    "blue", "green", "red", "orange", "purple", "black", "white",
    "alpha", "beta", "gamma", "delta", "omega", "sigma",
    "nova", "apex", "peak", "zen", "flux", "core", "spark",
    "bolt", "flash", "swift", "rapid", "turbo", "jet",
]

# Letter combinations for short company names (3-4 letters)
SHORT_SLUGS = []
for length in [3, 4]:
    for combo in string.ascii_lowercase[:16]:
        SHORT_SLUGS.append(combo * length)
    for i in range(200):
        SHORT_SLUGS.append("".join(random.choices(string.ascii_lowercase[:16], k=length)))

# Hybrid names: word + number, number + word, etc.
HYBRID_SLUGS = []
for w in COMMON_WORDS[:30]:
    for n in range(1, 50):
        HYBRID_SLUGS.append(f"{w}{n}")
        HYBRID_SLUGS.append(f"{n}{w}")

# YC-style slug patterns
YC_SLUGS = []
for w1 in COMMON_WORDS[:20]:
    for w2 in COMMON_WORDS[:20]:
        YC_SLUGS.append(f"{w1}-{w2}")


def generate_all_slugs():
    """Generate ~100K+ unique slugs to probe."""
    slugs = set()
    # Direct company names
    for name in TECH_COMPANIES:
        slugs.add(name.lower().replace(" ", "").replace(".", ""))
        slugs.add(name.lower().replace(" ", "-"))
        slugs.add(name.lower().replace(" ", ""))
    # Common words as company names
    for w in COMMON_WORDS:
        slugs.add(w)
    # Short combinations
    slugs.update(SHORT_SLUGS)
    # Hybrid patterns
    for w in COMMON_WORDS[:30]:
        for n in range(1, 50):
            slugs.add(f"{w}{n}")
            slugs.add(f"{w}-{n}")
            slugs.add(f"{n}{w}")
            slugs.add(f"{n}-{w}")
    # YC-style patterns
    for w1 in COMMON_WORDS[:20]:
        for w2 in COMMON_WORDS[:20]:
            slugs.add(f"{w1}-{w2}")
    # Triple combos
    for w1 in COMMON_WORDS[:10]:
        for w2 in COMMON_WORDS[:10]:
            for w3 in COMMON_WORDS[:10]:
                slugs.add(f"{w1}{w2}{w3}")
                slugs.add(f"{w1}-{w2}-{w3}")
    # Company name variants
    for name in TECH_COMPANIES:
        clean = name.lower().replace(" ", "").replace(".", "")
        slugs.add(clean)
        slugs.add(clean + "inc")
        slugs.add(clean + "corp")
        slugs.add(clean + "labs")
        slugs.add(clean + "io")
        slugs.add(clean + "ai")
        slugs.add(clean + "app")
        slugs.add(clean + "tech")
        slugs.add(clean + "dev")
        slugs.add(clean + "hq")
        slugs.add(clean + "hq")
        slugs.add(clean + "group")
        slugs.add(clean + "systems")
        slugs.add(clean + "works")
        slugs.add(clean + "digital")
        slugs.add(clean + "cloud")
        slugs.add(clean + "data")
        slugs.add("the" + clean)
        slugs.add("get" + clean)
        slugs.add("my" + clean)
        slugs.add("go" + clean)
        slugs.add("try" + clean)
        slugs.add("use" + clean)
    # More letter combos (4-letter)
    for c1 in string.ascii_lowercase[:16]:
        for c2 in string.ascii_lowercase[:16]:
            for c3 in string.ascii_lowercase[:16]:
                for c4 in string.ascii_lowercase[:16]:
                    slugs.add(f"{c1}{c2}{c3}{c4}")
    return list(slugs)


# ── ATS Probing ──

async def probe_greenhouse(session, slug):
    """Check if a Greenhouse board exists and return all jobs."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
        if not isinstance(data, dict):
            return None
        jobs_raw = data.get("jobs", [])
        if not jobs_raw:
            return None
        jobs = []
        for j in jobs_raw:
            title = j.get("title", "")
            jid = j.get("id", "")
            jurl = j.get("absolute_url", f"https://boards.greenhouse.io/{slug}/jobs/{jid}")
            loc = ""
            locs = j.get("location", {})
            if isinstance(locs, dict):
                loc = locs.get("name", "")
            jobs.append({
                "url": jurl, "title": title, "company": slug,
                "location": loc, "source": f"gh:{slug}", "id": str(jid), "ts": NOW,
            })
        return jobs
    except:
        return None


async def probe_lever(session, slug):
    """Check if a Lever board exists and return all jobs."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
        if not isinstance(data, list) or not data:
            return None
        jobs = []
        for j in data:
            title = j.get("text", "")
            jurl = j.get("hostedUrl", "")
            jid = j.get("id", "")
            team = j.get("categories", {}).get("team", "")
            jobs.append({
                "url": jurl, "title": title, "company": slug,
                "location": team, "source": f"lever:{slug}", "id": str(jid), "ts": NOW,
            })
        return jobs
    except:
        return None


async def probe_ashby(session, slug):
    """Check if an Ashby board exists and return jobs."""
    url = f"https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams"
    try:
        payload = {"operationName": "ApiJobBoardWithTeams",
                   "variables": {"organizationHostedJobsPageName": slug},
                   "query": "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) { teams { name jobs { id title locationName employmentType } } } }"}
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=6)) as r:
            if r.status != 200:
                return None
            data = await r.json()
        board = data.get("data", {}).get("jobBoard", {})
        teams = board.get("teams", [])
        if not teams:
            return None
        jobs = []
        for team in teams:
            for j in team.get("jobs", []):
                jid = j.get("id", "")
                title = j.get("title", "")
                loc = j.get("locationName", "")
                jobs.append({
                    "url": f"https://jobs.ashbyhq.com/{slug}/job/{jid}",
                    "title": title, "company": slug,
                    "location": loc, "source": f"ashby:{slug}", "id": str(jid), "ts": NOW,
                })
        return jobs if jobs else None
    except:
        return None


async def probe_smartrecruiters(session, slug):
    """Check SmartRecruiters API."""
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
        if not isinstance(data, dict):
            return None
        content = data.get("content", [])
        if not content:
            return None
        jobs = []
        for j in content:
            title = j.get("name", "")
            jid = j.get("id", "")
            jurl = j.get("ref", "")
            loc = j.get("location", {}).get("city", "")
            jobs.append({
                "url": jurl, "title": title, "company": slug,
                "location": loc, "source": f"sr:{slug}", "id": str(jid), "ts": NOW,
            })
        return jobs
    except:
        return None


async def probe_workable(session, slug):
    """Check Workable API."""
    url = f"https://{slug}.workable.com/spi/v3/accounts/{slug}/jobs"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
        if not isinstance(data, dict):
            return None
        jobs_raw = data.get("jobs", [])
        if not jobs_raw:
            return None
        jobs = []
        for j in jobs_raw:
            title = j.get("title", "")
            jid = j.get("id", "")
            jurl = j.get("url", "")
            loc = j.get("location", "")
            jobs.append({
                "url": jurl, "title": title, "company": slug,
                "location": loc, "source": f"workable:{slug}", "id": str(jid), "ts": NOW,
            })
        return jobs
    except:
        return None


# ── Main ──

async def probe_slug(session, slug):
    """Probe one slug across all ATS platforms."""
    all_jobs = []

    # Greenhouse first (most common)
    jobs = await probe_greenhouse(session, slug)
    if jobs:
        all_jobs.extend(jobs)
        _counter["boards"] += 1
        _stats["gh"] = _stats.get("gh", 0) + 1

    # Lever
    jobs = await probe_lever(session, slug)
    if jobs:
        all_jobs.extend(jobs)
        _counter["boards"] += 1
        _stats["lever"] = _stats.get("lever", 0) + 1

    # Ashby
    jobs = await probe_ashby(session, slug)
    if jobs:
        all_jobs.extend(jobs)
        _counter["boards"] += 1
        _stats["ashby"] = _stats.get("ashby", 0) + 1

    # SmartRecruiters
    jobs = await probe_smartrecruiters(session, slug)
    if jobs:
        all_jobs.extend(jobs)
        _counter["boards"] += 1
        _stats["sr"] = _stats.get("sr", 0) + 1

    # Workable
    jobs = await probe_workable(session, slug)
    if jobs:
        all_jobs.extend(jobs)
        _counter["boards"] += 1
        _stats["wk"] = _stats.get("wk", 0) + 1

    return all_jobs

_stats: dict = {}

async def worker(wid, sem, q, connector):
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            try:
                slug = q.get_nowait()
            except asyncio.QueueEmpty:
                break
            async with sem:
                try:
                    jobs = await probe_slug(session, slug)
                except:
                    jobs = []
                if jobs:
                    for j in jobs:
                        try:
                            _write_q.put_nowait(j)
                        except queue.Full:
                            pass
                    _counter["boards"] += 1
                    if len(jobs) >= 2:
                        log(f"  W{wid%50:03d} +{len(jobs):3d} {slug}")
                _counter["probed"] += 1
                if _counter["probed"] % 500 == 0:
                    log(f"  Probed: {_counter['probed']:,} | Boards: {_counter['boards']} | New: {_counter['new']:,} | DB: {db_count():,}")
            q.task_done()


async def main():
    log("=" * 70)
    log("ATS Hunter V3 — Probe 100K+ slugs across 5 ATS platforms")
    log("=" * 70)

    total = db_count()
    log(f"DB: {total:,}")

    # Load dedup keys
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    rows = conn.execute("SELECT dedupe_key FROM jobs").fetchall()
    conn.close()
    for r in rows:
        _seen.add(r[0])
    log(f"Dedup: {len(_seen):,}")

    # Generate slugs
    slugs = generate_all_slugs()
    random.shuffle(slugs)
    log(f"Total slugs to probe: {len(slugs):,}")

    # Checkpoint
    done = set()
    if CP.exists():
        try:
            cp = json.loads(CP.read_text("utf-8"))
            done = set(cp.get("done", []))
            log(f"Resuming from checkpoint: {len(done):,} done")
        except: pass

    remaining = [s for s in slugs if s not in done]
    log(f"Remaining: {len(remaining):,}")

    q = asyncio.Queue()
    for s in remaining:
        q.put_nowait(s)

    connector = aiohttp.TCPConnector(limit=200, limit_per_host=20, ttl_dns_cache=300)
    sem = asyncio.Semaphore(200)
    workers = [asyncio.create_task(worker(i, sem, q, connector)) for i in range(200)]

    # Reporter
    async def reporter():
        while True:
            await asyncio.sleep(30)
            ct = db_count()
            log(f"  STATUS | DB={ct:,} | +{_counter['new']:,} new | boards={_counter['boards']} | probed={_counter['probed']:,}")
            try:
                CP.write_text(json.dumps({"done": list(done), "found": _stats}), "utf-8")
            except: pass
    reporter_task = asyncio.create_task(reporter())

    await asyncio.gather(*workers)
    reporter_task.cancel()

    ct = db_count()
    log("=" * 70)
    log(f"DONE | Probed: {_counter['probed']:,} | Boards: {_counter['boards']} | New: +{_counter['new']:,} | DB: {ct:,}")
    log(f"ATS breakdown: {_stats}")
    log("=" * 70)


if __name__ == "__main__":
    writer = threading.Thread(target=db_writer, daemon=True)
    writer.start()
    asyncio.run(main())
