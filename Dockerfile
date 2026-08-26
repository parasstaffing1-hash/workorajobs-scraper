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

# Install supervisor to manage both processes
RUN pip install supervisor

# Create supervisor config
RUN echo "[program:web]\ncommand=python -m scripts.workora_app\nautostart=true\nautorestart=true\nstdout_logfile=/dev/stdout\nstdout_logfile_maxbytes=0\nstderr_logfile=/dev/stderr\nstderr_logfile_maxbytes=0\n\n[program:scraper]\ncommand=python scripts/render_scraper.py\nautostart=true\nautorestart=true\nstdout_logfile=/app/logs/scraper.log\nstderr_logfile=/app/logs/scraper.log" > /etc/supervisor/conf.d/supervisord.conf

# Start both processes
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
