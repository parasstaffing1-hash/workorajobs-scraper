#!/usr/bin/env python3
"""
Scrape jobs from every major free source on the web — no API keys needed.

Sources:
1. Google Jobs - meta aggregator (Indeed, LinkedIn, Glassdoor, ZipRecruiter, etc.)
2. Jooble - meta job search engine (free API)
3. Monster - global job board
4. Dice - tech-focused job board
5. SimplyHired - aggregates from many sources
6. Wellfound - startup jobs (formerly AngelList)
7. BuiltIn - tech company jobs
8. Naukri - India's biggest job board
9. LinkedIn - with pagination (bypass 60-job guest cap)
10. Indeed - multi-page pagination

Usage:
    python scripts/scrape_web_jobs.py --query "Software Engineer" --location "Noida"
    python scripts/scrape_web_jobs.py --searches searches.yaml
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"

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


# ---------------------------------------------------------------- Google Jobs
def scrape_google_jobs(p, query, location) -> list[dict]:
    """Google Jobs - the biggest meta aggregator. Shows results from Indeed,
    LinkedIn, Glassdoor, ZipRecruiter, and dozens of other sources."""
    q = query.replace(" ", "+")
    l = location.replace(" ", "+") if location else ""
    url = f"https://www.google.com/search?q={q}+jobs+in+{l}&ibp=htl;jobs"
    print(f"[google] {url}")
    if not goto(p, url):
        return []
    time.sleep(4)

    # Scroll to load more results
    for _ in range(8):
        p.mouse.wheel(0, 3000)
        time.sleep(1.2)

    html = p.content()
    out = []

    # Google Jobs embeds structured data in JSON-LD
    for m in re.finditer(r'application/ld\+json"', html):
        try:
            blob = html[m.end():]
            j = blob[blob.find(">") + 1:blob.find("</script>")]
            data = json.loads(j)
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                out.append({
                    "title": data.get("name", ""),
                    "company": data.get("hiringOrganization", {}).get("name", ""),
                    "location": data.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                        if isinstance(data.get("jobLocation"), dict) else "",
                    "url": data.get("url", ""),
                    "posted_at": data.get("datePosted", ""),
                    "posted_text": "",
                    "jobkey": data.get("identifier", {}).get("value", ""),
                    "source": "google_jobs",
                })
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        loc = item.get("jobLocation", {})
                        if isinstance(loc, dict):
                            loc = loc.get("address", {}).get("addressLocality", "")
                        elif isinstance(loc, list) and loc:
                            loc = loc[0].get("address", {}).get("addressLocality", "")
                        out.append({
                            "title": item.get("name", ""),
                            "company": item.get("hiringOrganization", {}).get("name", ""),
                            "location": str(loc),
                            "url": item.get("url", ""),
                            "posted_at": item.get("datePosted", ""),
                            "posted_text": "",
                            "jobkey": item.get("identifier", {}).get("value", ""),
                            "source": "google_jobs",
                        })
        except Exception:
            continue

    # Fallback: parse from rendered cards
    if not out:
        from parsel import Selector
        sel = Selector(text=html)
        for card in sel.css("[data-ved]"):
            title = card.css("h3::text").get("")
            if not title or len(title) < 5:
                continue
            out.append({
                "title": title,
                "company": "",
                "location": location or "",
                "url": card.css("a::attr(href)").get(""),
                "posted_at": None,
                "posted_text": "",
                "jobkey": "",
                "source": "google_jobs",
            })

    return out


# ---------------------------------------------------------------- Jooble
def scrape_jooble(query, location) -> list[dict]:
    """Jooble - meta job search engine with free API."""
    q = query.replace(" ", "%20")
    l = location.replace(" ", "%20") if location else ""
    url = f"https://jooble.org/api/"
    print(f"[jooble] API search: {query} in {location or 'anywhere'}")
    try:
        import httpx
        resp = httpx.post(
            url,
            json={"keywords": query, "location": l or "", "page": 1},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  -> Jooble API returned {resp.status_code}")
            return []
        data = resp.json()
    except Exception as e:
        print(f"  -> Jooble FAILED: {e}")
        return []

    out = []
    now = datetime.now(timezone.utc)
    for j in (data.get("jobs") or []):
        posted_at = j.get("date") or j.get("datePosted")
        if posted_at:
            try:
                posted_at = datetime.fromisoformat(posted_at.replace("Z", "+00:00")).isoformat()
            except Exception:
                posted_at = now.isoformat()
        else:
            posted_at = now.isoformat()
        out.append({
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "location": j.get("location", ""),
            "url": j.get("url", ""),
            "posted_at": posted_at,
            "posted_text": j.get("date", ""),
            "jobkey": j.get("id", ""),
            "source": "jooble",
        })
    return out


# ---------------------------------------------------------------- Monster
def scrape_monster(p, query, location) -> list[dict]:
    """Monster - global job board with public search."""
    q = query.replace(" ", "+")
    l = location.replace(" ", "+") if location else ""
    url = f"https://www.monster.com/jobs/search?q={q}&where={l}&page=1&so=m.h.sh"
    print(f"[monster] {url}")
    if not goto(p, url):
        return []
    time.sleep(3)

    for _ in range(5):
        p.mouse.wheel(0, 2500)
        time.sleep(1)

    html = p.content()
    out = []

    # Monster embeds job data in JSON-LD
    for m in re.finditer(r'application/ld\+json"', html):
        try:
            blob = html[m.end():]
            j = blob[blob.find(">") + 1:blob.find("</script>")]
            data = json.loads(j)
            if isinstance(data, dict) and data.get("@type") == "JobPosting":
                out.append({
                    "title": data.get("name", ""),
                    "company": data.get("hiringOrganization", {}).get("name", ""),
                    "location": data.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                        if isinstance(data.get("jobLocation"), dict) else "",
                    "url": data.get("url", ""),
                    "posted_at": data.get("datePosted", ""),
                    "posted_text": "",
                    "jobkey": data.get("identifier", {}).get("value", ""),
                    "source": "monster",
                })
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        out.append({
                            "title": item.get("name", ""),
                            "company": item.get("hiringOrganization", {}).get("name", ""),
                            "location": item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                                if isinstance(item.get("jobLocation"), dict) else "",
                            "url": item.get("url", ""),
                            "posted_at": item.get("datePosted", ""),
                            "posted_text": "",
                            "jobkey": item.get("identifier", {}).get("value", ""),
                            "source": "monster",
                        })
        except Exception:
            continue

    # Fallback: parse rendered cards
    if not out:
        from parsel import Selector
        sel = Selector(text=html)
        for card in sel.css("[class*='job-card'], [class*='JobCard'], [class*='card-content']"):
            title = card.css("h2::text, [class*='title']::text").get("")
            company = card.css("[class*='company']::text").get("")
            loc = card.css("[class*='location']::text").get("")
            href = card.css("a::attr(href)").get("")
            if title and len(title) > 3:
                out.append({
                    "title": title.strip(),
                    "company": company.strip(),
                    "location": loc.strip(),
                    "url": href or "",
                    "posted_at": None,
                    "posted_text": "",
                    "jobkey": "",
                    "source": "monster",
                })

    return out


# ---------------------------------------------------------------- Dice
def scrape_dice(p, query, location) -> list[dict]:
    """Dice - tech-focused job board."""
    q = query.replace(" ", "%20")
    l = location.replace(" ", "%20") if location else ""
    url = f"https://www.dice.com/jobs?q={q}&location={l}&postedDate=ONE&page=1&pageSize=20"
    print(f"[dice] {url}")
    if not goto(p, url):
        return []
    time.sleep(3)

    for _ in range(5):
        p.mouse.wheel(0, 2500)
        time.sleep(1)

    html = p.content()
    out = []

    # Dice embeds JSON-LD
    for m in re.finditer(r'application/ld\+json"', html):
        try:
            blob = html[m.end():]
            j = blob[blob.find(">") + 1:blob.find("</script>")]
            data = json.loads(j)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        out.append({
                            "title": item.get("name", ""),
                            "company": item.get("hiringOrganization", {}).get("name", ""),
                            "location": item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                                if isinstance(item.get("jobLocation"), dict) else "",
                            "url": item.get("url", ""),
                            "posted_at": item.get("datePosted", ""),
                            "posted_text": "",
                            "jobkey": item.get("identifier", {}).get("value", ""),
                            "source": "dice",
                        })
        except Exception:
            continue

    # Fallback: parse rendered cards
    if not out:
        from parsel import Selector
        sel = Selector(text=html)
        for card in sel.css("[class*='job-card'], [class*='JobCard'], [id*='job-card']"):
            title = card.css("h3::text, [class*='title']::text").get("")
            company = card.css("[class*='company']::text").get("")
            loc = card.css("[class*='location']::text").get("")
            href = card.css("a::attr(href)").get("")
            if title and len(title) > 3:
                out.append({
                    "title": title.strip(),
                    "company": company.strip(),
                    "location": loc.strip(),
                    "url": href or "",
                    "posted_at": None,
                    "posted_text": "",
                    "jobkey": "",
                    "source": "dice",
                })

    return out


# ---------------------------------------------------------------- SimplyHired
def scrape_simplyhired(p, query, location) -> list[dict]:
    """SimplyHired - aggregates from many sources."""
    q = query.replace(" ", "+")
    l = location.replace(" ", "+") if location else ""
    url = f"https://www.simplyhired.com/search?q={q}&l={l}&fdb=1"
    print(f"[simplyhired] {url}")
    if not goto(p, url):
        return []
    time.sleep(3)

    for _ in range(5):
        p.mouse.wheel(0, 2500)
        time.sleep(1)

    html = p.content()
    out = []

    # SimplyHired embeds JSON-LD
    for m in re.finditer(r'application/ld\+json"', html):
        try:
            blob = html[m.end():]
            j = blob[blob.find(">") + 1:blob.find("</script>")]
            data = json.loads(j)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        out.append({
                            "title": item.get("name", ""),
                            "company": item.get("hiringOrganization", {}).get("name", ""),
                            "location": item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                                if isinstance(item.get("jobLocation"), dict) else "",
                            "url": item.get("url", ""),
                            "posted_at": item.get("datePosted", ""),
                            "posted_text": "",
                            "jobkey": item.get("identifier", {}).get("value", ""),
                            "source": "simplyhired",
                        })
        except Exception:
            continue

    # Fallback: parse rendered cards
    if not out:
        from parsel import Selector
        sel = Selector(text=html)
        for card in sel.css("[class*='job-card'], [class*='jobposting'], [class*='result']"):
            title = card.css("h2::text, [class*='title']::text").get("")
            company = card.css("[class*='company']::text").get("")
            loc = card.css("[class*='location']::text").get("")
            href = card.css("a::attr(href)").get("")
            if title and len(title) > 3:
                out.append({
                    "title": title.strip(),
                    "company": company.strip(),
                    "location": loc.strip(),
                    "url": href or "",
                    "posted_at": None,
                    "posted_text": "",
                    "jobkey": "",
                    "source": "simplyhired",
                })

    return out


# ---------------------------------------------------------------- Wellfound (AngelList)
def scrape_wellfound(p, query, location) -> list[dict]:
    """Wellfound - startup jobs (formerly AngelList)."""
    q = query.replace(" ", "%20")
    l = location.replace(" ", "%20") if location else ""
    url = f"https://wellfound.com/jobs?role={q}&location={l}"
    print(f"[wellfound] {url}")
    if not goto(p, url):
        return []
    time.sleep(4)

    for _ in range(6):
        p.mouse.wheel(0, 3000)
        time.sleep(1.2)

    html = p.content()
    out = []

    # Wellfound embeds job data in JSON-LD
    for m in re.finditer(r'application/ld\+json"', html):
        try:
            blob = html[m.end():]
            j = blob[blob.find(">") + 1:blob.find("</script>")]
            data = json.loads(j)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        out.append({
                            "title": item.get("name", ""),
                            "company": item.get("hiringOrganization", {}).get("name", ""),
                            "location": item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                                if isinstance(item.get("jobLocation"), dict) else "",
                            "url": item.get("url", ""),
                            "posted_at": item.get("datePosted", ""),
                            "posted_text": "",
                            "jobkey": item.get("identifier", {}).get("value", ""),
                            "source": "wellfound",
                        })
        except Exception:
            continue

    # Fallback: parse rendered cards
    if not out:
        from parsel import Selector
        sel = Selector(text=html)
        for card in sel.css("[class*='job-card'], [class*='job-listing'], [class*='jobposting']"):
            title = card.css("h2::text, [class*='title']::text").get("")
            company = card.css("[class*='company']::text").get("")
            loc = card.css("[class*='location']::text").get("")
            href = card.css("a::attr(href)").get("")
            if title and len(title) > 3:
                out.append({
                    "title": title.strip(),
                    "company": company.strip(),
                    "location": loc.strip(),
                    "url": href or "",
                    "posted_at": None,
                    "posted_text": "",
                    "jobkey": "",
                    "source": "wellfound",
                })

    return out


# ---------------------------------------------------------------- BuiltIn
def scrape_builtin(p, query, location) -> list[dict]:
    """BuiltIn - tech company jobs."""
    q = query.replace(" ", "+")
    l = location.replace(" ", "+") if location else ""
    url = f"https://builtin.com/jobs?search={q}&location={l}"
    print(f"[builtin] {url}")
    if not goto(p, url):
        return []
    time.sleep(3)

    for _ in range(5):
        p.mouse.wheel(0, 2500)
        time.sleep(1)

    html = p.content()
    out = []

    # BuiltIn embeds JSON-LD
    for m in re.finditer(r'application/ld\+json"', html):
        try:
            blob = html[m.end():]
            j = blob[blob.find(">") + 1:blob.find("</script>")]
            data = json.loads(j)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        out.append({
                            "title": item.get("name", ""),
                            "company": item.get("hiringOrganization", {}).get("name", ""),
                            "location": item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                                if isinstance(item.get("jobLocation"), dict) else "",
                            "url": item.get("url", ""),
                            "posted_at": item.get("datePosted", ""),
                            "posted_text": "",
                            "jobkey": item.get("identifier", {}).get("value", ""),
                            "source": "builtin",
                        })
        except Exception:
            continue

    # Fallback: parse rendered cards
    if not out:
        from parsel import Selector
        sel = Selector(text=html)
        for card in sel.css("[class*='job-card'], [class*='job-listing'], [class*='jobposting']"):
            title = card.css("h2::text, [class*='title']::text").get("")
            company = card.css("[class*='company']::text").get("")
            loc = card.css("[class*='location']::text").get("")
            href = card.css("a::attr(href)").get("")
            if title and len(title) > 3:
                out.append({
                    "title": title.strip(),
                    "company": company.strip(),
                    "location": loc.strip(),
                    "url": href or "",
                    "posted_at": None,
                    "posted_text": "",
                    "jobkey": "",
                    "source": "builtin",
                })

    return out


# ---------------------------------------------------------------- Naukri (India's biggest)
def scrape_naukri(p, query, location) -> list[dict]:
    """Naukri - India's biggest job board. Uses special handling to bypass Akamai."""
    q = query.replace(" ", "%20")
    l = location.replace(" ", "%20") if location else ""
    # Naukri uses a different URL pattern for each city
    city_map = {
        "delhi": "delhi", "noida": "noida", "gurgaon": "gurgaon",
        "bengaluru": "bangalore", "mumbai": "mumbai", "pune": "pune",
        "hyderabad": "hyderabad", "chennai": "chennai", "kolkata": "kolkata",
    }
    city_slug = city_map.get(location.lower(), location.lower()) if location else ""
    url = f"https://www.naukri.com/{q}-jobs-in-{city_slug}" if city_slug else f"https://www.naukri.com/{q}-jobs"
    print(f"[naukri] {url}")
    if not goto(p, url):
        return []
    time.sleep(5)

    # Naukri may block with CAPTCHA - check
    html = p.content()
    if "captcha" in html.lower() or "access denied" in html.lower():
        print("  -> Naukri blocked (CAPTCHA/access denied)")
        return []

    for _ in range(6):
        p.mouse.wheel(0, 3000)
        time.sleep(1.2)

    html = p.content()
    out = []

    # Naukri embeds JSON-LD
    for m in re.finditer(r'application/ld\+json"', html):
        try:
            blob = html[m.end():]
            j = blob[blob.find(">") + 1:blob.find("</script>")]
            data = json.loads(j)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "JobPosting":
                        out.append({
                            "title": item.get("name", ""),
                            "company": item.get("hiringOrganization", {}).get("name", ""),
                            "location": item.get("jobLocation", {}).get("address", {}).get("addressLocality", "")
                                if isinstance(item.get("jobLocation"), dict) else "",
                            "url": item.get("url", ""),
                            "posted_at": item.get("datePosted", ""),
                            "posted_text": "",
                            "jobkey": item.get("identifier", {}).get("value", ""),
                            "source": "naukri",
                        })
        except Exception:
            continue

    # Fallback: parse rendered cards
    if not out:
        from parsel import Selector
        sel = Selector(text=html)
        for card in sel.css("[class*='jobTuple'], [class*='job-card'], [class*='jobposting']"):
            title = card.css("a::text, [class*='title']::text").get("")
            company = card.css("[class*='company']::text").get("")
            loc = card.css("[class*='location']::text").get("")
            href = card.css("a::attr(href)").get("")
            if title and len(title) > 3:
                out.append({
                    "title": title.strip(),
                    "company": company.strip(),
                    "location": loc.strip(),
                    "url": href or "",
                    "posted_at": None,
                    "posted_text": "",
                    "jobkey": "",
                    "source": "naukri",
                })

    return out


