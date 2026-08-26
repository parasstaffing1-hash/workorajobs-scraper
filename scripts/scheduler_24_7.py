#!/usr/bin/env python3
"""24/7 Scheduler for Workora Jobs - runs scrapers daily and sends email alerts.

This script runs continuously on your server and:
1. Runs scrapers every 6 hours to keep jobs fresh
2. Sends email alerts to users at 9 AM daily
3. Monitors system health and logs stats
4. Auto-restarts on errors

Usage:
    python -m scripts.scheduler_24_7
    
Or run as Windows Service:
    python scripts/scheduler_24_7.py --service
"""
import os
import sys
import time
import json
import signal
import sqlite3
import smtplib
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading

# ── Config ────────────────────────────────────────────────────
DB_PATH = Path(__file__).resolve().parent.parent / "jobs.db"
LOG_PATH = Path(__file__).resolve().parent.parent / "scheduler.log"
STATS_PATH = Path(__file__).resolve().parent.parent / "scheduler_stats.json"

# Email config (set via environment variables)
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "alerts@workorajobs.com")

# R2 config
R2_SYNC_ENABLED = bool(os.environ.get("R2_ACCESS_KEY_ID", ""))

# Schedule config
SCRAPER_INTERVAL_HOURS = 6  # Run scraper every 6 hours
ALERT_CHECK_INTERVAL = 3600  # Check for alerts every hour
HEALTH_CHECK_INTERVAL = 300  # Health check every 5 minutes
STATS_LOG_INTERVAL = 600     # Log stats every 10 minutes
R2_SYNC_INTERVAL = 7200      # Sync to R2 every 2 hours
BACKUP_INTERVAL = 86400      # Backup database daily

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('scheduler')

# Shutdown flag
shutdown_flag = False

def signal_handler(signum, frame):
    global shutdown_flag
    log.info(f"Received signal {signum}, shutting down...")
    shutdown_flag = True

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ── Database Helpers ──────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_stats():
    """Get current database statistics."""
    try:
        conn = get_db()
        stats = {}
        stats['total_jobs'] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        stats['fresh_1h'] = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE first_seen_at > datetime('now', '-1 hour')"
        ).fetchone()[0]
        stats['fresh_24h'] = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE first_seen_at > datetime('now', '-1 day')"
        ).fetchone()[0]
        stats['fresh_7d'] = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE first_seen_at > datetime('now', '-7 days')"
        ).fetchone()[0]
        stats['companies'] = conn.execute(
            "SELECT COUNT(DISTINCT LOWER(company)) FROM jobs"
        ).fetchone()[0]
        try:
            stats['users'] = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            stats['alerts'] = conn.execute(
                "SELECT COUNT(*) FROM job_alerts WHERE is_active = 1"
            ).fetchone()[0]
        except:
            stats['users'] = 0
            stats['alerts'] = 0
        stats['timestamp'] = datetime.now().isoformat()
        conn.close()
        return stats
    except Exception as e:
        log.error(f"Error getting stats: {e}")
        return {}


def save_stats(stats):
    """Save stats to file."""
    try:
        with open(STATS_PATH, 'w') as f:
            json.dump(stats, f, indent=2)
    except Exception as e:
        log.error(f"Error saving stats: {e}")


# ── Scraper Runner ───────────────────────────────────────────

def run_scraper():
    """Run the scraper to fetch new jobs."""
    log.info("=== Starting scraper run ===")
    try:
        before = get_stats().get('total_jobs', 0)
        
        # Run the scraper module
        result = os.system(
            f'cd "{Path(__file__).resolve().parent.parent}" && '
            f'.venv/Scripts/python.exe -m scripts.jobspy_scraper 2>&1 | tail -20'
        )
        
        after = get_stats().get('total_jobs', 0)
        new_jobs = after - before
        
        log.info(f"Scraper completed. New jobs: {new_jobs} (total: {after})")
        
        # Save run stats
        stats = get_stats()
        stats['last_scraper_run'] = datetime.now().isoformat()
        stats['last_scraper_new'] = new_jobs
        save_stats(stats)
        
        return True
    except Exception as e:
        log.error(f"Scraper error: {e}")
        return False


# ── Email Alerts ──────────────────────────────────────────────

