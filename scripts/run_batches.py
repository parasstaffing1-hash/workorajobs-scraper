#!/usr/bin/env python3
"""Batch runner: keeps calling batch_scrape.py until 1M jobs or all searches done.

Uses subprocess to call batch_scrape.py repeatedly.
Each batch = 100 unique jobs in ~60 seconds.
"""
import subprocess
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
BATCH_SCRIPT = ROOT / "scripts" / "batch_scrape.py"
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
LOG = ROOT / ".freebuff" / "runner_log.txt"

TARGET = 1_000_000


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8", errors="replace") as f:
        f.write(line + "\n")


def count_jobs():
    try:
        c = sqlite3.connect(str(DB))
        t = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        f7 = c.execute("SELECT COUNT(*) FROM jobs WHERE posted_at >= date('now', '-7 days')").fetchone()[0]
        c.close()
        return t, f7
    except:
        return 0, 0


def run_batch():
    """Run one batch of 100 jobs. Returns True if successful."""
    try:
        result = subprocess.run(
            [PYTHON, "-u", str(BATCH_SCRIPT)],
            capture_output=True, text=True, timeout=300,  # 5 min max per batch
            cwd=str(ROOT)
        )
        # Print last few lines of output
        lines = (result.stdout + result.stderr).strip().split("\n")
        for line in lines[-5:]:
            if line.strip():
                log(f"  {line.strip()}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log("  Batch timed out (300s), continuing...")
        return True
    except Exception as e:
        log(f"  Batch error: {e}")
        return False


def main():
    log("=" * 60)
    log("BATCH RUNNER STARTED")
    
    total, fresh7 = count_jobs()
    log(f"DB: {total:,} | Fresh7d: {fresh7:,} | Gap 1M: {max(0, TARGET-total):,}")
    
    batch_num = 0
    start = time.time()
    
    while True:
        total, fresh7 = count_jobs()
        elapsed = (time.time() - start) / 60
        
        if total >= TARGET:
            log(f"\n*** 1M TARGET REACHED! *** Total: {total:,}")
            break
        
        batch_num += 1
        log(f"\n--- Batch #{batch_num} | Total: {total:,} | Fresh7d: {fresh7:,} | Gap: {max(0, TARGET-total):,} | {elapsed:.0f}min ---")
        
        ok = run_batch()
        
        total_after, fresh7_after = count_jobs()
        gained = total_after - total
        
        if gained == 0 and not ok:
            log(f"  No gain and batch failed. Waiting 5s before retry...")
            time.sleep(5)
            continue
        
        if gained == 0:
            log(f"  No new jobs (search exhausted). Next search will start.")
        
        # Calculate ETA
        if batch_num > 0:
            avg_per_batch = (total_after - 134_882) / batch_num  # from starting point
            remaining = max(0, TARGET - total_after)
            eta_min = (remaining / max(avg_per_batch, 1)) * 1.0  # ~1 min per batch
            log(f"  +{gained} | Rate: {avg_per_batch:.0f}/batch | ETA: {eta_min:.0f}min to 1M")
    
    final_total, final_fresh = count_jobs()
    elapsed = (time.time() - start) / 60
    log(f"\n{'=' * 60}")
    log(f"COMPLETE: {batch_num} batches | {final_total:,} total | {final_fresh:,} fresh 7d")
    log(f"Time: {elapsed:.0f}min | Avg: {(final_total - 134_882)/max(batch_num,1):.0f} jobs/batch")
    log(f"{'=' * 60}")


if __name__ == "__main__":
    main()