# ---------------------------------------------------------------- LinkedIn Pagination
def scrape_linkedin_paginated(p, query, location, max_pages=10) -> list[dict]:
    """LinkedIn with pagination to bypass the 60-job guest cap."""
    q = query.replace(" ", "%20")
    loc = location.replace(" ", "%20") if location else ""
    all_jobs = []

    for start in range(0, max_pages * 25, 25):
        if q and loc:
            url = (
                f"https://in.linkedin.com/jobs/search?keywords={q}&location={loc}"
                f"&start={start}&f_TPR=r86400"
            )
        elif loc and not q:
            slug = location.lower().split(",")[0].strip().replace(" ", "-")
            url = f"https://in.linkedin.com/jobs/{slug}-jobs?start={start}&f_TPR=r86400"
        elif q and not loc:
            url = (
                f"https://in.linkedin.com/jobs/search?keywords={q}"
                f"&start={start}&f_TPR=r86400"
            )
        else:
            url = f"https://in.linkedin.com/jobs?f_TPR=r86400&start={start}"

        print(f"[linkedin-paginated] page {start//25 + 1}: {url}")
        if not goto(p, url):
            break
        time.sleep(3)
        for _ in range(4):
            p.mouse.wheel(0, 2500)
            time.sleep(1)

        html = p.content()
        from parsel import Selector
        sel = Selector(text=html)
        page_jobs = []

        for c in sel.css("li.base-card, .job-search-card, .base-search-card"):
            lines = [l.strip() for l in c.css("::text").getall() if l.strip()]
            if len(lines) < 3:
                continue
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
            page_jobs.append({
                "title": title,
                "company": company,
                "location": loc,
                "url": href.split("?")[0] if href else "",
                "posted_at": posted_at,
                "posted_text": rel or (date[:10] if date else ""),
                "jobkey": jid.group(1) if jid else "",
                "source": "linkedin",
            })

        if not page_jobs:
            break
        all_jobs.extend(page_jobs)
        print(f"  -> {len(page_jobs)} jobs (total {len(all_jobs)})")
        time.sleep(2)

    return all_jobs


