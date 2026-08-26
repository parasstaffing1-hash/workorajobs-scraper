#!/usr/bin/env python3
"""Self-relaunching wrapper for mega_batch_v2.
Runs the mega batch, saves checkpoint, and relaunches itself.
Continues until 1M jobs or manually stopped.
"""
import subprocess
import sys
import time
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
TARGET = 1_000_000
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
SCRIPT = str(ROOT / "scripts" / "mega_batch_v2.py")

def count_jobs():
    try:
        conn = sqlite3.connect(str(DB))
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        fresh = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE first_seen_at >= datetime('now','-7 days')"
        ).fetchone()[0]
        conn.close()
        return total, fresh
    except Exception:
        return 0, 0

LOG_FILE = ROOT / "mega_batch_v2_log.txt"
WRAPPER_LOG = ROOT / "wrapper_log.txt"

def wlog(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(WRAPPER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def main():
    round_num = 0
    while True:
        round_num += 1
        total, fresh = count_jobs()
        wlog(f"ROUND {round_num} | DB: {total:,} | Fresh7d: {fresh:,} | Gap: {max(0, TARGET-total):,}")
        
        if fresh >= TARGET:
            wlog(f"TARGET REACHED! {fresh:,} fresh jobs >= {TARGET:,}")
            break
        
        # Run the mega batch (with 55 min timeout)
        try:
            log_fd = open(LOG_FILE, "a", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                [PYTHON, "-u", SCRIPT],
                cwd=str(ROOT),
                stdout=log_fd,
                stderr=log_fd,
            )
            try:
                proc.wait(timeout=55 * 60)
            except subprocess.TimeoutExpired:
                wlog(f"55min timeout, saving checkpoint and relaunching...")
                proc.kill()
                proc.wait(timeout=5)
            log_fd.close()
            
            if proc.returncode not in (0, None):
                wlog(f"Process exited code {proc.returncode}, relaunching...")
        except Exception as e:
            wlog(f"Error: {e}, relaunching in 10s...")
            time.sleep(10)
        
        time.sleep(2)

if __name__ == "__main__":
    main()
