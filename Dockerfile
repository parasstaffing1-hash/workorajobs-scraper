FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y \
    curl wget gnupg sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Ensure dirs
RUN mkdir -p logs templates static/css static/js

EXPOSE 8000

# Start web app (scraper will be started separately via Render)
CMD ["python", "-m", "scripts.workora_app"]
