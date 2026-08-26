#!/usr/bin/env python3
"""ATS HUNTER — Find thousands of new company career boards.

Strategy:
1. Generate 500K+ company name variations (real company names + common patterns)
2. Probe all ATS platforms in parallel (Greenhouse, Lever, Ashby, SmartRecruiters, Workable)
3. Each valid board = 5-500 unique jobs we don't have yet
4. Run continuously, rotating through slug batches

This is the key to reaching 1M — discovering boards no one has scraped before.
"""
from __future__ import annotations
import hashlib, json, os, random, sqlite3, string, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "hunter_cp.json"
LOG_PATH = ROOT / "hunter_log.txt"
TARGET = 1_000_000
WORKERS = 40
ROUND_TIMEOUT = 1800  # 30 min

_lock = threading.Lock()
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        try:
            with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{ts}] {msg}\n")
        except: pass


# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════

class JobDB:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-64000")
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
                         "ats_hunter", j.get("id",""), j.get("posted"),
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


# ═══════════════════════════════════════════════════════════════
# HTTP
# ═══════════════════════════════════════════════════════════════

import httpx

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

def safe_get(client, url, timeout=10):
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        resp = client.get(url, headers=headers, timeout=timeout)
        return resp
    except:
        return None


# ═══════════════════════════════════════════════════════════════
# SLUG GENERATOR — Millions of company name variations
# ═══════════════════════════════════════════════════════════════

# Real company names (1000+)
REAL_COMPANIES = [
    # Tech Giants
    "google", "microsoft", "apple", "amazon", "meta", "facebook", "netflix",
    "tesla", "nvidia", "adobe", "salesforce", "oracle", "ibm", "intel",
    "cisco", "vmware", "crowdstrike", "fortinet", "paloalto",
    # Unicorns
    "openai", "anthropic", "figma", "canva", "notion", "linear", "vercel",
    "supabase", "cloudflare", "fastly", "twilio", "stripe", "shopify",
    "datadog", "newrelic", "sentry", "grafana", "mongodb", "elastic",
    "docker", "hashicorp", "github", "gitlab", "slack", "discord",
    "intercom", "zendesk", "hubspot", "segment", "amplitude", "posthog",
    "brex", "ramp", "mercury", "rippling", "gusto", "workday",
    "chime", "sofi", "robinhood", "coinbase", "instacart", "doordash",
    "lyft", "uber", "airbnb", "spotify", "twitch", "pinterest", "snap",
    "tiktok", "bytedance", "epic-games", "riot-games", "roblox",
    "waymo", "cruise", "zoox", "aurora", "spacex", "blue-origin",
    "flipkart", "swiggy", "zomato", "paytm", "phonepe", "razorpay",
    "cred", "meesho", "zepto", "blinkit", "freshworks", "zoho",
    "postman", "groww", "zerodha", "policybazaar", "dream11",
    "servicenow", "sap", "accenture", "deloitte", "pwc", "ey",
    "capgemini", "cognizant", "infosys", "wipro", "hcl",
    "tech-mahindra", "ltimindtree", "persistent", "mphasis",
    "atlassian", "zendesk", "freshdesk", "asana", "monday", "coda",
    "airtable", "smartsheet", "calendly", "loom", "gong", "salesloft",
    "auth0", "okta", "1password", "jfrog", "snyk", "sonar",
    "pagerduty", "opsgenie", "circleci", "buildkite", "launchdarkly",
    "planetscale", "turso", "neon", "railway", "fly", "render",
    "algolia", "typesense", "meilisearch", "braze", "iterable",
    "cashapp", "block", "square", "affirm", "klarna", "plaid", "adyen",
    "navan", "tripactions", "travelperk",
    "unity", "unreal", "godot", "blender",
    "terraform", "pulumi", "ansible",
    "prometheus", "honeycomb", "bugsnag", "rollbar",
    "twilio", "vonage", "plivo",
    "stripe", "braintree", "checkout",
    "plaid", "affirm", "klarna", "afterpay",
    "mercury", "brex", "ramp",
    # Indian
    "byju", "unacademy", "upgrad", "physics-wallah",
    "urban-company", "practo", "medibuddy",
    "acko", "digit", "ola", "rapido", "porter",
    "dailyhunt", "1mg", "pharmeasy", "mygate", "nobroker",
    "slice", "jupiter", "fi-money", "freecharge",
    "noBroker", "housing", "bharatpe", "grofers",
    # More Global
    "atlassian", "zendesk", "hubspot", "intercom", "drift",
    "rippling", "gusto", "bamboohr", "leapsome", "culture-amp",
    "lattice", "15five", "notion", "coda", "airtable",
    "asana", "clickup", "todoist", "figma", "sketch", "canva",
    "unity", "unreal", "docker", "podman", "rancher",
    "terraform", "pulumi", "prometheus", "datadog", "newrelic",
    "sentry", "twilio", "vonage", "stripe", "braintree", "adyen",
    "plaid", "affirm", "klarna", "mercury", "brex", "ramp",
    # Media/Entertainment
    "spotify", "netflix", "disney", "warner", "paramount",
    "lionsgate", "sony", "nbcuniversal", "fox", "bbc",
    "hulu", "peacock", "hbomax", "apple-tv", "prime-video",
    # Gaming
    "activision", "blizzard", "ea", "ubisoft", "take-two",
    "capcom", "konami", "square-enix", "bandai-namco", "sega",
    "tencent-games", "netease-games", "supercell", "king",
    "zynga", "playrix", "miHoYo", "hoYoverse",
    # Finance
    "goldman-sachs", "jpmorgan", "morgan-stanley", "citadel",
    "two-sigma", "deshaw", "point72", "jump-trading",
    "hrt", "virtu", "optiver", "imc", "susquehanna",
    # Aerospace
    "spacex", "blue-origin", "rocket-lab", "relativity",
    "firefly", "astrobotic", "momentus", "virgin-galactic",
    # Biotech
    "moderna", "biontech", "genentech", "regeneron", "amgen",
    "gilead", "celgene", "biogen", "vertex", "illumina",
    "23andme", "color", "invitae", "tempus", "foundation-medicine",
]

