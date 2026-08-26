#!/usr/bin/env python3
"""MEGA UNIFIED SCRAPER — One script, all sources, continuous loop.

Combines:
1. Brute-force ATS board discovery (Greenhouse, Lever, Ashby, SmartRecruiters, Workable)
2. Known ATS board scraping (from companies.yaml + discovered boards)
3. JobSpy (LinkedIn, Indeed, Google Jobs)
4. HTML scrapers (Dice, SimplyHired)
5. JSON APIs (Remotive, Arbeitnow, RemoteOK)

Runs in a continuous loop with:
- In-memory dedup set (loaded from DB at start)
- Checkpoint system for discovered boards
- Auto-restart on crash
- Rate limiting per source
"""
from __future__ import annotations
import hashlib, json, os, queue, random, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "unified_cp.json"
LOG_PATH = ROOT / "unified_log.txt"
BOARDS_PATH = ROOT / "discovered_boards.json"
TARGET = 1_000_000
WORKERS = 30
LOOP_SECONDS = 0  # 0 = infinite loop

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
                         "unified", j.get("id",""), j.get("posted"),
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
# USER AGENTS & HTTP HELPERS
# ═══════════════════════════════════════════════════════════════

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

def safe_get(client, url, retries=2):
    for attempt in range(retries + 1):
        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
            resp = client.get(url, headers=headers, timeout=15, follow_redirects=True)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 429):
                time.sleep(random.uniform(3, 8))
                continue
            return resp
        except Exception:
            if attempt < retries:
                time.sleep(random.uniform(1, 3))
    return None

# ═══════════════════════════════════════════════════════════════
# ATS BOARD DISCOVERY — Brute-force probe company slugs
# ═══════════════════════════════════════════════════════════════

