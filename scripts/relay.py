#!/usr/bin/env python3
"""RELAY — runs bulletproof_scraper in loops, auto-relaunches after each round.
Logs to file. Runs until 1M jobs or manual stop.

Launch: start /b python.exe -u scripts/relay.py > relay.log 2>&1
"""
import subprocess
import sys
import time
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
SCRIPT = str(ROOT / "scripts" / "batch_engine_v2.py")
LOG = ROOT / "relay_log.txt"
TARGET = 1_000_000
ROUND_HOURS = 8  # each round runs for up to 8 hours


def rlog(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def count():
    try:
        conn = sqlite3.connect(str(DB))
        t = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        f = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE first_seen_at >= datetime('now','-7 days')"
        ).fetchone()[0]
        conn.close()
        return t, f
    except Exception:
        return 0, 0


def main():
    round_num = 0
    while True:
        round_num += 1
        t, f = count()
        gap = max(0, TARGET - f)
        rlog(f"=== ROUND {round_num} | DB={t:,} | Fresh7d={f:,} | Gap={gap:,} ===")

        if f >= TARGET:
            rlog(f"TARGET REACHED! {f:,} fresh jobs >= {TARGET:,}")
            break

        try:
            # Run bulletproof_scraper with stdout/stderr to a log file
            scraper_log = open(ROOT / "scraper_stdout.log", "a", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                [PYTHON, "-u", SCRIPT, "--hours", str(ROUND_HOURS)],
                cwd=str(ROOT),
                stdout=scraper_log,
                stderr=scraper_log,
            )
            try:
                proc.wait(timeout=(ROUND_HOURS + 1) * 3600)
            except subprocess.TimeoutExpired:
                rlog("Timeout — killing scraper, saving checkpoint...")
                proc.kill()
                proc.wait(timeout=10)
            scraper_log.close()

            rc = proc.returncode
            if rc not in (0, None):
                rlog(f"Scraper exited code {rc}")
        except Exception as e:
            rlog(f"Error: {e}")

        # Brief pause then relaunch
        time.sleep(5)


if __name__ == "__main__":
    main()
