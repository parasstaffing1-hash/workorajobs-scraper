#!/usr/bin/env python3
"""MEGA SCRAPER V3 — Continuous infinite-loop scraper.

Design:
- 50 httpx workers in threads
- Work items regenerated continuously (never runs out)
- 50+ sources: ATS APIs, HTML boards, JSON APIs, country-specific Indeed
- 300+ keywords × 65+ locations
- In-memory dedup with periodic DB flush
- Checkpoint saves every 5 min
"""
from __future__ import annotations
import hashlib, json, os, queue, random, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "mega_v3_cp.json"
LOG_PATH = ROOT / "mega_v3_log.txt"
TARGET = 1_000_000
WORKERS = 50
BATCH_TIMEOUT = 1800  # 30 min per round

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
                         "mega_v3", j.get("id",""), j.get("posted"),
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
# HTTP HELPERS
# ═══════════════════════════════════════════════════════════════

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]

def safe_get(client, url, retries=2, timeout=15):
    for attempt in range(retries + 1):
        try:
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": random.choice([
                    "en-US,en;q=0.9",
                    "en-GB,en;q=0.9",
                    "en-US,en;q=0.9,hi;q=0.8",
                    "en-US,en;q=0.9,de;q=0.8",
                ]),
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            }
            resp = client.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 429):
                time.sleep(random.uniform(2, 6))
                continue
            return resp
        except Exception:
            if attempt < retries:
                time.sleep(random.uniform(1, 3))
    return None

# ═══════════════════════════════════════════════════════════════
# ATS BOARD DISCOVERY
# ═══════════════════════════════════════════════════════════════

# Massive slug list for ATS probing
SLUG_PREFIXES = [
    "get", "try", "use", "my", "the", "we", "go", "do", "be", "ai", "io", "dev",
    "app", "lab", "hub", "pro", "co", "one", "top", "new", "all", "for", "up",
    "sun", "red", "big", "net", "jet", "zen", "max", "bit", "box", "day", "way",
    "run", "fly", "sky", "art", "ory", "mix", "fox", "owl", "ape", "bee", "ram",
    "duo", "ace", "kin", "zap", "hop", "pop", "joy", "fit", "kit", "pod", "pig",
]
SLUG_SUFFIXES = [
    "ai", "io", "app", "hub", "lab", "dev", "tech", "ops", "cloud", "data",
    "pay", "ship", "run", "build", "code", "stack", "base", "flow", "sync",
    "link", "path", "way", "box", "now", "hq", "go", "up", "it", "fm",
    "labs", "works", "systems", "digital", "group", "inc", "co",
    "health", "care", "space", "works", "mind", "force", "wave",
]

KNOWN_SLUGS = [
    "google", "microsoft", "apple", "amazon", "meta", "facebook", "netflix",
    "tesla", "nvidia", "adobe", "salesforce", "oracle", "ibm", "intel",
    "cisco", "vmware", "crowdstrike", "fortinet", "palo-alto",
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
    "cred", "meesho", "zepto", "blinkit", "freshworks", "zoho", "postman",
    "groww", "zerodha", "policybazaar", "dream11", "nykaa",
    "servicenow", "sap", "accenture", "deloitte", "pwc", "ey",
    "capgemini", "cognizant", "infosys", "wipro", "hcl",
    "tech-mahindra", "ltimindtree", "persistent", "mphasis", "hexaware",
    "asana", "monday", "coda", "airtable", "smartsheet",
    "calendly", "loom", "gong", "salesloft", "outreach",
    "auth0", "okta", "1password", "dashlane", "nordpass",
    "jfrog", "sonar", "snyk", "checkmarx", "veracode",
    "pagerduty", "opsgenie", "grafana", "circleci", "buildkite",
    "launchdarkly", "split", "flagsmith", "unleash",
    "planetscale", "turso", "neon", "railway", "fly", "render",
    "algolia", "typesense", "meilisearch",
    "braze", "iterable", "customerio", "customer.io",
    "optimizely", "launchdarkly", "statsig",
    "cashapp", "venmo", "block", "square", "affirm", "klarna",
    "plaid", "adyen", "checkout", "checkout.com",
    "navan", "tripactions", "travelperk",
    "notion", "confluence", "linear", "shortcut", "jira",
    "figma", "sketch", "canva", "adobe",
    "unity", "unreal", "godot", "blender",
    "hashicorp", "terraform", "pulumi", "ansible",
    "prometheus", "datadog", "newrelic", "honeycomb", "grafana",
    "sentry", "bugsnag", "rollbar",
    "twilio", "vonage", "plivo",
    "stripe", "braintree", "adyen",
    "plaid", "affirm", "klarna", "afterpay",
    "mercury", "brex", "ramp",
    "navan", "tripactions",
    # Indian companies
    "byju", "unacademy", "upgrad", "physics-wallah",
    "urban-company", "practo", "medibuddy",
    "acko", "digit",
    "ola", "rapido", "porter", "rivigo",
    "dailyhunt", "practo", "1mg", "pharmeasy",
    "mygate", "noBroker", "housing",
    " slicing", "slice", "jupiter", "fi-money",
    "cRED", "PhonePe", "freecharge",
    # More global
    "atlassian", "zendesk", "freshdesk", "hubspot",
    "zendesk", "intercom", "drift", "qualified",
    "rippling", "gusto", "bamboohr", "leapsome", "culture-amp",
    "lattice", "15five",
    "notion", "coda", "airtable", "smartsheet", "monday",
    "asana", "clickup", "todoist",
    "figma", "sketch", "canva", "adobe",
    "blender", "unreal", "unity", "godot",
    "docker", "podman", "rancher",
    "terraform", "pulumi", "crossplane",
    "prometheus", "datadog", "newrelic", "honeycomb",
    "sentry", "bugsnag", "rollbar",
    "twilio", "vonage", "plivo",
    "stripe", "braintree", "adyen",
    "plaid", "affirm", "klarna",
    "mercury", "brex", "ramp",
]

