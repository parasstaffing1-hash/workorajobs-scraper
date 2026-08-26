#!/usr/bin/env python3
"""Surf the web for fresh jobs by keyword, last 24h (configurable).

Browses real job sites with Playwright and collects jobs posted in the
last N hours, exactly like a human searching:
  1. LinkedIn (in.linkedin.com)  - f_TPR=r86400 = last 24h filter
  2. Indeed India (in.indeed.com) - sort=date + exact pubDate from JSON
  3. Glassdoor India              - "posted in last 24h" filter
Stores everything in jobs.db, deduped by job key/URL.

Usage:
    python scripts/surf_fresh_jobs.py --query "Python Developer" --location "Bengaluru"
    python scripts/surf_fresh_jobs.py --query "Data Engineer" --location "" --hours 48
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

QUERY = "US IT Recruiter"  # default; override with --query
LOCATION = "Noida"         # default; override with --location
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def open_browser(pw):
    b = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = b.new_context(user_agent=UA, locale="en-US")
    return b, ctx


def goto(p, url, tries=3):
    for a in range(tries):
        try:
            p.goto(url, timeout=45000, wait_until="domcontentloaded")
            return True
        except Exception as e:
            print(f"    retry {a}: {e}")
            time.sleep(5)
    return False


# ---------------------------------------------------------------- LinkedIn
def scrape_linkedin(p, query, location) -> list[dict]:
    q = query.replace(" ", "%20")
    loc = location.replace(" ", "%20") if location else ""
    if q and loc:
        url = (
            f"https://in.linkedin.com/jobs/search?keywords={q}&location={loc}"
            f"&f_TPR=r86400"  # posted in last 24 hours
        )
    elif loc and not q:
        # Location-only: LinkedIn's city page keeps results scoped to the city
        slug = location.lower().split(",")[0].strip().replace(" ", "-")
        url = f"https://in.linkedin.com/jobs/{slug}-jobs?f_TPR=r86400"
    elif q and not loc:
        url = (
            f"https://in.linkedin.com/jobs/search?keywords={q}"
            f"&f_TPR=r86400"  # posted in last 24 hours
        )
    else:
        url = "https://in.linkedin.com/jobs?f_TPR=r86400"
    print(f"[linkedin] {url}")
    if not goto(p, url):
        return []
    time.sleep(4)
    for _ in range(6):
        p.mouse.wheel(0, 3000)
        time.sleep(1.2)
    html = p.content()
    from parsel import Selector

    sel = Selector(text=html)
    out = []
    for c in sel.css("li.base-card, .job-search-card, .base-search-card"):
        lines = [l.strip() for l in c.css("::text").getall() if l.strip()]
        if len(lines) < 3:
            continue
        # Layout: [title, title, company, location, meta..., X hours ago]
        title = lines[0]
        company = lines[2] if len(lines) > 2 else ""
        loc = ""
        rel = ""
        for line in lines[3:]:
            low = line.lower()
            if "ago" in low or "hour" in low or "day" in low or "week" in low:
                rel = line
            elif "apply" in low or "promoted" in low or "new" == low:
                continue
            elif not loc and ("india" in low or "," in line):
                loc = line
        date = c.css("time::attr(datetime)").get("")
        href = c.css("a::attr(href)").get("")
        posted_at = None
        if date:
            posted_at = date[:10] + "T00:00:00+00:00"
        elif rel:
            now = datetime.now(timezone.utc)
            mh = re.search(r"(\d+)\s*hour", rel)
            md = re.search(r"(\d+)\s*day", rel)
            if mh:
                posted_at = (now - timedelta(hours=int(mh.group(1)))).isoformat()
            elif md:
                posted_at = (now - timedelta(days=int(md.group(1)))).isoformat()
        jid = re.search(r"view/(\d+)", href or "")
        out.append({
            "title": title,
            "company": company,
            "location": loc,
            "url": href.split("?")[0] if href else "",
            "posted_at": posted_at,
            "posted_text": rel or (date[:10] if date else ""),
            "jobkey": jid.group(1) if jid else "",
            "source": "linkedin",
        })
    return out


# ---------------------------------------------------------------- Indeed
def _indeed_jobs_from_html(html):
    dec = json.JSONDecoder()
    jobs = []
    for m in re.finditer(r'"formattedRelativeTime"', html):
        pos = m.start()
        probe = pos
        found = None
        for _ in range(5000):
            idx = html.rfind("{", 0, probe)
            if idx < 0:
                break
            try:
                obj, end = dec.raw_decode(html[idx:])
                if end > (pos - idx) and "jobkey" in obj:
                    found = obj
                    break
            except Exception:
                pass
            probe = idx - 1
            if probe < 0:
                break
        if found:
            jobs.append(found)
    return jobs


def scrape_indeed(p, query, location) -> list[dict]:
    q = query.replace(" ", "+")
    l = location.replace(" ", "+")
    # Location-only mode: paginate to capture as many fresh jobs as possible
    max_pages = 8 if not q else 1
    url = f"https://in.indeed.com/jobs?q={q}&l={l}&sort=date"
    print(f"[indeed] {url} (pages={max_pages})")
    if not goto(p, url):
        return []
    time.sleep(3)
    p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(2)
    html = p.content()

    jobs = _indeed_jobs_from_html(html)
    # Follow next pages via ?start= (Indeed's pagination param)
    for page in range(2, max_pages + 1):
        time.sleep(3)  # Indeed rate-limits rapid repeat requests
        start = (page - 1) * 10
        page_url = f"https://in.indeed.com/jobs?q={q}&l={l}&sort=date&start={start}"
        if not goto(p, page_url):
            break
        time.sleep(2.5)
        p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)
        more = _indeed_jobs_from_html(p.content())
        if not more:
            break
        jobs.extend(more)

    # de-dup by jobkey
    seen = set()
    uniq = []
    for j in jobs:
        k = j.get("jobkey")
        if k in seen:
            continue
        seen.add(k)
        uniq.append(j)

    now = datetime.now(timezone.utc)
    out = []
    for j in uniq:
        ts = j.get("pubDate") or j.get("createDate") or j.get("datePublished")
        if isinstance(ts, (int, float)) and ts > 0:
            posted_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        else:
            rel = (j.get("formattedRelativeTime") or "").lower()
            if "just posted" in rel or "today" in rel:
                posted_at = now
            else:
                m2 = re.search(r"(\d+)\s*\+?\s*day", rel)
                posted_at = now - timedelta(days=int(m2.group(1))) if m2 else None
        link = j.get("link") or ""
        url = (
            f"https://in.indeed.com{link}"
            if link.startswith("/")
            else f"https://in.indeed.com/viewjob?jk={j.get('jobkey', '')}"
        )
        out.append({
            "title": j.get("title", ""),
            "company": j.get("company") or j.get("sourceEmployerName") or "",
            "location": j.get("formattedLocation") or "",
            "url": url,
            "posted_at": posted_at.isoformat() if posted_at else None,
            "posted_text": j.get("formattedRelativeTime") or "",
            "jobkey": j.get("jobkey", ""),
            "source": "indeed",
        })
    return out


# ---------------------------------------------------------------- apna
APNA_SLUGS = {"delhi": "in-delhi", "bengaluru": "in-bengaluru", "gurgaon": "in-gurgaon",
              "pune": "in-pune", "mumbai": "in-mumbai", "hyderabad": "in-hyderabad",
              "chennai": "in-chennai", "kolkata": "in-kolkata", "noida": "in-noida"}


def scrape_apna(p, query, location) -> list[dict]:
    """apna.co - 93K+ vacancies in Delhi. SSR JSON carries full job objects;
    paginates via ?page=N. Note: apna shows all-time jobs, no guest date
    filter, so we take jobs and let the caller's freshness filter decide."""
    if not location:
        return []
    city = location.lower().split(",")[0].strip()
    slug = APNA_SLUGS.get(city, "in-" + city.replace(" ", "-"))
    url = f"https://apna.co/jobs/{slug}"
    print(f"[apna] {url}")
    if not goto(p, url):
        return []
    time.sleep(4)
    out = []
    seen = set()
    for page in range(1, 12):
        if page > 1:
            if not goto(p, f"{url}?page={page}"):
                break
            time.sleep(3)
        html = p.content()
        # Extract job links + titles from rendered cards
        jobs = p.evaluate(
            """() => Array.from(document.querySelectorAll('a[href*=\"/job/\"]')).map(a => ({
              href: a.href,
              title: (a.querySelector('[class*=title], h3, h2') || a).textContent.trim().slice(0, 120),
            })).filter(j => /^https?:\/\/(www\.)?apna\.co\/job\//.test(j.href) && j.title.length > 3)"""
        )
        if not jobs:
            break
        added = 0
        for j in jobs:
            # apna URL: /job/{city}/{slug}-{id}
            m = re.search(r"-(\d+)$", j["href"])
            jid = m.group(1) if m else j["href"]
            if jid in seen:
                continue
            seen.add(jid)
            added += 1
            # apna ignores the keyword: same city feed every time, spanning
            # many cities. Keep the job's own city from its URL so the
            # location filter works, and skip it if it's not our city.
            um = re.match(r"https?://(?:www\.)?apna\.co/job/([^/]+)/", j["href"])
            jcity = um.group(1).replace("-", " ") if um else ""
            out.append({
                "title": j["title"],
                "company": "",
                "location": jcity or location,
                "url": j["href"],
                "posted_at": datetime.now(timezone.utc).isoformat(),  # live listing
                "posted_text": "live",
                "jobkey": jid,
                "source": "apna",
            })
        print(f"    apna page {page}: +{added} (total {len(out)})")
        if added == 0:
            break
    return out