def send_email(to_email: str, subject: str, body: str) -> bool:
    """Send email via SMTP."""
    if not SMTP_USER or not SMTP_PASS:
        log.warning("SMTP not configured, skipping email send")
        return False
    
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"Workora Jobs <{EMAIL_FROM}>"
        msg['To'] = to_email
        
        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #2563eb; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
                .content {{ background: #f8fafc; padding: 20px; border: 1px solid #e2e8f0; }}
                .job-card {{ background: white; padding: 15px; margin: 10px 0; border-radius: 8px; border: 1px solid #e2e8f0; }}
                .job-title {{ font-weight: bold; color: #2563eb; text-decoration: none; }}
                .job-meta {{ color: #64748b; font-size: 14px; margin-top: 5px; }}
                .cta {{ text-align: center; padding: 20px; }}
                .btn {{ background: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; }}
                .footer {{ text-align: center; padding: 20px; color: #94a3b8; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🚀 Workora Jobs Alert</h1>
                </div>
                <div class="content">
                    {body}
                </div>
                <div class="cta">
                    <a href="https://workorajobs.com/jobs" class="btn">View All Jobs</a>
                </div>
                <div class="footer">
                    <p>You're receiving this because you set up a job alert on Workora Jobs.</p>
                    <p><a href="https://workorajobs.com/alerts">Manage your alerts</a></p>
                </div>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))
        
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        
        log.info(f"Email sent to {to_email}: {subject}")
        return True
    except Exception as e:
        log.error(f"Email send failed to {to_email}: {e}")
        return False


def check_and_send_alerts():
    """Check for new jobs matching user alerts and send notifications."""
    log.info("Checking for active alerts...")
    
    try:
        conn = get_db()
        alerts = conn.execute(
            "SELECT * FROM job_alerts WHERE is_active = 1"
        ).fetchall()
        
        if not alerts:
            log.info("No active alerts found")
            return
        
        log.info(f"Found {len(alerts)} active alerts")
        
        for alert in alerts:
            user_id = alert['user_id']
            keywords = alert['keywords'] or ''
            locations = alert['locations'] or ''
            name = alert['name'] or 'Job Alert'
            
            # Get user email
            user = conn.execute(
                "SELECT email, username FROM users WHERE id = ?",
                (user_id,)
            ).fetchone()
            
            if not user:
                continue
            
            # Search for matching jobs
            conditions = ["is_active = 1"]
            params = []
            
            if keywords:
                for kw in keywords.split(','):
                    kw = kw.strip()
                    if kw:
                        conditions.append(
                            "(title LIKE ? OR description LIKE ? OR company LIKE ? OR tags LIKE ?)"
                        )
                        like = f"%{kw}%"
                        params.extend([like, like, like, like])
            
            if locations:
                for loc in locations.split(','):
                    loc = loc.strip()
                    if loc:
                        conditions.append("location LIKE ?")
                        params.append(f"%{loc}%")
            
            # Only get jobs from last 24 hours
            conditions.append("first_seen_at > datetime('now', '-1 day')")
            
            where = " AND ".join(conditions)
            
            jobs = conn.execute(
                f"SELECT title, company, location, url, salary, source "
                f"FROM jobs WHERE {where} ORDER BY posted_at DESC LIMIT 10",
                params
            ).fetchall()
            
            if not jobs:
                log.info(f"No new jobs for alert '{name}' (user: {user['email']})")
                continue
            
            # Build email
            body = f"""
            <h2>🎯 {len(jobs)} New Jobs Matching "{keywords}"</h2>
            <p>Hi {user['username']},</p>
            <p>We found {len(jobs)} new jobs matching your alert "<strong>{name}</strong>":</p>
            """
            
            for job in jobs:
                salary = f" - {job['salary']}" if job['salary'] else ""
                body += f"""
                <div class="job-card">
                    <div class="job-title">{job['title'] or 'Unknown Position'}</div>
                    <div class="job-meta">
                        🏢 {job['company'] or 'Unknown'} | 📍 {job['location'] or 'Remote'}{salary}
                    </div>
                </div>
                """
            
            body += f"<p><a href='https://workorajobs.com/jobs?q={keywords}'>See all matching jobs →</a></p>"
            
            # Send email
            subject = f"🚀 {len(jobs)} New Jobs: {keywords[:50]}"
            send_email(user['email'], subject, body)
            
            # Update last sent
            conn.execute(
                "UPDATE job_alerts SET last_sent = datetime('now') WHERE id = ?",
                (alert['id'],)
            )
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        log.error(f"Alert check error: {e}")


# ── Health Check ──────────────────────────────────────────────

def health_check():
    """Monitor system health and log stats."""
    try:
        stats = get_stats()
        save_stats(stats)
        
        log.info(
            f"Health: {stats.get('total_jobs', 0):,} jobs | "
            f"{stats.get('fresh_24h', 0):,} new/24h | "
            f"{stats.get('fresh_1h', 0):,} new/hour | "
            f"{stats.get('companies', 0):,} companies"
        )
        
        # Check if scraper is keeping up (at least 100 new jobs per day)
        if stats.get('fresh_24h', 0) < 100:
            log.warning("Low job freshness! Running scraper...")
            run_scraper()
            
    except Exception as e:
        log.error(f"Health check error: {e}")


# ── R2 Sync ──────────────────────────────────────────────────

def sync_to_r2():
    """Sync new jobs to Cloudflare R2."""
    if not R2_SYNC_ENABLED:
        return
    
    try:
        from scripts.r2_sync import sync_new_to_r2, backup_database, export_daily
        sync_new_to_r2()
    except Exception as e:
        log.error(f"R2 sync error: {e}")


def backup_to_r2():
    """Backup database to R2."""
    if not R2_SYNC_ENABLED:
        return
    
    try:
        from scripts.r2_sync import backup_database
        backup_database()
    except Exception as e:
        log.error(f"R2 backup error: {e}")


def export_to_r2():
    """Export daily report to R2."""
    if not R2_SYNC_ENABLED:
        return
    
    try:
        from scripts.r2_sync import export_daily
        export_daily()
    except Exception as e:
        log.error(f"R2 export error: {e}")


# ── Main Scheduler Loop ──────────────────────────────────────

def run_scheduler():
    """Main scheduler loop that runs tasks at intervals."""
    log.info("=" * 60)
    log.info("Workora Jobs Scheduler Starting...")
    log.info(f"Database: {DB_PATH}")
    log.info(f"Scraper interval: {SCRAPER_INTERVAL_HOURS}h")
    log.info(f"Alert check interval: {ALERT_CHECK_INTERVAL}s")
    log.info(f"R2 sync: {'enabled' if R2_SYNC_ENABLED else 'disabled'}")
    log.info("=" * 60)
    
    # Initial health check
    health_check()
    
    # Initial scraper run
    log.info("Running initial scraper...")
    run_scraper()
    
    # Initial R2 sync
    if R2_SYNC_ENABLED:
        log.info("Initial R2 sync...")
        sync_to_r2()
    
    # Initialize timers
    last_scraper = time.time()
    last_alert_check = time.time()
    last_health_check = time.time()
    last_stats_log = time.time()
    last_r2_sync = time.time()
    last_backup = time.time()
    
    # Main loop
    while not shutdown_flag:
        try:
            now = time.time()
            
            # Run scraper every N hours
            if now - last_scraper >= SCRAPER_INTERVAL_HOURS * 3600:
                run_scraper()
                last_scraper = time.time()
            
            # Check alerts every hour
            if now - last_alert_check >= ALERT_CHECK_INTERVAL:
                check_and_send_alerts()
                last_alert_check = time.time()
            
            # Health check every 5 minutes
            if now - last_health_check >= HEALTH_CHECK_INTERVAL:
                health_check()
                last_health_check = time.time()
            
            # Log stats every 10 minutes
            if now - last_stats_log >= STATS_LOG_INTERVAL:
                stats = get_stats()
                save_stats(stats)
                last_stats_log = time.time()
            
            # Sync to R2 every 2 hours
            if R2_SYNC_ENABLED and (now - last_r2_sync >= R2_SYNC_INTERVAL):
                sync_to_r2()
                last_r2_sync = time.time()
            
            # Backup to R2 daily
            if R2_SYNC_ENABLED and (now - last_backup >= BACKUP_INTERVAL):
                backup_to_r2()
                last_backup = time.time()
            
            # Sleep for 30 seconds before next check
            time.sleep(30)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"Scheduler error: {e}")
            time.sleep(60)  # Wait longer on error
    
    log.info("Scheduler stopped.")


# ── CLI ───────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Workora Jobs 24/7 Scheduler")
    parser.add_argument("--service", action="store_true", help="Run as Windows service")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--scraper-only", action="store_true", help="Run scraper once")
    parser.add_argument("--alerts-only", action="store_true", help="Check alerts once")
    parser.add_argument("--r2-sync", action="store_true", help="Sync to R2")
    parser.add_argument("--stats", action="store_true", help="Show stats and exit")
    args = parser.parse_args()
    
    if args.stats:
        stats = get_stats()
        print(json.dumps(stats, indent=2))
        return
    
    if args.scraper_only:
        run_scraper()
        return
    
    if args.alerts_only:
        check_and_send_alerts()
        return
    
    if args.r2_sync:
        sync_to_r2()
        return
    
    if args.once:
        health_check()
        run_scraper()
        check_and_send_alerts()
        if R2_SYNC_ENABLED:
            sync_to_r2()
        return
    
    run_scheduler()


if __name__ == "__main__":
    main()
