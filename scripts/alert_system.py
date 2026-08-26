#!/usr/bin/env python3
"""Alert System — Get notified via Telegram, Slack, or Email when new jobs appear.

Setup:
    export TELEGRAM_TOKEN=your_bot_token
    export TELEGRAM_CHAT_ID=your_chat_id
    export SLACK_WEBHOOK=https://hooks.slack.com/services/xxx
    export SMTP_HOST=smtp.gmail.com
    export SMTP_PORT=587
    export SMTP_USER=your@email.com
    export SMTP_PASS=your_password
    export ALERT_EMAIL=recipient@email.com

Usage:
    python -m scripts.alert_system --keyword "python developer" --location "remote"
    python -m scripts.alert_system --telegram --keyword "react"
    python -m scripts.alert_system --slack --daily
"""
from __future__ import annotations
import json, os, sqlite3, smtplib, time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "alert_system.log"
ALERT_STATE = ROOT / "alert_state.json"

# Config from environment
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    try:
        with open(ALERT_STATE, "r") as f:
            return json.load(f)
    except:
        return {"last_check": None, "seen_urls": []}


def save_state(state):
    with open(ALERT_STATE, "w") as f:
        json.dump(state, f)


def send_telegram(message):
    """Send message via Telegram bot."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        import httpx
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        r = httpx.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log(f"Telegram error: {e}")
        return False


def send_slack(message):
    """Send message via Slack webhook."""
    if not SLACK_WEBHOOK:
        return False
    try:
        import httpx
        payload = {"text": message}
        r = httpx.post(SLACK_WEBHOOK, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log(f"Slack error: {e}")
        return False


def send_email(subject, body):
    """Send email notification."""
    if not SMTP_USER or not ALERT_EMAIL:
        return False
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = ALERT_EMAIL
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        log(f"Email error: {e}")
        return False


def format_jobs_telegram(jobs, keyword):
    """Format jobs for Telegram notification."""
    msg = f"🔔 <b>New Jobs Found!</b>\n"
    msg += f"🔍 Keyword: <code>{keyword}</code>\n"
    msg += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    msg += f"📊 Found: {len(jobs)} new jobs\n\n"

    for i, job in enumerate(jobs[:10], 1):
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        location = job.get("location", "")
        url = job.get("url", "")
        msg += f"<b>{i}. {title}</b>\n"
        msg += f"   🏢 {company}"
        if location:
            msg += f" | 📍 {location}"
        msg += "\n"
        if url:
            msg += f"   🔗 <a href=\"{url}\">Apply Now</a>\n"
        msg += "\n"

    if len(jobs) > 10:
        msg += f"... and {len(jobs) - 10} more jobs\n"

    return msg


def format_jobs_slack(jobs, keyword):
    """Format jobs for Slack notification."""
    msg = f"🔔 *New Jobs Found!*\n"
    msg += f"🔍 Keyword: `{keyword}`\n"
    msg += f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
    msg += f"📊 Found: {len(jobs)} new jobs\n\n"

    for i, job in enumerate(jobs[:10], 1):
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        location = job.get("location", "")
        url = job.get("url", "")
        msg += f"*{i}. {title}*\n"
        msg += f"   🏢 {company}"
        if location:
            msg += f" | 📍 {location}"
        msg += "\n"
        if url:
            msg += f"   🔗 <{url}|Apply Now>\n"
        msg += "\n"

    if len(jobs) > 10:
        msg += f"... and {len(jobs) - 10} more jobs\n"

    return msg


def format_email_html(jobs, keyword):
    """Format jobs as HTML email."""
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
    <h2 style="color: #3b82f6;">🔔 New Jobs Found!</h2>
    <p><strong>Keyword:</strong> {keyword}</p>
    <p><strong>Date:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
    <p><strong>Found:</strong> {len(jobs)} new jobs</p>
    <hr>
    """

    for i, job in enumerate(jobs[:20], 1):
        title = job.get("title", "Unknown")
        company = job.get("company", "Unknown")
        location = job.get("location", "")
        url = job.get("url", "")
        html += f"""
        <div style="margin-bottom: 15px; padding: 10px; border: 1px solid #eee; border-radius: 5px;">
            <h3 style="margin: 0; color: #1f2937;">{i}. {title}</h3>
            <p style="margin: 5px 0; color: #6b7280;">
                🏢 {company} {'| 📍 ' + location if location else ''}
            </p>
            {'<a href="' + url + '" style="color: #3b82f6;">Apply Now →</a>' if url else ''}
        </div>
        """

    if len(jobs) > 20:
        html += f"<p>... and {len(jobs) - 20} more jobs</p>"

    html += """
    <hr>
    <p style="color: #9ca3af; font-size: 12px;">
        Sent by LeadFlow Job Scraper | <a href="http://localhost:8000">View Dashboard</a>
    </p>
    </body>
    </html>
    """
    return html


