#!/usr/bin/env python3
"""WMI-based infinite loop: launch fast_scrape.py, wait, repeat.
Uses WMI to launch each round so processes survive parent death.
"""
import time
import wmi
import sqlite3
from pathlib import Path

ROOT = Path(r"C:\Users\Administrator\Documents\ATS")
DB = ROOT / "jobs.db"
TARGET = 1_000_000
PYTHON = r"C:\Users\Administrator\Documents\ATS\.venv\Scripts\python.exe"
SCRIPT = r"C:\Users\Administrator\Documents\ATS\scripts\fast_scrape.py"

def count():
    try:
        c = sqlite3.connect(str(DB))
        t = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        c.close()
        return t
    except:
        return 0

def main():
    wm = wmi.WMI()
    round_num = 0
    while True:
        round_num += 1
        total = count()
        if total >= TARGET:
            print(f"TARGET REACHED: {total:,}")
            break
        
        print(f"Round {round_num}: DB={total:,}, Gap={max(0,TARGET-total):,}")
        
        # Launch via WMI
        result = wm.Win32_Process.Create(
            CommandLine=f'cmd.exe /c cd /d {ROOT} && "{PYTHON}" -B "{SCRIPT}" --hours 0.13',
            CurrentDirectory=str(ROOT)
        )
        print(f"  WMI launch: {result}")
        
        # Wait for process to finish (poll every 30s)
        time.sleep(30)
        
        # Check if still running
        while True:
            total = count()
            procs = wm.Win32_Process(Name="python.exe")
            scrape_procs = [p for p in procs if "fast_scrape" in (p.CommandLine or "")]
            if not scrape_procs:
                print(f"  Round done. DB={total:,}")
                break
            time.sleep(10)
        
        # Brief pause
        time.sleep(3)

if __name__ == "__main__":
    main()
