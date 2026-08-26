#!/bin/bash
set -e

echo "========================================="
echo "  Workora Jobs - Starting Services"
echo "========================================="

# Initialize database tables
echo "Step 1: Initializing database..."
python -c "
from scripts.models import init_db
init_db()
print('Database tables created!')
"

# Show current stats
python -c "
import sqlite3, os
db = os.path.join(os.path.dirname(os.path.abspath('.')), 'app', 'jobs.db')
try:
    c = sqlite3.connect('/app/jobs.db')
    total = c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
    print(f'Jobs in database: {total}')
except:
    print('Database is empty - scraper will populate it')
"

# Start scraper in background
echo "Step 2: Starting scraper in background..."
nohup python scripts/render_scraper.py > /app/logs/scraper.log 2>&1 &
SCRAPER_PID=$!
echo "Scraper started with PID: $SCRAPER_PID"

# Wait a few seconds for scraper to initialize
sleep 3

# Start web app in foreground
echo "Step 3: Starting web app on port 8000..."
exec python -m scripts.workora_app