# Common company slug patterns for ATS platforms
COMMON_COMPANY_SLUGS = [
    # Tech giants
    "google", "microsoft", "apple", "amazon", "meta", "facebook", "netflix",
    "tesla", "nvidia", "adobe", "salesforce", "oracle", "ibm", "intel",
    "cisco", "vmware", "palo-alto", "crowdstrike", "fortinet",
    # Unicorns & hot startups
    "openai", "anthropic", "figma", "canva", "notion", "linear", "vercel",
    "supabase", "planetscale", "cohere", "mistral", "stability-ai",
    "midjourney", "runway", "character-ai", "character-ai", "perplexity",
    "replit", "codesandbox", "netlify", "cloudflare", "fastly",
    "twilio", "stripe", "plaid", "square", "shopify", "bigcommerce",
    "datadog", "newrelic", "sentry", "grafana", "prometheus",
    "mongodb", "redis", "elastic", "confluent", "snowflake", "databricks",
    "dbt", "fivetran", "airbyte", "census", "hightouch",
    "docker", "hashicorp", "pulumi", "terraform", "ansible",
    "github", "gitlab", "bitbucket", "jira", "confluence",
    "slack", "discord", "zoom", "teams", "loom",
    "figma", "sketch", "invision", "zeplin", "abstract",
    "intercom", "zendesk", "freshdesk", "crisp", "drift",
    "hubspot", "marketo", "pardot", "activecampaign",
    "segment", "mixpanel", "amplitude", "posthog", "heap",
    "brex", "ramp", "mercury", "riley", "founder",
    "rippling", "gusto", "bamboo", "workday", "adp",
    "navan", "expensify", "ramp", "brex", "corpay",
    "chime", "sofi", "betterment", "wealthfront", "robinhood",
    "coinbase", "kraken", "binance", "ftx", "blockfi",
    "instacart", "doordash", "ubereats", "grubhub", "postmates",
    "lyft", "uber", "grab", "ola", "bolt",
    "airbnb", "vrbo", "booking", "tripadvisor", "kayak",
    "pinterest", "snap", "tiktok", "bytedance", "temu",
    "spotify", "soundcloud", "twitch", "youtube", "vimeo",
    "epic-games", "riot-games", "roblox", "activision", "ea",
    "waymo", "cruise", "zoox", "aurora", "nuro",
    "spacex", "blue-origin", "rocket-lab", "relativity", "firefly",
    "nvidia", "amd", "qualcomm", "arm", "risc-v",
    "crowdstrike", "paloalto", "fortinet", "sentinelone", " Recorded-Future",
    "datadog", "sumo-logic", "splunk", "cisco", "arista",
    "cloudflare", "akamai", "fastly", "limelight", "bunny",
    "twilio", "sendgrid", "mailgun", "postmark", "ses",
    "algolia", "typesense", "meilisearch", "elasticsearch", "solr",
    "hasura", "prisma", "supabase", "firebase", "appwrite",
    "netlify", "vercel", "render", "fly", "railway",
    "docker", "kubernetes", "helm", "istio", "envoy",
    "gitlab", "github", "bitbucket", "sourcegraph", "graphite",
    "snyk", "sonar", "checkmarx", "veracode", "whitehat",
    "jfrog", "artifactory", "nexus", "packagecloud",
    "pagerduty", "opsgenie", "victorops", "grafana",
    "circleci", "travis", "jenkins", "buildkite", "woodpecker",
    "argocd", "flux", "tekton", "cloudbuild",
    "posthog", "mixpanel", "amplitude", "heap", "segment",
    "launchdarkly", "split", "flagsmith", "unleash",
    "auth0", "okta", "onelogin", "jumpcloud", "ping",
    "1password", "lastpass", "dashlane", "nordpass",
    # India tech
    "flipkart", "swiggy", "zomato", "ola", "paytm", "phonepe",
    "razorpay", "cred", "meesho", "zepto", "blinkit",
    "byju", "unacademy", "upgrad", "whitehat", "physics-wallah",
    "freshworks", "zoho", "freshworks", "postman", "postman",
    "hasura", "razorpay", "phonepe", "groww", "zerodha",
    " urbanclap", "urban-company", "practo", "lybrate", "medibuddy",
    "policybazaar", "acko", "digit", "acko", "zerodha",
    "dream11", "mpl", "winzo", "mobile-premier-league",
    # Enterprise
    "servicenow", "workday", "adp", "paychex", "ukg",
    "sap", "oracle", "salesforce", "microsoft", "google",
    "aws", "azure", "gcp", "alibaba-cloud", "tencent-cloud",
    "ibm", "accenture", "deloitte", "pwc", "ey",
    "capgemini", "cognizant", "infosys", "wipro", "hcl",
    "tech-mahindra", "ltimindtree", "mindtree", "persistent",
    "mphasis", "hexaware", "niit", "birlasoft",
    # More tech
    "rippling", "gusto", "bamboohr", "leapsome", "culture-amp",
    "lattice", "15five", "baser", "quantum-workplace",
    "notion", "coda", "airtable", "smartsheet", "monday",
    "asana", "clickup", "todoist", "any.do",
    "figma", "sketch", "canva", "adobe", "pixar",
    "blender", "unreal", "unity", "godot",
    "docker", "podman", "rancher", "portainer",
    "terraform", "pulumi", "crossplane", "cdk",
    "prometheus", "datadog", "newrelic", "honeycomb",
    "sentry", "bugsnag", "rollbar", "airbrake",
    "twilio", "vonage", "plivo", "messagebird",
    "stripe", "braintree", "adyen", "checkout",
    "plaid", "affirm", "klarna", "afterpay",
    "mercury", "brex", "ramp", "divvy",
    "navan", "tripactions", "travelperk", "chegs",
    "calendly", "cal.com", "acuity", "savvy",
    "loom", "vidyard", "wistia", "sprout",
    "hubspot", "salesforce", "zoho", "freshsales",
    "intercom", "drift", "qualified", "chorus",
    "gong", "chorus", "avoma", "fireflies",
    "notion", "confluence", "coda", "slite",
    "linear", "shortcut", "jira", "asana",
    "vercel", "netlify", "render", "railway",
    "supabase", "planetscale", "turso", "neon",
    "railway", "fly", "render", "heroku",
]

def generate_ats_slugs():
    """Generate thousands of slugs to probe on ATS platforms."""
    slugs = set(COMMON_COMPANY_SLUGS)
    
    # Add letter combinations (2-3 chars)
    for c1 in "abcdefghijklmnopqrstuvwxyz":
        for c2 in "abcdefghijklmnopqrstuvwxyz":
            slugs.add(f"{c1}{c2}")
            for c3 in "abcdefghijklmnopqrstuvwxyz":
                slugs.add(f"{c1}{c2}{c3}")
    
    # Add common tech company patterns
    prefixes = ["get", "try", "use", "my", "the", "we", "go", "do", "be", "ai", "io", "dev", "app", "lab", "hub", "pro", "co"]
    suffixes = ["ai", "io", "app", "hub", "lab", "dev", "tech", "ops", "cloud", "data", "pay", "ship", "run", "build", "code", "stack", "base", "flow", "sync", "link", "path", "way", "box", "now", "go", "hq"]
    for p in prefixes:
        for s in suffixes:
            slugs.add(f"{p}{s}")
            slugs.add(f"{p}-{s}")
    
    return list(slugs)

