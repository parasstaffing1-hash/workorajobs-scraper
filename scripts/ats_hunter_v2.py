#!/usr/bin/env python3
"""ATS Board Hunter — Probe thousands of ATS slugs to discover new boards.

Each new Greenhouse/Lever/Ashby/SmartRecruiters board gives 20-800 fresh jobs.
This is the FASTEST way to grow past 686K.

Strategy: generate 50K+ slugs, probe them in parallel, save valid boards.
Then use those boards to pull all their jobs.
"""
from __future__ import annotations
import hashlib, json, os, random, sqlite3, string, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "hunter_v2_cp.json"
LOG_PATH = ROOT / "hunter_v2_log.txt"
TARGET = 1_000_000
WORKERS = 50
BATCH_TIMEOUT = 3600

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
                         "hunter_v2", j.get("id",""), j.get("posted"),
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

def safe_get(client, url, retries=2, timeout=12):
    for attempt in range(retries + 1):
        try:
            resp = client.get(url, headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "application/json, text/html, */*",
            }, timeout=timeout, follow_redirects=True)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 429):
                time.sleep(random.uniform(2, 5))
                continue
            return resp
        except Exception:
            if attempt < retries:
                time.sleep(random.uniform(0.5, 2))
    return None

# ═══════════════════════════════════════════════════════════════
# MASSIVE SLUG GENERATION
# ═══════════════════════════════════════════════════════════════

# Well-known companies (thousands)
COMPANY_NAMES = [
    # FAANG + Big Tech
    "google", "microsoft", "apple", "amazon", "meta", "facebook", "netflix",
    "tesla", "nvidia", "adobe", "salesforce", "oracle", "ibm", "intel",
    "cisco", "vmware", "crowdstrike", "fortinet", "palo-alto", "qualys",
    # AI/ML
    "openai", "anthropic", "hugging-face", "stability-ai", "midjourney",
    "cohere", "ai21", "inflection", "Character.AI", "runway",
    # Design/Product
    "figma", "canva", "notion", "linear", "zeplin", "invision",
    "sketch", "maze", "useberry", "hotjar",
    # Dev Tools
    "vercel", "netlify", "supabase", "firebase", "appwrite",
    "cloudflare", "fastly", "akamai", "limelight",
    "twilio", "sendgrid", "mailgun", "postmark",
    "stripe", "square", "paypal", "braintree",
    "shopify", "bigcommerce", "woocommerce",
    "algolia", "typesense", "meilisearch",
    # Data/Analytics
    "datadog", "newrelic", "sentry", "grafana", "prometheus",
    "segment", "amplitude", "mixpanel", "posthog", "heap",
    "snowflake", "databricks", "dbt", "fivetran", "airbyte",
    "looker", "metabase", "superset", "redash",
    # Databases
    "mongodb", "elastic", "confluent", "redis", "couchbase",
    "planetscale", "turso", "neon", "cockroach", "citus",
    "timescale", "influx", "clickhouse", "druid",
    # DevOps/Infra
    "docker", "hashicorp", "pulumi", "ansible", "rancher",
    "github", "gitlab", "bitbucket", "jira", "confluence",
    "circleci", "buildkite", "jenkins", "argo", "flux",
    "istio", "envoy", "kong", "tyk",
    # Communication
    "slack", "discord", "zoom", "teams", "loom",
    "intercom", "drift", "qualified", "crisp",
    "zendesk", "freshdesk", "helpscout", "front",
    "gong", "chorus", "chorus.ai", "salesloft", "outreach",
    # CRM/Sales
    "hubspot", "salesforce", "zoho", "pipedrive", "close",
    "apollo", "zoominfo", "clearbit", "leadfeeder",
    # HR/People
    "bamboohr", "workday", "rippling", "gusto", "deel",
    "lattice", "15five", "leapsome", "culture-amp",
    "greenhouse", "lever", "ashby", "smartrecruiters", "workable",
    # Security
    "okta", "auth0", "1password", "dashlane", "nordpass",
    "snyk", "checkmarx", "veracode", "sonar", "jfrog",
    "darktrace", "sentinelone", "cybereason", "Recorded Future",
    # Cloud/Infra
    "hashicorp", "terraform", "pulumi", "crossplane",
    "rancher", "docker", "podman", "buildah",
    # Finance
    "brex", "ramp", "mercury", "mercury.com",
    "chime", "sofi", "robinhood", "coinbase", "kraken",
    "plaid", "adyen", "checkout", "checkout.com",
    "affirm", "klarna", "afterpay", "zip",
    "block", "cashapp", "venmo", "square",
    "navan", "tripactions", "travelperk",
    # Delivery/Gig
    "instacart", "doordash", "ubereats", "grubhub",
    "lyft", "uber", "airbnb", "booking", "expedia",
    "grab", "gojek", "ola", "rapido",
    # Entertainment/Media
    "spotify", "twitch", "pinterest", "snap", "tumblr",
    "tiktok", "bytedance", "reddit", "quora", "medium",
    "substack", "beehiiv", "ghost",
    # Gaming
    "epic-games", "riot-games", "roblox", "activision", "blizzard",
    "valve", "supercell", "king", "mojang", "ea",
    "unity", "unreal", "godot",
    # Space/Robotics
    "spacex", "blue-origin", "rocket-lab", "relativity-space",
    "waymo", "cruise", "zoox", "aurora", "nuro",
    "boston-dynamics", "Figure", "1x", "agility-robotics",
    # Indian Companies
    "flipkart", "swiggy", "zomato", "paytm", "phonepe", "razorpay",
    "cred", "meesho", "zepto", "blinkit", "grofers",
    "freshworks", "zoho", "postman", "browserstack",
    "groww", "zerodha", "upstox", "policybazaar",
    "dream11", "nykaa", "mamaearth", "boat",
    "byju", "unacademy", "upgrad", "physics-wallah",
    "urban-company", "practo", "medibuddy", "pharmeasy",
    "acko", "digit", "porter", "rivigo",
    "dailyhunt", "1mg", "mygate", "noBroker", "housing",
    "slice", "jupiter", "fi-money", "cRED",
    # Indian IT Services
    "tcs", "infosys", "wipro", "hcl", "tech-mahindra",
    "ltimindtree", "persistent", "mphasis", "hexaware",
    "cognizant", "capgemini", "accenture",
    # Consultancies
    "deloitte", "pwc", "ey", "kpmg",
    "mckinsey", "bcg", "bain",
    # European
    "sap", "siemens", "bosch", "philips", "asml",
    "arm", "dynatrace", "softmax", "trivago",
    "soundcloud", "delivery-heros", "zalando", "otto",
    "scale-ai", "labelbox", "weights-biases",
    "harvey-ai", "retool", "zapier",
]