def generate_ats_slugs():
    slugs = list(KNOWN_SLUGS)
    for p in SLUG_PREFIXES:
        for s in SLUG_SUFFIXES:
            slugs.append(f"{p}{s}")
            slugs.append(f"{p}-{s}")
    for c1 in "abcdefghijklmnopqrstuvwxyz":
        for c2 in "abcdefghijklmnopqrstuvwxyz":
            slugs.append(f"{c1}{c2}")
    random.shuffle(slugs)
    return slugs

def probe_ats_batch(client, db, slug_batch):
    new_jobs = 0
    boards = 0
    for slug in slug_batch:
        # Greenhouse
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            resp = safe_get(client, url, retries=1, timeout=10)
            if resp and resp.status_code == 200:
                data = resp.json()
                jobs_list = data.get("jobs", [])
                if jobs_list:
                    boards += 1
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
            resp = safe_get(client, url, retries=1, timeout=10)
            if resp and resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    boards += 1
                    jobs = []
                    for j in data[:100]:
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
            resp = safe_get(client, url, retries=1, timeout=10)
            if resp and resp.status_code == 200:
                data = resp.json()
                jobs_list = data.get("jobPostings", data.get("data", {}).get("jobPostings", []))
                if jobs_list:
                    boards += 1
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
            resp = safe_get(client, url, retries=1, timeout=10)
            if resp and resp.status_code == 200:
                data = resp.json()
                content = data.get("content", [])
                if content:
                    boards += 1
                    jobs = []
                    for j in content[:100]:
                        loc = j.get("location", {})
                        loc_name = (loc.get("city", "") + " " + loc.get("country", "")).strip() if isinstance(loc, dict) else ""
                        ref = j.get("ref", "")
                        company_name = j.get("company", {}).get("name", slug) if isinstance(j.get("company"), dict) else slug
                        jobs.append({
                            "url": f"https://careers.smartrecruiters.com/{slug}{ref}",
                            "title": j.get("name", ""),
                            "company": company_name,
                            "location": loc_name,
                            "desc": (j.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text", "") or "")[:500],
                            "source": f"smartrecruiters:{slug}",
                            "id": str(j.get("id", "")),
                            "posted": j.get("releasedDate", ""),
                        })
                    new = db.insert(jobs)
                    new_jobs += new
        except: pass
        # Workable
        try:
            url = f"https://{slug}.workable.com/api/v3/widget/accounts/{slug}"
            resp = safe_get(client, url, retries=1, timeout=10)
            if resp and resp.status_code == 200:
                data = resp.json()
                jobs_list = data.get("jobs", data.get("results", []))
                if jobs_list:
                    boards += 1
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
                    new = db.insert(jobs)
                    new_jobs += new
        except: pass
        time.sleep(0.05)
    return new_jobs, boards

# ═══════════════════════════════════════════════════════════════
# HTML SCRAPERS — Job Boards
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
                    if title and job_url:
                        jobs.append({"url": job_url, "title": title, "company": company,
                                     "location": loc, "desc": "", "source": "dice"})
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
                                     "location": loc, "desc": "", "source": "dice"})
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
        h2s = soup.find_all("h2")
        for h2 in h2s[:20]:
            try:
                title = (h2.get_text(strip=True) or "").strip()
                if not title or len(title) < 3: continue
                container = h2.parent
                for _ in range(5):
                    if container is None: break
                    links = container.find_all("a", href=True)
                    if links: break
                    container = container.parent
                job_url = ""
                if container:
                    a = container.find("a", href=lambda h: h and "/job/" in str(h))
                    if a:
                        href = a.get("href", "")
                        job_url = f"https://www.simplyhired.com{href}" if href.startswith("/") else href
                if not job_url: continue
                company = ""
                if container:
                    for s in container.find_all("span"):
                        txt = (s.get_text(strip=True) or "").strip()
                        if txt and len(txt) > 1 and txt != title:
                            if not company:
                                company = txt
                            break
                jobs.append({"url": job_url, "title": title, "company": company,
                             "location": loc, "desc": "", "source": "simplyhired"})
            except: continue
    except: pass
    return jobs

