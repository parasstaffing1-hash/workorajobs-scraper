#!/bin/bash
set -e

echo "Starting Workora Jobs..."

# Initialize database
python -c "from scripts.models import init_db; init_db(); print('DB initialized')"

# Start scraper in background
nohup python scripts/render_scraper.py > /app/logs/scraper.log 2>&1 &
echo "Scraper started"

# Start web app
exec python -m scripts.workora_app
