# 🚀 Job Scraper — Deployment Guide

## Quick Start (1 Command)

### Windows
```bat
deploy.bat
```

### Linux/Mac
```bash
chmod +x deploy.sh
./deploy.sh
```

This builds Docker images, starts all services, and gives you a live dashboard.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Docker Compose                  │
├────────────────┬────────────────┬───────────────┤
│   API Server   │  Scraper V4    │  ATS Hunter   │
│   (FastAPI)    │  (40 workers)  │  (50 workers) │
│   Port 8000    │  50+ sources   │  28K+ slugs   │
│   Dashboard    │  500 keywords  │  6 ATS APIs   │
│   /api/*       │  200 locations │               │
├────────────────┴────────────────┴───────────────┤
│              SQLite (jobs.db)                     │
└─────────────────────────────────────────────────┘
```

## Services

| Service | Description | Port |
|---------|-------------|------|
| `api` | FastAPI server + dashboard | 8000 |
| `scraper-v4` | Main job scraper (Dice, SimplyHired, etc.) | - |
| `ats-hunter` | Discovers new company career pages | - |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Live dashboard (auto-refreshing) |
| `/api/health` | GET | Health check + total count |
| `/api/stats` | GET | Full stats (sources, companies, daily) |
| `/api/jobs?q=...` | GET | Search jobs with filters |
| `/api/sources` | GET | All sources and counts |
| `/api/webhook/n8n?days=1` | GET | n8n daily trigger |
| `/docs` | GET | OpenAPI documentation |

### Example Queries

```bash
# Get software engineer jobs from last 24h
curl "http://localhost:8000/api/jobs?q=software+engineer&days=1&limit=100"

# Get jobs from specific location
curl "http://localhost:8000/api/jobs?location=remote&limit=50"

# Get jobs from specific source
curl "http://localhost:8000/api/jobs?source=greenhouse:stripe&days=7"

# n8n daily webhook
curl "http://localhost:8000/api/webhook/n8n?days=1&keyword=developer&limit=500"
```

## n8n Integration

1. Open n8n → Create new workflow
2. Add **Cron Trigger** → Set to 9:00 AM daily
3. Add **HTTP Request** node:
   - Method: GET
   - URL: `http://YOUR_SERVER:8000/api/webhook/n8n?days=1&limit=500`
4. Add processing node (Send Email, Telegram, etc.)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///app/jobs.db` | Database connection |
| `API_PORT` | `8000` | API server port |
| `REQUIRE_API_KEY` | `false` | Require API keys |
| `ADMIN_API_KEY` | - | Admin key for key management |
| `API_KEYS` | - | Comma-separated API keys |
| `RATE_LIMIT_RPM` | `120` | Rate limit per key per minute |
| `TELEGRAM_BOT_TOKEN` | - | Telegram alerts token |
| `TELEGRAM_CHAT_ID` | - | Telegram chat ID |

## Manual Deployment (Without Docker)

```bash
# Install Python 3.11+
pip install -r requirements.txt

# Start API server
python -m uvicorn scripts.api_server:app --host 0.0.0.0 --port 8000

# Start scraper (in another terminal)
python -B scripts/mega_scraper_v4.py

# Start ATS hunter (in another terminal)
python -B scripts/ats_hunter_v2.py
```

## Updating

```bash
# Pull latest code
git pull

# Rebuild and restart
docker compose up -d --build
```

## Monitoring

```bash
# Watch all logs
docker compose logs -f

# Watch specific service
docker compose logs -f api
docker compose logs -f scraper-v4

# Check health
curl http://localhost:8000/api/health

# Check stats
curl http://localhost:8000/api/stats | python -m json.tool
```

## Troubleshooting

### API server won't start
```bash
docker compose logs api
# Usually port 8000 is in use - change API_PORT in .env
```

### Scraper not producing jobs
```bash
docker compose logs scraper-v4
# Check if rate-limited - scrapers auto-retry
```

### Out of memory
```bash
# Reduce scraper workers by editing mega_scraper_v4.py:
# WORKERS = 40  →  WORKERS = 20
```

## Current Stats

- **Total Jobs**: 700,000+
- **Fresh 7d**: 700,000+
- **Scraping Rate**: ~400 jobs/minute
- **Sources**: 50+ (Dice, SimplyHired, Indeed, LinkedIn, ATS APIs, JSON APIs)
- **Keywords**: 500+ (software engineering, AI/ML, DevOps, etc.)
- **Locations**: 200+ (US, India, Europe, Asia Pacific)