# Common tech company name patterns
PATTERNS = {
    "prefixes": [
        "get", "try", "use", "my", "the", "we", "go", "do", "be", "ai",
        "io", "dev", "app", "lab", "hub", "pro", "co", "one", "top", "new",
        "all", "for", "up", "sun", "red", "big", "net", "jet", "zen", "max",
        "bit", "box", "day", "way", "run", "fly", "sky", "art", "ory", "mix",
        "fox", "owl", "ape", "bee", "ram", "duo", "ace", "kin", "zap", "hop",
        "pop", "joy", "fit", "kit", "pod", "pig", "no", "so", "ok", "oh",
    ],
    "roots": [
        "tech", "soft", "ware", "code", "data", "cloud", "net", "sys",
        "bit", "byte", "pixel", "logic", "flow", "sync", "link", "path",
        "wave", "bolt", "spark", "core", "edge", "stack", "base", "mind",
        "brain", "neural", "deep", "learn", "train", "model", "algo",
        "quant", "signal", "pulse", "wave", "shift", "forge", "craft",
        "build", "make", "form", "mix", "blend", "fuse", "merge", "join",
        "push", "pull", "lift", "rise", "jump", "dash", "rush", "zoom",
    ],
    "suffixes": [
        "ai", "io", "app", "hub", "lab", "dev", "tech", "ops", "cloud",
        "data", "pay", "ship", "run", "build", "code", "stack", "base",
        "flow", "sync", "link", "path", "way", "box", "now", "hq", "go",
        "up", "it", "fm", "labs", "works", "systems", "digital", "group",
        "inc", "co", "health", "care", "space", "mind", "force", "wave",
        "point", "square", "circle", "line", "curve", "angle",
        "rocket", "star", "moon", "sun", "earth", "mars", "nova",
    ],
    "connectors": ["", "-", "_", "."],
}