# Company name variations
SLUG_VARIANTS = []
for name in COMPANY_NAMES:
    clean = name.lower().replace(" ", "").replace(".", "").replace("-", "")
    SLUG_VARIANTS.extend([
        name,                    # original
        clean,                   # no separators
        name.replace("-", ""),   # no hyphens
        name.replace(" ", "-"),  # hyphenated
        name.replace(" ", ""),   # no spaces
    ])
# Deduplicate
SLUG_VARIANTS = list(dict.fromkeys(SLUG_VARIANTS))

# Random slug patterns
def generate_random_slugs(count=30000):
    """Generate random company-like slug patterns."""
    slugs = []
    prefixes = [
        "get", "try", "use", "my", "the", "we", "go", "do", "be", "ai",
        "io", "dev", "app", "lab", "hub", "pro", "co", "one", "top", "new",
        "all", "for", "up", "sun", "red", "big", "net", "jet", "zen", "max",
        "bit", "box", "day", "way", "run", "fly", "sky", "art", "ory", "mix",
        "fox", "owl", "ape", "ram", "duo", "ace", "kin", "zap", "hop", "pop",
        "joy", "fit", "kit", "pod", "pig", "owl", "bee", "cat", "dog", "cow",
        "raw", "old", "hot", "wet", "dry", "sad", "mad", "fun", "sun", "run",
    ]
    suffixes = [
        "ai", "io", "app", "hub", "lab", "dev", "tech", "ops", "cloud", "data",
        "pay", "ship", "run", "build", "code", "stack", "base", "flow", "sync",
        "link", "path", "way", "box", "now", "hq", "go", "up", "it", "fm",
        "labs", "works", "systems", "digital", "group", "inc", "co",
        "health", "care", "space", "mind", "force", "wave", "arc", "gate",
        "forge", "spark", "pulse", "hive", "nest", "root", "core", "node",
        "forge", "smith", "craft", "works", "factory", "studio", "agency",
    ]
    for p in prefixes:
        for s in suffixes:
            slugs.append(f"{p}{s}")
            slugs.append(f"{p}-{s}")
            slugs.append(f"{p}_{s}")

    # Two-letter combos
    for c1 in string.ascii_lowercase:
        for c2 in string.ascii_lowercase:
            slugs.append(f"{c1}{c2}")
            slugs.append(f"{c1}{c2}ai")
            slugs.append(f"{c1}{c2}io")
            slugs.append(f"{c1}{c2}tech")

    # Three-letter combos (most common company name patterns)
    for c1 in string.ascii_lowercase[:8]:  # a-h only to keep manageable
        for c2 in string.ascii_lowercase:
            for c3 in string.ascii_lowercase:
                slugs.append(f"{c1}{c2}{c3}")

    # Random words + tech suffixes
    words = [
        "blue", "green", "black", "white", "gold", "silver", "copper", "iron",
        "fast", "quick", "swift", "rapid", "turbo", "hyper", "mega", "ultra",
        "bright", "smart", "wise", "sharp", "clever", "sage", "noble", "bold",
        "alpha", "beta", "gamma", "delta", "omega", "sigma", "theta", "zeta",
        "nova", "solar", "lunar", "stellar", "cosmic", "astral", "nebula",
        "fire", "ice", "storm", "thunder", "lightning", "cloud", "frost",
        "hawk", "eagle", "wolf", "fox", "bear", "lion", "tiger", "deer",
        "oak", "pine", "maple", "cedar", "elm", "palm", "sage", "fern",
        "vertex", "nexus", "apex", "zenith", "pinnacle", "summit", "crest",
        "bridge", "harbor", "haven", "port", "gate", "door", "window", "wall",
    ]
    for w in words:
        for s in suffixes:
            slugs.append(f"{w}{s}")
            slugs.append(f"{w}-{s}")

    # Deduplicate
    seen = set()
    unique = []
    for s in slugs:
        s = s.lower().strip()
        if s and s not in seen and 2 <= len(s) <= 30:
            seen.add(s)
            unique.append(s)
    return unique

