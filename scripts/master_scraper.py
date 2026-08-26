#!/usr/bin/env python3
"""Master job scraper — combines every free source for maximum coverage.

Sources (all free, no API keys):
  1. JobSpy → LinkedIn (200+ jobs per search, bypasses 60-job guest cap)
  2. JobSpy → Indeed US (supplements our in.indeed.com)
  3. JobSpy → Google Jobs (meta-aggregator)
  4. surf_fresh_jobs → apna (250+ Indian jobs per city)
  5. surf_fresh_jobs → Shine (300+ Indian jobs per search)
  6. surf_fresh_jobs → Indeed India (15+ per search)

Usage:
    python scripts/master_scraper.py --query "Software Engineer" --location "Delhi" --hours 48
    python scripts/master_scraper.py --searches searches.yaml
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ──────────────────────────────────────────────────────────────────
# JobSpy scraper (LinkedIn, Indeed US, Google Jobs)
# ──────────────────────────────────────────────────────────────────
def scrape_jobspy(query: str, location: str, results_wanted: int = 50, sites: list[str] | None = None) -> list[dict]:
    """Use JobSpy to get LinkedIn + Indeed jobs. Loops with offset pagination
    until we start getting duplicates (exhausted all results)."""
    from jobspy import scrape_jobs, Country

    if sites is None:
        sites = ["linkedin", "indeed"]  # Google causes 429s - disabled
    loc = f"{location}, India" if location else ""
    safe_sites = [s for s in sites if s != "google"]

    all_jobs = []
    seen_urls = set()
    batch_size = min(results_wanted, 50)  # JobSpy caps at ~50 per call
    offset = 0
    max_pages = 5  # 5 × 50 = 250 unique jobs per search (5× old 60 cap)
    empty_streak = 0

    for page in range(max_pages):
        try:
            result = scrape_jobs(
                site_name=safe_sites,
                search_term=query,
                location=loc,
                results_wanted=batch_size,
                country=Country.INDIA if location else None,
                offset=offset,
            )
        except Exception as e:
            print(f"  [jobspy] page {page+1} ERROR: {e}")
            break

        if result is None or len(result) == 0:
            empty_streak += 1
            if empty_streak >= 2:
                break
            offset += batch_size
            time.sleep(1)
            continue

        empty_streak = 0
        new_count = 0
        for _, row in result.iterrows():
            url = row.get("job_url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            new_count += 1

            posted = None
            dp = row.get("date_posted")
            if dp is not None:
                try:
                    if hasattr(dp, "isoformat"):
                        posted = dp.isoformat()
                    else:
                        posted = str(dp)
                except Exception:
                    pass

            salary = ""
            if row.get("min_amount") and row.get("max_amount"):
                salary = f"{row['currency'] or ''} {row['min_amount']}-{row['max_amount']} {row.get('interval', '')}"

            all_jobs.append({
                "title": str(row.get("title", "")),
                "company": str(row.get("company", "")),
                "location": str(row.get("location", "")),
                "url": url,
                "posted_at": posted,
                "posted_text": str(row.get("date_posted", "")),
                "jobkey": str(row.get("id", "")),
                "source": f"jobspy:{row.get('site', 'unknown')}",
                "salary": salary,
                "description": str(row.get("description", ""))[:500] if row.get("description") else "",
                "tags": ",".join(str(s) for s in (row.get("skills") or []) if s),
            })

        print(f"  [jobspy] page {page+1}: +{new_count} new (total {len(all_jobs)})")
        if new_count == 0:
            break  # all duplicates — exhausted
        offset += batch_size
        time.sleep(0.5)  # reduced for speed

    print(f"  [jobspy] TOTAL: {len(all_jobs)} unique jobs from LinkedIn+Indeed")
    return all_jobs


# ──────────────────────────────────────────────────────────────────
# Surf scrapers (apna, Shine, Indeed India) — reuse surf_fresh_jobs
# ──────────────────────────────────────────────────────────────────
def _open_browser(pw):
    b = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = b.new_context(user_agent=UA, locale="en-US")
    return b, ctx


def _goto(p, url, tries=3):
    for a in range(tries):
        try:
            p.goto(url, timeout=45000, wait_until="domcontentloaded")
            return True
        except Exception as e:
            print(f"    retry {a}: {e}")
            time.sleep(5)
    return False


def scrape_surf_linkedin(p, query, location) -> list[dict]:
    """LinkedIn via Playwright (supplements JobSpy for pagination)."""
    # Import from surf_fresh_jobs
    sys.path.insert(0, str(ROOT / "scripts"))
    from surf_fresh_jobs import scrape_linkedin
    try:
        return scrape_linkedin(p, query, location)
    except Exception as e:
        print(f"  [surf:linkedin] ERROR: {e}")
        return []


def scrape_surf_apna(p, query, location) -> list[dict]:
    """apna.co — 93K+ Indian jobs per city."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from surf_fresh_jobs import scrape_apna
    try:
        return scrape_apna(p, query, location)
    except Exception as e:
        print(f"  [surf:apna] ERROR: {e}")
        return []


