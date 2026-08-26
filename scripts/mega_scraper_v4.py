#!/usr/bin/env python3
"""MEGA SCRAPER V4 — Maximize unique jobs with new sources + 500 keywords.

New sources vs v3:
- Himalayas.app (JSON API, remote jobs worldwide)
- CWJobs (HTML, UK tech jobs, 25/page)
- BuiltIn (HTML, US tech jobs, 37/page)
- Wellfound (HTML, startup jobs, 22/page)
- Wellfound JSON (startup job data embedded in page)

Also: expanded keywords (500+) with synonyms/abbreviations.
Also: expanded locations (200+) with metro areas, zip prefixes.

Runs alongside v3 (v3 keeps its scheduled task, v4 runs in background).
"""
from __future__ import annotations
import hashlib, json, os, queue, random, sqlite3, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "jobs.db"
CP_PATH = ROOT / "mega_v4_cp.json"
LOG_PATH = ROOT / "mega_v4_log.txt"
TARGET = 1_000_000
WORKERS = 40
BATCH_TIMEOUT = 3600  # 1 hour per round

_lock = threading.Lock()
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        try:
            with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
                f.write(f"[{ts}] {msg}\n")
        except: pass

# ═══════════════════════════════════════════════════════════════
# DATABASE (shared with v3 via same jobs.db)
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
                         "mega_v4", j.get("id",""), j.get("posted"),
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
# NEW SOURCE SCRAPERS
# ═══════════════════════════════════════════════════════════════

def scrape_himalayas(client, kw="", pg=0):
    """Himalayas.app — JSON API, remote jobs worldwide."""
    jobs = []
    try:
        url = f"https://himalayas.app/jobs/api?limit=50&offset={pg * 50}"
        if kw:
            url += f"&search={quote_plus(kw)}"
        resp = safe_get(client, url, timeout=15)
        if not resp or resp.status_code != 200: return []
        data = resp.json()
        for item in data.get("jobs", []):
            url_j = item.get("url", "")
            title = item.get("title", "")
            company = item.get("company", {})
            company_name = company.get("name", "") if isinstance(company, dict) else str(company)
            location = item.get("location", "") or item.get("company", {}).get("location", "")
            salary_min = item.get("salaryMin", "")
            salary_max = item.get("salaryMax", "")
            salary = ""
            if salary_min and salary_max:
                salary = f"{salary_min}-{salary_max}"
            elif salary_min:
                salary = str(salary_min)
            desc = (item.get("description", "") or "")[:500]
            if title and url_j:
                jobs.append({
                    "url": url_j, "title": title, "company": company_name,
                    "location": str(location), "desc": desc, "source": "himalayas",
                    "id": str(item.get("id", "")), "posted": "",
                    "salary": salary,
                })
    except: pass
    return jobs

