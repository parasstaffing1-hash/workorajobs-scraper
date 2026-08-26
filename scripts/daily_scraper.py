#!/usr/bin/env python3
"""Daily scraper — runs all sources simultaneously for maximum coverage.

Combines:
  1. JobSpy parallel (LinkedIn+Indeed) — high-traffic, keyword-based
  2. ATS APIs (Greenhouse, Lever, Ashby, etc.) — per-company, no dedup
  3. Surf (apna, Shine, LinkedIn, Indeed India) — browser-based
  4. Adzuna API — multi-country
  5. RSS feeds — tech news job boards

Run daily via Windows Task Scheduler at 9 AM.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG_FILE = ROOT / ".freebuff" / "daily_scraper.log"

DB_LOCK = Lock()


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ════════════════════════════════════════════════════════════════
# JobSpy parallel scraper (reuses parallel_scraper.py)
# ════════════════════════════════════════════════════════════════
def run_jobspy_parallel(keywords: list[str], locations: list[str],
                        threads: int = 10, max_pages: int = 5) -> int:
    """Run JobSpy searches in parallel. Returns new job count."""
    from parallel_scraper import search_one, store_jobs, load_checkpoint, save_checkpoint, CP_FILE

    cp = load_checkpoint()
    completed_set = set(cp["completed"])
    searches = [(kw, loc) for kw in keywords for loc in locations]
    remaining = [(kw, loc) for kw, loc in searches if f"{kw}|{loc}" not in completed_set]

    log(f"[JobSpy] {len(remaining)} new searches ({len(completed_set)} already done)")

    if not remaining:
        return 0

    conn = sqlite3.connect(DB)
    grand_new = 0
    batch_size = threads * 5  # 5 batches of 10

    for batch_start in range(0, len(remaining), batch_size):
        batch = remaining[batch_start:batch_start + batch_size]

        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = {executor.submit(search_one, kw, loc, max_pages): (kw, loc)
                       for kw, loc in batch}

            for future in as_completed(futures):
                kw, loc = futures[future]
                try:
                    result = future.result()
                    jobs = result["jobs"]
                    if result.get("error"):
                        continue
                    tag = f"daily,{kw.lower().replace(' ', '-')[:30]},{loc.lower().replace(' ', '-')[:20]}"
                    new = store_jobs(conn, jobs, tag)
                    grand_new += new
                    completed_set.add(f"{kw}|{loc}")
                    if new > 0:
                        log(f"  [JobSpy] {kw:30s} | {loc or 'global':12s}: +{new} new")
                except Exception as e:
                    log(f"  [JobSpy] ERROR {kw}|{loc}: {e}")

        # Checkpoint
        cp = {"completed": list(completed_set), "stats": {"new": grand_new}}
        save_checkpoint(cp)

    conn.close()
    log(f"[JobSpy] Total: +{grand_new} new jobs")
    return grand_new


# ════════════════════════════════════════════════════════════════
# ATS API collector (runs existing collect command)
# ════════════════════════════════════════════════════════════════
def run_ats_collect() -> int:
    """Run the existing ATS API collector. Returns new job count."""
    conn = sqlite3.connect(DB)
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    log("[ATS] Running ATS API collector...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "jobcollector.cli", "collect", "--limit", "500"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            log(f"[ATS] Collector error: {result.stderr[-200:]}")
    except Exception as e:
        log(f"[ATS] Collector failed: {e}")

    conn = sqlite3.connect(DB)
    after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    new = after - before
    log(f"[ATS] +{new} new jobs")
    return new


# ════════════════════════════════════════════════════════════════
# Surf browser scraper (apna, Shine, LinkedIn, Indeed India)
# ════════════════════════════════════════════════════════════════
def run_surf() -> int:
    """Run the surf_fresh_jobs browser scraper. Returns new job count."""
    conn = sqlite3.connect(DB)
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    log("[Surf] Running browser scraper (apna/Shine/LinkedIn/Indeed)...")
    try:
        result = subprocess.run(
            [sys.executable, "-u", str(ROOT / "scripts" / "surf_fresh_jobs.py"),
             "--searches", str(ROOT / "searches.yaml")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=600
        )
        # Extract new count from output
        new = 0
        for line in result.stdout.split("\n"):
            m = re.search(r"\+(\d+) new", line)
            if m:
                new += int(m.group(1))
    except Exception as e:
        log(f"[Surf] Failed: {e}")
        new = 0

    log(f"[Surf] +{new} new jobs")
    return new


# ════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Daily scraper — all sources combined")
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--skip-jobspy", action="store_true")
    ap.add_argument("--skip-ats", action="store_true")
    ap.add_argument("--skip-surf", action="store_true")
    args = ap.parse_args()

    log("=" * 60)
    log("DAILY SCRAPER — all sources")
    log("=" * 60)

    start = time.time()
    total_new = 0

    # 1. ATS APIs (fastest, most unique)
    if not args.skip_ats:
        total_new += run_ats_collect()

    # 2. JobSpy parallel (LinkedIn+Indeed)
    if not args.skip_jobspy:
        from parallel_scraper import KEYWORDS, INDIAN_CITIES, GLOBAL_CITIES
        total_new += run_jobspy_parallel(
            KEYWORDS[:40],  # top 40 keywords
            INDIAN_CITIES + GLOBAL_CITIES[:5],
            threads=args.threads,
            max_pages=5,
        )

    # 3. Surf browser scraper (apna, Shine, LinkedIn, Indeed India)
    if not args.skip_surf:
        total_new += run_surf()

    elapsed = time.time() - start
    conn = sqlite3.connect(DB)
    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    log("")
    log("=" * 60)
    log(f"DAILY SCRAPE COMPLETE")
    log(f"New jobs today:  {total_new:,}")
    log(f"DB total:        {final:,}")
    log(f"Time:            {elapsed/60:.1f} min")
    log(f"Rate:            {total_new/(elapsed/60):.0f} new/min")
    log(f"Gap to 1M:       {max(0, 1000000 - final):,}")
    log("=" * 60)


if __name__ == "__main__":
    main()
