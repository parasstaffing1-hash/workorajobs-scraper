#!/usr/bin/env python3
"""Run each search from searches.yaml as an independent subprocess.
If one search crashes, the rest still run. Each stores results to DB immediately.

Writes all output to .freebuff/sweep.log (safe for pythonw with no console).
"""
import subprocess, sys, os, time, yaml
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / ".freebuff"
LOG_DIR.mkdir(exist_ok=True)
LOG = LOG_DIR / "sweep.log"

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}\n"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()

# Clear old log
LOG.write_text(f"=== Sweep started {datetime.now().isoformat()} ===\n", encoding="utf-8")

searches = yaml.safe_load((ROOT / "searches.yaml").read_text(encoding="utf-8")).get("searches", [])
log(f"Loaded {len(searches)} searches from searches.yaml")

for i, search in enumerate(searches):
    q = search.get("keywords", "")
    locs = search.get("locations", "")
    hours = search.get("hours", 24)
    
    tmp_yaml = LOG_DIR / f"search_{i}.yaml"
    tmp_yaml.write_text(yaml.dump({"searches": [search]}, default_flow_style=False), encoding="utf-8")
    
    log(f"Search {i+1}/{len(searches)}: {q} | {locs} ({hours}h)")
    
    cmd = [
        sys.executable, "-u",
        str(ROOT / "scripts" / "master_scraper.py"),
        "--searches", str(tmp_yaml),
        "--sources", "jobspy",
    ]
    
    try:
        result = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=600
        )
        # Write output to log
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(result.stdout[-3000:] if result.stdout else "(no stdout)")
            if result.stderr:
                f.write(f"\nSTDERR: {result.stderr[-500:]}")
            if result.returncode != 0:
                f.write(f"\nEXIT CODE: {result.returncode}")
            f.flush()
        log(f"Search {i+1} done (exit={result.returncode})")
    except subprocess.TimeoutExpired:
        log(f"Search {i+1} TIMEOUT after 300s")
    except Exception as e:
        log(f"Search {i+1} ERROR: {e}")
    
    time.sleep(1)

log(f"=== ALL {len(searches)} SEARCHES COMPLETE ===")