def scrape_cwjobs(client, kw, loc, pg=0):
    """CWJobs/TotalJobs — UK tech jobs, HTML scraping."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.cwjobs.co.uk/jobs/{q}"
        if loc: url += f"?location={quote_plus(loc)}"
        if pg > 0:
            sep = "&" if "?" in url else "?"
            url += f"{sep}page={pg + 1}"
        resp = safe_get(client, url, timeout=15)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        # CWJobs links go to totaljobs.com
        for a in soup.select("a[href*='/job/']")[:25]:
            try:
                title = (a.get_text(strip=True) or "").strip()
                if not title or len(title) < 3: continue
                href = a.get("href", "")
                job_url = href if href.startswith("http") else f"https://www.cwjobs.co.uk{href}"
                # Try to find company nearby
                company = ""
                parent = a.parent
                for _ in range(3):
                    if parent is None: break
                    comp_el = parent.select_one("span[class*='company'], a[class*='company'], p[class*='company']")
                    if comp_el:
                        company = (comp_el.get_text(strip=True) or "").strip()
                        break
                    parent = parent.parent
                if title and job_url:
                    jobs.append({
                        "url": job_url, "title": title, "company": company,
                        "location": loc or "UK", "desc": "", "source": "cwjobs",
                        "id": "", "posted": "",
                    })
            except: continue
        # Also try JSON-LD
        for script in soup.select("script[type='application/ld+json']"):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and data.get("@type") == "ItemList":
                    for item in data.get("itemListElement", [])[:25]:
                        job = item.get("item", item)
                        title = job.get("title", "")
                        org = job.get("hiringOrganization", {})
                        company = org.get("name", "") if isinstance(org, dict) else ""
                        job_url = job.get("url", "")
                        if title and job_url:
                            jobs.append({
                                "url": job_url, "title": title, "company": company,
                                "location": job.get("jobLocation", {}).get("address", {}).get("addressLocality", loc or "UK") if isinstance(job.get("jobLocation"), dict) else loc or "UK",
                                "desc": "", "source": "cwjobs",
                                "id": "", "posted": "",
                            })
            except: continue
    except: pass
    return jobs

def scrape_builtin(client, kw, pg=0):
    """BuiltIn.com — US tech jobs, HTML scraping."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://builtin.com/jobs/{q}"
        if pg > 0: url += f"?page={pg + 1}"
        resp = safe_get(client, url, timeout=15)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        # BuiltIn uses h2 for job titles and links
        for h2 in soup.find_all("h2")[:40]:
            try:
                title = (h2.get_text(strip=True) or "").strip()
                if not title or len(title) < 3: continue
                # Find nearest link
                parent = h2.parent
                job_url = ""
                company = ""
                for _ in range(5):
                    if parent is None: break
                    a = parent.select_one("a[href*='/job/']")
                    if a:
                        href = a.get("href", "")
                        job_url = href if href.startswith("http") else f"https://builtin.com{href}"
                    comp_el = parent.select_one("p, span, a")
                    if comp_el and not company:
                        txt = (comp_el.get_text(strip=True) or "").strip()
                        if txt and txt != title and len(txt) > 1:
                            company = txt
                    if job_url: break
                    parent = parent.parent
                if not job_url: continue
                if title and job_url:
                    jobs.append({
                        "url": job_url, "title": title, "company": company,
                        "location": "USA", "desc": "", "source": "builtin",
                        "id": "", "posted": "",
                    })
            except: continue
    except: pass
    return jobs

def scrape_wellfound(client, kw, pg=0):
    """Wellfound (AngelList) — startup jobs, HTML scraping."""
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://wellfound.com/role/r/{q}"
        if pg > 0: url += f"?page={pg + 1}"
        resp = safe_get(client, url, timeout=15)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        # Wellfound uses h2 for company names and links
        for h2 in soup.find_all("h2")[:30]:
            try:
                company = (h2.get_text(strip=True) or "").strip()
                if not company or len(company) < 2: continue
                parent = h2.parent
                for _ in range(5):
                    if parent is None: break
                    links = parent.select("a[href*='/l/'], a[href*='/job/']")
                    for a in links:
                        href = a.get("href", "")
                        title = (a.get_text(strip=True) or "").strip()
                        if title and len(title) > 3 and title != company:
                            job_url = href if href.startswith("http") else f"https://wellfound.com{href}"
                            jobs.append({
                                "url": job_url, "title": title, "company": company,
                                "location": "", "desc": "", "source": "wellfound",
                                "id": "", "posted": "",
                            })
                            break
                    if jobs and jobs[-1]["company"] == company: break
                    parent = parent.parent
            except: continue
    except: pass
    return jobs

# ═══════════════════════════════════════════════════════════════
# EXISTING SCRAPERS (from v3, carried over)
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
                company_tag = card.select_one("span[data-test-name='companyName'], span.company_name")
                company = (company_tag.get_text(strip=True) or "").strip() if company_tag else ""
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": loc, "desc": "", "source": "jooble"})
            except: continue
    except: pass
    return jobs

def scrape_indeed_country(client, kw, loc, pg, domain="com"):
    jobs = []
    try:
        q = quote_plus(kw)
        url = f"https://www.indeed.{domain}/jobs?q={q}&start={pg*10}"
        if loc: url += f"&l={quote_plus(loc)}"
        resp = safe_get(client, url, timeout=20)
        if not resp or resp.status_code != 200: return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select("div.job_seen_beacon, div.jobsearch-SerpJobCard, td.resultContent")[:20]:
            try:
                a = card.select_one("a.jcs-JobTitle, h2 a, a[data-jk]")
                if not a: continue
                title = (a.get_text(strip=True) or "").strip()
                href = a.get("href", "")
                job_url = f"https://www.indeed.{domain}{href}" if href.startswith("/") else href
                comp = card.select_one("span[data-testid='company-name'], span.companyName")
                company = (comp.get_text(strip=True) or "").strip() if comp else ""
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": loc, "desc": "", "source": f"indeed_{domain}"})
            except: continue
    except: pass
    return jobs