def all_slugs():
    """Combine known companies + random slugs."""
    slugs = list(SLUG_VARIANTS)
    slugs.extend(generate_random_slugs(30000))
    # Deduplicate
    seen = set()
    unique = []
    for s in slugs:
        s = s.lower().strip()
        if s and s not in seen and 2 <= len(s) <= 30:
            seen.add(s)
            unique.append(s)
    return unique

# ═══════════════════════════════════════════════════════════════
# ATS PROBE FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def probe_greenhouse(client, slug):
    """Probe Greenhouse boards API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    resp = safe_get(client, url, retries=1, timeout=10)
    if resp and resp.status_code == 200:
        try:
            data = resp.json()
            jobs_list = data.get("jobs", [])
            if jobs_list:
                company = data.get("name", slug)
                return [{"url": j.get("absolute_url", ""),
                         "title": j.get("title", ""),
                         "company": company,
                         "location": (j.get("location", {}) or {}).get("name", "") if isinstance(j.get("location"), dict) else "",
                         "desc": (j.get("content", "") or "")[:500],
                         "source": f"greenhouse:{slug}",
                         "id": str(j.get("id", "")),
                         "posted": j.get("updated_at", "")}
                        for j in jobs_list[:200]]
        except: pass
    return []

def probe_lever(client, slug):
    """Probe Lever postings API."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    resp = safe_get(client, url, retries=1, timeout=10)
    if resp and resp.status_code == 200:
        try:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                return [{"url": j.get("hostedUrl", ""),
                         "title": j.get("text", ""),
                         "company": slug,
                         "location": j.get("categories", {}).get("location", ""),
                         "desc": (j.get("descriptionPlain", "") or "")[:500],
                         "source": f"lever:{slug}",
                         "id": str(j.get("id", "")),
                         "posted": str(j.get("createdAt", ""))}
                        for j in data[:200]]
        except: pass
    return []

