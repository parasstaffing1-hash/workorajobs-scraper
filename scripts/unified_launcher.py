#!/usr/bin/env python3
"""Persistent launcher for mega_unified.py — runs in infinite loop, auto-restarts."""
import subprocess, sys, time
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
SCRIPT = str(ROOT / "scripts" / "mega_unified.py")
LOG = ROOT / "unified_launcher_log.txt"

DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
NO_WINDOW = 0x08000000

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        with open(LOG, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{ts}] {msg}\n")
    except: pass

def main():
    log("=== Unified Launcher started ===")
    round_num = 0
    while True:
        round_num += 1
        log(f"=== Round {round_num} ===")
        try:
            proc = subprocess.Popen(
                [PYTHON, "-B", SCRIPT],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=DETACHED | NO_WINDOW,
            )
            log(f"PID={proc.pid}")
            try:
                proc.wait(timeout=7200)  # 2 hour max per round
                log(f"Exited code={proc.returncode}")
            except subprocess.TimeoutExpired:
                log("Timeout 2h, killing")
                try: proc.kill()
                except: pass
                try: proc.wait(timeout=5)
                except: pass
        except Exception as e:
            log(f"FAILED: {e}")
        log(f"Round {round_num} done, restarting in 3s...")
        time.sleep(3)

if __name__ == "__main__":
    main()