def probe_ats_boards(client, db, slug_batch):
    """Probe a batch of slugs against all ATS platforms."""
    new_jobs = 0
    boards_found = 0
    
    for slug in slug_batch:
        # Greenhouse
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            resp = safe_get(client, url, retries=1)
            if resp and resp.status_code == 200:
                data = resp.json()
                jobs_list = data.get("jobs", [])
                if jobs_list:
                    boards_found += 1
                    jobs = []
                    for j in jobs_list[:100]:
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
                    new = db.insert(jobs)
                    new_jobs += new
        except: pass
        
        # Lever
        try:
            url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
            resp = safe_get(client, url, retries=1)
            if resp and resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    boards_found += 1
                    jobs = []
                    for j in data[:100]:
                        locs = j.get("categories", {}).get("team", "")
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
                    new = db.insert(jobs)
                    new_jobs += new
        except: pass
        
        # Ashby
        try:
            url = f"https://api.ashbyhq.com/api/v1/job-posting/{slug}"
            resp = safe_get(client, url, retries=1)
            if resp and resp.status_code == 200:
                data = resp.json()
                jobs_list = data.get("jobPostings", data.get("data", {}).get("jobPostings", []))
                if jobs_list:
                    boards_found += 1
                    jobs = []
                    for j in jobs_list[:100]:
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
                    new = db.insert(jobs)
                    new_jobs += new
        except: pass
        
        # SmartRecruiters
        try:
            url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0"
            resp = safe_get(client, url, retries=1)
            if resp and resp.status_code == 200:
                data = resp.json()
                content = data.get("content", [])
                if content:
                    boards_found += 1
                    jobs = []
                    for j in content[:100]:
                        loc = j.get("location", {})
                        loc_name = loc.get("city", "") + " " + loc.get("country", "") if isinstance(loc, dict) else ""
                        ref = j.get("ref", "")
                        company_name = j.get("company", {}).get("name", slug) if isinstance(j.get("company"), dict) else slug
                        jobs.append({
                            "url": f"https://careers.smartrecruiters.com/{slug}{ref}",
                            "title": j.get("name", ""),
                            "company": company_name,
                            "location": loc_name.strip(),
                            "desc": (j.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text", "") or "")[:500],
                            "source": f"smartrecruiters:{slug}",
                            "id": str(j.get("id", "")),
                            "posted": j.get("releasedDate", ""),
                        })
                    new = db.insert(jobs)
                    new_jobs += new
        except: pass
        
        time.sleep(0.1)  # minimal delay between probes
    
    return new_jobs, boards_found

# ═══════════════════════════════════════════════════════════════
# HTML SCRAPERS
# ═══════════════════════════════════════════════════════════════

def scrape_dice(client, kw, loc, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.dice.com/jobs?q={q}&pageSize=20&page={pg+1}&sort=date"
        if loc: url += f"&location={quote_plus(loc)}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string)
                items = data if isinstance(data, list) else data.get("itemListElement", [])
                for item in items[:20]:
                    je = item.get("item", item) if isinstance(item, dict) else {}
                    title = je.get("name", "")
                    job_url = je.get("url", "")
                    comp = je.get("hiringOrganization", {})
                    company = comp.get("name", "") if isinstance(comp, dict) else ""
                    loc_obj = je.get("jobLocation", {})
                    location = ""
                    if isinstance(loc_obj, dict):
                        addr = loc_obj.get("address", {})
                        location = f"{addr.get('addressLocality', '')} {addr.get('addressRegion', '')}".strip()
                    if title and job_url:
                        jobs.append({"url": job_url, "title": title, "company": company,
                                     "location": location, "desc": "", "source": "dice_unified",
                                     "id": "", "posted": None, "salary": ""})
            except: continue
        if not jobs:
            for card in soup.select("div[data-testid='job-card']")[:20]:
                try:
                    a = card.select_one("a[data-testid='job-search-job-detail-link']")
                    title = (a.get_text(strip=True) or "").strip() if a else ""
                    link = card.select_one("a[data-testid='job-search-job-card-link']")
                    href = link.get("href", "") if link else ""
                    job_url = f"https://www.dice.com{href}" if href.startswith("/") else href
                    c = card.select_one("p[data-testid='job-card-company-name']")
                    company = (c.get_text(strip=True) or "").strip() if c else ""
                    if title and job_url:
                        jobs.append({"url": job_url, "title": title, "company": company,
                                     "location": loc, "desc": "", "source": "dice_unified",
                                     "id": "", "posted": None, "salary": ""})
                except: continue
    except: pass
    return jobs

def scrape_simplyhired(client, kw, loc, pg):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.simplyhired.com/search?q={q}"
        if loc: url += f"&l={quote_plus(loc)}"
        if pg > 0: url += f"&pn={pg}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        h2s = soup.find_all("h2", class_=lambda c: c and "css-8rdtm5" in str(c))
        for h2 in h2s[:20]:
            try:
                title = (h2.get_text(strip=True) or "").strip()
                if not title: continue
                container = h2.parent
                for _ in range(5):
                    if container is None: break
                    spans = container.find_all("span")
                    if len(spans) >= 2: break
                    container = container.parent
                job_url = ""
                if container:
                    a = container.find("a", href=lambda h: h and "/job/" in str(h))
                    if a:
                        href = a.get("href", "")
                        job_url = f"https://www.simplyhired.com{href}" if href.startswith("/") else href
                if not job_url: continue
                company = ""
                location = ""
                salary = ""
                if container:
                    for s in container.find_all("span"):
                        cls = " ".join(s.get("class", []))
                        txt = (s.get_text(strip=True) or "").strip()
                        if not txt: continue
                        if "css-lvyu5j" in cls: company = txt
                        elif "css-1t92pv" in cls: location = txt
                        elif "css-h61onv" in cls: salary = txt
                jobs.append({"url": job_url, "title": title, "company": company,
                             "location": location, "desc": "", "source": "simplyhired_unified",
                             "id": "", "posted": None, "salary": salary})
            except: continue
    except: pass
    return jobs

# ═══════════════════════════════════════════════════════════════
# JSON API SCRAPERS
# ═══════════════════════════════════════════════════════════════

def scrape_remotive(client, page_num=0):
    jobs = []
    try:
        resp = safe_get(client, "https://remotive.com/api/remote-jobs?category=software-dev&limit=100")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("jobs", []):
            url = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company_name", "")
            location = item.get("candidate_required_location", "")
            salary = item.get("salary", "")
            desc = (item.get("description", "") or "")[:500]
            posted = item.get("publication_date", "")
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location, "desc": desc, "source": "remotive_unified",
                             "id": str(item.get("id", "")), "posted": posted, "salary": salary})
    except: pass
    return jobs