def scrape_surf_shine(p, query, location) -> list[dict]:
    """Shine.com — India's big board, 35K+ jobs."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from surf_fresh_jobs import scrape_shine
    try:
        return scrape_shine(p, query, location)
    except Exception as e:
        print(f"  [surf:shine] ERROR: {e}")
        return []


def scrape_surf_indeed(p, query, location) -> list[dict]:
    """Indeed India — supplement to JobSpy Indeed."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from surf_fresh_jobs import scrape_indeed
    try:
        return scrape_indeed(p, query, location)
    except Exception as e:
        print(f"  [surf:indeed] ERROR: {e}")
        return []


# ──────────────────────────────────────────────────────────────────
# SimplyHired scraper (via __NEXT_DATA__ — 3K+ results per search)
# ──────────────────────────────────────────────────────────────────
def scrape_simplyhired(p, query, location, max_pages=1) -> list[dict]:
    """SimplyHired has structured __NEXT_DATA__ with 20 jobs per page,
    totalResultCount often 3K+. Paginates via page=N&cursor=..."""
    q = query.replace(" ", "+") if query else ""
    l = location.replace(" ", "+") if location else ""
    base = f"https://www.simplyhired.com/search?q={q}&l={l}"
    print(f"  [simplyhired] {base}")

    out = []
    seen_keys = set()
    for page in range(1, max_pages + 1):
        url = base if page == 1 else f"{base}&pn={page}"
        if not _goto(p, url):
            break
        time.sleep(3)
        html = p.content()
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                      html, re.DOTALL)
        if not m:
            break
        try:
            data = json.loads(m.group(1))
        except Exception:
            break
        props = data.get("props", {}).get("pageProps", {})
        jobs_list = props.get("jobs", [])
        if not jobs_list:
            break
        result_count = props.get("resultCount", 0)
        if page == 1:
            print(f"    simplyhired: {result_count} total results")
        for j in jobs_list:
            jk = j.get("jobKey", "")
            if jk in seen_keys:
                continue
            seen_keys.add(jk)
            url_j = f"https://www.simplyhired.com/job/{jk}"
            company_obj = j.get("company") or {}
            if isinstance(company_obj, dict):
                company_name = company_obj.get("name", "")
            else:
                company_name = str(company_obj)
            location_obj = j.get("location") or {}
            if isinstance(location_obj, dict):
                loc_name = location_obj.get("name", "")
            else:
                loc_name = str(location_obj)
            out.append({
                "title": j.get("title", ""),
                "company": company_name,
                "location": loc_name,
                "url": url_j,
                "posted_at": None,
                "posted_text": "",
                "jobkey": jk,
                "source": "simplyhired",
                "description": j.get("snippet", ""),
                "tags": ",".join(j.get("requirements", [])[:5]),
            })
        print(f"    page {page}: +{len(jobs_list)} (total {len(out)})")
    return out


# ──────────────────────────────────────────────────────────────────
# Dice scraper (via React streaming — extract job data from chunks)
# ──────────────────────────────────────────────────────────────────
def scrape_dice(p, query, location, max_pages=5) -> list[dict]:
    """Dice uses React Server Components streaming. Job data is embedded
    in self.__next_f.push chunks. We extract companyName, jobTitle, etc."""
    q = query.replace(" ", "%20") if query else ""
    l = location.replace(" ", "%20") if location else ""
    base = f"https://www.dice.com/jobs?q={q}&location={l}&postedDate=ONE&pageSize=20"
    print(f"  [dice] {base}")

    out = []
    seen_ids = set()
    for page in range(1, max_pages + 1):
        url = f"{base}&page={page}"
        if not _goto(p, url):
            break
        time.sleep(4)
        html = p.content()
        chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)
        for chunk in chunks:
            try:
                unescaped = chunk.encode("utf-8").decode("unicode_escape")
            except Exception:
                continue
            for jm in re.finditer(
                r'"jobGuid":"([^"]+)".*?"jobTitle":"([^"]+)".*?"companyName":"([^"]+)"',
                unescaped,
            ):
                jguid = jm.group(1)
                if jguid in seen_ids:
                    continue
                seen_ids.add(jguid)
                job_url = f"https://www.dice.com/job-detail/{jguid}"
                loc_match = re.search(
                    rf'"jobGuid":"{re.escape(jguid)}".*?"formattedLocation":"([^"]+)"',
                    unescaped,
                )
                loc = loc_match.group(1) if loc_match else ""
                out.append({
                    "title": jm.group(2),
                    "company": jm.group(3),
                    "location": loc,
                    "url": job_url,
                    "posted_at": None,
                    "posted_text": "",
                    "jobkey": jguid,
                    "source": "dice",
                })
        if page == 1:
            print(f"    dice: {len(out)} jobs from page 1")
        else:
            print(f"    page {page}: total {len(out)}")
    return out


