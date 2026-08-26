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

# Start both web app and scraper
CMD ["bash", "-c", "python -c 'from scripts.models import init_db; init_db(); print(\"DB initialized\")' && nohup python scripts/render_scraper.py > /app/logs/scraper.log 2>&1 & python -m scripts.workora_app"]
