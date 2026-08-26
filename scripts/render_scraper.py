"""Render Scraper - Fetches jobs from web sources using HTTP."""
import os
import sys
import time
import sqlite3
import hashlib
import json
from datetime import datetime

DB_PATH = "/app/jobs.db"
LOG_PATH = "/app/logs/scraper.log"

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
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

def make_dedupe_key(title, company, location, url):
    raw = f"{title}|{company}|{location}|{url}".lower().strip()
    return hashlib.md5(raw.encode()).hexdigest()

def save_job(job):
    try:
        conn = get_db()
        dedupe = make_dedupe_key(
            job.get("title", ""),
            job.get("company", ""),
            job.get("location", ""),
            job.get("url", "")
        )
        conn.execute("""
            INSERT OR IGNORE INTO jobs 
            (dedupe_key, title, company, location, url, description, 
             tags, source, source_kind, salary, posted_at, first_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            dedupe, job.get("title", ""), job.get("company", ""),
            job.get("location", ""), job.get("url", ""),
            job.get("description", "")[:2000],
            json.dumps(job.get("tags", [])),
            job.get("source", "unknown"), job.get("source_kind", ""),
            job.get("salary", ""), job.get("posted_at", "")
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        log(f"Error saving: {e}")
        return False

def scrape_greenhouse():
    """Scrape Greenhouse ATS companies - FREE, no auth needed."""
    log("Scraping Greenhouse ATS...")
    jobs_scraped = 0
    companies = [
        "airbnb", "stripe", "spotify", "reddit", "discord",
        "figma", "notion", "vercel", "netlify", "cloudflare",
        "datadog", "gitlab", "github", "robinhood", "coinbase",
        "plaid", "rippling", "brex", "instacart", "doordash",
        "twitch", "pinterest", "snap", "lyft", "uber",
        "mongodb", "elastic", "databricks", "snowflake", "hashicorp"
    ]
    try:
        import httpx
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        for company in companies:
            try:
                url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
                with httpx.Client() as client:
                    resp = client.get(url, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        jobs = data.get("jobs", [])
                        for job in jobs[:15]:
                            loc = job.get("location", {})
                            save_job({
                                "title": job.get("title", ""),
                                "company": company.title(),
                                "location": loc.get("name", "Remote") if loc else "Remote",
                                "url": job.get("absolute_url", ""),
                                "description": str(job.get("content", ""))[:2000],
                                "tags": [],
                                "source": f"greenhouse:{company}",
                                "source_kind": "ats",
                                "salary": "",
                                "posted_at": job.get("updated_at", "")
                            })
                            jobs_scraped += 1
                        log(f"  {company}: {len(jobs)} jobs")
                time.sleep(0.5)
            except Exception as e:
                log(f"  {company} error: {e}")
        log(f"Greenhouse done: {jobs_scraped} jobs")
    except ImportError:
        log("httpx not installed")
    except Exception as e:
        log(f"Greenhouse error: {e}")
    return jobs_scraped

def scrape_lever():
    """Scrape Lever ATS companies - FREE, no auth needed."""
    log("Scraping Lever ATS...")
    jobs_scraped = 0
    companies = [
        "lever", "netlify", "postmates", "upstart", "gitlab",
        "posthog", "cal-com", "linear", "supabase", "planetscale",
        "vercel", "segment", "amplitude", "mixpanel", "invision"
    ]
    try:
        import httpx
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        for company in companies:
            try:
                url = f"https://api.lever.co/v0/postings/{company}?mode=json"
                with httpx.Client() as client:
                    resp = client.get(url, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        jobs = resp.json()
                        for job in jobs[:15]:
                            cats = job.get("categories", {})
                            save_job({
                                "title": job.get("text", ""),
                                "company": company.title().replace("-", " "),
                                "location": cats.get("location", "Remote"),
                                "url": job.get("hostedUrl", ""),
                                "description": job.get("descriptionPlain", "")[:2000],
                                "tags": [cats.get("department", "")],
                                "source": f"lever:{company}",
                                "source_kind": "ats",
                                "salary": "",
                                "posted_at": str(job.get("createdAt", ""))
                            })
                            jobs_scraped += 1
                        log(f"  {company}: {len(jobs)} jobs")
                time.sleep(0.5)
            except Exception as e:
                log(f"  {company} error: {e}")
        log(f"Lever done: {jobs_scraped} jobs")
    except ImportError:
        log("httpx not installed")
    except Exception as e:
        log(f"Lever error: {e}")
    return jobs_scraped

def scrape_smartrecruiters():
    """Scrape SmartRecruiters ATS companies - FREE."""
    log("Scraping SmartRecruiters...")
    jobs_scraped = 0
    companies = [
        "canva", "grab", "wise", "revolut", "n26",
        "ui-path", "mongodb", "redis", "elastic", "snyk"
    ]
    try:
        import httpx
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        for company in companies:
            try:
                url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
                with httpx.Client() as client:
                    resp = client.get(url, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        jobs = data.get("content", [])
                        for job in jobs[:10]:
                            loc = job.get("location", {})
                            save_job({
                                "title": job.get("name", ""),
                                "company": company.title().replace("-", " "),
                                "location": loc.get("city", "Remote") + ", " + loc.get("country", "") if loc else "Remote",
                                "url": job.get("ref", ""),
                                "description": job.get("Job ad content", {}).get("description", "")[:2000] if isinstance(job.get("Job ad content"), dict) else "",
                                "tags": [],
                                "source": f"smartrecruiters:{company}",
                                "source_kind": "ats",
                                "salary": "",
                                "posted_at": job.get("releasedDate", "")
                            })
                            jobs_scraped += 1
                        log(f"  {company}: {len(jobs)} jobs")
                time.sleep(0.5)
            except Exception as e:
                log(f"  {company} error: {e}")
        log(f"SmartRecruiters done: {jobs_scraped} jobs")
    except ImportError:
        log("httpx not installed")
    except Exception as e:
        log(f"SmartRecruiters error: {e}")
    return jobs_scraped

def scrape_jobspy():
    """Scrape using JobSpy (LinkedIn, Indeed, etc)."""
    log("Starting JobSpy scrape...")
    jobs_scraped = 0
    keywords = [
        "software engineer", "python developer", "full stack developer",
        "frontend developer", "backend developer", "data engineer",
        "devops engineer", "data scientist", "product manager",
        "cloud engineer", "react developer", "java developer"
    ]
    locations = [
        "New York", "San Francisco", "Austin", "Seattle",
        "Remote", "London", "Bangalore", "Berlin"
    ]
    try:
        from jobspy import Scraper
        scraper = Scraper()
        
        for keyword in keywords[:4]:
            for location in locations[:4]:
                try:
                    results = scraper.search(
                        site=["indeed", "linkedin"],
                        search_term=keyword,
                        location=location,
                        results_wanted=15,
                        hours_old=168
                    )
                    if results and hasattr(results, 'jobs'):
                        for job in results.jobs:
                            save_job({
                                "title": getattr(job, 'title', ''),
                                "company": getattr(job, 'company', ''),
                                "location": getattr(job, 'location', location),
                                "url": getattr(job, 'url', ''),
                                "description": str(getattr(job, 'description', ''))[:2000],
                                "tags": [keyword],
                                "source": f"jobspy:{getattr(job, 'site', 'unknown')}",
                                "source_kind": "jobspy",
                                "salary": str(getattr(job, 'salary', '')) if getattr(job, 'salary', None) else "",
                                "posted_at": str(getattr(job, 'date_posted', '')) if getattr(job, 'date_posted', None) else ""
                            })
                            jobs_scraped += 1
                    time.sleep(2)
                except Exception as e:
                    log(f"  {keyword} error: {e}")
        log(f"JobSpy done: {jobs_scraped} jobs")
    except ImportError:
        log("jobspy not installed")
    except Exception as e:
        log(f"JobSpy error: {e}")
    return jobs_scraped

def main():
    log("=" * 50)
    log("Render Scraper Starting...")
    log("=" * 50)
    
    try:
        c = get_db()
        total = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        c.close()
        log(f"Current jobs: {total}")
    except:
        log("Fresh database")
    
    while True:
        try:
            log("--- Starting scraping round ---")
            
            t1 = scrape_greenhouse()
            t2 = scrape_lever()
            t3 = scrape_smartrecruiters()
            t4 = scrape_jobspy()
            
            total_new = t1 + t2 + t3 + t4
            
            c = get_db()
            total_now = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            c.close()
            
            log(f"Round complete: +{total_new} new jobs (total: {total_now})")
            log("Waiting 30 minutes...")
            time.sleep(1800)
            
        except KeyboardInterrupt:
            log("Stopped")
            break
        except Exception as e:
            log(f"Error: {e}")
            time.sleep(300)

if __name__ == "__main__":
    main()