# ──────────────────────────────────────────────────────────────────
# Naukri bypass — scrape via Playwright with cookie persistence
# ──────────────────────────────────────────────────────────────────
def scrape_naukri(p, query, location) -> list[dict]:
    """Naukri.com — India's #1 job portal (uses Playwright to bypass CAPTCHA)."""
    q = query.replace(" ", "%20")
    loc = location.replace(" ", "%20") if location else ""
    url = f"https://www.naukri.com/{q.lower().replace(' ', '-')}-jobs-in-{loc.lower().replace(' ', '-')}?experience=0&jobAge=1"
    print(f"  [naukri] {url}")

    if not _goto(p, url):
        return []

    # Wait for page to load and check for CAPTCHA
    time.sleep(5)
    html = p.content()

    # Check for CAPTCHA/bot detection
    if "captcha" in html.lower() or "recaptcha" in html.lower():
        print("  [naukri] CAPTCHA detected — trying alternative approach")
        # Try the API endpoint directly
        api_url = f"https://www.naukri.com/jobapi/v3/search?noOfResults=20&urlType=search_by_key_loc&searchType=adv&keyword={q}&location={loc}&pageNo=1"
        result = p.evaluate(
            """async (url) => {
                const r = await fetch(url, {
                    headers: {
                        'appid': '109',
                        'systemid': '109',
                        'Authorization': '2YotnFZFEjr1zCsic1A2',
                        'Content-Type': 'application/json'
                    }
                });
                return {status: r.status, text: await r.text()};
            }""",
            api_url,
        )
        if result["status"] == 200:
            try:
                data = json.loads(result["text"])
                jobs = data.get("jobDetails", [])
                out = []
                for j in jobs:
                    out.append({
                        "title": j.get("title", ""),
                        "company": j.get("companyName", ""),
                        "location": j.get("placeholders", [{}])[0].get("label", "") if j.get("placeholders") else "",
                        "url": f"https://www.naukri.com{j.get('jdURL', '')}",
                        "posted_at": None,
                        "posted_text": j.get("footerPlaceholderLabel", ""),
                        "jobkey": str(j.get("jobId", "")),
                        "source": "naukri",
                    })
                print(f"  [naukri] {len(out)} jobs via API")
                return out
            except Exception:
                pass
        return []

    # Fallback: parse rendered cards
    from parsel import Selector
    sel = Selector(text=html)
    out = []
    for card in sel.css(".srp-grid .tuple"):
        title = card.css(".title::text").get("").strip()
        company = card.css(".companyName::text").get("").strip()
        loc_text = card.css(".location::text").get("").strip()
        href = card.css("a::attr(href)").get("")
        jid = card.css("a::attr(data-id)").get("")
        if title:
            out.append({
                "title": title,
                "company": company,
                "location": loc_text,
                "url": href or "",
                "posted_at": None,
                "posted_text": "",
                "jobkey": jid or "",
                "source": "naukri",
            })

    print(f"  [naukri] {len(out)} jobs parsed from HTML")
    return out


# ──────────────────────────────────────────────────────────────────
# Location aliases
# ──────────────────────────────────────────────────────────────────
ALIASES = {
    "gurgaon": ["gurgaon", "gurugram"],
    "bengaluru": ["bengaluru", "bangalore"],
    "noida": ["noida", "greater noida", "gurugram", "gurgaon", "delhi", "faridabad", "ghaziabad", "dadri"],
    "delhi": ["delhi", "new delhi", "delhi ncr", "ncr", "noida", "gurugram", "gurgaon", "faridabad", "ghaziabad", "greater noida"],
    "mumbai": ["mumbai", "bombay"],
    "chennai": ["chennai", "madras"],
    "kolkata": ["kolkata", "calcutta"],
    "pune": ["pune", "pimpri", "chinchwad"],
    "hyderabad": ["hyderabad", "secunderabad"],
}
STOP = {"the", "a", "an", "in", "of", "for", "and", "or", "with", "at"}