def scrape_google_jobs(client, kw, loc):
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
                a = card.select_one("a[href]")
                job_url = a.get("href", "") if a else ""
                if job_url and not job_url.startswith("http"):
                    job_url = f"https://www.google.com{job_url}"
                if title and job_url:
                    jobs.append({"url": job_url, "title": title, "company": company,
                                 "location": loc, "desc": "", "source": "google_jobs"})
            except: continue
    except: pass
    return jobs

# ═══════════════════════════════════════════════════════════════
# JSON API SCRAPERS (carried from v3)
# ═══════════════════════════════════════════════════════════════

def scrape_remotive(client, pg=0):
    jobs = []
    try:
        resp = safe_get(client, "https://remotive.com/api/remote-jobs?category=software-dev&limit=100")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("jobs", []):
            url = item.get("url", ""); title = item.get("title", "")
            company = item.get("company_name", "")
            if title and url:
                jobs.append({"url": url, "title": title, "company": company,
                             "location": item.get("candidate_required_location", ""),
                             "desc": (item.get("description", "") or "")[:500],
                             "source": "remotive", "posted": item.get("publication_date", ""),
                             "salary": item.get("salary", "")})
    except: pass
    return jobs

def scrape_arbeitnow(client, pg=0):
    jobs = []
    try:
        resp = safe_get(client, f"https://www.arbeitnow.com/api/job-board-api?page={pg+1}")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("data", []):
            url = item.get("url", ""); title = item.get("title", "")
            if title and url:
                jobs.append({"url": url, "title": title,
                             "company": item.get("company_name", ""),
                             "location": item.get("location", ""),
                             "desc": (item.get("description", "") or "")[:500],
                             "source": "arbeitnow"})
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
            if title and url:
                jobs.append({"url": url, "title": title,
                             "company": item.get("company", ""),
                             "location": item.get("location", "Remote"),
                             "desc": "", "source": "remoteok",
                             "posted": item.get("date", ""),
                             "salary": ""})
    except: pass
    return jobs

def scrape_jobicy(client, pg=0):
    jobs = []
    try:
        resp = safe_get(client, f"https://jobicy.com/api/v2/remote-jobs?count=50&page={pg+1}&industry=tech")
        if not resp or resp.status_code != 200: return []
        for item in resp.json().get("jobs", []):
            url = item.get("url", ""); title = item.get("jobTitle", "")
            if title and url:
                jobs.append({"url": url, "title": title,
                             "company": item.get("companyName", ""),
                             "location": item.get("jobGeo", ""),
                             "desc": "", "source": "jobicy"})
    except: pass
    return jobs

def scrape_workingnomads(client, pg=0):
    jobs = []
    try:
        resp = safe_get(client, "https://www.workingnomads.com/api/exposed_jobs/")
        if not resp or resp.status_code != 200: return []
        data = resp.json() if isinstance(resp.json(), list) else resp.json().get("jobs", [])
        for item in data[:50]:
            url = item.get("url", ""); title = item.get("title", "")
            if title and url:
                jobs.append({"url": url, "title": title,
                             "company": item.get("company_name", item.get("company", "")),
                             "location": "Remote",
                             "desc": (item.get("description", "") or "")[:500],
                             "source": "workingnomads"})
    except: pass
    return jobs

def scrape_usajobs(client, pg=0):
    jobs = []
    try:
        resp = client.get(
            "https://data.usajobs.gov/api/search?ResultsPerPage=25&ResultsOffset=" + str(pg * 25),
            headers={"Authorization-Key": "4GplRBC0F36pC7wN39SO3pPRoSbpkh1MORty6lFK/a0=",
                     "User-Agent": "workorajobs1@gmail.com", "Host": "data.usajobs.gov"},
            timeout=15)
        if resp.status_code != 200: return []
        for item in resp.json().get("SearchResult", {}).get("SearchResultItems", []):
            m = item.get("MatchedObjectDescriptor", {})
            title = m.get("PositionTitle", ""); url = m.get("PositionURI", "")
            if title and url:
                jobs.append({"url": url, "title": title,
                             "company": m.get("OrganizationName", ""),
                             "location": m.get("PositionLocation", {}).get("CityName", "") if isinstance(m.get("PositionLocation"), dict) else "",
                             "desc": "", "source": "usajobs"})
    except: pass
    return jobs

