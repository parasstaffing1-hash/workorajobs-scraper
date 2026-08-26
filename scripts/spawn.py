#!/usr/bin/env python3
"""Spawn the bulletproof scraper as a fully detached process and exit."""
import subprocess
import sys

PYTHON = r"C:\Users\Administrator\Documents\ATS\.venv\Scripts\pythonw.exe"
SCRIPT = r"C:\Users\Administrator\Documents\ATS\scripts\bulletproof_scraper.py"
HOURS = sys.argv[1] if len(sys.argv) > 1 else "8"

proc = subprocess.Popen(
    [PYTHON, "-B", SCRIPT, "--hours", HOURS],
    cwd=r"C:\Users\Administrator\Documents\ATS",
    creationflags=0x00000008 | 0x08000000,  # DETACHED_PROCESS | CREATE_NO_WINDOW
    close_fds=True,
    stdin=subprocess.DEVNULL,
)
print(f"Launched PID={proc.pid}")
