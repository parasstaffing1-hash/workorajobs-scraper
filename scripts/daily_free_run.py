#!/usr/bin/env python3
"""Daily FREE job collection — no paid APIs, 100% keyless.

Runs, in order:
1. Browser scrapers (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Dice, Naukri)
   — bypasses the paid Indeed/LinkedIn/Glassdoor APIs entirely.
2. `jobcollect collect` (boards + ATS + RSS + career pages — all free).
3. Free board APIs (Remotive, RemoteOK, Arbeitnow, Jobicy, WWR).
4. Regenerates dashboard.html.

Usage:
    python scripts/daily_free_run.py              # everything
    python scripts/daily_free_run.py --browser-only
    python scripts/daily_free_run.py --api-only
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = str(ROOT / ".venv" / "Scripts" / "jobcollect.exe")
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def run(cmd: list[str], timeout: int = 1800) -> tuple[int, str]:
    log(f"RUN: {' '.join(cmd)}")
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "")[-3000:]
        if proc.returncode != 0:
            out += (proc.stderr or "")[-2000:]
        elapsed = time.time() - started
        log(f"DONE: exit={proc.returncode} ({elapsed:.0f}s)")
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT after {timeout}s")
        return -1, ""


def browser_run(limit: int = 150) -> None:
    """Run browser scrapers (free — bypasses paid APIs)."""
    log("=== BROWSER SCRAPERS (free) ===")
    boards = ["indeed", "linkedin", "glassdoor"]
    for board in boards:
        rc, out = run([EXE, "browser-scrape", "--boards", board, "--limit", str(limit)])
        if rc == 0:
            for line in out.splitlines()[-6:]:
                print(f"    {line}")
        time.sleep(2)


def api_run() -> None:
    """Run the free API-based collection (boards/ATS/RSS/careers)."""
    log("=== FREE API COLLECTION ===")
    rc, out = run([EXE, "collect", "--sources", "board,ats,rss"])
    if rc == 0:
        for line in out.splitlines()[-15:]:
            print(f"    {line}")


def main():
    parser = argparse.ArgumentParser(description="Daily free job collection")
    parser.add_argument("--browser-only", action="store_true")
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--limit", type=int, default=150)
    args = parser.parse_args()

    log("Starting daily FREE job run")
    log(f"Project: {ROOT}")

    if not args.api_only:
        browser_run(limit=args.limit)
    if not args.browser_only:
        api_run()

    # Always regenerate the dashboard at the end
    log("=== REGENERATE DASHBOARD ===")
    run([EXE, "report"])
    log("=== DONE ===")


if __name__ == "__main__":
    main()