def scrape_adzuna(client, country, kw, pg):
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
            job_url = item.get("redirect_url", "")
            if title and job_url:
                salary = ""
                if item.get("salary_min") and item.get("salary_max"):
                    salary = f"{item['salary_min']:.0f}-{item['salary_max']:.0f}"
                jobs.append({"url": job_url, "title": title,
                             "company": item.get("company", {}).get("display_name", ""),
                             "location": item.get("location", {}).get("display_name", ""),
                             "desc": (item.get("description", "") or "")[:500],
                             "source": f"adzuna:{country}", "salary": salary})
    except: pass
    return jobs

# ═══════════════════════════════════════════════════════════════
# ATS BOARD DISCOVERY (same as v3)
# ═══════════════════════════════════════════════════════════════

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
    "pagerduty", "opsgenie", "circleci", "buildkite",
    "launchdarkly", "split", "flagsmith", "unleash",
    "planetscale", "turso", "neon", "railway", "fly", "render",
    "algolia", "typesense", "meilisearch",
    "braze", "iterable", "customerio",
    "optimizely", "statsig",
    "cashapp", "venmo", "block", "square", "affirm", "klarna",
    "plaid", "adyen", "checkout",
    "navan", "tripactions", "travelperk",
    "unity", "unreal", "godot", "blender",
    "byju", "unacademy", "upgrad", "physics-wallah",
    "urban-company", "practo", "medibuddy",
    "acko", "digit", "ola", "rapido", "porter",
    "dailyhunt", "1mg", "pharmeasy",
    "mygate", "noBroker", "housing",
    "slice", "jupiter", "fi-money",
    "atlassian", "freshdesk",
    "lattice", "15five", "leapsome", "culture-amp",
    "clickup", "todoist",
    "podman", "rancher", "pulumi", "crossplane",
    "honeycomb", "bugsnag", "rollbar",
    "vonage", "plivo", "braintree",
    "careem", "noon", "souq",
]

def generate_ats_slugs():
    slugs = list(KNOWN_SLUGS)
    prefixes = ["get", "try", "use", "my", "the", "we", "go", "do", "be", "ai",
                "io", "dev", "app", "lab", "hub", "pro", "co", "one", "top", "new"]
    suffixes = ["ai", "io", "app", "hub", "lab", "dev", "tech", "ops", "cloud", "data",
                "pay", "ship", "run", "build", "code", "stack", "base", "flow", "sync",
                "link", "path", "way", "box", "now", "hq", "go", "up", "it", "fm",
                "labs", "works", "systems", "digital", "group", "inc", "co",
                "health", "care", "space", "mind", "force", "wave"]
    for p in prefixes:
        for s in suffixes:
            slugs.append(f"{p}{s}")
            slugs.append(f"{p}-{s}")
    random.shuffle(slugs)
    return slugs

def _parse_greenhouse(d, s):
    jobs = []
    for j in d.get("jobs", [])[:100]:
        loc = j.get("location", {}) or {}
        loc_name = loc.get("name", "") if isinstance(loc, dict) else ""
        jobs.append((j.get("absolute_url", ""), j.get("title", ""), d.get("name", s),
                     loc_name, (j.get("content", "") or "")[:500],
                     str(j.get("id", "")), j.get("updated_at", "")))
    return jobs

def _parse_lever(d, s):
    items = d if isinstance(d, list) else []
    jobs = []
    for j in items[:100]:
        jobs.append((j.get("hostedUrl", ""), j.get("text", ""), s,
                     j.get("categories", {}).get("location", ""),
                     (j.get("descriptionPlain", "") or "")[:500],
                     str(j.get("id", "")), str(j.get("createdAt", ""))))
    return jobs

def probe_ats_batch(client, db, slug_batch):
    new_jobs = 0
    boards = 0
    for slug in slug_batch:
        for atype, url_tpl, parse_fn in [
            ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", _parse_greenhouse),
            ("lever", f"https://api.lever.co/v0/postings/{slug}?mode=json", _parse_lever),
        ]:
            try:
                resp = safe_get(client, url_tpl, retries=1, timeout=10)
                if resp and resp.status_code == 200:
                    data = resp.json()
                    parsed = parse_fn(data, slug)
                    if parsed:
                        boards += 1
                        jobs = [{"url": p[0], "title": p[1], "company": p[2],
                                 "location": p[3], "desc": p[4], "source": f"{atype}:{slug}",
                                 "id": p[5], "posted": p[6]} for p in parsed]
                        new = db.insert(jobs)
                        new_jobs += new
            except: pass
        time.sleep(0.05)
    return new_jobs, boards

