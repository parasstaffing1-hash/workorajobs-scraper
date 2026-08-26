#!/bin/bash
set -e

echo "=== Job Scraper Production Entry Point ==="
echo "Starting API server on port ${PORT:-8000}..."

# Start API server in background
python -m uvicorn scripts.api_server:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --log-level info &
API_PID=$!

echo "Starting scraper..."
# Start scraper in background
python -B scripts/pagination_scraper.py &
SCRAPER_PID=$!

echo "API PID: $API_PID | Scraper PID: $SCRAPER_PID"

# Trap shutdown signals
trap "kill $API_PID $SCRAPER_PID 2>/dev/null; exit 0" SIGTERM SIGINT

# Wait for either to exit
wait -n $API_PID $SCRAPER_PID
echo "A process exited. Shutting down..."
kill $API_PID $SCRAPER_PID 2>/dev/null
exit 1
