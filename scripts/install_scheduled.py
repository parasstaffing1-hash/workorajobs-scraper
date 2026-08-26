#!/usr/bin/env python3
"""Install a recurring scheduled task via cmd.exe to avoid Git Bash /mo mangling."""
import subprocess
import time

# Delete old tasks
subprocess.run(['schtasks', '/Delete', '/TN', 'ScraperLoop', '/F'], capture_output=True)
time.sleep(1)

# Create task that runs every 10 minutes
r = subprocess.run([
    'schtasks', '/Create', '/TN', 'ScraperLoop',
    '/TR', r'cmd /c cd /d C:\Users\Administrator\Documents\ATS && .venv\Scripts\python.exe -B scripts\fast_scrape.py --hours 0.13',
    '/SC', 'minute', '/MO', '10', '/F', '/RU', 'Administrator'
], capture_output=True, text=True)
print("Create:", r.stdout.strip(), r.stderr.strip())

# Run it now
r2 = subprocess.run(['schtasks', '/Run', '/TN', 'ScraperLoop'], capture_output=True, text=True)
print("Run:", r2.stdout.strip(), r2.stderr.strip())