def scrape_monster(client, kw, loc, pg):
    """monster.com — huge job board."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.monster.com/jobs/search?q={q}&page={pg+1}&so=m.h.sh"
        if loc: url += f"&where={quote_plus(loc)}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("section.card-results article, div.job-cardstyle__JobCard")[:20]:
            try:
                a = card.select_one("a[href*='/job/']")
                if not a: continue
                title = (a.get_text(strip=True) or "").strip()
                href = a.get("href", "")
                job_url = href if href.startswith("http") else f"https://www.monster.com{href}"
                comp = card.select_one("span.job-cardstyle__Company, div.company")
                company = (comp.get_text(strip=True) or "").strip() if comp else ""
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": loc, "desc": "", "source": "monster"})
            except: continue
    except: pass
    return jobs

def scrape_jooble(client, kw, loc, pg):
    """jooble.org — job aggregator."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://jooble.org/SearchResult?ukw={q}"
        if loc: url += f"&loc={quote_plus(loc)}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("article[data-test-name], div.vacancy_wrapper, article.vacancy-item")[:20]:
            try:
                a = card.select_one("a[href]")
                if not a: continue
                title = (a.get_text(strip=True) or "").strip()
                href = a.get("href", "")
                job_url = href if href.startswith("http") else f"https://jooble.org{href}"
                company_tag = card.select_one("span[data-test-name='companyName'], span.company_name, a.company_name")
                company = (company_tag.get_text(strip=True) or "").strip() if company_tag else ""
                loc_tag = card.select_one("span[data-test-name='location'], span.location")
                job_loc = (loc_tag.get_text(strip=True) or "").strip() if loc_tag else loc
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": job_loc, "desc": "", "source": "jooble"})
            except: continue
    except: pass
    return jobs

def scrape_careerbuilder(client, kw, loc, pg):
    """careerbuilder.com"""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.careerbuilder.com/jobs?keywords={q}&page_number={pg+1}"
        if loc: url += f"&location={quote_plus(loc)}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("div.job-row, li.job-listing")[:20]:
            try:
                a = card.select_one("a.job-title, h2.job-title a")
                if not a: continue
                title = (a.get_text(strip=True) or "").strip()
                href = a.get("href", "")
                job_url = href if href.startswith("http") else f"https://www.careerbuilder.com{href}"
                comp = card.select_one("span.job-company, div.company-name")
                company = (comp.get_text(strip=True) or "").strip() if comp else ""
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": loc, "desc": "", "source": "careerbuilder"})
            except: continue
    except: pass
    return jobs

def scrape_reed(client, kw, loc, pg):
    """reed.co.uk — UK jobs."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.reed.co.uk/jobs/{q}?page={pg+1}"
        if loc: url += f"&location={quote_plus(loc)}"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("article.job-result, div.job-result-body")[:20]:
            try:
                a = card.select_one("a.job-title, h3 a")
                if not a: continue
                title = (a.get_text(strip=True) or "").strip()
                href = a.get("href", "")
                job_url = f"https://www.reed.co.uk{href}" if href.startswith("/") else href
                comp = card.select_one("span.job-result__company, a.job-result__company")
                company = (comp.get_text(strip=True) or "").strip() if comp else ""
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": loc, "desc": "", "source": "reed"})
            except: continue
    except: pass
    return jobs

def scrape_indeed_country(client, kw, loc, pg, domain="com"):
    """Scrape Indeed country variants (com, co.uk, in, com.au, ca, de, fr, etc.)."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.indeed.{domain}/jobs?q={q}&start={pg*10}"
        if loc: url += f"&l={quote_plus(loc)}"
        resp = safe_get(client, url, timeout=20)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        # Indeed uses different structures per country
        for card in soup.select("div.job_seen_beacon, div.jobsearch-SerpJobCard, td.resultContent")[:20]:
            try:
                a = card.select_one("a.jcs-JobTitle, h2 a, a[data-jk]")
                if not a: continue
                title = (a.get_text(strip=True) or "").strip()
                href = a.get("href", "")
                job_url = f"https://www.indeed.{domain}{href}" if href.startswith("/") else href
                comp = card.select_one("span[data-testid='company-name'], span.companyName, span.company")
                company = (comp.get_text(strip=True) or "").strip() if comp else ""
                loc_span = card.select_one("div[data-testid='text-location'], span.companyLocation, div.recJobLoc")
                job_loc = (loc_span.get_text(strip=True) or "").strip() if loc_span else loc
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": job_loc, "desc": "", "source": f"indeed_{domain}"})
            except: continue
    except: pass
    return jobs