def probe_ashby(client, slug):
    """Probe Ashby job postings API."""
    url = f"https://api.ashbyhq.com/api/v1/job-posting/{slug}"
    resp = safe_get(client, url, retries=1, timeout=10)
    if resp and resp.status_code == 200:
        try:
            data = resp.json()
            jobs_list = data.get("jobPostings", data.get("data", {}).get("jobPostings", []))
            if jobs_list:
                company = data.get("organizationName", slug)
                return [{"url": f"https://jobs.ashbyhq.com/{slug}/{j.get('id', '')}",
                         "title": j.get("title", ""),
                         "company": company,
                         "location": j.get("locationName", ""),
                         "desc": (j.get("description", "") or "")[:500],
                         "source": f"ashby:{slug}",
                         "id": str(j.get("id", "")),
                         "posted": j.get("publishedAt", "")}
                        for j in jobs_list[:200]]
        except: pass
    return []

def probe_smartrecruiters(client, slug):
    """Probe SmartRecruiters API."""
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0"
    resp = safe_get(client, url, retries=1, timeout=10)
    if resp and resp.status_code == 200:
        try:
            data = resp.json()
            content = data.get("content", [])
            if content:
                return [{"url": f"https://careers.smartrecruiters.com/{slug}{j.get('ref', '')}",
                         "title": j.get("name", ""),
                         "company": (j.get("company", {}) or {}).get("name", slug) if isinstance(j.get("company"), dict) else slug,
                         "location": f"{(j.get('location', {}) or {}).get('city', '')} {(j.get('location', {}) or {}).get('country', '')}".strip(),
                         "desc": "",
                         "source": f"smartrecruiters:{slug}",
                         "id": str(j.get("id", "")),
                         "posted": j.get("releasedDate", "")}
                        for j in content[:100]]
        except: pass
    return []

def probe_workable(client, slug):
    """Probe Workable API."""
    url = f"https://{slug}.workable.com/api/v3/widget/accounts/{slug}"
    resp = safe_get(client, url, retries=1, timeout=10)
    if resp and resp.status_code == 200:
        try:
            data = resp.json()
            jobs_list = data.get("jobs", data.get("results", []))
            if jobs_list:
                return [{"url": j.get("url", j.get("apply_url", "")),
                         "title": j.get("title", ""),
                         "company": j.get("department", slug),
                         "location": str(j.get("location", "")),
                         "desc": "",
                         "source": f"workable:{slug}",
                         "id": str(j.get("shortcode", "")),
                         "posted": j.get("updated_at", "")}
                        for j in jobs_list[:100]]
        except: pass
    return []