# ---------------------------------------------------------------- Shine
SHINE_ALIASES = {"delhi": "delhi", "bengaluru": "bangalore", "gurgaon": "gurgaon",
                 "pune": "pune", "mumbai": "mumbai", "hyderabad": "hyderabad",
                 "chennai": "chennai", "kolkata": "kolkata", "noida": "noida"}


def scrape_shine(p, query, location) -> list[dict]:
    """Shine.com - India's big board (35K+ jobs in Delhi). Uses their
    public JSON search API: count + per-job fields incl. jPDate (posted date)."""
    if not location:
        return []
    slug = SHINE_ALIASES.get(location.lower().split(",")[0].strip(),
                             location.lower().split(",")[0].strip().replace(" ", "-"))
    q = query.replace(" ", "-").lower() if query else ""
    path = f"{q}-jobs-in-{slug}" if q else f"jobs-in-{slug}"
    url = f"https://www.shine.com/job-search/{path}"
    print(f"[shine] {url}")
    if not goto(p, url):
        return []
    time.sleep(3)

    fl = (
        "id,jRUrl,jPDate,jKwd,jCName,jJT,jLoc,jSal,jExp,jJobType,jPJ,"
        "jRR,jRE,jSJ,jCrwSt,early_applicant_badge"
    )
    out = []
    seen = set()
    now = datetime.now(timezone.utc)
    # Shine's search API sorts by relevance, so fresh jobs are scattered across
    # pages. Walk up to 40 pages (50/page = 2000 jobs) and keep fresh ones.
    api = (
        f"/api/v2/search/simple/?q={path}&qActual={path}&fl={fl}"
        f"&url={path}&only_facet=false&expansion=true&expert_edge_flag=true"
        f"&rows=50"
    )
    for page in range(1, 16):
        res = p.evaluate(
            "async (u) => { const r = await fetch(u); "
            "return {status: r.status, text: await r.text()}; }",
            f"{api}&page={page}",
        )
        if res["status"] != 200:
            break
        try:
            import json as _json

            d = _json.loads(res["text"])
        except Exception:
            break
        docs = d.get("results") or []
        if not docs:
            break
        for j in docs:
            jid = j.get("id")
            if jid in seen:
                continue
            seen.add(jid)
            pdate = j.get("jPDate")
            posted_at = None
            if isinstance(pdate, (int, float)) and pdate > 0:
                posted_at = datetime.fromtimestamp(pdate / 1000, tz=timezone.utc)
            elif isinstance(pdate, str) and re.match(r"\d{4}-\d{2}-\d{2}", pdate):
                try:
                    posted_at = datetime.fromisoformat(pdate.replace(" ", "T")).replace(tzinfo=timezone.utc)
                except Exception:
                    posted_at = datetime.fromisoformat(pdate[:10]).replace(tzinfo=timezone.utc)
            jurl = j.get("jRUrl") or ""
            title = j.get("jJT") or j.get("jKwd") or ""
            jid = str(jid)
            # Shine job URL: /jobs/{title-slug}/{company-slug}/{id}
            def slugify(s):
                s = re.sub(r"[^a-z0-9 ]", "", (s or "").lower())
                return "-".join(s.split())[:80]
            if jurl and jurl.startswith("http"):
                url = jurl
            elif jurl and jurl.startswith("/"):
                url = f"https://www.shine.com{jurl}"
            else:
                company = j.get("jCName") or ""
                url = f"https://www.shine.com/jobs/{slugify(title)}/{slugify(company)}/{jid}"
            out.append({
                "title": title,
                "company": j.get("jCName") or "",
                "location": ", ".join(j.get("jLoc")) if isinstance(j.get("jLoc"), list) else (j.get("jLoc") or location),
                "url": url,
                "posted_at": posted_at.isoformat() if posted_at else None,
                "posted_text": "",
                "jobkey": jid,
                "source": "shine",
            })
        if page % 10 == 0:
            time.sleep(1.5)
    return out