# 2/3 letter real domain hacks
DOMAIN_WORDS = [
    "ai", "io", "ly", "me", "we", "it", "go", "do", "so", "up",
    "in", "at", "on", "to", "by", "or", "am", "an", "no", "be",
    "id", "tv", "fm", "cc", "tv", "im", "is", "ok", "hi", "yo",
]


def generate_massive_slug_set():
    """Generate 500K+ unique slugs to probe."""
    slugs = set()

    # 1. Real company names
    slugs.update(REAL_COMPANIES)

    # 2. Domain hacks
    for w in DOMAIN_WORDS:
        slugs.add(w)

    # 3. Prefix + Root combos (80 × 60 = 4,800)
    for p in PATTERNS["prefixes"]:
        for r in PATTERNS["roots"]:
            for c in PATTERNS["connectors"]:
                slugs.add(f"{p}{c}{r}")

    # 4. Root + Suffix combos (60 × 35 = 2,100)
    for r in PATTERNS["roots"]:
        for s in PATTERNS["suffixes"]:
            for c in PATTERNS["connectors"]:
                slugs.add(f"{r}{c}{s}")

    # 5. Prefix + Suffix combos (80 × 35 = 2,800)
    for p in PATTERNS["prefixes"]:
        for s in PATTERNS["suffixes"]:
            for c in PATTERNS["connectors"]:
                slugs.add(f"{p}{c}{s}")

    # 6. Letter combos (2-3 chars)
    for c1 in string.ascii_lowercase:
        for c2 in string.ascii_lowercase:
            slugs.add(f"{c1}{c2}")
            for c3 in string.ascii_lowercase:
                slugs.add(f"{c1}{c2}{c3}")

    # 7. Common suffix patterns
    for s in ["labs", "tech", "soft", "io", "ai", "dev", "hub", "co", "inc"]:
        for c1 in string.ascii_lowercase:
            for c2 in string.ascii_lowercase:
                slugs.add(f"{c1}{c2}{s}")
                for c3 in string.ascii_lowercase:
                    slugs.add(f"{c1}{c2}{c3}{s}")

    # 8. Real-ish patterns with numbers
    for name in ["tech", "data", "cloud", "code", "byte", "pixel", "flow"]:
        for i in range(100):
            slugs.add(f"{name}{i}")
            slugs.add(f"{name}-{i}")
            slugs.add(f"{name}_{i}")

    # 9. Numeric slugs (common company slugs)
    for i in range(10000):
        slugs.add(str(i))

    log(f"Generated {len(slugs):,} unique slugs")
    return list(slugs)


# ═══════════════════════════════════════════════════════════════
# ATS PROBERS
# ═══════════════════════════════════════════════════════════════

def probe_greenhouse(client, slug):
    """Probe Greenhouse board — most popular ATS."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    resp = safe_get(client, url, timeout=8)
    if not resp or resp.status_code != 200:
        return []
    try:
        data = resp.json()
        jobs_list = data.get("jobs", [])
        if not jobs_list:
            return []
        jobs = []
        for j in jobs_list[:200]:
            loc = j.get("location", {})
            loc_name = loc.get("name", "") if isinstance(loc, dict) else ""
            jobs.append({
                "url": j.get("absolute_url", ""),
                "title": j.get("title", ""),
                "company": data.get("name", slug),
                "location": loc_name,
                "desc": (j.get("content", "") or "")[:500],
                "source": f"greenhouse:{slug}",
                "id": str(j.get("id", "")),
                "posted": j.get("updated_at", ""),
            })
        return jobs
    except:
        return []


def probe_lever(client, slug):
    """Probe Lever job board."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    resp = safe_get(client, url, timeout=8)
    if not resp or resp.status_code != 200:
        return []
    try:
        data = resp.json()
        if not isinstance(data, list) or len(data) == 0:
            return []
        jobs = []
        for j in data[:200]:
            location = j.get("categories", {}).get("location", "")
            jobs.append({
                "url": j.get("hostedUrl", ""),
                "title": j.get("text", ""),
                "company": slug,
                "location": location,
                "desc": (j.get("descriptionPlain", "") or "")[:500],
                "source": f"lever:{slug}",
                "id": str(j.get("id", "")),
                "posted": j.get("createdAt", ""),
            })
        return jobs
    except:
        return []