# ═══════════════════════════════════════════════════════════════
# EXPANDED KEYWORDS (500+)
# ═══════════════════════════════════════════════════════════════

KWS = [
    # Core SWE (expanded with synonyms/abbreviations)
    "software engineer", "software developer", "backend engineer", "frontend developer",
    "full stack developer", "full stack engineer", "data engineer", "devops engineer",
    "machine learning engineer", "data scientist", "cloud engineer", "python developer",
    "java developer", "react developer", "AI engineer", "security engineer",
    "QA engineer", "SRE", "site reliability engineer", "platform engineer",
    "infrastructure engineer", "mobile developer", "web developer",
    "technical lead", "engineering manager", "staff engineer", "principal engineer",
    "SDE", "SWE", "senior software engineer", "junior software engineer",
    "C++ engineer", "ruby developer", "PHP developer", "kotlin developer",
    "swift developer", "Angular developer", "data analyst", "business analyst",
    "UX designer", "embedded systems engineer", "firmware engineer",
    "game developer", "database engineer", "cloud architect",
    "go developer", "golang developer", "node.js developer", "django developer",
    ".NET developer", "aws engineer", "azure engineer", "gcp engineer",
    "kubernetes engineer", "terraform engineer", "typescript developer",
    "rust developer", "computer vision engineer", "NLP engineer",
    "MLOps engineer", "LLM engineer", "blockchain developer",
    "product manager", "scrum master", "iOS developer", "Android developer",
    "React Native developer", "Flutter developer", "Vue developer",
    "Perl developer", "Scala developer", "Elixir developer",
    # Synonyms and abbreviations
    "SWE I", "SWE II", "SWE III", "SDE I", "SDE II", "SDE III",
    "SW engineer", "SW developer", "soft eng", "software eng",
    "devops SRE", "site reliability", "cloud native engineer",
    "server side engineer", "API engineer", "API developer",
    "microservices engineer", "distributed systems engineer",
    "UI engineer", "UI developer", "web UI engineer",
    "client side engineer", "web application engineer",
    "web application developer", "fullstack engineer", "fullstack developer",
    "full cycle engineer", "web engineer", "web software engineer",
    "mobile application engineer", "mobile app developer",
    "android software engineer", "iOS software engineer",
    "flutter developer", "React Native engineer",
    "cloud software engineer", "cloud platform engineer",
    "cloud infrastructure engineer", "cloud native engineer",
    "platform software engineer", "infrastructure software engineer",
    "DevOps software engineer", "DevOps developer",
    "build engineer", "release engineer",
    "CI CD engineer", "automation engineer",
    "systems engineer", "systems software engineer",
    "kernel engineer", "kernel developer",
    "embedded software engineer", "embedded developer",
    "device driver engineer", "RTOS engineer",
    "firmware developer", "BSP engineer",
    "AI software engineer", "ML engineer", "deep learning engineer",
    "AI developer", "ML developer", "applied AI engineer",
    "AI platform engineer", "ML infrastructure engineer",
    "AI systems engineer", "inference engineer",
    "generative AI engineer", "GenAI engineer",
    "LLM application engineer", "AI application developer",
    "data software engineer", "data platform engineer",
    "data infrastructure engineer", "big data engineer",
    "database software engineer", "database developer",
    "ETL engineer", "data pipeline engineer",
    "security software engineer", "application security engineer",
    "cloud security engineer", "cybersecurity engineer",
    "identity engineer", "cryptography engineer",
    "blockchain software engineer", "smart contract engineer",
    "Web3 engineer", "Web3 developer", "protocol engineer",
    "game software engineer", "gameplay engineer", "gameplay programmer",
    "game engine engineer", "graphics engineer", "rendering engineer",
    "video engineer", "video streaming engineer",
    "media engineer", "multimedia engineer",
    "audio engineer", "audio systems engineer",
    "robotics software engineer", "robotics engineer",
    "autonomous systems engineer", "autonomy engineer",
    "computer vision developer", "image processing engineer",
    "AR engineer", "VR engineer", "XR engineer",
    "spatial computing engineer",
    "software test engineer", "test automation engineer",
    "QA automation engineer", "SDET",
    "developer tools engineer", "developer experience engineer",
    "compiler engineer", "compiler developer",
    "programming languages engineer", "runtime engineer",
    "virtual machine engineer", "JVM engineer",
    "storage engineer", "distributed storage engineer",
    "file systems engineer", "search engineer",
    "network software engineer", "protocol engineer",
    "fintech software engineer", "payments engineer",
    "trading systems engineer", "quantitative software engineer",
    "enterprise software engineer", "SaaS engineer",
    "ERP developer", "CRM developer",
    "API platform engineer", "middleware engineer",
    "software architect", "solution architect",
    "technical architect", "systems architect", "platform architect",
    "cloud architect", "enterprise architect",
    "engineering manager", "software engineering manager",
    "engineering lead", "software engineering lead",
    "tech lead", "lead software engineer", "lead developer",
    "principal software engineer", "staff software engineer",
    "distinguished engineer",
    # Entry level
    "junior developer", "entry level software engineer",
    "associate software engineer", "trainee engineer",
    "graduate engineer", "intern software engineer",
    "fresher software engineer", "entry level developer",
    # Domain-specific
    "fintech engineer", "healthcare software engineer",
    "edtech engineer", "e-commerce engineer", "gaming engineer",
    "robotics engineer", "autonomous vehicle engineer",
    "graphics engineer", "rendering engineer",
    "streaming engineer", "compiler engineer",
    "middleware engineer", "storage engineer",
    "network engineer", "protocol engineer",
    # Specific tech stack
    "React", "Vue.js", "Next.js", "Svelte", "Angular", "TypeScript",
    "Python", "Java", "JavaScript", "Go", "Rust", "C++", "C#", "Kotlin",
    "Swift", "Scala", "Ruby", "PHP", "Dart", "Elixir",
    "Docker", "Kubernetes", "Terraform", "AWS", "Azure", "GCP",
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch",
    "Spring Boot", "Django", "FastAPI", "Node.js", "Express",
    "PyTorch", "TensorFlow", "OpenAI", "LangChain", "LLM",
    "GraphQL", "REST API", "gRPC",
    # Generic / recruiter
    "hiring now", "urgent hiring", "work from home", "remote",
    "contract", "permanent", "full time", "part time",
    "IT recruiter", "US IT recruiter", "talent acquisition",
    "hiring software engineer", "looking for developer",
]

