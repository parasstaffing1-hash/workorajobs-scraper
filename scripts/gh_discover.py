#!/usr/bin/env python3
"""Scrape Greenhouse job board directory to discover company slugs,
then probe all ATS platforms and scrape valid boards."""
from __future__ import annotations
import json, re, sqlite3, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
import httpx

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP = ROOT / ".freebuff" / "gh_discover_cp.json"
LOG = ROOT / ".freebuff" / "gh_discover.log"
DB_LOCK = Lock()
S = httpx.Client(timeout=8, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

def log(m):
    l = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(l, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f: f.write(l + "\n")

def load_cp():
    if CP.exists():
        try: return json.loads(CP.read_text())
        except: pass
    return {"done": [], "new": 0, "valid": 0}

def save_cp(d):
    CP.parent.mkdir(parents=True, exist_ok=True)
    CP.write_text(json.dumps(d))

def discover_gh_slugs():
    """Discover Greenhouse slugs by scraping their embed pages."""
    slugs = set()
    # Greenhouse has an embeddable widget that lists companies
    # Try scraping the widget endpoint
    for page in range(1, 50):
        try:
            r = S.get(f"https://boards.greenhouse.io/embed/job_board?for=&b=greenhouse&page={page}")
            if r.status_code != 200:
                break
            found = re.findall(r'href="/([a-z0-9_-]+)/jobs"', r.text.lower())
            if not found:
                found = re.findall(r'data-board-token="([a-z0-9_-]+)"', r.text.lower())
            if not found:
                break
            slugs.update(found)
            if page % 10 == 0:
                log(f"  Page {page}: {len(slugs)} slugs found")
        except:
            break
    return slugs


def discover_from_playwright():
    """Use Playwright to scrape the Greenhouse job board directory."""
    slugs = set()
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = b.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        p = ctx.new_page()

        # Greenhouse job board directory
        p.goto("https://boards.greenhouse.io/", timeout=30000)
        time.sleep(3)

        # Extract company links
        links = p.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        for link in links:
            m = re.search(r"boards\.greenhouse\.io/([a-z0-9_-]+)/jobs", link.lower())
            if m:
                slugs.add(m.group(1))

        # Try the explore page
        try:
            p.goto("https://www.greenhouse.com/job-boards", timeout=30000)
            time.sleep(3)
            links2 = p.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            for link in links2:
                m = re.search(r"boards\.greenhouse\.io/([a-z0-9_-]+)", link.lower())
                if m:
                    slugs.add(m.group(1))
        except:
            pass

        b.close()
        pw.stop()
    except Exception as e:
        log(f"Playwright error: {e}")
    return slugs


def gh(slug):
    try:
        r = S.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        if r.status_code != 200: return []
        d = r.json(); jobs = d.get("jobs", [])
        if not jobs: return []
        return [{"title": j.get("title",""), "company": d.get("name",slug),
                 "location": (j.get("location",{}) or {}).get("name","") if isinstance(j.get("location"),dict) else str(j.get("location","")),
                 "url": j.get("absolute_url",""), "posted_at": j.get("updated_at") or j.get("created_at"),
                 "jobkey": str(j.get("id","")), "source": f"greenhouse:{slug}",
                 "description": (j.get("content") or "")[:500],
                 "tags": (j.get("departments") or [{}])[0].get("name","") if j.get("departments") else ""} for j in jobs]
    except: return []

def lv(slug):
    try:
        r = S.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if r.status_code != 200: return []
        d = r.json()
        if not isinstance(d, list) or not d: return []
        return [{"title": j.get("text",""), "company": j.get("categories",{}).get("team",slug),
                 "location": j.get("categories",{}).get("location",""),
                 "url": j.get("hostedUrl",""),
                 "posted_at": datetime.fromtimestamp(j.get("createdAt",0)/1000, tz=timezone.utc).isoformat() if j.get("createdAt") else None,
                 "jobkey": j.get("id",""), "source": f"lever:{slug}",
                 "description": (j.get("descriptionPlain") or "")[:500],
                 "tags": j.get("teamsPlain","")} for j in d]
    except: return []

def ab(slug):
    try:
        r = S.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if r.status_code != 200: return []
        d = r.json(); b = d.get("jobBoard",{}); ops = b.get("openings",[])
        if not ops: return []
        return [{"title": j.get("title",""), "company": b.get("name",slug),
                 "location": j.get("locationName",""), "url": j.get("url",""),
                 "posted_at": j.get("publishedAt"), "jobkey": j.get("id",""),
                 "source": f"ashby:{slug}", "description": "",
                 "tags": j.get("departmentName","")} for j in ops]
    except: return []

def sr(slug):
    try:
        r = S.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
        if r.status_code != 200: return []
        d = r.json(); c = d.get("content",[])
        if not c: return []
        return [{"title": j.get("name",""), "company": j.get("company",{}).get("name",slug),
                 "location": ((j.get("location") or {}).get("city","")+", "+((j.get("location") or {}).get("country",""))).strip(", "),
                 "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('ref','')}",
                 "posted_at": j.get("releasedDate"), "jobkey": str(j.get("id","")),
                 "source": f"smartrecruiters:{slug}", "description": "", "tags": ""} for j in c]
    except: return []

def probe(slug):
    for fn in [gh, lv, ab, sr]:
        jobs = fn(slug)
        if jobs: return jobs
    return []

def store(conn, jobs, tag):
    new = 0; now = datetime.now(timezone.utc).isoformat()
    with DB_LOCK:
        for j in jobs:
            if not j.get("title") or not j.get("url"): continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO jobs (dedupe_key,title,company,location,description,url,source,source_kind,external_id,posted_at,salary,tags,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (j["url"] or j.get("jobkey",""), j["title"], j.get("company",""),
                     j.get("location",""), j.get("description",""), j["url"],
                     j["source"], "ats", j.get("jobkey",""), j.get("posted_at"),
                     j.get("salary",""), tag, now, now))
                if cur.rowcount > 0: new += 1
            except: continue
        conn.commit()
    return new


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=30)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    # Step 1: Discover slugs
    log("Discovering Greenhouse slugs...")
    gh_slugs = discover_gh_slugs()
    log(f"Greenhouse embed: {len(gh_slugs)} slugs")

    pw_slugs = discover_from_playwright()
    log(f"Playwright: {len(pw_slugs)} slugs")

    all_slugs = gh_slugs | pw_slugs
    log(f"Total unique slugs: {len(all_slugs)}")

    if not all_slugs:
        log("No slugs discovered, using fallback")
        return

    cp = load_cp() if args.resume else {"done": [], "new": 0, "valid": 0}
    done = set(cp["done"])
    remaining = sorted(all_slugs - done)
    log(f"Already done: {len(done)}, Remaining: {len(remaining)}")

    conn = sqlite3.connect(DB)
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {before:,}")

    gn, gv = cp["new"], cp["valid"]
    start = time.time()
    bs = args.threads * 10

    for bi in range(0, len(remaining), bs):
        batch = remaining[bi:bi+bs]
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(probe, s): s for s in batch}
            for f in as_completed(futures):
                slug = futures[f]; done.add(slug)
                try:
                    jobs = f.result()
                    if jobs:
                        gv += 1
                        src = jobs[0].get("source","?")
                        new = store(conn, jobs, f"ghdir,{slug}")
                        gn += new
                        if new > 0:
                            log(f"  +{slug:30s} {src:30s} {len(jobs):4d} +{new:4d}")
                except: pass

        save_cp({"done": list(done), "new": gn, "valid": gv})
        el = time.time() - start
        cur = before + gn
        rate = gn / (el/60) if el > 0 else 0
        log(f"  Batch {bi//bs+1}: {cur:,} (+{gn:,}) | {gv} valid | {rate:.0f}/min")

    el = time.time() - start
    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    log(f"\n{'='*60}")
    log(f"Slugs: {len(done)} | Valid: {gv} | New: {gn:,} | Total: {final:,}")
    log(f"Time: {el/60:.1f}min | Gap 1M: {max(0,1000000-final):,}")
    log(f"{'='*60}")


if __name__ == "__main__":
    main()