# ═══════════════════════════════════════════════════════════════
# JSON API SCRAPERS
# ═══════════════════════════════════════════════════════════════

def scrape_remotive(client, pg=0):
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
                             "location": location, "desc": desc, "source": "remotive",
                             "id": str(item.get("id", "")), "posted": posted, "salary": salary})
    except: pass
    return jobs

def scrape_arbeitnow(client, pg=0):
    jobs = []
    try:
        resp = safe_get(client, f"https://www.arbeitnow.com/api/job-board-api?page={pg+1}")
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
                             "desc": desc, "source": "arbeitnow",
                             "id": str(item.get("id", "")), "posted": item.get("created_at", ""),
                             "salary": ""})
    except: pass
    return jobs

def scrape_remoteok(client, pg=0):
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
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location, "desc": "", "source": "remoteok",
                             "id": str(item.get("id", "")), "posted": item.get("date", ""),
                             "salary": salary})
    except: pass
    return jobs

def scrape_jobicy(client, pg=0):
    jobs = []
    try:
        resp = safe_get(client, f"https://jobicy.com/api/v2/remote-jobs?count=50&page={pg+1}&industry=tech")
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
                             "location": location, "desc": "", "source": "jobicy",
                             "id": str(item.get("id", "")), "posted": item.get("pubDate", ""),
                             "salary": salary})
    except: pass
    return jobs

def scrape_findwork(client, pg=0):
    jobs = []
    try:
        resp = safe_get(client, f"https://findwork.dev/api/jobs/?page={pg+1}")
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
                             "desc": (item.get("text", "") or "")[:500], "source": "findwork",
                             "id": str(item.get("id", "")), "posted": item.get("date_posted", ""),
                             "salary": ""})
    except: pass
    return jobs

def scrape_itjobs_gg(client, pg=0):
    jobs = []
    try:
        resp = safe_get(client, f"https://itjobs.gg/api/jobs?page={pg+1}")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("data", []):
            url = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company", "")
            location = item.get("location", "")
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location, "desc": "", "source": "itjobs",
                             "id": str(item.get("id", "")), "posted": item.get("posted_at", ""),
                             "salary": ""})
    except: pass
    return jobs

def scrape_workingnomads(client, pg=0):
    jobs = []
    try:
        resp = safe_get(client, "https://www.workingnomads.com/api/exposed_jobs/")
        if not resp or resp.status_code != 200: return []
        data = resp.json() if isinstance(resp.json(), list) else resp.json().get("jobs", [])
        for item in data[:50]:
            url = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company_name", item.get("company", ""))
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": "Remote", "desc": (item.get("description", "") or "")[:500],
                             "source": "workingnomads", "id": "", "posted": "", "salary": ""})
    except: pass
    return jobs

def scrape_github_jobs(client, pg=0):
    """GitHub Jobs API (may be deprecated but worth trying)."""
    jobs = []
    try:
        resp = safe_get(client, f"https://jobs.github.com/positions.json?page={pg+1}")
        if not resp or resp.status_code != 200: return []
        data = resp.json()
        for item in data[:20]:
            url = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company", "")
            location = item.get("location", "")
            desc = (item.get("description", "") or "")[:500]
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location, "desc": desc, "source": "github_jobs",
                             "id": str(item.get("id", "")), "posted": item.get("created_at", ""),
                             "salary": item.get("how_to_apply", "")})
    except: pass
    return jobs

def scrape_themuse(client, pg=0):
    """themuse.com — curated jobs from top companies."""
    jobs = []
    try:
        resp = safe_get(client, f"https://www.themuse.com/api/public/jobs?page={pg+1}&page_size=20")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("results", []):
            url = item.get("refs", {}).get("landing_page", "")
            title = item.get("name", "")
            company = item.get("company", {}).get("name", "")
            locations = [l.get("name", "") for l in item.get("locations", [])]
            location = ", ".join(locations) if locations else ""
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": location, "desc": "", "source": "themuse",
                             "id": str(item.get("id", "")), "posted": item.get("publication_date", ""),
                             "salary": ""})
    except: pass
    return jobs