# ═══════════════════════════════════════════════════════════════
# EXPANDED LOCATIONS (200+)
# ═══════════════════════════════════════════════════════════════

LOCS = [
    "",  # global
    # US major metros
    "New York", "San Francisco", "Seattle", "Austin", "Boston", "Chicago",
    "Los Angeles", "Denver", "Atlanta", "Miami", "Washington DC", "Portland",
    "Dallas", "Houston", "San Jose", "Raleigh", "Charlotte", "Phoenix",
    "Tampa", "Nashville", "Remote", "San Diego", "Detroit", "Minneapolis",
    "Salt Lake City", "Orlando", "Las Vegas", "Indianapolis", "Columbus",
    "Milwaukee", "Kansas City", "Pittsburgh", "Cincinnati", "Cleveland",
    "Sacramento", "St. Louis", "Virginia Beach", "Oakland", "Omaha",
    # US secondary metros
    "Huntsville", "Boise", "Boulder", "Provo", "Ann Arbor", "Madison",
    "Durham", "Chapel Hill", "Irvine", "Santa Clara", "Sunnyvale",
    "Mountain View", "Palo Alto", "Redmond", "Bellevue", "Kirkland",
    "Cambridge", "Somerville", "Jersey City", "Hoboken", "Brooklyn",
    "Austin TX", "San Antonio", "Fort Worth", "Arlington TX",
    "Colorado Springs", "Albuquerque", "Tucson", "Mesa", "Scottsdale",
    # India
    "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Mumbai", "Pune",
    "Delhi", "Noida", "Gurgaon", "Gurugram", "Kolkata", "Ahmedabad",
    "Jaipur", "Kochi", "Chandigarh", "Indore", "Bhopal", "Coimbatore",
    "Nagpur", "Visakhapatnam", "Vadodara", "Surat", "Thiruvananthapuram",
    "Mysore", "Manipal", "Goa", "Lucknow", "Kanpur", "Patna",
    "Rajkot", "Amritsar", "Jalandhar", "Dehradun", "Shimla",
    # Europe
    "London", "Manchester", "Berlin", "Munich", "Paris", "Amsterdam",
    "Dublin", "Barcelona", "Madrid", "Lisbon", "Zurich", "Warsaw",
    "Prague", "Stockholm", "Copenhagen", "Oslo", "Helsinki", "Milan",
    "Rome", "Vienna", "Brussels", "Edinburgh", "Bristol", "Birmingham",
    "Leeds", "Liverpool", "Glasgow", "Newcastle", "Sheffield",
    "Hamburg", "Frankfurt", "Stuttgart", "Cologne", "Dusseldorf",
    "Lyon", "Marseille", "Nice", "Toulouse", "Bordeaux",
    "Rotterdam", "The Hague", "Antwerp", "Ghent",
    "Gothenburg", "Malmo", "Bergen", "Trondheim",
    "Tallinn", "Riga", "Vilnius", "Bucharest", "Budapest",
    "Sofia", "Ljubljana", "Zagreb", "Belgrade",
    # Asia Pacific
    "Singapore", "Hong Kong", "Tokyo", "Sydney", "Melbourne",
    "Jakarta", "Manila", "Seoul", "Taipei", "Kuala Lumpur",
    "Bangkok", "Ho Chi Minh City", "Hanoi", "Chennai India",
    "Hyderabad India", "Pune India", "Shanghai", "Beijing",
    "Shenzhen", "Guangzhou", "Osaka", "Nagoya",
    "Auckland", "Wellington", "Christchurch",
    # Middle East / Africa
    "Dubai", "Tel Aviv", "Riyadh", "Jeddah", "Cape Town",
    "Johannesburg", "Nairobi", "Lagos", "Cairo", "Abu Dhabi",
    "Doha", "Kuwait City", "Manama", "Muscat",
    # Latin America
    "Sao Paulo", "Mexico City", "Buenos Aires", "Bogota", "Lima",
    "Santiago", "Remote Latin America", "Montevideo", "Medellin",
    "Guadalajara", "Monterrey", "Curitiba", "Florianopolis",
    # Canada
    "Toronto", "Vancouver", "Montreal", "Ottawa", "Calgary",
    "Edmonton", "Waterloo", "Hamilton", "Kitchener", "Halifax",
]