# ---------------------------------------------------------------- Indeed Pagination
def scrape_indeed_paginated(p, query, location, max_pages=10) -> list[dict]:
    """Indeed with multi-page pagination."""
    q = query.replace(" ", "+")
    l = location.replace(" ", "+") if location else ""
    all_jobs = []

    for page in range(1, max_pages + 1):
        start = (page - 1) * 10
        url = f"https://in.indeed.com/jobs?q={q}&l={l}&sort=date&start={start}"
        print(f"[indeed-paginated] page {page}: {url}")
        if not goto(p, url):
            break
        time.sleep(3)
        p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        html = p.content()
        dec = json.JSONDecoder()
        page_jobs = []

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
                page_jobs.append(found)

        if not page_jobs:
            break

        now = datetime.now(timezone.utc)
        for j in page_jobs:
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
            all_jobs.append({
                "title": j.get("title", ""),
                "company": j.get("company") or j.get("sourceEmployerName") or "",
                "location": j.get("formattedLocation") or "",
                "url": url,
                "posted_at": posted_at.isoformat() if posted_at else None,
                "posted_text": j.get("formattedRelativeTime") or "",
                "jobkey": j.get("jobkey", ""),
                "source": "indeed",
            })

        print(f"  -> {len(page_jobs)} jobs (total {len(all_jobs)})")
        time.sleep(3)

    return all_jobs


