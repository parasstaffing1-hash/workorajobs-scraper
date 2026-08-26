#!/bin/bash
# Launch the production daemon in background
cd "$(dirname "$0")/.."

# Kill any existing scrapers
tasklist 2>/dev/null | grep -i python | awk '{print $2}' | while read pid; do
    kill -9 $pid 2>/dev/null
done

sleep 2

# Start daemon
.venv/Scripts/python.exe -m scripts.daemon_runner > daemon.log 2>&1 &
echo "Daemon started. Check daemon.log for status."
