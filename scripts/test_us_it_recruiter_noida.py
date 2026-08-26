#!/usr/bin/env python3
"""TEST: Get all US IT Recruiter jobs posted in last 24h in Noida.

Scrapes in.indeed.com with Playwright (no API key), extracts jobs from
Indeed's embedded mosaic JSON which carries exact relative dates
("Just posted", "N days ago") per job. Filters to last 24h + Noida.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"

QUERY = "US IT Recruiter"
LOCATION = "Noida"
URL = f"https://in.indeed.com/jobs?q={QUERY.replace(' ', '+')}&l={LOCATION}&sort=date"


def parse_relative(text: str, now: datetime) -> datetime | None:
    """Parse Indeed relative dates: 'Just posted', 'Today', 'N days ago', '30+ days ago'."""
    text = (text or "").strip().lower()
    if not text:
        return None
    if "just posted" in text or "today" in text or "24h" in text:
        return now
    m = re.search(r"(\d+)\s*\+?\s*day", text)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s*hour", text)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.search(r"(\d+)\s*week", text)
    if m:
        return now - timedelta(weeks=int(m.group(1)))
    m = re.search(r"(\d+)\s*month", text)
    if m:
        return now - timedelta(days=30 * int(m.group(1)))
    return None


def extract_jobs(html: str) -> list[dict]:
    """Pull job objects out of Indeed's embedded JSON by locating each
    formattedRelativeTime marker and decoding the enclosing object."""
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


def main():
    from playwright.sync_api import sync_playwright

    print(f"Scraping: {URL}")
    pw = sync_playwright().start()
    b = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = b.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        locale="en-US",
    )
    p = ctx.new_page()
    for attempt in range(4):
        try:
            p.goto(URL, timeout=45000)
            break
        except Exception as e:
            print(f"  retry {attempt}: {e}")
            time.sleep(6)
    time.sleep(3)
    p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(2)
    html = p.content()
    p.close()
    b.close()
    pw.stop()

    raw = extract_jobs(html)
    print(f"\n{len(raw)} jobs found in embedded JSON")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    results = []
    for j in raw:
        emp = j.get("employer") or {}
        loc = j.get("formattedLocation") or ""
        rel = j.get("formattedRelativeTime") or ""
        # Prefer exact epoch ms (pubDate/createDate); fall back to relative text
        ts = j.get("pubDate") or j.get("createDate") or j.get("datePublished") or j.get("dateOnIndeed")
        if isinstance(ts, (int, float)) and ts > 0:
            posted_at = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        else:
            posted_at = parse_relative(rel, now)
        link = j.get("link") or ""
        url = f"https://in.indeed.com{link}" if link.startswith("/") else (
            f"https://in.indeed.com/viewjob?jk={j.get('jobkey', '')}" if link else ""
        )
        results.append({
            "title": j.get("title", ""),
            "company": emp.get("name") or j.get("sourceEmployerName") or j.get("company") or "",
            "location": loc,
            "url": url,
            "posted_text": rel,
            "posted_at": posted_at,
            "jobkey": j.get("jobkey", ""),
        })

    fresh = [r for r in results if r["posted_at"] and r["posted_at"] >= cutoff]
    fresh_noida = [r for r in fresh if "noida" in (r["location"] or "").lower()]

    print(f"\n=== US IT Recruiter in Noida, last 24h ===")
    print(f"Fresh (<24h): {len(fresh)}  |  Fresh + Noida: {len(fresh_noida)}")
    for r in fresh_noida[:20]:
        print(f"  [{r['posted_text']}] {r['title'][:55]}")
        print(f"     company={r['company'][:35]} | loc={r['location'][:35]} | {r['url'][:70]}")

    # Store to DB
    conn = sqlite3.connect(DB)
    new = 0
    for r in fresh_noida:
        if not r["title"] or not r["url"]:
            continue
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (dedupe_key, title, company, location, description, url, source,
                    source_kind, external_id, posted_at, salary, tags,
                    first_seen_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r["url"], r["title"], r["company"], r["location"], "",
                    r["url"], "indeed", "browser", r["jobkey"],
                    r["posted_at"].isoformat() if r["posted_at"] else None, "",
                    "us-it-recruiter,noida",
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            if cur.rowcount > 0:
                new += 1
        except Exception:
            continue
    conn.commit()
    conn.close()
    print(f"\n[DB] {new} new jobs inserted into {DB}")


if __name__ == "__main__":
    main()
