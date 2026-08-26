#!/usr/bin/env python3
"""Self-relaunching scraper loop. Keeps running until 1M jobs or all searches done."""
import subprocess
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"

def count():
    c = sqlite3.connect(str(DB))
    t = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    c.close()
    return t

def run(cmd, label):
    try:
        p = subprocess.Popen(
            cmd, shell=True, cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x00000008  # DETACHED_PROCESS
        )
        print(f"[LAUNCH] {label} PID={p.pid}", flush=True)
        return p
    except Exception as e:
        print(f"[ERROR] {label}: {e}", flush=True)
        return None

TARGET = 1_000_000
start = time.time()

while True:
    total = count()
    elapsed = (time.time() - start) / 60
    print(f"\n[STATUS] Total: {total:,} | Gap: {max(0, TARGET-total):,} | Elapsed: {elapsed:.0f}min", flush=True)
    
    if total >= TARGET:
        print(f"\n*** 1M REACHED! *** Total: {total:,}", flush=True)
        break
    
    # Launch JobSpy sweep
    p1 = run(".venv\\Scripts\\python.exe -u scripts\\jobspy_sweep.py --resume", "JobSpy")
    # Launch ATS slug probe
    p2 = run(".venv\\Scripts\\python.exe -u scripts\\mega_slug_probe.py --resume", "MegaSlug")
    
    # Monitor for 5 minutes, then relaunch
    for i in range(60):  # 60 * 5s = 5 min
        time.sleep(5)
        total = count()
        elapsed = (time.time() - start) / 60
        gap = max(0, TARGET - total)
        
        # Check if processes are alive
        alive1 = p1.poll() is None if p1 else False
        alive2 = p2.poll() is None if p2 else False
        
        if i % 12 == 0:  # Every minute
            print(f"  [{elapsed:.0f}min] Total: {total:,} | Gap: {gap:,} | P1: {'alive' if alive1 else 'dead'} | P2: {'alive' if alive2 else 'dead'}", flush=True)
        
        if total >= TARGET:
            print(f"\n*** 1M REACHED! *** Total: {total:,}", flush=True)
            # Kill the scrapers
            try:
                if p1 and p1.poll() is None: p1.terminate()
                if p2 and p2.poll() is None: p2.terminate()
            except: pass
            break
        
        # If both dead, break to relaunch
        if not alive1 and not alive2:
            print(f"  Both dead, relaunching...", flush=True)
            break
    
    # Clean up dead processes
    try:
        if p1 and p1.poll() is None: p1.terminate()
        if p2 and p2.poll() is None: p2.terminate()
    except: pass
    
    time.sleep(2)

print("\n[DONE]", flush=True)
