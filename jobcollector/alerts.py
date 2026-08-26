"""Alerting system — Telegram + Email notifications.

Set these environment variables:
    TELEGRAM_BOT_TOKEN  — from @BotFather
    TELEGRAM_CHAT_ID    — your chat ID (get from @userinfobot)
    ALERT_EMAIL         — email for failure notifications
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS — email config
"""
from __future__ import annotations

import os
import json
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── Configuration ──────────────────────────────────────────────
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")


def send_telegram(message: str) -> bool:
    """Send message via Telegram bot."""
    if not TG_TOKEN or not TG_CHAT_ID:
        return False
    try:
        import httpx
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        resp = httpx.post(url, json={
            "chat_id": TG_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[ALERT] Telegram failed: {e}")
        return False


def send_email(subject: str, body: str) -> bool:
    """Send email alert."""
    if not ALERT_EMAIL or not SMTP_HOST:
        return False
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER or "scraper@jobcollector.com"
        msg["To"] = ALERT_EMAIL

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"[ALERT] Email failed: {e}")
        return False


# ── Alert Functions ────────────────────────────────────────────

def alert_scraper_started(workers: int = 50, sources: int = 50):
    msg = (
        f"🚀 <b>Scraper Started</b>\n\n"
        f"Workers: {workers}\n"
        f"Sources: {sources}\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    send_telegram(msg)


def alert_scraper_crashed(error: str, round_num: int = 0):
    msg = (
        f"💥 <b>Scraper CRASHED</b>\n\n"
        f"Round: {round_num}\n"
        f"Error: <code>{error[:500]}</code>\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    send_telegram(msg)
    send_email(f"[ALERT] Scraper Crashed - Round {round_num}", error)


def alert_rate_dropped(rate: float, expected: float):
    if rate < expected * 0.2:  # Less than 20% of expected
        msg = (
            f"⚠️ <b>Rate Drop Detected</b>\n\n"
            f"Current: {rate:.0f} jobs/min\n"
            f"Expected: {expected:.0f} jobs/min\n"
            f"Drop: {((1 - rate/expected) * 100):.0f}%\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        send_telegram(msg)


def alert_milestone(total: int):
    """Notify when hitting milestones."""
    milestones = [100000, 250000, 500000, 750000, 1000000, 2000000, 5000000]
    if total in milestones or (total > 1000000 and total % 1000000 == 0):
        msg = (
            f"🎯 <b>Milestone: {total:,} JOBS!</b>\n\n"
            f"The scraper just crossed {total:,} unique jobs.\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        send_telegram(msg)


def alert_daily_summary(total: int, fresh_7d: int, fresh_24h: int,
                        sources: int, companies: int, rate: float):
    msg = (
        f"📊 <b>Daily Scraper Report</b>\n\n"
        f"Total: <b>{total:,}</b> jobs\n"
        f"Fresh (7d): {fresh_7d:,}\n"
        f"Fresh (24h): {fresh_24h:,}\n"
        f"Rate: {rate:.0f} jobs/min\n"
        f"Sources: {sources}\n"
        f"Companies: {companies:,}\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    send_telegram(msg)
    send_email(
        f"[Daily] Job Scraper Report — {total:,} jobs",
        f"Total: {total:,}\nFresh 7d: {fresh_7d:,}\nFresh 24h: {fresh_24h:,}\n"
        f"Rate: {rate:.0f}/min\nSources: {sources}\nCompanies: {companies:,}"
    )


def alert_ip_banned(source: str, ip: str = ""):
    msg = (
        f"🚫 <b>IP Possibly Banned</b>\n\n"
        f"Source: {source}\n"
        f"IP: {ip or 'unknown'}\n"
        f"Action: Switching proxy\n"
        f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    send_telegram(msg)