# ---------------------------------------------------------------- main
def main():
    import argparse

    ap = argparse.ArgumentParser(description="Scrape jobs from every major free source")
    ap.add_argument("--query", default="Software Engineer", help="job keyword(s)")
    ap.add_argument("--location", default="", help="city/region")
    ap.add_argument("--hours", type=int, default=24, help="freshness window in hours")
    ap.add_argument("--dry-run", action="store_true", help="don't write to DB")
    ap.add_argument("--db", default=str(DB), help="path to SQLite database")
    ap.add_argument("--sources", default="google,jooble,monster,dice,simplyhired,wellfound,builtin,naukri,linkedin,indeed",
                    help="comma-separated sources to scrape")
    args = ap.parse_args()

    db_path = Path(args.db)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    b, ctx = open_browser(pw)
    p = ctx.new_page()

    all_jobs = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)

    # Scrape each source
    for source in sources:
        try:
            if source == "google":
                jobs = scrape_google_jobs(p, args.query, args.location)
            elif source == "jooble":
                jobs = scrape_jooble(args.query, args.location)
            elif source == "monster":
                jobs = scrape_monster(p, args.query, args.location)
            elif source == "dice":
                jobs = scrape_dice(p, args.query, args.location)
            elif source == "simplyhired":
                jobs = scrape_simplyhired(p, args.query, args.location)
            elif source == "wellfound":
                jobs = scrape_wellfound(p, args.query, args.location)
            elif source == "builtin":
                jobs = scrape_builtin(p, args.query, args.location)
            elif source == "naukri":
                jobs = scrape_naukri(p, args.query, args.location)
            elif source == "linkedin":
                jobs = scrape_linkedin_paginated(p, args.query, args.location)
            elif source == "indeed":
                jobs = scrape_indeed_paginated(p, args.query, args.location)
            else:
                print(f"Unknown source: {source}")
                continue

            print(f"[{source}] {len(jobs)} jobs scraped")
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"[{source}] FAILED: {e}")
        time.sleep(3)

    p.close()
    b.close()
    pw.stop()

    # Filter by freshness
    fresh = []
    for j in all_jobs:
        pa = j.get("posted_at")
        if not pa:
            continue
        try:
            dt = datetime.fromisoformat(pa.replace("Z", "+00:00"))
        except Exception:
            continue
        if dt >= cutoff:
            fresh.append(j)

    print(f"\nTotal scraped: {len(all_jobs)} | Fresh (<{args.hours}h): {len(fresh)}")

    if args.dry_run:
        for j in sorted(fresh, key=lambda x: x.get("posted_at", ""), reverse=True)[:30]:
            print(f"  [{j['source']}] {j['title'][:45]} | {j['company'][:25]} | {j['location'][:20]}")
        return

    # Write to DB
    conn = sqlite3.connect(db_path)
    new = 0
    for j in fresh:
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
                    j["posted_at"], "", f"web,{j['source']}",
                    now.isoformat(), now.isoformat(),
                ),
            )
            if cur.rowcount > 0:
                new += 1
        except Exception:
            continue
    conn.commit()
    conn.close()
    print(f"\n[DB] {new} new jobs inserted into {db_path}")


if __name__ == "__main__":
    main()