def probe_ashby(client, slug):
    """Probe Ashby ATS."""
    url = f"https://api.ashbyhq.com/api/v1/job-posting/{slug}"
    resp = safe_get(client, url, timeout=8)
    if not resp or resp.status_code != 200:
        return []
    try:
        data = resp.json()
        jobs_list = data.get("jobPostings", data.get("data", {}).get("jobPostings", []))
        if not jobs_list:
            return []
        jobs = []
        for j in jobs_list[:200]:
            jobs.append({
                "url": f"https://jobs.ashbyhq.com/{slug}/{j.get('id', '')}",
                "title": j.get("title", ""),
                "company": data.get("organizationName", slug),
                "location": j.get("locationName", ""),
                "desc": (j.get("description", "") or "")[:500],
                "source": f"ashby:{slug}",
                "id": str(j.get("id", "")),
                "posted": j.get("publishedAt", ""),
            })
        return jobs
    except:
        return []


def probe_smartrecruiters(client, slug):
    """Probe SmartRecruiters."""
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0"
    resp = safe_get(client, url, timeout=8)
    if not resp or resp.status_code != 200:
        return []
    try:
        data = resp.json()
        content = data.get("content", [])
        if not content:
            return []
        jobs = []
        for j in content[:100]:
            loc = j.get("location", {})
            loc_name = ""
            if isinstance(loc, dict):
                loc_name = (loc.get("city", "") + " " + loc.get("country", "")).strip()
            ref = j.get("ref", "")
            company_name = j.get("company", {}).get("name", slug) if isinstance(j.get("company"), dict) else slug
            jobs.append({
                "url": f"https://careers.smartrecruiters.com/{slug}{ref}",
                "title": j.get("name", ""),
                "company": company_name,
                "location": loc_name,
                "desc": "",
                "source": f"smartrecruiters:{slug}",
                "id": str(j.get("id", "")),
                "posted": j.get("releasedDate", ""),
            })
        return jobs
    except:
        return []


