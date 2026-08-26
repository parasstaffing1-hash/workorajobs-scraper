#!/usr/bin/env python3
"""WATCHDOG — runs forever, checks if scraper is alive every 30s, relaunches if dead.
Launch via schtasks DAILY or run directly.
"""
import subprocess
import time
import sqlite3
import os
from pathlib import Path

ROOT = Path(r"C:\Users\Administrator\Documents\ATS")
PYTHONW = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
SCRIPT = str(ROOT / "scripts" / "batch_engine_v2.py")
DB = ROOT / "jobs.db"
LOG = ROOT / "watchdog_log.txt"
TARGET = 1_000_000

def wlog(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def count():
    try:
        c = sqlite3.connect(str(DB))
        t = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        c.close()
        return t
    except:
        return 0

def find_scraper_pid():
    """Find running batch_engine_v2.py process PID."""
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10
        )
        pids = []
        for line in r.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split(",")
            if len(parts) >= 2:
                pid = parts[1].strip('"')
                pids.append(int(pid))
        return pids
    except:
        return []

def launch_scraper():
    """Launch scraper as fully detached process."""
    try:
        # Write a temp bat and start it
        bat = ROOT / "_watchdog_launch.bat"
        bat.write_text(
            f'@echo off\r\ncd /d {ROOT}\r\n'
            f'"{PYTHONW}" -B "{SCRIPT}" --hours 8\r\n',
            encoding="utf-8"
        )
        os.startfile(str(bat))
        wlog("Launched scraper via os.startfile")
        return True
    except Exception as e:
        wlog(f"Launch failed: {e}")
        return False

def main():
    wlog("=" * 50)
    wlog("WATCHDOG STARTED — monitoring scraper")
    wlog("=" * 50)
    
    last_launch = 0
    check_count = 0
    
    while True:
        time.sleep(30)
        check_count += 1
        
        total = count()
        gap = max(0, TARGET - total)
        
        if total >= TARGET:
            wlog(f"TARGET REACHED! {total:,} >= {TARGET:,}")
            break
        
        # Check if scraper is alive by looking for python.exe processes
        # and checking if batch_v2_log.txt is being updated
        log_path = ROOT / "batch_v2_log.txt"
        log_age = float("inf")
        if log_path.exists():
            log_age = time.time() - log_path.stat().st_mtime
        
        # If log hasn't been updated in 120 seconds, scraper is probably dead
        if log_age > 120:
            since_launch = time.time() - last_launch
            if since_launch > 60:  # Don't relaunch too fast
                wlog(f"Scraper dead (log stale {log_age:.0f}s). DB={total:,} Gap={gap:,}")
                if launch_scraper():
                    last_launch = time.time()
        elif check_count % 10 == 0:  # Log alive status every 5 min
            wlog(f"Scraper alive. DB={total:,} Gap={gap:,} LogAge={log_age:.0f}s")

if __name__ == "__main__":
    main()