def probe_jobvite(client, slug):
    """Probe Jobvite RSS feed."""
    url = f"https://careers.jobvite.com/{slug}/search?q=&nl=1&fr=false"
    resp = safe_get(client, url, retries=1, timeout=10)
    if resp and resp.status_code == 200:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            jobs = []
            for a in soup.select("a[href*='/job/']")[:50]:
                title = (a.get_text(strip=True) or "").strip()
                href = a.get("href", "")
                job_url = f"https://careers.jobvite.com{href}" if href.startswith("/") else href
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": slug,
                                 "location": "", "desc": "", "source": f"jobvite:{slug}"})
            if jobs:
                return jobs
        except: pass
    return []

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    reset = "--reset" in sys.argv

    log("=" * 70)
    log("ATS BOARD HUNTER V2 — 50 workers, 6 ATS platforms, 50K+ slugs")
    log("=" * 70)

    db = JobDB()
    total = db.count()
    log(f"DB: {total:,} | Gap: {max(0, TARGET-total):,}")

    db.load_dedup_keys()

    # Generate all slugs
    slugs = all_slugs()
    log(f"Total slugs to probe: {len(slugs):,}")

    # Load checkpoint
    done = set()
    if not reset and CP_PATH.exists():
        try:
            cp = json.loads(CP_PATH.read_text("utf-8"))
            done = set(cp.get("done", []))
        except: pass
    if reset:
        done.clear()

    remaining = [s for s in slugs if s not in done]
    log(f"Remaining slugs: {len(remaining):,}")

    if not remaining:
        log("All slugs probed! Resetting...")
        done.clear()
        remaining = slugs[:]

    start = time.time()
    counter = {"iters": 0, "new": 0, "boards": 0}
    c_lock = threading.Lock()
    stop_event = threading.Event()

    def worker(wid):
        import httpx
        client = httpx.Client(timeout=15, follow_redirects=True,
                              limits=httpx.Limits(max_connections=5, max_keepalive_connections=3))
        try:
            while not stop_event.is_set():
                try:
                    slug = remaining.pop(0) if remaining else None
                except IndexError:
                    log(f"  W{wid:02d} All slugs exhausted! Regenerating...")
                    new_slugs = all_slugs()
                    remaining.extend([s for s in new_slugs if s not in done])
                    if not remaining: break
                    slug = remaining.pop(0)
                if slug is None: break

                try:
                    time.sleep(random.uniform(0.05, 0.2))

                    all_jobs = []
                    # Try all ATS platforms
                    for probe_fn in [probe_greenhouse, probe_lever, probe_ashby,
                                     probe_smartrecruiters, probe_workable]:
                        try:
                            jobs = probe_fn(client, slug)
                            if jobs:
                                all_jobs.extend(jobs)
                        except: pass

                    new = db.insert(all_jobs) if all_jobs else 0

                    with c_lock:
                        counter["iters"] += 1
                        counter["new"] += new
                        done.add(slug)
                        if new > 0:
                            counter["boards"] += 1

                    if new > 0:
                        sources = set(j["source"].split(":")[0] for j in all_jobs)
                        log(f"  W{wid:02d} +{new:3d} from {slug} ({'+'.join(sources)})")

                    if counter["iters"] % 500 == 0:
                        ct = db.count()
                        rate = counter["new"] / max((time.time()-start)/60, 0.1)
                        log(f"  [{counter['iters']:,}] +{counter['new']:,} | DB={ct:,} | {rate:.0f}/min | Boards={counter['boards']:,} | Gap={max(0,TARGET-ct):,}")
                        try:
                            CP_PATH.write_text(json.dumps({"done": list(done),
                                "total_new": counter["new"], "boards": counter["boards"]}), "utf-8")
                        except: pass

                except Exception as e:
                    log(f"  W{wid:02d} ERR: {e}")

        except Exception as e:
            log(f"  W{wid:02d} FATAL: {e}")
        finally:
            try: client.close()
            except: pass

    log(f"Launching {WORKERS} hunters...")
    threads = []
    for i in range(WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.02)

    # Heartbeat
    def heartbeat():
        while not stop_event.is_set():
            time.sleep(120)
            try:
                ct = db.count()
                elapsed_min = (time.time() - start) / 60
                log(f"  [HB] DB={ct:,} | iters={counter['iters']:,} new={counter['new']:,} boards={counter['boards']:,} | {elapsed_min:.0f}min | Gap={max(0,TARGET-ct):,}")
                CP_PATH.write_text(json.dumps({"done": list(done),
                    "total_new": counter["new"], "boards": counter["boards"]}), "utf-8")
            except: pass
    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()

    for t in threads:
        t.join(timeout=BATCH_TIMEOUT)

    stop_event.set()
    time.sleep(2)

    try:
        CP_PATH.write_text(json.dumps({"done": list(done),
            "total_new": counter["new"], "boards": counter["boards"]}), "utf-8")
    except: pass

    ft = db.count()
    elapsed = (time.time() - start) / 60
    db.close()

    log("=" * 70)
    log(f"ROUND COMPLETE | New: +{counter['new']:,} | Boards: {counter['boards']:,}")
    log(f"DB: {ft:,} | Gap: {max(0, TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 70)

if __name__ == "__main__":
    main()