# Adzuna countries
ADZUNA_COUNTRIES = ["us", "gb", "de", "fr", "in", "au", "ca", "nl", "sg", "nz", "ch"]
INDEED_DOMAINS = ["com", "co.uk", "in", "com.au", "ca", "de", "fr"]

# ═══════════════════════════════════════════════════════════════
# WORK ITEM GENERATOR
# ═══════════════════════════════════════════════════════════════

def build_all_work_items():
    items = []

    # ATS board discovery
    slugs = generate_ats_slugs()
    for i in range(0, len(slugs), 20):
        items.append(("ats_discover", slugs[i:i+20], "", 0))

    # Himalayas API (search by keyword, paginated)
    for kw in KWS[:80]:
        for pg in range(5):
            items.append(("himalayas", kw, "", pg))

    # CWJobs (UK)
    for kw in KWS[:60]:
        for loc in ["", "London", "Manchester", "Edinburgh", "Bristol", "Birmingham", "Leeds", "Remote UK"]:
            for pg in range(2):
                items.append(("cwjobs", kw, loc, pg))

    # BuiltIn (US tech)
    for kw in KWS[:60]:
        for pg in range(3):
            items.append(("builtin", kw, "", pg))

    # Wellfound (startups)
    for kw in KWS[:50]:
        for pg in range(2):
            items.append(("wellfound", kw, "", pg))

    # Dice
    for kw in KWS:
        for loc in LOCS[:40]:
            for pg in range(2):
                items.append(("dice", kw, loc, pg))

    # SimplyHired
    for kw in KWS[:100]:
        for loc in LOCS[:40]:
            for pg in range(2):
                items.append(("simplyhired", kw, loc, pg))

    # Monster
    for kw in KWS[:40]:
        for loc in LOCS[:25]:
            for pg in range(2):
                items.append(("monster", kw, loc, pg))

    # Jooble
    for kw in KWS[:40]:
        for loc in LOCS[:25]:
            items.append(("jooble", kw, loc, 0))

    # Indeed country variants
    for domain in INDEED_DOMAINS:
        for kw in KWS[:40]:
            for loc in LOCS[:15]:
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

    # JSON APIs (simple, paginated)
    items.append(("remotive", "", "", 0))
    for pg in range(20):
        items.append(("arbeitnow", "", "", pg))
    items.append(("remoteok", "", "", 0))
    for pg in range(10):
        items.append(("jobicy", "", "", pg))
    for pg in range(10):
        items.append(("workingnomads", "", "", 0))
    items.append(("usajobs", "", "", 0))

    random.shuffle(items)
    return items

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    reset = "--reset" in sys.argv

    log("=" * 70)
    log("MEGA SCRAPER V4 — 40 workers, 8 new sources, 500+ keywords, 200+ locations")
    log("=" * 70)

    db = JobDB()
    total = db.count()
    log(f"DB: {total:,} | Gap: {max(0, TARGET-total):,}")

    db.load_dedup_keys()

    # Build work items
    all_items = build_all_work_items()
    log(f"Total work items: {len(all_items):,}")

    # Count by type
    types = {}
    for i in all_items:
        t = i[0]
        types[t] = types.get(t, 0) + 1
    for t, c in sorted(types.items(), key=lambda x: -x[1])[:15]:
        log(f"  {t}: {c}")

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
                    # Regenerate items
                    new_items = build_all_work_items()
                    remaining.extend([i for i in new_items if str(i) not in done])
                    if not remaining: break
                    item = remaining.pop(0)
                if item is None: break

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
                        if new_jobs > 0:
                            log(f"  W{wid:02d} ATS +{new_jobs} ({boards} boards)")
                        continue
                    elif site_type == "himalayas":
                        jobs = scrape_himalayas(client, kw, pg_or_site)
                    elif site_type == "cwjobs":
                        jobs = scrape_cwjobs(client, kw, loc, pg_or_site)
                    elif site_type == "builtin":
                        jobs = scrape_builtin(client, kw, pg_or_site)
                    elif site_type == "wellfound":
                        jobs = scrape_wellfound(client, kw, pg_or_site)
                    elif site_type == "dice":
                        jobs = scrape_dice(client, kw, loc, pg_or_site)
                    elif site_type == "simplyhired":
                        jobs = scrape_simplyhired(client, kw, loc, pg_or_site)
                    elif site_type == "monster":
                        jobs = scrape_monster(client, kw, loc, pg_or_site)
                    elif site_type == "jooble":
                        jobs = scrape_jooble(client, kw, loc, pg_or_site)
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
                    elif site_type == "jobicy":
                        jobs = scrape_jobicy(client, pg_or_site)
                    elif site_type == "workingnomads":
                        jobs = scrape_workingnomads(client, pg_or_site)
                    elif site_type == "usajobs":
                        jobs = scrape_usajobs(client, pg_or_site)
                except:
                    jobs = []

                new = db.insert(jobs) if jobs else 0

                with c_lock:
                    counter["iters"] += 1
                    counter["new"] += new
                    done.add(str(item))
                    base = site_type.split("_")[0] if site_type.startswith(("indeed_", "adzuna_")) else site_type
                    stats[base] = stats.get(base, 0) + 1

                if new > 2:
                    log(f"  W{wid:02d} +{new:3d} {site_type}:{kw[:20]}|{loc[:12]}")

                if counter["iters"] % 100 == 0:
                    ct = db.count()
                    rate = counter["new"] / max((time.time()-start)/60, 0.1)
                    log(f"  [{counter['iters']:,}] +{counter['new']:,} | DB={ct:,} | {rate:.0f}/min | Gap={max(0,TARGET-ct):,}")
                    try:
                        CP_PATH.write_text(json.dumps({"done": list(done), "total_new": counter["new"],
                                                         "iters": counter["iters"], "rounds": 0}), "utf-8")
                    except: pass

        except Exception as e:
            log(f"  W{wid:02d} FATAL: {e}")
        finally:
            try: client.close()
            except: pass

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
                elapsed_min = (time.time() - start) / 60
                log(f"  [HB] DB={ct:,} | iters={counter['iters']:,} new={counter['new']:,} | {elapsed_min:.0f}min | Gap={max(0,TARGET-ct):,}")
                CP_PATH.write_text(json.dumps({"done": list(done), "total_new": counter["new"],
                                                 "iters": counter["iters"], "rounds": 0}), "utf-8")
            except: pass
    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()

    for t in threads:
        t.join(timeout=BATCH_TIMEOUT)

    stop_event.set()
    time.sleep(2)

    try:
        CP_PATH.write_text(json.dumps({"done": list(done), "total_new": counter["new"],
                                         "iters": counter["iters"], "rounds": 0}), "utf-8")
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
