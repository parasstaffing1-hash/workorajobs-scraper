"""Render Scraper - Fetches jobs from 10,000+ companies and saves to R2."""
import os, sys, time, sqlite3, hashlib, json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PATH = os.environ.get("DB_PATH", "/app/jobs.db")
LOG_PATH = "/app/logs/scraper.log"
COMPANIES_FILE = os.environ.get("COMPANIES_FILE", "data/companies_10k.json")
CHECKPOINT_FILE = "/app/scraper_checkpoint.json"

# R2 config
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET_NAME", "workorajobs")

_r2_client = None

def get_r2():
    global _r2_client
    if _r2_client is None and R2_ACCOUNT_ID and R2_ACCESS_KEY:
        try:
            import boto3
            _r2_client = boto3.client(
                "s3",
                endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
                aws_access_key_id=R2_ACCESS_KEY,
                aws_secret_access_key=R2_SECRET_KEY,
                region_name="auto",
            )
        except Exception as e:
            log(f"R2 init error: {e}")
    return _r2_client

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except:
        pass

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def dedupe_key(title, company, location, url):
    raw = f"{title}|{company}|{location}|{url}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()

def save_job(job):
    """Save job to SQLite and optionally R2."""
    saved = False
    dk = dedupe_key(job.get("title",""), job.get("company",""),
                     job.get("location",""), job.get("url",""))
    try:
        conn = get_db()
        conn.execute("""INSERT OR IGNORE INTO jobs
            (dedupe_key,title,company,location,url,description,
             tags,source,source_kind,salary,posted_at,first_seen_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (dk, job.get("title",""), job.get("company",""),
             job.get("location",""), job.get("url",""),
             job.get("description","")[:2000],
             json.dumps(job.get("tags",[])),
             job.get("source",""), job.get("source_kind",""),
             job.get("salary",""), job.get("posted_at","")))
        conn.commit()
        saved = conn.total_changes > 0
        conn.close()
    except Exception as e:
        log(f"DB error: {e}")

    if R2_ACCOUNT_ID and get_r2():
        try:
            key = f"jobs/{dk}.json"
            get_r2().put_object(
                Bucket=R2_BUCKET, Key=key,
                Body=json.dumps(job, default=str).encode(),
                ContentType="application/json")
        except:
            pass
    return saved

def load_checkpoint():
    try:
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    except:
        return {"idx": 0, "total_new": 0, "rounds": 0}

def save_checkpoint(cp):
    try:
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(cp, f)
    except:
        pass

def load_companies():
    try:
        with open(COMPANIES_FILE) as f:
            return json.load(f)
    except Exception as e:
        log(f"Error loading companies: {e}")
        return []

# ===== SCRAPERS =====

def scrape_greenhouse_batch(companies_batch):
    """Scrape a batch of Greenhouse companies."""
    import httpx
    new_count = 0
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    for co in companies_batch:
        slug = co.get("slug", "")
        gh_url = co.get("greenhouse_url")
        if not gh_url:
            continue
        try:
            with httpx.Client(follow_redirects=True) as client:
                resp = client.get(gh_url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    jobs = data.get("jobs", [])
                    for j in jobs[:20]:
                        loc = j.get("location", {}) or {}
                        save_job({
                            "title": j.get("title", ""),
                            "company": co.get("name", slug),
                            "location": loc.get("name", "Remote"),
                            "url": j.get("absolute_url", ""),
                            "description": str(j.get("content", ""))[:2000],
                            "tags": [],
                            "source": f"greenhouse:{slug}",
                            "source_kind": "ats",
                            "salary": "",
                            "posted_at": j.get("updated_at", ""),
                        })
                        new_count += 1
                elif resp.status_code == 404:
                    pass  # Board doesn't exist
        except:
            pass
        time.sleep(0.2)
    return new_count

def scrape_lever_batch(companies_batch):
    """Scrape a batch of Lever companies."""
    import httpx
    new_count = 0
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    for co in companies_batch:
        slug = co.get("slug", "")
        lever_url = co.get("lever_url")
        if not lever_url:
            continue
        try:
            with httpx.Client(follow_redirects=True) as client:
                resp = client.get(lever_url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    jobs = resp.json()
                    for j in jobs[:20]:
                        cats = j.get("categories", {}) or {}
                        save_job({
                            "title": j.get("text", ""),
                            "company": co.get("name", slug),
                            "location": cats.get("location", "Remote"),
                            "url": j.get("hostedUrl", ""),
                            "description": j.get("descriptionPlain", "")[:2000],
                            "tags": [cats.get("department", "")],
                            "source": f"lever:{slug}",
                            "source_kind": "ats",
                            "salary": "",
                            "posted_at": str(j.get("createdAt", "")),
                        })
                        new_count += 1
        except:
            pass
        time.sleep(0.2)
    return new_count

def scrape_smartrecruiters_batch(companies_batch):
    """Scrape SmartRecruiters companies."""
    import httpx
    new_count = 0
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    for co in companies_batch:
        slug = co.get("slug", "")
        sr_url = co.get("smartrecruiters_url")
        if not sr_url:
            continue
        try:
            with httpx.Client(follow_redirects=True) as client:
                resp = client.get(sr_url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    jobs = data.get("content", [])
                    for j in jobs[:20]:
                        loc = j.get("location", {}) or {}
                        city = loc.get("city", "")
                        country = loc.get("country", "")
                        loc_str = f"{city}, {country}".strip(", ") or "Remote"
                        save_job({
                            "title": j.get("name", ""),
                            "company": co.get("name", slug),
                            "location": loc_str,
                            "url": j.get("ref", ""),
                            "description": j.get("descriptionPlain", "")[:2000],
                            "tags": [],
                            "source": f"smartrecruiters:{slug}",
                            "source_kind": "ats",
                            "salary": "",
                            "posted_at": j.get("releasedDate", ""),
                        })
                        new_count += 1
        except:
            pass
        time.sleep(0.2)
    return new_count

def scrape_jobspy_batch(keywords, locations):
    """Scrape Indeed/LinkedIn via JobSpy."""
    new_count = 0
    try:
        from jobspy import Scraper
        scraper = Scraper()
        for kw in keywords:
            for loc in locations:
                try:
                    results = scraper.search(
                        site=["indeed", "linkedin"],
                        search_term=kw, location=loc,
                        results_wanted=20, hours_old=168)
                    if results and hasattr(results, 'jobs'):
                        for j in results.jobs:
                            save_job({
                                "title": getattr(j, 'title', ''),
                                "company": getattr(j, 'company', ''),
                                "location": getattr(j, 'location', loc),
                                "url": getattr(j, 'url', ''),
                                "description": str(getattr(j, 'description', ''))[:2000],
                                "tags": [kw],
                                "source": f"jobspy:{getattr(j, 'site', 'unknown')}",
                                "source_kind": "jobspy",
                                "salary": str(getattr(j, 'salary', '')) if getattr(j, 'salary', None) else "",
                                "posted_at": str(getattr(j, 'date_posted', '')) if getattr(j, 'date_posted', None) else "",
                            })
                            new_count += 1
                    time.sleep(1)
                except:
                    pass
    except ImportError:
        pass
    return new_count

def run_scrape_round():
    """Run one full scrape round across all 10K companies."""
    cp = load_checkpoint()
    companies = load_companies()
    
    if not companies:
        log("No companies found!")
        return 0
    
    total = len(companies)
    start_idx = cp["idx"] % total  # Resume from checkpoint
    batch_size = 200  # Companies per batch
    round_new = 0
    
    log(f"=== Scrape Round {cp['rounds']+1} ===")
    log(f"Companies: {total} | Starting at: {start_idx}")
    
    idx = start_idx
    while idx < total:
        batch = companies[idx:idx+batch_size]
        
        # Separate by ATS type
        gh_batch = [c for c in batch if c.get("ats") == "greenhouse"]
        lever_batch = [c for c in batch if c.get("ats") == "lever"]
        sr_batch = [c for c in batch if c.get("ats") == "smartrecruiters"]
        career_batch = [c for c in batch if c.get("ats") == "career_page"]
        
        t1 = scrape_greenhouse_batch(gh_batch) if gh_batch else 0
        t2 = scrape_lever_batch(lever_batch) if lever_batch else 0
        t3 = scrape_smartrecruiters_batch(sr_batch) if sr_batch else 0
        
        batch_new = t1 + t2 + t3
        round_new += batch_new
        
        log(f"  Batch {idx//batch_size+1}: +{batch_new} (GH:{t1} Lev:{t2} SR:{t3}) | Total this round: {round_new}")
        
        idx += batch_size
        
        # Save checkpoint every 5 batches
        if (idx - start_idx) % (batch_size * 5) == 0:
            cp["idx"] = idx
            cp["total_new"] += batch_new
            save_checkpoint(cp)
    
    # JobSpy at the end with ALL job categories from dedicated file
    try:
        from scripts.job_keywords import ALL_KEYWORDS, ALL_LOCATIONS
    except ImportError:
        from job_keywords import ALL_KEYWORDS, ALL_LOCATIONS
    
    js_new = scrape_jobspy_batch(ALL_KEYWORDS, ALL_LOCATIONS)
    round_new += js_new
    log(f"  JobSpy: +{js_new} jobs")
    
    # Update checkpoint for next round
    cp["idx"] = 0  # Reset for next round
    cp["total_new"] += round_new
    cp["rounds"] += 1
    save_checkpoint(cp)
    
    return round_new

def main():
    log("=" * 60)
    log("RENDER SCRAPER - 10K Company Edition")
    log(f"R2: {'connected' if R2_ACCOUNT_ID else 'not configured'}")
    log("=" * 60)
    
    # Init DB
    try:
        conn = get_db()
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key TEXT UNIQUE,
            title TEXT, company TEXT, location TEXT, url TEXT,
            description TEXT, tags TEXT, source TEXT, source_kind TEXT,
            salary TEXT, posted_at TEXT, first_seen_at TEXT
        )""")
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.close()
        log(f"DB ready. Current jobs: {total}")
    except Exception as e:
        log(f"DB init error: {e}")
    
    # Run scraping rounds
    log("Starting scrape from 10K company list...")
    new_jobs = run_scrape_round()
    
    # Final count
    try:
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.close()
        log(f"Done! +{new_jobs} new jobs. Total in DB: {total}")
    except:
        log(f"Done! +{new_jobs} new jobs")
    
    return new_jobs

if __name__ == "__main__":
    main()
