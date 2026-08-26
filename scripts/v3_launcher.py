#!/usr/bin/env python3
"""Persistent launcher for mega_scraper_v3 — runs rounds forever."""
import subprocess, sys, time, os
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
SCRIPT = str(ROOT / "scripts" / "mega_scraper_v3.py")
LOG = ROOT / "v3_launcher_log.txt"

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with open(LOG, "a", encoding="utf-8", errors="replace") as f:
        f.write(f"[{ts}] {msg}\n")

def run_round(round_num):
    log(f"Starting round {round_num}...")
    try:
        proc = subprocess.Popen(
            [PYTHON, "-B", SCRIPT],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        # Wait up to 45 min per round
        try:
            proc.wait(timeout=2700)
        except subprocess.TimeoutExpired:
            log(f"Round {round_num} timed out, killing...")
            proc.kill()
            proc.wait(timeout=10)
        log(f"Round {round_num} finished (exit={proc.returncode})")
    except Exception as e:
        log(f"Round {round_num} error: {e}")

def main():
    log("=" * 50)
    log("V3 LAUNCHER — Infinite loop")
    log("=" * 50)
    round_num = 0
    while True:
        round_num += 1
        run_round(round_num)
        log(f"Sleeping 10s before next round...")
        time.sleep(10)

if __name__ == "__main__":
    main()
