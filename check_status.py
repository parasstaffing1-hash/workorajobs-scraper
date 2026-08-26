import sqlite3
import os
import json

db_path = os.path.join(os.path.dirname(__file__), 'jobs.db')
conn = sqlite3.connect(db_path)
total = conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
fresh = conn.execute("SELECT COUNT(*) FROM jobs WHERE posted_at >= datetime('now','-7 days')").fetchone()[0]
today = conn.execute("SELECT COUNT(*) FROM jobs WHERE posted_at >= datetime('now','-1 day')").fetchone()[0]

# Source breakdown
sources = conn.execute('SELECT source, COUNT(*) as c FROM jobs GROUP BY source ORDER BY c DESC').fetchall()

# Top companies
companies = conn.execute('SELECT company, COUNT(*) as c FROM jobs WHERE company != "" GROUP BY company ORDER BY c DESC LIMIT 10').fetchall()

print(f"=== JOB DATABASE STATUS ===")
print(f"Total jobs:    {total:,}")
print(f"Fresh (7d):    {fresh:,}")
print(f"Fresh (24h):   {today:,}")
print(f"\n=== BY SOURCE ===")
for s, c in sources:
    print(f"  {s:25s} {c:>8,}")
print(f"\n=== TOP 10 COMPANIES ===")
for co, c in companies:
    print(f"  {co:30s} {c:>6,}")

# Check processes
import subprocess
result = subprocess.run('tasklist /FI "IMAGENAME eq pythonw.exe"', capture_output=True, text=True)
lines = [l for l in result.stdout.split('\n') if 'pythonw' in l.lower()]
print(f"\n=== RUNNING PROCESSES ===")
if lines:
    for l in lines:
        print(f"  {l.strip()}")
else:
    print("  No pythonw processes running")

# Check scheduled tasks
result = subprocess.run('schtasks /query /tn UnifiedScraper /fo LIST', capture_output=True, text=True)
if 'UnifiedScraper' in result.stdout:
    print("\n=== SCHEDULED TASK: UnifiedScraper ===")
    for line in result.stdout.split('\n'):
        if line.strip():
            print(f"  {line.strip()}")
else:
    print("\n  UnifiedScraper task NOT found")

# Check checkpoint
cp_path = os.path.join(os.path.dirname(__file__), 'unified_cp.json')
if os.path.exists(cp_path):
    with open(cp_path) as f:
        cp = json.load(f)
    print(f"\n=== CHECKPOINT ===")
    print(f"  Done items: {cp.get('done_count', 0):,}")
    print(f"  Total new:  {cp.get('total_new', 0):,}")
    print(f"  Batches:    {cp.get('batch_count', 0):,}")

conn.close()