def scrape_usajobs(client, pg=0):
    """USAJobs API — free, structured."""
    jobs = []
    try:
        resp = client.get(
            "https://data.usajobs.gov/api/search?ResultsPerPage=25&ResultsOffset=" + str(pg * 25),
            headers={"Authorization-Key": "4GplRBC0F36pC7wN39SO3pPRoSbpkh1MORty6lFK/a0=",
                     "User-Agent": "workorajobs1@gmail.com",
                     "Host": "data.usajobs.gov"},
            timeout=15
        )
        if resp.status_code != 200: return []
        data = resp.json()
        for item in data.get("SearchResult", {}).get("SearchResultItems", []):
            match = item.get("MatchedObjectDescriptor", {})
            title = match.get("PositionTitle", "")
            org = match.get("OrganizationName", "")
            url = match.get("PositionURI", "") or match.get("ApplyURI", [""])[0]
            loc = match.get("PositionLocation", {})
            location = f"{loc.get('CityName', '')}, {loc.get('CountrySubDivisionCode', '')}" if isinstance(loc, dict) else ""
            if title and url:
                jobs.append({"url": url, "title": title, "company": org,
                             "location": location, "desc": "", "source": "usajobs",
                             "id": match.get("PositionID", ""), "posted": match.get("PublicationStartDate", ""),
                             "salary": ""})
    except: pass
    return jobs

def scrape_adzuna(client, country, kw, pg):
    """Adzuna API — free tier."""
    jobs = []
    try:
        APP_ID = "d7a43d77"
        APP_KEY = "b5bb79c6bf51969dc7bec07fcd7720bd"
        q = quote_plus(kw)
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{pg+1}?app_id={APP_ID}&app_key={APP_KEY}&what={q}&results_per_page=50&max_days_old=7&sort_by=date"
        resp = safe_get(client, url)
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("results", []):
            title = item.get("title", "")
            company = item.get("company", {}).get("display_name", "")
            location = item.get("location", {}).get("display_name", "")
            job_url = item.get("redirect_url", "")
            desc = (item.get("description", "") or "")[:500]
            salary = ""
            if item.get("salary_min") and item.get("salary_max"):
                salary = f"{item.get('salary_min'):.0f}-{item.get('salary_max'):.0f}"
            if title and job_url:
                jobs.append({"url": job_url, "title": title, "company": company,
                             "location": location, "desc": desc, "source": f"adzuna:{country}",
                             "id": str(item.get("id", "")), "posted": item.get("created", ""),
                             "salary": salary})
    except: pass
    return jobs

