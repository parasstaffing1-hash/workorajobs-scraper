#!/usr/bin/env python3
"""N8n Integration — Visual workflow builder for scraping + alerts + reports.

Provides webhook endpoints for N8n workflows and JSON payloads
for automating daily scraping, alerts, and report generation.

Usage:
    python -m scripts.n8n_integration --setup
    python -m scripts.n8n_integration --webhook-port 5678
    python -m scripts.n8n_integration --export-workflow
"""
from __future__ import annotations
import json, os, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "n8n_integration.log"
WORKFLOWS_DIR = ROOT / "n8n_workflows"


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── N8n Workflow Templates ──────────────────────────────────
DAILY_SCRAPE_WORKFLOW = {
    "name": "LeadFlow Daily Scrape",
    "nodes": [
        {
            "name": "Cron Trigger",
            "type": "n8n-nodes-base.cron",
            "parameters": {"triggerTimes": {"item": [{"mode": "everyDay", "hour": 6}]}},
        },
        {
            "name": "Start Scraping",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "url": "http://localhost:8000/api/health",
                "method": "GET",
            },
        },
        {
            "name": "Run Scraper",
            "type": "n8n-nodes-base.executeCommand",
            "parameters": {
                "command": "cd " + str(ROOT) + " && python -m scripts.unified_lead_scraper --rounds 1"
            },
        },
        {
            "name": "Get Stats",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "url": "http://localhost:8000/api/stats",
                "method": "GET",
            },
        },
        {
            "name": "Send Summary",
            "type": "n8n-nodes-base.emailSend",
            "parameters": {
                "fromEmail": "alerts@leadflow.com",
                "toEmail": "admin@company.com",
                "subject": "Daily LeadFlow Report - {{new_jobs}} new jobs",
                "text": "Total: {{total_jobs}}\nCompanies: {{companies}}",
            },
        },
    ],
    "connections": {
        "Cron Trigger": {"main": [[{"node": "Run Scraper", "type": "main", "index": 0}]]},
        "Run Scraper": {"main": [[{"node": "Get Stats", "type": "main", "index": 0}]]},
        "Get Stats": {"main": [[{"node": "Send Summary", "type": "main", "index": 0}]]},
    }
}

ALERT_WORKFLOW = {
    "name": "LeadFlow Job Alerts",
    "nodes": [
        {
            "name": "Cron Trigger",
            "type": "n8n-nodes-base.cron",
            "parameters": {"triggerTimes": {"item": [{"mode": "everyDay", "hour": 9}]}},
        },
        {
            "name": "Get New Jobs",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "url": "http://localhost:8000/api/webhook/n8n?days=1&limit=50",
                "method": "GET",
            },
        },
        {
            "name": "Send Telegram",
            "type": "n8n-nodes-base.telegram",
            "parameters": {
                "chatId": "{{TELEGRAM_CHAT_ID}}",
                "text": "🔔 {{jobs_count}} new jobs today!\n\nTop jobs:\n{{jobs_summary}}",
            },
        },
    ],
    "connections": {
        "Cron Trigger": {"main": [[{"node": "Get New Jobs", "type": "main", "index": 0}]]},
        "Get New Jobs": {"main": [[{"node": "Send Telegram", "type": "main", "index": 0}]]},
    }
}

EXPORT_WORKFLOW = {
    "name": "LeadFlow Daily Export",
    "nodes": [
        {
            "name": "Cron Trigger",
            "type": "n8n-nodes-base.cron",
            "parameters": {"triggerTimes": {"item": [{"mode": "everyDay", "hour": 22}]}},
        },
        {
            "name": "Export Jobs",
            "type": "n8n-nodes-base.httpRequest",
            "parameters": {
                "url": "http://localhost:8000/api/jobs?days=1&limit=1000",
                "method": "GET",
            },
        },
        {
            "name": "Save to Google Sheets",
            "type": "n8n-nodes-base.googleSheets",
            "parameters": {
                "sheetId": "{{GOOGLE_SHEET_ID}}",
                "operation": "append",
            },
        },
    ],
    "connections": {
        "Cron Trigger": {"main": [[{"node": "Export Jobs", "type": "main", "index": 0}]]},
        "Export Jobs": {"main": [[{"node": "Save to Google Sheets", "type": "main", "index": 0}]]},
    }
}