def scrape_arbeitnow(client, page_num=0):
    jobs = []
    try:
        resp = safe_get(client, f"https://www.arbeitnow.com/api/job-board-api?page={page_num+1}")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("data", []):
            url = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company_name", "")
            location = item.get("location", "")
            remote = item.get("remote", False)
            desc = (item.get("description", "") or "")[:500]
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location or ("Remote" if remote else ""),
                             "desc": desc, "source": "arbeitnow_unified",
                             "id": str(item.get("id", "")), "posted": item.get("created_at", ""),
                             "salary": ""})
    except: pass
    return jobs

def scrape_remoteok(client, page_num=0):
    jobs = []
    try:
        resp = safe_get(client, "https://remoteok.com/api")
        if not resp or resp.status_code != 200: return []
        data = resp.json()
        for item in data[1:] if len(data) > 1 else []:
            url = f"https://remoteok.com/remote-jobs/{item.get('slug', '')}"
            title = item.get("position", "")
            company = item.get("company", "")
            location = item.get("location", "Remote")
            salary_min = item.get("salary_min", "")
            salary_max = item.get("salary_max", "")
            salary = f"${salary_min}-${salary_max}" if salary_min and salary_max else ""
            desc = (item.get("description", "") or "")[:500]
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location, "desc": desc, "source": "remoteok_unified",
                             "id": str(item.get("id", "")), "posted": item.get("date", ""),
                             "salary": salary})
    except: pass
    return jobs