def check_new_jobs(keyword, location="", hours=24):
    """Check for new jobs matching criteria since last alert."""
    state = load_db()
    seen_urls = set(state.get("seen_urls", []))

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

    if hours > 0:
        conditions.append("first_seen_at >= datetime('now', ?)")
        params.append(f"-{hours} hours")

    where = " AND ".join(conditions)
    params.append(500)

    rows = conn.execute(
        f"SELECT title, company, location, url, source, posted_at "
        f"FROM jobs WHERE {where} ORDER BY first_seen_at DESC LIMIT ?",
        params
    ).fetchall()
    conn.close()

    # Filter out already-seen URLs
    new_jobs = []
    for row in rows:
        url = row[3] or ""
        if url not in seen_urls:
            new_jobs.append({
                "title": row[0],
                "company": row[1],
                "location": row[2],
                "url": url,
                "source": row[4],
                "posted_at": row[5],
            })
            seen_urls.add(url)

    # Update state
    state["seen_urls"] = list(seen_urls)[-10000:]  # Keep last 10K
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    return new_jobs


def send_alerts(jobs, keyword, telegram=False, slack=False, email=False):
    """Send alerts via configured channels."""
    if not jobs:
        log("No new jobs to alert about")
        return

    sent = 0

    if telegram or (not telegram and not slack and not email):
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            msg = format_jobs_telegram(jobs, keyword)
            if send_telegram(msg):
                log(f"Telegram alert sent: {len(jobs)} jobs")
                sent += 1

    if slack or (not telegram and not slack and not email):
        if SLACK_WEBHOOK:
            msg = format_jobs_slack(jobs, keyword)
            if send_slack(msg):
                log(f"Slack alert sent: {len(jobs)} jobs")
                sent += 1

    if email or (not telegram and not slack and not email):
        if SMTP_USER and ALERT_EMAIL:
            subject = f"🔔 {len(jobs)} New Jobs - {keyword} - {datetime.now().strftime('%Y-%m-%d')}"
            body = format_email_html(jobs, keyword)
            if send_email(subject, body):
                log(f"Email alert sent: {len(jobs)} jobs")
                sent += 1

    if sent == 0:
        log("No alert channels configured. Set TELEGRAM_TOKEN, SLACK_WEBHOOK, or SMTP_* env vars.")
        # Print to console as fallback
        print(f"\n{'='*60}")
        print(f"ALERT: {len(jobs)} new jobs for '{keyword}'")
        print(f"{'='*60}")
        for i, job in enumerate(jobs[:10], 1):
            print(f"  {i}. {job['title']} @ {job['company']}")
        print()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", default="software engineer")
    parser.add_argument("--location", default="")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--slack", action="store_true")
    parser.add_argument("--email", action="store_true")
    parser.add_argument("--daily", action="store_true", help="Send daily summary")
    args = parser.parse_args()

    log(f"Checking for new jobs: keyword={args.keyword}, hours={args.hours}")
    jobs = check_new_jobs(args.keyword, args.location, args.hours)
    log(f"Found {len(jobs)} new jobs")

    send_alerts(jobs, args.keyword, args.telegram, args.slack, args.email)


if __name__ == "__main__":
    main()
