#!/usr/bin/env python3
"""Persistent launcher v4 — runs mega_scraper then universal_scraper SEQUENTIALLY.
Never both at once (causes OOM from Playwright + API workers)."""
import subprocess, sys, time
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
MEGA = str(ROOT / "scripts" / "mega_scraper.py")
UNIVERSAL = str(ROOT / "scripts" / "universal_scraper.py")
LOG = ROOT / "launcher_log.txt"

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
NO_WINDOW = 0x08000000

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        with open(LOG, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{ts}] {msg}\n")
    except: pass

def run_scraper(script, label, args=None, timeout=600):
    cmd = [PYTHON, "-B", script]
    if args: cmd.extend(args)
    try:
        log(f"{label}: launching...")
        proc = subprocess.Popen(cmd, cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=DETACHED | NO_WINDOW)
        log(f"{label}: PID={proc.pid}")
        try:
            proc.wait(timeout=timeout)
            log(f"{label}: exited code={proc.returncode}")
        except subprocess.TimeoutExpired:
            log(f"{label}: timeout after {timeout}s, killing")
            try: proc.kill()
            except: pass
            try: proc.wait(timeout=5)
            except: pass
    except Exception as e:
        log(f"{label}: FAILED {e}")

def main():
    log("=== Launcher v4 (sequential) started ===")
    round_num = 0
    while True:
        round_num += 1
        log(f"=== Round {round_num} ===")

        # Phase 1: API scraper (fast, low memory)
        run_scraper(MEGA, "mega", ["--hours", "0.12"], timeout=600)

        # Phase 2: Playwright scraper (slower, high memory)
        run_scraper(UNIVERSAL, "universal", [], timeout=900)

        log(f"Round {round_num} done, restarting in 5s...")
        time.sleep(5)

if __name__ == "__main__":
    main()