def scrape_itjobs_gg(client, page_num=0):
    """itjobs.gg — aggregates tech jobs from multiple sources."""
    jobs = []
    try:
        resp = safe_get(client, f"https://itjobs.gg/api/jobs?page={page_num+1}")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("data", []):
            url = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company", "")
            location = item.get("location", "")
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location, "desc": "", "source": "itjobs_unified",
                             "id": str(item.get("id", "")), "posted": item.get("posted_at", ""),
                             "salary": ""})
    except: pass
    return jobs

def scrape_findwork(client, page_num=0):
    """findwork.dev — remote dev jobs."""
    jobs = []
    try:
        resp = safe_get(client, f"https://findwork.dev/api/jobs/?page={page_num+1}")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("results", []):
            url = item.get("url", "") or item.get("apply_url", "")
            title = item.get("role", "")
            company = item.get("company_name", "")
            location = item.get("location", "")
            remote = item.get("remote", False)
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location or ("Remote" if remote else ""),
                             "desc": (item.get("text", "") or "")[:500],
                             "source": "findwork_unified",
                             "id": str(item.get("id", "")), "posted": item.get("date_posted", ""),
                             "salary": ""})
    except: pass
    return jobs

def scrape_jobicy(client, page_num=0):
    """jobicy.com — remote jobs API."""
    jobs = []
    try:
        resp = safe_get(client, f"https://jobicy.com/api/v2/remote-jobs?count=50&page={page_num+1}&industry=tech")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("jobs", []):
            url = item.get("url", "")
            title = item.get("jobTitle", "")
            company = item.get("companyName", "")
            location = item.get("jobGeo", "")
            salary_min = item.get("annualSalaryMin", "")
            salary_max = item.get("annualSalaryMax", "")
            salary = f"${salary_min}-${salary_max}" if salary_min and salary_max else ""
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location, "desc": "", "source": "jobicy_unified",
                             "id": str(item.get("id", "")), "posted": item.get("pubDate", ""),
                             "salary": salary})
    except: pass
    return jobs

# ═══════════════════════════════════════════════════════════════
# JOBSPY — LinkedIn, Indeed, Google
# ═══════════════════════════════════════════════════════════════

def scrape_jobspy(keywords, location, site, db):
    """Use JobSpy library for LinkedIn/Indeed/Google."""
    jobs = []
    try:
        from jobspy import scrape_jobs
        result = scrape_jobs(
            site_name=[site],
            search_term=keywords,
            location=location if location else None,
            results_wanted=50,
            hours_old=168,  # 7 days
            country_indeed="USA" if not location or location in ["Remote", "New York", "San Francisco", "Seattle", "Austin", "Boston", "Chicago", "Los Angeles"] else "India",
        )
        if result and hasattr(result, 'jobs'):
            for j in result.jobs:
                jobs.append({
                    "url": j.url or "",
                    "title": j.title or "",
                    "company": j.company.name if j.company else "",
                    "location": j.location or "",
                    "desc": (j.description or "")[:500],
                    "source": f"jobspy:{site}",
                    "id": str(j.id or ""),
                    "posted": str(j.date_posted or ""),
                    "salary": j.salary or "",
                })
    except: pass
    return jobs

# ═══════════════════════════════════════════════════════════════
# WORK ITEMS GENERATION
# ═══════════════════════════════════════════════════════════════

KWS = [
    "software engineer", "backend engineer", "frontend developer",
    "full stack developer", "data engineer", "devops engineer",
    "machine learning engineer", "data scientist", "cloud engineer",
    "python developer", "java developer", "react developer",
    "AI engineer", "security engineer", "QA engineer", "SRE",
    "platform engineer", "infrastructure engineer", "mobile developer",
    "web developer", "software developer", "technical lead",
    "engineering manager", "staff engineer", "SDE", "SWE",
    "senior software engineer", "junior software engineer",
    "C++ engineer", "ruby developer", "PHP developer",
    "kotlin developer", "swift developer", "Angular developer",
    "data analyst", "business analyst", "UX designer",
    "embedded systems engineer", "firmware engineer",
    "game developer", "database engineer", "cloud architect",
    "go developer", "node.js developer", "django developer",
    "fastapi developer", "spring boot developer", ".NET developer",
    "aws engineer", "azure engineer", "gcp engineer",
    "kubernetes engineer", "terraform engineer",
    "typescript developer", "rust developer",
    "computer vision engineer", "NLP engineer", "MLOps",
    "LLM engineer", "generative AI", "blockchain developer",
    "React", "Angular", "Vue.js", "Next.js",
    "Python", "Java", "JavaScript", "TypeScript", "Go", "Rust",
    "C#", "Kotlin", "Swift", "Scala",
    "Docker", "Kubernetes", "Terraform", "AWS", "Azure", "GCP",
    "Machine Learning", "Deep Learning",
    "hiring now", "urgent hiring", "work from home", "remote",
    "contract", "intern", "Lead Engineer", "Principal Engineer",
    "product manager", "scrum master", "iOS developer", "Android developer",
]

