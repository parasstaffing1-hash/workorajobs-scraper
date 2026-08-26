#!/usr/bin/env python3
"""Run the master scraper sweep in background with its own log."""
import subprocess, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = ROOT / ".freebuff" / "sweep_final.log"
log.parent.mkdir(exist_ok=True)

cmd = [
    str(ROOT / ".venv" / "Scripts" / "python.exe"),
    "-u",  # unbuffered
    str(ROOT / "scripts" / "master_scraper.py"),
    "--query", "Software Engineer,Data Engineer,Backend Engineer,Frontend Developer,DevOps Engineer,Machine Learning Engineer,React Developer,Python Developer,Java Developer,SDE",
    "--location", "Delhi,Bengaluru,Mumbai,Hyderabad,Pune,Chennai",
    "--hours", "168",
    "--sources", "jobspy",
]

with open(log, "w") as f:
    proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=str(ROOT))
    print(f"Launched PID={proc.pid}, log={log}")
    # Wait briefly to confirm it started
    import time
    time.sleep(3)
    if proc.poll() is not None:
        print(f"Process exited immediately with code {proc.returncode}")
        with open(log) as lf:
            print(lf.read()[-500:])
    else:
        print("Process running...")
