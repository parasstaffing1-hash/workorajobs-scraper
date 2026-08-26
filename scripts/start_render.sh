#!/bin/bash
# Startup script for Render - initializes database

echo "Starting Workora Jobs..."

# Initialize database tables
python -c "
from scripts.models import init_db
init_db()
print('Database initialized!')
"

# Start the web app
python -m scripts.workora_app