def scrape_google_jobs(client, kw, loc):
    """Google Jobs (via SerpAPI-style scraping)."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.google.com/search?q={q}+jobs"
        if loc: url += f"+in+{quote_plus(loc)}"
        url += "&ibp=htl;jobs"
        resp = safe_get(client, url, timeout=20)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("li.iFjolb, div.iFjolb, div.BjJfJf")[:20]:
            try:
                title_el = card.select_one("div.BjJfJf")
                title = (title_el.get_text(strip=True) or "").strip() if title_el else ""
                company_el = card.select_one("div.vNEEBe, div.nJlQNd")
                company = (company_el.get_text(strip=True) or "").strip() if company_el else ""
                loc_el = card.select_one("div.Qk80Jf")
                location = (loc_el.get_text(strip=True) or "").strip() if loc_el else ""
                a = card.select_one("a[href]")
                job_url = a.get("href", "") if a else ""
                if job_url and not job_url.startswith("http"):
                    job_url = f"https://www.google.com{job_url}"
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": location, "desc": "", "source": "google_jobs",
                                 "id": "", "posted": "", "salary": ""})
            except: continue
    except: pass
    return jobs

# ═══════════════════════════════════════════════════════════════
# JOBSPY — LinkedIn, Indeed (library)
# ═══════════════════════════════════════════════════════════════

def scrape_jobspy(keywords, location, site):
    jobs = []
    try:
        from jobspy import scrape_jobs
        result = scrape_jobs(
            site_name=[site],
            search_term=keywords,
            location=location if location else None,
            results_wanted=50,
            hours_old=168,
            country_indeed="USA" if not location or location in [
                "Remote", "New York", "San Francisco", "Seattle", "Austin", "Boston",
                "Chicago", "Los Angeles", "Denver", "Atlanta", "Miami", "Washington DC",
                "Portland", "Dallas", "Houston", "Remote",
            ] else "India",
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
# KEYWORDS & LOCATIONS (massively expanded)
# ═══════════════════════════════════════════════════════════════

KWS = [
    # Core
    "software engineer", "software developer", "backend engineer", "frontend developer",
    "full stack developer", "full stack engineer", "data engineer", "devops engineer",
    "machine learning engineer", "data scientist", "cloud engineer", "python developer",
    "java developer", "react developer", "AI engineer", "security engineer",
    "QA engineer", "SRE", "platform engineer", "infrastructure engineer",
    "mobile developer", "web developer", "technical lead", "engineering manager",
    "staff engineer", "SDE", "SWE", "senior software engineer", "junior software engineer",
    "C++ engineer", "ruby developer", "PHP developer", "kotlin developer", "swift developer",
    "Angular developer", "data analyst", "business analyst", "UX designer",
    "embedded systems engineer", "firmware engineer", "game developer", "database engineer",
    "cloud architect", "go developer", "node.js developer", "django developer",
    ".NET developer", "aws engineer", "azure engineer", "gcp engineer",
    "kubernetes engineer", "terraform engineer", "typescript developer", "rust developer",
    "computer vision engineer", "NLP engineer", "MLOps engineer", "LLM engineer",
    "blockchain developer", "product manager", "scrum master",
    "iOS developer", "Android developer", "React Native developer", "Flutter developer",
    # Entry level
    "junior developer", "entry level software engineer", "associate software engineer",
    "trainee engineer", "graduate engineer", "intern software engineer",
    # Senior/Lead
    "senior backend engineer", "senior frontend engineer", "lead developer",
    "principal engineer", "distinguished engineer", "architect",
    # Domain
    "fintech engineer", "payments engineer", "healthcare software engineer",
    "edtech engineer", "e-commerce engineer", "gaming engineer",
    "robotics engineer", "autonomous vehicle engineer", "AR engineer", "VR engineer",
    "graphics engineer", "rendering engineer", "video engineer", "streaming engineer",
    "compiler engineer", "runtime engineer", "API engineer", "integration engineer",
    "middleware engineer", "storage engineer", "file systems engineer",
    "network engineer", "protocol engineer", "cryptography engineer",
    # Specific tech
    "React", "Vue.js", "Next.js", "Svelte", "Angular", "TypeScript",
    "Python", "Java", "JavaScript", "Go", "Rust", "C++", "C#", "Kotlin",
    "Swift", "Scala", "Ruby", "PHP", "Dart", "Elixir",
    "Docker", "Kubernetes", "Terraform", "AWS", "Azure", "GCP",
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch",
    "Spring Boot", "Django", "FastAPI", "Node.js", "Express",
    "PyTorch", "TensorFlow", "OpenAI", "LangChain", "LLM",
    "GraphQL", "REST API", "gRPC",
    # Roles
    "hiring now", "urgent hiring", "work from home", "remote",
    "contract", "permanent", "full time", "part time",
    "Lead Engineer", "Principal Engineer", "VP Engineering",
    "Director of Engineering", "Head of Engineering",
    "IT recruiter", "US IT recruiter", "talent acquisition",
]

LOCS = [
    "",  # empty = global/no location filter
    # US
    "New York", "San Francisco", "Seattle", "Austin", "Boston", "Chicago",
    "Los Angeles", "Denver", "Atlanta", "Miami", "Washington DC", "Portland",
    "Dallas", "Houston", "San Jose", "Raleigh", "Charlotte", "Phoenix",
    "Tampa", "Nashville", "Remote", "San Diego", "Detroit", "Minneapolis",
    "Salt Lake City", "Orlando", "Las Vegas", "Indianapolis", "Columbus",
    "Milwaukee", "Kansas City", "Pittsburgh", "Cincinnati", "Cleveland",
    "Sacramento", "St. Louis", "Virginia Beach", "Oakland", "Omaha",
    # India
    "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Mumbai", "Pune",
    "Delhi", "Noida", "Gurgaon", "Gurugram", "Kolkata", "Ahmedabad",
    "Jaipur", "Kochi", "Chandigarh", "Indore", "Bhopal", "Coimbatore",
    "Nagpur", "Visakhapatnam", "Vadodara", "Surat", "Thiruvananthapuram",
    # Europe
    "London", "Manchester", "Berlin", "Munich", "Paris", "Amsterdam",
    "Dublin", "Barcelona", "Madrid", "Lisbon", "Zurich", "Warsaw",
    "Prague", "Stockholm", "Copenhagen", "Oslo", "Helsinki", "Milan",
    "Rome", "Vienna", "Brussels", "Edinburgh", "Bristol", "Birmingham",
    # Asia Pacific
    "Singapore", "Hong Kong", "Tokyo", "Sydney", "Melbourne",
    "Jakarta", "Manila", "Seoul", "Taipei", "Kuala Lumpur",
    # Middle East / Africa
    "Dubai", "Tel Aviv", "Riyadh", "Jeddah", "Cape Town",
    # Latin America
    "Sao Paulo", "Mexico City", "Buenos Aires", "Bogota", "Lima",
    "Santiago", "Remote Latin America",
    # Canada
    "Toronto", "Vancouver", "Montreal", "Ottawa", "Calgary",
]

# Country codes for Adzuna
ADZUNA_COUNTRIES = ["us", "gb", "de", "fr", "in", "au", "ca", "nl", "sg", "nz", "ch"]

# Country domains for Indeed
INDEED_DOMAINS = ["com", "co.uk", "in", "com.au", "ca", "co.in", "de", "fr", "co.jp"]

# ═══════════════════════════════════════════════════════════════
# WORK ITEM GENERATOR (continuous)
# ═══════════════════════════════════════════════════════════════

def build_all_work_items():
    """Generate the full work item set."""
    items = []

    # ATS board discovery
    slugs = generate_ats_slugs()
    for i in range(0, len(slugs), 20):
        items.append(("ats_discover", slugs[i:i+20], "", 0))

    # JSON APIs
    items.append(("remotive", "", "", 0))
    for pg in range(20):
        items.append(("arbeitnow", "", "", pg))
    items.append(("remoteok", "", "", 0))
    for pg in range(10):
        items.append(("itjobs", "", "", pg))
    for pg in range(10):
        items.append(("findwork", "", "", pg))
    for pg in range(10):
        items.append(("jobicy", "", "", pg))
    for pg in range(5):
        items.append(("github_jobs", "", "", pg))
    items.append(("workingnomads", "", "", 0))
    for pg in range(10):
        items.append(("themuse", "", "", pg))
    items.append(("usajobs", "", "", 0))

    # Dice
    for kw in KWS:
        for loc in LOCS[:30]:
            for pg in range(3):
                items.append(("dice", kw, loc, pg))

    # SimplyHired
    for kw in KWS[:60]:
        for loc in LOCS[:30]:
            for pg in range(3):
                items.append(("simplyhired", kw, loc, pg))

    # Monster
    for kw in KWS[:40]:
        for loc in LOCS[:20]:
            for pg in range(2):
                items.append(("monster", kw, loc, pg))

    # Jooble
    for kw in KWS[:40]:
        for loc in LOCS[:20]:
            for pg in range(2):
                items.append(("jooble", kw, loc, pg))

    # CareerBuilder
    for kw in KWS[:30]:
        for loc in LOCS[:20]:
            for pg in range(2):
                items.append(("careerbuilder", kw, loc, pg))

    # Reed (UK)
    for kw in KWS[:30]:
        for loc in ["London", "Manchester", "Edinburgh", "Bristol", "Birmingham", "Remote"]:
            for pg in range(2):
                items.append(("reed", kw, loc, pg))

    # Indeed country variants
    for domain in INDEED_DOMAINS:
        for kw in KWS[:40]:
            for loc in LOCS[:20]:
                items.append((f"indeed_{domain}", kw, loc, 0))

    # Adzuna
    for country in ADZUNA_COUNTRIES:
        for kw in KWS[:30]:
            for pg in range(3):
                items.append((f"adzuna_{country}", kw, "", pg))

    # Google Jobs
    for kw in KWS[:30]:
        for loc in LOCS[:20]:
            items.append(("google_jobs", kw, loc, 0))

    # JobSpy (LinkedIn/Indeed via library)
    for kw in KWS[:40]:
        for loc in LOCS[:25]:
            for site in ["linkedin", "indeed"]:
                items.append(("jobspy", kw, loc, site))

    random.shuffle(items)
    return items

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    reset = "--reset" in sys.argv

    log("=" * 70)
    log("MEGA SCRAPER V3 — 50 workers, 50+ sources, continuous loop")
    log("=" * 70)

    db = JobDB()
    total = db.count()
    log(f"DB: {total:,} | Gap: {max(0, TARGET-total):,}")

    db.load_dedup_keys()

    # Build work items
    all_items = build_all_work_items()
    log(f"Total work items: {len(all_items):,}")

    # Load checkpoint
    done = set()
    if not reset and CP_PATH.exists():
        try:
            cp = json.loads(CP_PATH.read_text("utf-8"))
            done = set(cp.get("done", []))
        except: pass
    if reset:
        done.clear()

    remaining = [i for i in all_items if str(i) not in done]
    log(f"Remaining: {len(remaining):,}")

    if not remaining:
        log("All items done! Resetting...")
        done.clear()
        remaining = all_items[:]

    # Stats
    start = time.time()
    counter = {"iters": 0, "new": 0}
    c_lock = threading.Lock()
    stats = {}
    stop_event = threading.Event()

    def worker(wid):
        import httpx
        client = httpx.Client(timeout=20, follow_redirects=True,
                              limits=httpx.Limits(max_connections=5, max_keepalive_connections=3))
        try:
            while not stop_event.is_set():
                try:
                    item = remaining.pop(0) if remaining else None
                except IndexError:
                    # Regenerate items!
                    new_items = build_all_work_items()
                    remaining.extend([i for i in new_items if str(i) not in done])
                    if not remaining:
                        break
                    item = remaining.pop(0)
                if item is None:
                    break

                site_type, kw, loc, pg_or_site = item
                jobs = []

                try:
                    time.sleep(random.uniform(0.1, 0.5))

                    if site_type == "ats_discover":
                        new_jobs, boards = probe_ats_batch(client, db, kw)
                        with c_lock:
                            counter["iters"] += 1
                            counter["new"] += new_jobs
                            done.add(str(item))
                            stats["ats_discover"] = stats.get("ats_discover", 0) + 1
                            iters = counter["iters"]
                        if new_jobs > 0:
                            log(f"  W{wid:02d} ATS +{new_jobs} ({boards} boards)")
                        continue
                    elif site_type == "dice":
                        jobs = scrape_dice(client, kw, loc, pg_or_site)
                    elif site_type == "simplyhired":
                        jobs = scrape_simplyhired(client, kw, loc, pg_or_site)
                    elif site_type == "monster":
                        jobs = scrape_monster(client, kw, loc, pg_or_site)
                    elif site_type == "jooble":
                        jobs = scrape_jooble(client, kw, loc, pg_or_site)
                    elif site_type == "careerbuilder":
                        jobs = scrape_careerbuilder(client, kw, loc, pg_or_site)
                    elif site_type == "reed":
                        jobs = scrape_reed(client, kw, loc, pg_or_site)
                    elif site_type.startswith("indeed_"):
                        domain = site_type.replace("indeed_", "")
                        jobs = scrape_indeed_country(client, kw, loc, pg_or_site, domain)
                    elif site_type.startswith("adzuna_"):
                        country = site_type.replace("adzuna_", "")
                        jobs = scrape_adzuna(client, country, kw, pg_or_site)
                    elif site_type == "google_jobs":
                        jobs = scrape_google_jobs(client, kw, loc)
                    elif site_type == "remotive":
                        jobs = scrape_remotive(client, pg_or_site)
                    elif site_type == "arbeitnow":
                        jobs = scrape_arbeitnow(client, pg_or_site)
                    elif site_type == "remoteok":
                        jobs = scrape_remoteok(client, pg_or_site)
                    elif site_type == "itjobs":
                        jobs = scrape_itjobs_gg(client, pg_or_site)
                    elif site_type == "findwork":
                        jobs = scrape_findwork(client, pg_or_site)
                    elif site_type == "jobicy":
                        jobs = scrape_jobicy(client, pg_or_site)
                    elif site_type == "github_jobs":
                        jobs = scrape_github_jobs(client, pg_or_site)
                    elif site_type == "workingnomads":
                        jobs = scrape_workingnomads(client, pg_or_site)
                    elif site_type == "themuse":
                        jobs = scrape_themuse(client, pg_or_site)
                    elif site_type == "usajobs":
                        jobs = scrape_usajobs(client, pg_or_site)
                    elif site_type == "jobspy":
                        jobs = scrape_jobspy(kw, loc, pg_or_site)
                except:
                    jobs = []

                new = db.insert(jobs) if jobs else 0

                with c_lock:
                    counter["iters"] += 1
                    counter["new"] += new
                    done.add(str(item))
                    # Normalize source for stats
                    base = site_type.split("_")[0] if site_type.startswith("indeed_") or site_type.startswith("adzuna_") else site_type
                    stats[base] = stats.get(base, 0) + 1
                    iters = counter["iters"]

                if new > 2:
                    log(f"  W{wid:02d} +{new:3d} {site_type}:{kw[:20]}|{loc[:12]}")

                if iters % 100 == 0:
                    ct = db.count()
                    rate = counter["new"] / max((time.time()-start)/60, 0.1)
                    log(f"  [{iters:,}] +{counter['new']:,} | DB={ct:,} | {rate:.0f}/min | Gap={max(0,TARGET-ct):,}")
                    # Save checkpoint
                    try:
                        CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
                    except: pass

        except Exception as e:
            log(f"  W{wid:02d} FATAL: {e}")
        finally:
            try: client.close()
            except: pass

    log(f"Launching {WORKERS} workers...")

    # Stagger thread starts to avoid thundering herd
    threads = []
    for i in range(WORKERS):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.05)

    # Heartbeat thread
    def heartbeat():
        while not stop_event.is_set():
            time.sleep(120)
            try:
                ct = db.count()
                elapsed_min = (time.time() - start) / 60
                log(f"  [HB] DB={ct:,} | iters={counter['iters']:,} new={counter['new']:,} | {elapsed_min:.0f}min | Gap={max(0,TARGET-ct):,}")
                CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
            except: pass
    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()

    # Wait for completion or timeout
    for t in threads:
        t.join(timeout=BATCH_TIMEOUT)

    stop_event.set()
    time.sleep(2)

    try:
        CP_PATH.write_text(json.dumps({"done": list(done)}), "utf-8")
    except: pass

    ft = db.count()
    elapsed = (time.time() - start) / 60
    db.close()

    log("=" * 70)
    log(f"ROUND COMPLETE | New: +{counter['new']:,}")
    log(f"Sites: " + " ".join(f"{k}:{v}" for k,v in sorted(stats.items())))
    log(f"DB: {ft:,} | Gap: {max(0, TARGET-ft):,}")
    log(f"Time: {elapsed:.1f}min | Rate: {counter['new']/max(elapsed,0.1):.0f}/min")
    log("=" * 70)

if __name__ == "__main__":
    main()