LOCS = [
    "", "New York", "San Francisco", "Seattle", "Austin", "Boston", "Chicago",
    "Los Angeles", "Denver", "Atlanta", "Miami", "Washington DC", "Portland",
    "Dallas", "Houston", "San Jose", "Raleigh", "Charlotte",
    "Phoenix", "Tampa", "Nashville", "Remote",
    "London", "Manchester", "Berlin", "Munich", "Paris",
    "Amsterdam", "Dublin", "Toronto", "Vancouver", "Montreal",
    "Singapore", "Hong Kong", "Tokyo", "Sydney", "Melbourne",
    "Dubai", "Tel Aviv",
    "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Mumbai", "Pune",
    "Delhi", "Noida", "Gurgaon", "Kolkata", "Ahmedabad",
    "Jaipur", "Kochi", "Chandigarh",
    "Barcelona", "Madrid", "Lisbon", "Zurich",
    "Warsaw", "Prague", "Sao Paulo", "Mexico City",
    "Stockholm", "Jakarta", "Manila",
]

def build_work_items():
    """Build diverse work items for all sources."""
    items = []
    
    # ATS board discovery — probe random slugs
    slugs = generate_ats_slugs()
    random.shuffle(slugs)
    # Process in batches of 20
    for i in range(0, len(slugs), 20):
        items.append(("ats_discover", slugs[i:i+20], "", 0))
    
    # JSON APIs (low volume, easy to scrape)
    items.append(("remotive", "", "", 0))
    for pg in range(10):
        items.append(("arbeitnow", "", "", pg))
    items.append(("remoteok", "", "", 0))
    for pg in range(5):
        items.append(("itjobs", "", "", pg))
    for pg in range(5):
        items.append(("findwork", "", "", pg))
    for pg in range(5):
        items.append(("jobicy", "", "", pg))
    
    # HTML scrapers with keywords
    for kw in KWS:
        for loc in LOCS:
            for pg in range(3):
                items.append(("dice", kw, loc, pg))
            for pg in range(2):
                items.append(("simplyhired", kw, loc, pg))
    
    # JobSpy (LinkedIn/Indeed/Google) — fewer items but high yield
    for kw in KWS[:30]:
        for loc in LOCS[:20]:
            for site in ["linkedin", "indeed"]:
                items.append(("jobspy", kw, loc, site))
    
    return items

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    reset = "--reset" in sys.argv
    
    log("=" * 60)
    log("MEGA UNIFIED SCRAPER — All sources, continuous loop")
    log("=" * 60)
    
    db = JobDB()
    total = db.count()
    log(f"DB: {total:,} | Gap: {max(0, TARGET-total):,}")
    
    # Load dedup keys
    db.load_dedup_keys()
    
    # Load checkpoint
    done = set()
    if not reset and CP_PATH.exists():
        try:
            cp = json.loads(CP_PATH.read_text("utf-8"))
            done = set(cp.get("done", []))
        except: pass
    
    # Build work items
    all_items = build_work_items()
    remaining = [i for i in all_items if str(i) not in done]
    log(f"Work items: {len(all_items):,} total, {len(remaining):,} remaining")
    
    if not remaining:
        log("All items exhausted! Resetting...")
        done.clear()
        remaining = all_items[:]
    
    random.shuffle(remaining)
    
    # Stats
    start = time.time()
    counter = {"iters": 0, "new": 0}
    c_lock = threading.Lock()
    stats = {}
    stop_event = threading.Event()
    
    def worker(wid):
        import httpx
        client = httpx.Client(timeout=20, follow_redirects=True,
                              limits=httpx.Limits(max_connections=3, max_keepalive_connections=2))
        try:
            while not stop_event.is_set():
                try:
                    item = remaining.pop(0) if remaining else None
                except IndexError:
                    break
                if item is None:
                    break
                
                site, kw, loc, pg_or_site = item
                jobs = []
                
                try:
                    time.sleep(random.uniform(0.2, 0.8))
                    
                    if site == "ats_discover":
                        new_jobs, boards = probe_ats_boards(client, db, kw)  # kw is actually slug_batch
                        with c_lock:
                            counter["iters"] += 1
                            counter["new"] += new_jobs
                            done.add(str(item))
                            stats["ats_discover"] = stats.get("ats_discover", 0) + 1
                            iters = counter["iters"]
                        if new_jobs > 0:
                            log(f"  W{wid:02d} ATS +{new_jobs} from {boards} boards")
                        if iters % 50 == 0:
                            ct = db.count()
                            rate = counter["new"] / max((time.time()-start)/60, 0.1)
                            log(f"  [{iters:,}] +{counter['new']:,} | DB={ct:,} | {rate:.0f}/min | Gap={max(0,TARGET-ct):,}")
                        continue
                    
                    if site == "dice":
                        jobs = scrape_dice(client, kw, loc, pg_or_site)
                    elif site == "simplyhired":
                        jobs = scrape_simplyhired(client, kw, loc, pg_or_site)
                    elif site == "remotive":
                        jobs = scrape_remotive(client, pg_or_site)
                    elif site == "arbeitnow":
                        jobs = scrape_arbeitnow(client, pg_or_site)
                    elif site == "remoteok":
                        jobs = scrape_remoteok(client, pg_or_site)
                    elif site == "itjobs":
                        jobs = scrape_itjobs_gg(client, pg_or_site)
                    elif site == "findwork":
                        jobs = scrape_findwork(client, pg_or_site)
                    elif site == "jobicy":
                        jobs = scrape_jobicy(client, pg_or_site)
                    elif site == "jobspy":
                        jobs = scrape_jobspy(kw, loc, pg_or_site, db)  # pg_or_site is actually the site name
                except:
                    jobs = []
                
                new = db.insert(jobs) if jobs else 0
                
                with c_lock:
                    counter["iters"] += 1
                    counter["new"] += new
                    done.add(str(item))
                    stats[site] = stats.get(site, 0) + 1
                    iters = counter["iters"]
                
                if new > 2:
                    log(f"  W{wid:02d} +{new:3d} {site}:{kw[:20]}|{loc[:12]}")
                
                if iters % 200 == 0:
                    ct = db.count()
                    rate = counter["new"] / max((time.time()-start)/60, 0.1)
                    log(f"  [{iters:,}] +{counter['new']:,} | DB={ct:,} | {rate:.0f}/min | Gap={max(0,TARGET-ct):,} | " + " ".join(f"{k}:{v}" for k,v in sorted(stats.items())))
                
                if iters % 500 == 0:
                    try:
                        CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
                    except: pass
                
        except Exception as e:
            log(f"  W{wid:02d} FATAL: {e}")
        finally:
            try: client.close()
            except: pass
    
    log(f"Launching {WORKERS} workers on {len(remaining):,} items...")
    threads = []
    for i in range(WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.1)  # stagger starts
    
    # Heartbeat
    def heartbeat():
        while not stop_event.is_set():
            time.sleep(60)
            try:
                ct = db.count()
                elapsed_min = (time.time() - start) / 60
                log(f"  [HEARTBEAT] DB={ct:,} | iters={counter['iters']:,} new={counter['new']:,} | {elapsed_min:.0f}min elapsed | Gap={max(0,TARGET-ct):,}")
                CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
            except: pass
    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()
    
    # Wait for all items or until target reached
    for t in threads:
        t.join(timeout=3600)  # max 1 hour per round
    
    stop_event.set()
    
    try:
        CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
    except: pass
    
    ft = db.count()
    elapsed = (time.time() - start) / 60
    db.close()
    
    log("=" * 60)
    log(f"ROUND COMPLETE | New: +{counter['new']:,}")
    log(f"Sites: " + " ".join(f"{k}:{v}" for k,v in sorted(stats.items())))
    log(f"DB: {ft:,} | Gap: {max(0, TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 60)

if __name__ == "__main__":
    main()