def is_loc(j, locations):
    if not locations or locations == [""]:
        return True
    loc_val = j.get("location", "")
    if isinstance(loc_val, list):
        loc_val = ", ".join(loc_val)
    l = loc_val.lower()
    for city in locations:
        c = city.lower().split(",")[0].strip()
        terms = ALIASES.get(c, [c])
        if any(t in l for t in terms):
            return True
    return False


# ──────────────────────────────────────────────────────────────────
# DB storage
# ──────────────────────────────────────────────────────────────────
def store_jobs(conn, jobs, tag):
    new = 0
    now = datetime.now(timezone.utc).isoformat()
    for j in jobs:
        if not j.get("title") or not j.get("url"):
            continue
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (dedupe_key, title, company, location, description, url, source,
                    source_kind, external_id, posted_at, salary, tags,
                    first_seen_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    j["url"] or j.get("jobkey", ""),
                    j["title"],
                    j.get("company", ""),
                    j.get("location", ""),
                    j.get("description", ""),
                    j["url"],
                    j["source"],
                    "browser",
                    j.get("jobkey", ""),
                    j.get("posted_at"),
                    j.get("salary", ""),
                    tag,
                    now,
                    now,
                ),
            )
            if cur.rowcount > 0:
                new += 1
        except Exception:
            continue
    conn.commit()
    return new


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────
def main():
    import argparse

    ap = argparse.ArgumentParser(description="Master job scraper — every free source, no API keys")
    ap.add_argument("--query", default="Software Engineer", help="job keyword(s)")
    ap.add_argument("--location", default="", help="city/region, comma list for multi-city")
    ap.add_argument("--hours", type=int, default=48, help="freshness window in hours")
    ap.add_argument("--dry-run", action="store_true", help="don't write to DB")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--searches", default=None, help="path to searches.yaml")
    ap.add_argument("--sources", default="jobspy,surf,simplyhired,dice",
                    help="comma-separated: jobspy, surf, simplyhired, dice, naukri")
    ap.add_argument("--start", type=int, default=0,
                    help="skip first N searches (to resume a failed sweep)")
    args = ap.parse_args()

    db_path = Path(args.db)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    # Load saved searches
    searches = []
    if args.searches:
        if yaml is None:
            sys.exit("PyYAML required for --searches (pip install pyyaml)")
        data = yaml.safe_load(Path(args.searches).read_text(encoding="utf-8"))
        entries = (data or {}).get("searches") or []
        for e in entries:
            queries = [k.strip() for k in str(e.get("keywords", "")).split(",") if k.strip()]
            locations = [l.strip() for l in str(e.get("locations", "")).split(",") if l.strip()] or [""]
            searches.append({
                "queries": queries or [""],
                "locations": locations,
                "hours": int(e.get("hours", 48)),
            })
    else:
        queries = [q.strip() for q in args.query.split(",") if q.strip()]
        locations = [l.strip() for l in args.location.split(",") if l.strip()] or [""]
        searches = [{"queries": queries, "locations": locations, "hours": args.hours}]

    # Start browser only if surf/simplyhired/dice/naukri sources need it
    pw = b = ctx = p = None
    needs_browser = any(s in sources for s in ("surf", "simplyhired", "dice", "naukri"))
    if needs_browser:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        b, ctx = _open_browser(pw)
        p = ctx.new_page()

    conn = sqlite3.connect(db_path)
    grand = {"scraped": 0, "new": 0}

    for si, search in enumerate(searches):
        if si < args.start:
            print(f"\n--- Skipping search {si+1}/{len(searches)} (--start={args.start})")
            continue
        queries = search["queries"]
        locations = search["locations"]
        hours = search["hours"]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        print(f"\n{'#'*60}\n# Search {si+1}/{len(searches)}: {', '.join(queries)} in {', '.join(locations)}\n{'#'*60}")

        all_jobs = []

        # ── JobSpy sources (LinkedIn, Indeed US, Google) ──
        if "jobspy" in sources:
            for q in queries:
                for loc in locations:
                    print(f"\n### [JobSpy] {q or 'any'} | {loc or 'anywhere'}")
                    try:
                        jobs = scrape_jobspy(q, loc, results_wanted=50)
                        all_jobs.extend(jobs)
                    except Exception as e:
                        print(f"  [jobspy] FAILED: {e}")
                    time.sleep(0.5)

        # ── Surf sources (apna, Shine, Indeed India, LinkedIn paginated) ──
        if "surf" in sources:
            # apna: once per location (ignores keywords)
            for loc in locations:
                print(f"\n### [apna] Location: {loc}")
                try:
                    jobs = scrape_surf_apna(p, "", loc)
                    all_jobs.extend(jobs)
                except Exception as e:
                    print(f"  [apna] FAILED: {e}")

            # LinkedIn + Indeed + Shine: per query×location
            for q in queries:
                for loc in locations:
                    print(f"\n### [Surf] {q or 'any'} | {loc}")
                    for name, fn in [("linkedin", scrape_surf_linkedin),
                                     ("indeed", scrape_surf_indeed),
                                     ("shine", scrape_surf_shine)]:
                        try:
                            jobs = fn(p, q, loc)
                            all_jobs.extend(jobs)
                        except Exception as e:
                            print(f"  [{name}] FAILED: {e}")
                        time.sleep(2)

        # ── SimplyHired (3K+ results via __NEXT_DATA__) ──
        if "simplyhired" in sources:
            for q in queries:
                for loc in locations:
                    print(f"\n### [SimplyHired] {q or 'any'} | {loc or 'anywhere'}")
                    try:
                        jobs = scrape_simplyhired(p, q, loc)
                        all_jobs.extend(jobs)
                    except Exception as e:
                        print(f"  [simplyhired] FAILED: {e}")
                    time.sleep(2)

        # ── Dice (tech jobs, React streaming) ──
        if "dice" in sources:
            for q in queries:
                for loc in locations:
                    print(f"\n### [Dice] {q or 'any'} | {loc or 'anywhere'}")
                    try:
                        jobs = scrape_dice(p, q, loc)
                        all_jobs.extend(jobs)
                    except Exception as e:
                        print(f"  [dice] FAILED: {e}")
                    time.sleep(2)

        # ── Naukri ──
        if "naukri" in sources:
            for q in queries:
                for loc in locations:
                    print(f"\n### [Naukri] {q} | {loc}")
                    try:
                        jobs = scrape_naukri(p, q, loc)
                        all_jobs.extend(jobs)
                    except Exception as e:
                        print(f"  [naukri] FAILED: {e}")

        # ── Filter by freshness ──
        fresh = []
        for j in all_jobs:
            pa = j.get("posted_at")
            if not pa:
                # No date from source — keep if URL looks like a real job posting
                # (apna/Shine jobs without dates are still real live listings)
                url = j.get("url", "")
                if url and j.get("title"):
                    fresh.append(j)
                continue
            try:
                dt = datetime.fromisoformat(str(pa).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt >= cutoff:
                fresh.append(j)

        print(f"\n=== {', '.join(queries)} in {', '.join(locations)}, last {hours}h ===")
        print(f"Scraped: {len(all_jobs)} | Fresh: {len(fresh)}")

        grand["scraped"] += len(all_jobs)

        if args.dry_run:
            for j in sorted(fresh, key=lambda x: x.get("posted_at") or "", reverse=True)[:20]:
                print(f"  [{j['source']}] {j['title'][:45]} | {j['company'][:25]} | {j['location'][:20]}")
            continue

        # ── Store ──
        tag = ",".join(w for q in queries for w in q.lower().split()[:3]) or "any"
        tag += "," + ",".join(c.lower().split(",")[0].strip() for c in locations if c)
        tag = re.sub(r"[^a-z0-9,]+", ",", tag.lower())[:100]

        new = store_jobs(conn, fresh, tag)
        grand["new"] += new
        print(f"  [DB] +{new} new jobs stored")

        # Checkpoint: record completed search so --start can resume
        cp = db_path.parent / ".freebuff" / "sweep_checkpoint.txt"
        cp.write_text(str(si + 1), encoding="utf-8")

    if p:
        p.close()
    if b:
        b.close()
    if pw:
        pw.stop()
    conn.close()

    print(f"\n{'='*60}")
    print(f"MASTER SCRAPE COMPLETE")
    print(f"Total scraped: {grand['scraped']}")
    print(f"New inserted:  {grand['new']}")
    print(f"Database: {db_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