# ---------------------------------------------------------------- Glassdoor
def scrape_glassdoor(p, query, location) -> list[dict]:
    q = query.replace(" ", "-").lower()
    loc = (location or "india").replace(" ", "-").lower()
    # IC4477468 = Noida region code; fall back to a generic India search for other cities
    ic = "_IC4477468" if location and "noida" in location.lower() else ""
    url = (
        f"https://www.glassdoor.co.in/Job/{loc}-{q}-jobs-SRCH_IL.0,5{ic}_KO6,"
        f"{len(loc) + len(q) + 6}.htm"
    )
    print(f"[glassdoor] {url}")
    if not goto(p, url):
        return []
    time.sleep(4)
    for _ in range(4):
        p.mouse.wheel(0, 2500)
        time.sleep(1.2)
    html = p.content()
    from parsel import Selector

    sel = Selector(text=html)
    out = []
    for c in sel.css(
        "li[data-job-id], .JobsList_jobListItem__, .react-job-listing"
    ):
        title = c.css(".jobTitle, [data-testid='job-title'], a::attr(title)").get("")
        company = c.css(".employerName, [data-testid='job-employer']::text").get("")
        loc = c.css(".location, [data-testid='job-location']::text").get("")
        href = c.css("a::attr(href)").get("")
        jid = c.attrib.get("data-job-id", "")
        if not title.strip():
            continue
        out.append({
            "title": title.strip(),
            "company": company.strip(),
            "location": loc.strip(),
            "url": f"https://www.glassdoor.co.in{href}" if href and href.startswith("/") else href or "",
            "posted_at": None,  # Glassdoor hides dates from guests
            "posted_text": "",
            "jobkey": jid,
            "source": "glassdoor",
        })
    # Fallback: JSON-LD
    if not out:
        for m in re.finditer(r'application/ld\+json"', html):
            try:
                blob = html[m.end():]
                j = blob[blob.find(">"):]
            except Exception:
                continue
    return out