def export_workflows():
    """Export N8n workflow templates."""
    WORKFLOWS_DIR.mkdir(exist_ok=True)

    workflows = {
        "daily_scrape.json": DAILY_SCRAPE_WORKFLOW,
        "job_alerts.json": ALERT_WORKFLOW,
        "daily_export.json": EXPORT_WORKFLOW,
    }

    for filename, workflow in workflows.items():
        filepath = WORKFLOWS_DIR / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(workflow, f, indent=2)
        log(f"Exported workflow: {filename}")

    # Create README
    readme = """
# N8n Workflow Integration

## Setup

1. Import workflows into N8n:
   - daily_scrape.json - Automated daily scraping
   - job_alerts.json - Daily job alerts at 9 AM
   - daily_export.json - Daily export to Google Sheets

2. Configure environment variables in N8n:
   - TELEGRAM_CHAT_ID
   - GOOGLE_SHEET_ID

3. Activate workflows in N8n UI.

## API Endpoints

- GET /api/webhook/n8n - Main webhook for N8n
- GET /api/webhook/n8n?days=1&keyword=python - Filtered jobs
- GET /api/stats - Database statistics
- GET /api/jobs?keyword=react&limit=100 - Search jobs

## Example N8n Workflow

1. Cron Trigger (daily at 9 AM)
2. HTTP Request → GET /api/webhook/n8n?days=1
3. Function → Format message
4. Telegram/Send Message → Send alert
"""
    readme_path = WORKFLOWS_DIR / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme)

    log("All workflows exported")
    return list(workflows.keys())


def get_n8n_webhook_data(days=1, keyword="", location="", limit=100):
    """Get data formatted for N8n webhooks."""
    conn = get_db()
    conditions = ["is_active = 1"]
    params = []

    if days > 0:
        conditions.append("first_seen_at >= datetime('now', ?)")
        params.append(f"-{days} days")

    if keyword:
        conditions.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like])

    if location:
        conditions.append("LOWER(location) LIKE ?")
        params.append(f"%{location.lower()}%")

    params.append(limit)

    rows = conn.execute(
        f"SELECT title, company, location, url, source, posted_at, salary "
        f"FROM jobs WHERE {' AND '.join(conditions)} "
        f"ORDER BY first_seen_at DESC LIMIT ?",
        params
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_active = 1").fetchone()[0]
    conn.close()

    jobs = [
        {
            "title": r[0], "company": r[1], "location": r[2],
            "url": r[3], "source": r[4], "posted_at": r[5], "salary": r[6]
        }
        for r in rows
    ]

    # Format for N8n
    summary = "\n".join([f"• {j['title']} @ {j['company']}" for j in jobs[:10]])

    return {
        "status": "ok",
        "total_jobs": total,
        "new_jobs": len(jobs),
        "jobs": jobs,
        "summary": summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--export-workflow", action="store_true")
    parser.add_argument("--webhook", action="store_true")
    parser.add_argument("--webhook-port", type=int, default=5678)
    args = parser.parse_args()

    if args.setup or args.export_workflow:
        files = export_workflows()
        print(f"Exported {len(files)} workflow templates to {WORKFLOWS_DIR}/")
    elif args.webhook:
        from fastapi import FastAPI, Query
        from fastapi.responses import JSONResponse
        import uvicorn

        app = FastAPI(title="LeadFlow N8n Webhooks")

        @app.get("/api/webhook/n8n")
        def webhook_n8n(days: int = 1, keyword: str = "", location: str = "", limit: int = 100):
            return get_n8n_webhook_data(days, keyword, location, limit)

        uvicorn.run(app, host="0.0.0.0", port=args.webhook_port)
    else:
        print("LeadFlow N8n Integration")
        print("  --setup           Export workflow templates")
        print("  --export-workflow Export workflows")
        print("  --webhook         Start webhook server")


if __name__ == "__main__":
    main()