def probe_workable(client, slug):
    """Probe Workable ATS."""
    url = f"https://{slug}.workable.com/api/v3/widget/accounts/{slug}"
    resp = safe_get(client, url, timeout=8)
    if not resp or resp.status_code != 200:
        return []
    try:
        data = resp.json()
        jobs_list = data.get("jobs", data.get("results", []))
        if not jobs_list:
            return []
        jobs = []
        for j in jobs_list[:100]:
            title = j.get("title", "")
            job_url = j.get("url", j.get("apply_url", ""))
            company = j.get("department", slug)
            location = j.get("location", "")
            if isinstance(location, dict):
                location = location.get("city", "") + " " + location.get("country", "")
            jobs.append({
                "url": job_url, "title": title, "company": company,
                "location": str(location).strip(), "desc": "",
                "source": f"workable:{slug}", "id": str(j.get("shortcode", "")),
                "posted": j.get("updated_at", ""),
            })
        return jobs
    except:
        return []


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    reset = "--reset" in sys.argv
    max_slugs = int(sys.argv[1]) if len(sys.argv) > 1 else 0  # 0 = all

    log("=" * 70)
    log("ATS HUNTER — Massive board discovery engine")
    log("=" * 70)

    db = JobDB()
    total = db.count()
    log(f"DB: {total:,} | Gap: {max(0, TARGET-total):,}")
    db.load_dedup_keys()

    # Load checkpoint
    done = set()
    if not reset and CP_PATH.exists():
        try:
            cp = json.loads(CP_PATH.read_text("utf-8"))
            done = set(cp.get("done", []))
        except:
            pass
    if reset:
        done.clear()
        log("Checkpoint reset")

    # Generate slugs
    all_slugs = generate_massive_slug_set()
    if max_slugs > 0:
        all_slugs = all_slugs[:max_slugs]
    random.shuffle(all_slugs)

    remaining = [s for s in all_slugs if s not in done]
    log(f"Total slugs: {len(all_slugs):,} | Done: {len(done):,} | Remaining: {len(remaining):,}")

    if not remaining:
        log("All slugs done! Resetting...")
        done.clear()
        remaining = all_slugs[:]

    # Stats
    start = time.time()
    counter = {"iters": 0, "new": 0, "boards": 0}
    c_lock = threading.Lock()
    stop_event = threading.Event()

    def worker(wid):
        client = httpx.Client(
            timeout=12, follow_redirects=True,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=3)
        )
        batch_size = 10  # slugs per batch
        try:
            while not stop_event.is_set():
                # Get next batch
                batch = []
                with c_lock:
                    for _ in range(batch_size):
                        if remaining:
                            try:
                                batch.append(remaining.pop(0))
                            except IndexError:
                                break
                if not batch:
                    # Regenerate
                    with c_lock:
                        new_slugs = generate_massive_slug_set()
                        remaining.extend([s for s in new_slugs if s not in done])
                        if not remaining:
                            break
                        batch = [remaining.pop(0) for _ in range(min(batch_size, len(remaining)))]
                    if not batch:
                        break

                # Probe all ATS platforms for each slug
                all_jobs = []
                valid_boards = 0

                for slug in batch:
                    if stop_event.is_set():
                        break

                    # Greenhouse (fastest, most common)
                    jobs = probe_greenhouse(client, slug)
                    if jobs:
                        all_jobs.extend(jobs)
                        valid_boards += 1

                    # Lever
                    jobs = probe_lever(client, slug)
                    if jobs:
                        all_jobs.extend(jobs)
                        valid_boards += 1

                    # Ashby
                    jobs = probe_ashby(client, slug)
                    if jobs:
                        all_jobs.extend(jobs)
                        valid_boards += 1

                    # SmartRecruiters
                    jobs = probe_smartrecruiters(client, slug)
                    if jobs:
                        all_jobs.extend(jobs)
                        valid_boards += 1

                    time.sleep(0.05)  # minimal delay

                # Insert all found jobs
                new = db.insert(all_jobs) if all_jobs else 0

                with c_lock:
                    counter["iters"] += 1
                    counter["new"] += new
                    counter["boards"] += valid_boards
                    for s in batch:
                        done.add(s)
                    iters = counter["iters"]

                if new > 0 or valid_boards > 0:
                    log(f"  W{wid:02d} +{new:4d} from {valid_boards} boards ({len(batch)} slugs)")

                if iters % 10 == 0:
                    ct = db.count()
                    elapsed = (time.time() - start) / 60
                    rate = counter["new"] / max(elapsed, 0.1)
                    log(f"  [{iters:,}] +{counter['new']:,} | DB={ct:,} | {rate:.0f}/min | Boards={counter['boards']:,} | Gap={max(0,TARGET-ct):,}")

                if iters % 50 == 0:
                    try:
                        CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
                    except:
                        pass

        except Exception as e:
            log(f"  W{wid:02d} FATAL: {e}")
        finally:
            try:
                client.close()
            except:
                pass

    log(f"Launching {WORKERS} workers...")
    threads = []
    for i in range(WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.05)

    # Heartbeat
    def heartbeat():
        while not stop_event.is_set():
            time.sleep(120)
            try:
                ct = db.count()
                elapsed = (time.time() - start) / 60
                log(f"  [HB] DB={ct:,} | new={counter['new']:,} | boards={counter['boards']:,} | {elapsed:.0f}min | Gap={max(0,TARGET-ct):,}")
                CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
            except:
                pass

    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()

    for t in threads:
        t.join(timeout=ROUND_TIMEOUT)

    stop_event.set()
    time.sleep(2)

    try:
        CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
    except:
        pass

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
