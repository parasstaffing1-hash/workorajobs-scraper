"""Runner that orchestrates all browser scrapers and persists to SQLite."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone

from .boards import SCRAPERS
from .scraper import ScrapedJob


def run_all_scrapers(
    keywords: list[str] | None = None,
    boards: list[str] | None = None,
    max_items: int = 200,
    db_path: str = "jobs.db",
    delay_s: float = 2.0,
) -> dict:
    """Run browser scrapers and store results.

    Uses a fresh Playwright instance per board (avoids singleton pool issues).
    """
    if keywords is None:
        keywords = [
            "software engineer",
            "backend engineer",
            "frontend engineer",
            "full stack developer",
            "data engineer",
            "machine learning engineer",
            "devops engineer",
            "cloud engineer",
            "python developer",
            "java developer",
            "react developer",
            "typescript developer",
            "golang developer",
            "rust developer",
        ]

    if boards is None:
        boards = list(SCRAPERS.keys())

    import os

    from playwright.sync_api import sync_playwright

    # Rotate through proxies if configured (comma-separated in env):
    #   JOBCOLLECT_PROXIES="http://user:pass@host1:port,http://user:pass@host2:port"
    proxies = [p.strip() for p in os.environ.get("JOBCOLLECT_PROXIES", "").split(",") if p.strip()]

    pw = sync_playwright().start()
    launch_kwargs = {}
    if proxies:
        # Use the first proxy for the whole browser instance
        launch_kwargs["proxy"] = {"server": proxies[0]}
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--no-first-run",
            "--disable-background-networking",
            "--disable-sync",
        ],
        **launch_kwargs,
    )

    # Resume support: only fetch jobs with URLs we haven't seen in the last 30 days.
    seen_urls = _load_seen_urls(db_path, days=30)

    stats = {}
    all_jobs: list[ScrapedJob] = []

    try:
        for idx, board_name in enumerate(boards):
            if board_name not in SCRAPERS:
                print(f"  Unknown board: {board_name}, skipping")
                continue

            # Rotate proxy per board if multiple are configured
            if proxies and len(proxies) > 1:
                proxy = proxies[idx % len(proxies)]
                # Launch a new browser with this proxy
                browser.close()
                browser = pw.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ],
                    proxy={"server": proxy},
                )
                print(f"  Using proxy #{idx % len(proxies) + 1} for {board_name}")

            print(f"\n[{board_name}] Starting scrape...")
            scraper_cls = SCRAPERS[board_name]
            scraper = scraper_cls(
                keywords=keywords,
                max_items=max_items,
                delay_s=delay_s,
            )

            try:
                jobs = scraper.run_with_browser(browser)
                # Filter to only unseen URLs (fresh jobs)
                fresh = [j for j in jobs if j.url and j.url not in seen_urls]
                print(f"  [{board_name}] Got {len(jobs)} jobs ({len(fresh)} new)")
                if scraper.errors:
                    for e in scraper.errors[:3]:
                        print(f"    [!] {e}")
                stats[board_name] = (len(jobs), 0, scraper.errors)
                all_jobs.extend(fresh)
            except Exception as exc:
                print(f"  [{board_name}] Failed: {exc}")
                stats[board_name] = (0, 0, [str(exc)])

            time.sleep(delay_s)
    finally:
        browser.close()
        pw.stop()

    # Persist to SQLite
    new_count = _persist_jobs(all_jobs, db_path)

    # Update stats with new counts
    for board_name in stats:
        seen, _, errors = stats[board_name]
        stats[board_name] = (seen, new_count if seen > 0 else 0, errors)

    return {
        "stats": stats,
        "total_seen": len(all_jobs),
        "total_new": new_count,
        "db_path": db_path,
    }


def _load_seen_urls(db_path: str, days: int = 30) -> set[str]:
    """Load URLs already in the DB (for resume support)."""
    conn = sqlite3.connect(db_path)
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT url FROM jobs WHERE url IS NOT NULL AND url != '' AND last_seen_at > ?",
            (cutoff,),
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()
    finally:
        conn.close()


def _persist_jobs(jobs: list[ScrapedJob], db_path: str) -> int:
    """Persist scraped jobs to SQLite. Returns count of new inserts."""
    conn = sqlite3.connect(db_path)
    new_count = 0

    for job in jobs:
        if not job.title or not job.url:
            continue

        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (dedupe_key, title, company, location, description, url, source,
                    source_kind, external_id, posted_at, salary, tags,
                    first_seen_at, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.url,  # dedupe_key
                    job.title,
                    job.company,
                    job.location,
                    job.description[:4000],
                    job.url,
                    job.source,
                    job.source_kind,
                    job.external_id,
                    job.posted_at.isoformat() if job.posted_at else None,
                    job.salary,
                    ",".join(job.tags),
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            if cur.rowcount > 0:
                new_count += 1
        except Exception:
            continue

    conn.commit()
    conn.close()
    return new_count
