#!/usr/bin/env python3
"""Launch mega_scraper_v4.py in a continuous loop, clearing checkpoint between rounds."""
import subprocess, sys, time, os, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CP = ROOT / "mega_v4_cp.json"
LOG = ROOT / "mega_v4_log.txt"
SCRIPT = ROOT / "scripts" / "mega_scraper_v4.py"

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}\n"
    print(line, end="", flush=True)
    try:
        with open(LOG, "a", encoding="utf-8", errors="replace") as f:
            f.write(line)
    except: pass

round_num = 0
while True:
    round_num += 1
    log(f"=== ROUND {round_num} START ===")
    
    # Clear checkpoint for fresh work items
    if CP.exists():
        try:
            CP.unlink()
            log("Cleared checkpoint")
        except: pass
    
    # Run the scraper
    try:
        proc = subprocess.run(
            [sys.executable, "-B", str(SCRIPT)],
            cwd=str(ROOT),
            timeout=3900,  # 65 min max
            capture_output=True,
            text=True,
        )
        log(f"Round {round_num} exit code: {proc.returncode}")
        if proc.stdout:
            for line in proc.stdout.strip().split("\n")[-5:]:
                log(f"  {line}")
    except subprocess.TimeoutExpired:
        log(f"Round {round_num} timed out (65min), restarting...")
    except Exception as e:
        log(f"Round {round_num} error: {e}")
    
    # Brief pause before next round
    time.sleep(5)