# ---------------------------------------------------------------- main
def main():
    import argparse

    ap = argparse.ArgumentParser(description="Surf the web for fresh jobs by keyword and/or location")
    ap.add_argument("--query", default=QUERY, help="job keyword(s), e.g. 'Python Developer'; '' for any role")
    ap.add_argument("--keywords", default=None,
                    help="comma-separated list of keywords to sweep (each gets its own fresh batch): "
                         "'Software Engineer,Data Engineer,React Developer'")
    ap.add_argument("--location", default=LOCATION,
                    help="city/region, e.g. 'Noida' or '' for remote/any; comma list to sweep several")
    ap.add_argument("--hours", type=int, default=24, help="freshness window in hours")
    ap.add_argument("--dry-run", action="store_true", help="don't write to DB")
    ap.add_argument("--match-title", default="auto",
                    help="title filter: 'auto' (require a keyword from query), 'any' (keep all), or a custom comma list")
    ap.add_argument("--searches", default=None,
                    help="path to a searches.yaml config; runs every saved search in it "
                         "(overrides --query/--keywords/--location/--hours per entry)")
    ap.add_argument("--db", default=str(DB),
                    help="path to the SQLite database (default jobs.db)")
    args = ap.parse_args()

    db_path = Path(args.db)

    # Load saved searches from searches.yaml when given. Each entry is its own
    # (queries, locations, hours, match) run, so a single invocation covers the
    # whole daily sweep.
    searches = []
    if args.searches:
        if yaml is None:
            sys.exit("PyYAML is required for --searches (pip install pyyaml)")
        try:
            data = yaml.safe_load(Path(args.searches).read_text(encoding="utf-8"))
        except Exception as exc:
            sys.exit(f"Failed to read {args.searches}: {exc}")
        entries = (data or {}).get("searches") or []
        for e in entries:
            queries = [k.strip() for k in str(e.get("keywords", "")).split(",") if k.strip()]
            locations = [l.strip() for l in str(e.get("locations", "")).split(",") if l.strip()] or [""]
            searches.append({
                "queries": queries or [""],
                "locations": locations,
                "hours": int(e.get("hours", 24)),
                "match": e.get("match", "auto"),
            })
        if not searches:
            sys.exit(f"No searches found in {args.searches}")
    else:
        query = args.query
        if args.keywords:
            queries = [k.strip() for k in args.keywords.split(",") if k.strip()]
        else:
            queries = [query]
        locations = [loc.strip() for loc in args.location.split(",") if loc.strip()] or [""]
        searches = [{"queries": queries, "locations": locations,
                     "hours": args.hours, "match": args.match_title}]

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    b, ctx = open_browser(pw)
    p = ctx.new_page()

    # Common city aliases (LinkedIn/Indeed spellings differ)
    ALIASES = {
        "gurgaon": ["gurgaon", "gurugram"],
        "bengaluru": ["bengaluru", "bangalore"],
        # NCR is one metro: a Noida search legitimately includes the whole belt
        "noida": ["noida", "greater noida", "gurugram", "gurgaon", "delhi", "faridabad", "ghaziabad", "dadri"],
        "delhi": ["delhi", "new delhi", "delhi ncr", "ncr", "noida", "gurugram", "gurgaon", "faridabad", "ghaziabad", "greater noida"],
        "mumbai": ["mumbai", "bombay"],
        "chennai": ["chennai", "madras"],
        "kolkata": ["kolkata", "calcutta"],
    }
    stop = {"the", "a", "an", "in", "of", "for", "and", "or", "with", "at"}

    conn = sqlite3.connect(db_path)
    grand = {"scraped": 0, "fresh": 0, "targeted": 0, "located": 0, "new": 0}

    def is_loc(j, locations):
        if not locations or locations == [""]:
            return True
        loc_val = j.get("location")
        if isinstance(loc_val, list):
            loc_val = ", ".join(loc_val)
        l = (loc_val or "").lower()
        for city in locations:
            c = city.lower().split(",")[0].strip()
            terms = ALIASES.get(c, [c])
            if any(t in l for t in terms):
                return True
        return False

    for search in searches:
        queries = search["queries"]
        locations = search["locations"]
        hours = search["hours"]
        match_title = search["match"]

        all_jobs = []
        for location in locations:
            # apna ignores the keyword (same city feed each time), so scrape it
            # once per location rather than once per keyword.
            print(f"\n### apna | Location: {location or 'anywhere'}")
            try:
                got = scrape_apna(p, "", location)
                print(f"  -> {len(got)} jobs")
                all_jobs.extend(got)
            except Exception as e:
                print(f"  -> apna FAILED: {e}")
        for query in queries:
            for location in locations:
                print(f"\n### Query: {query or 'any'} | Location: {location or 'anywhere'}")
                for fn in (scrape_linkedin, scrape_indeed, scrape_shine):
                    try:
                        got = fn(p, query, location)
                        print(f"  -> {len(got)} jobs")
                        all_jobs.extend(got)
                    except Exception as e:
                        print(f"  -> FAILED: {e}")
                    time.sleep(3)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        fresh = []
        for j in all_jobs:
            pa = j.get("posted_at")
            if not pa:
                continue  # no date info -> not provably fresh
            try:
                dt = datetime.fromisoformat(pa)
            except Exception:
                continue
            if dt >= cutoff:
                fresh.append(j)

        # Derive a title filter from the sweep's keywords so results match
        all_kw = set()
        for q in queries:
            all_kw.update(w for w in re.findall(r"[a-z0-9+#.-]+", q.lower()) if w not in stop)
        kw = sorted(all_kw)
        if match_title == "any" or not kw:
            targeted = list(fresh)
        elif match_title != "auto":
            terms = [t.strip().lower() for t in match_title.split(",") if t.strip()]
            targeted = [j for j in fresh if any(t in (j.get("title") or "").lower() for t in terms)]
        else:
            targeted = [
                j for j in fresh
                if any(k in (j.get("title") or "").lower() for k in kw)
            ]

        located = [j for j in targeted if is_loc(j, locations)]
        tag = ",".join(kw[:4]) or "any"
        tag += "," + ",".join(c.lower().split(",")[0].strip() for c in locations if c)
        print(f"\n=== {', '.join(queries) or 'any role'} in {', '.join(locations) or 'anywhere'}, last {hours}h ===")
        print(f"Scraped: {len(all_jobs)} | Fresh: {len(fresh)} | "
              f"Title-match: {len(targeted)} | Located: {len(located)}")
        for j in sorted(located, key=lambda x: x["posted_at"] or "", reverse=True)[:15]:
            print(f"  [{j['source']}] {j['posted_text'] or j['posted_at'][:10]} "
                  f"| {j['title'][:45]} | {j['company'][:28]} | {j['location'][:25]}")

        grand["scraped"] += len(all_jobs)
        grand["fresh"] += len(fresh)
        grand["targeted"] += len(targeted)
        grand["located"] += len(located)
        if args.dry_run:
            continue

        for j in located:
            if not j["title"] or not j["url"]:
                continue
            try:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO jobs
                       (dedupe_key, title, company, location, description, url, source,
                        source_kind, external_id, posted_at, salary, tags,
                        first_seen_at, last_seen_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        j["url"] or j["jobkey"], j["title"], j["company"], j["location"],
                        "", j["url"], j["source"], "browser", j["jobkey"],
                        j["posted_at"], "", tag,
                        datetime.now(timezone.utc).isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                if cur.rowcount > 0:
                    grand["new"] += 1
            except Exception:
                continue
        conn.commit()

    p.close()
    b.close()
    pw.stop()
    conn.close()
    print(f"\n=== TOTAL: {grand['scraped']} scraped | {grand['fresh']} fresh | "
          f"{grand['targeted']} title-match | {grand['located']} located | "
          f"{grand['new']} new inserted into {db_path}")


if __name__ == "__main__":
    main()
