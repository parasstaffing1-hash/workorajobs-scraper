#!/usr/bin/env python3
"""Slack/Discord Bot — Interactive job search, alerts, and lead management.

Usage:
    export SLACK_TOKEN=xoxb-your-token
    python -m scripts.slack_bot
"""
from __future__ import annotations
import json, os, sqlite3, time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "slack_bot.log"

SLACK_TOKEN = os.environ.get("SLACK_TOKEN", "")


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def search_jobs(keyword="", location="", limit=10):
    conn = get_db()
    conditions = ["is_active = 1"]
    params = []

    if keyword:
        conditions.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like])

    if location:
        conditions.append("LOWER(location) LIKE ?")
        params.append(f"%{location.lower()}%")

    params.append(limit)

    rows = conn.execute(
        f"SELECT title, company, location, url, source FROM jobs "
        f"WHERE {' AND '.join(conditions)} ORDER BY first_seen_at DESC LIMIT ?",
        params
    ).fetchall()

    conn.close()
    return [{"title": r[0], "company": r[1], "location": r[2], "url": r[3], "source": r[4]} for r in rows]


def get_stats():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_active = 1").fetchone()[0]
    companies = conn.execute("SELECT COUNT(DISTINCT LOWER(TRIM(company))) FROM jobs WHERE company != ''").fetchone()[0]
    conn.close()
    return {"total": total, "companies": companies}


def format_jobs_message(jobs, keyword):
    if not jobs:
        return f"🔍 No jobs found for '{keyword}'"

    msg = f"🔍 *{len(jobs)} Jobs found for '{keyword}'*\n\n"
    for i, j in enumerate(jobs[:5], 1):
        msg += f"*{i}. {j['title']}*\n"
        msg += f"   🏢 {j['company']} | 📍 {j['location']}\n"
        if j['url']:
            msg += f"   <{j['url']}|Apply>\n"
        msg += "\n"

    if len(jobs) > 5:
        msg += f"... and {len(jobs) - 5} more. View all at http://localhost:8000"

    return msg


def format_stats_message():
    stats = get_stats()
    return f"📊 *LeadFlow Stats*\n• Total Jobs: {stats['total']:,}\n• Companies: {stats['companies']:,}\n• Dashboard: http://localhost:8000"


def handle_slack_event(event):
    """Handle a Slack event."""
    text = event.get("text", "").lower()
    user = event.get("user", "")
    channel = event.get("channel", "")

    if not text.startswith("!job") and not text.startswith("!search") and not text.startswith("!stats"):
        return None

    if text.startswith("!stats"):
        return format_stats_message()

    if text.startswith("!job") or text.startswith("!search"):
        parts = text.split()[1:]
        keyword = " ".join(parts) if parts else "software engineer"
        jobs = search_jobs(keyword, limit=5)
        return format_jobs_message(jobs, keyword)

    return None


def run_slack_bot():
    """Run the Slack bot."""
    if not SLACK_TOKEN:
        log("SLACK_TOKEN not set")
        return

    try:
        from slack_sdk import WebClient
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.request import SocketModeRequest
        from slack_sdk.socket_mode.response import SocketModeResponse

        client = SocketModeClient(
            app_token=os.environ.get("SLACK_APP_TOKEN", ""),
            web_client=WebClient(token=SLACK_TOKEN),
        )

        def process(client, req: SocketModeRequest):
            if req.type == "events_api":
                response = SocketMessage()
                event = req.payload.get("event", {})

                if event.get("type") == "message":
                    reply = handle_slack_event(event)
                    if reply:
                        client.web_client.chat_postMessage(
                            channel=event.get("channel"),
                            text=reply
                        )

                client.send_socket_mode_response(response)

        client.socket_mode_request_listeners.append(process)
        client.connect()
        log("Slack bot started")
        import asyncio
        asyncio.ensure_future(client.start())

    except ImportError:
        log("slack-sdk not installed. pip install slack-sdk slack-bolt")
    except Exception as e:
        log(f"Slack bot error: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Test commands locally")
    args = parser.parse_args()

    if args.test:
        print("Testing commands:")
        print(f"\n{format_stats_message()}")
        print(f"\n{format_jobs_message(search_jobs('software engineer', limit=5), 'software engineer')}")
    else:
        run_slack_bot()


if __name__ == "__main__":
    main()
