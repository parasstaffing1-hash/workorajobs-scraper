#!/usr/bin/env python3
"""Scheduler — Cron-based daily scraping + 9AM reports + alerts.

Sets up scheduled tasks for:
1. Daily scraping (runs every 4 hours)
2. Email finding (runs daily)
3. Lead scoring (runs daily)
4. Alert notifications (runs daily at 9 AM)
5. PDF report generation (runs daily at 9 AM)

Usage:
    python -m scripts.scheduler --setup
    python -m scripts.scheduler --run-now
    python -m scripts.scheduler --status
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "scheduler.log"
TASKS_FILE = ROOT / "scheduler_tasks.json"

PYTHON = str(Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_script(script_name, args=None):
    """Run a Python script."""
    cmd = [PYTHON, "-m", script_name]
    if args:
        cmd.extend(args)
    try:
        log(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.stdout:
            log(result.stdout[:500])
        if result.stderr:
            log(f"STDERR: {result.stderr[:500]}")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log(f"Timeout running {script_name}")
        return False
    except Exception as e:
        log(f"Error running {script_name}: {e}")
        return False


def job_scraping():
    """Run job scraping from all backends."""
    log("=== Starting Job Scraping ===")
    return run_script("scripts.unified_lead_scraper", ["--rounds", "1"])


def email_finding():
    """Run email finder on recent jobs."""
    log("=== Starting Email Finding ===")
    return run_script("scripts.email_finder", ["--limit", "200"])


def lead_scoring():
    """Run lead scoring on all jobs."""
    log("=== Starting Lead Scoring ===")
    return run_script("scripts.lead_scorer", ["--update-all", "--limit", "5000"])


def dedup_cleanup():
    """Run deduplication."""
    log("=== Starting Dedup Cleanup ===")
    return run_script("scripts.dedup_engine", ["--dedup", "--threshold", "0.85"])


def send_alerts():
    """Send daily alert notifications."""
    log("=== Sending Daily Alerts ===")
    return run_script("scripts.alert_system", ["--keyword", "software engineer", "--hours", "24"])


def generate_report():
    """Generate daily PDF report."""
    log("=== Generating Daily Report ===")
    return run_script("scripts.pdf_export", ["--analytics"])


def company_enrichment():
    """Run company enrichment."""
    log("=== Running Company Enrichment ===")
    return run_script("scripts.dataforge_enricher", ["--limit", "500"])


def proxy_refresh():
    """Refresh proxy pool."""
    log("=== Refreshing Proxies ===")
    return run_script("scripts.proxy_pool", ["--refresh", "--count", "50"])


def daily_pipeline():
    """Run the full daily pipeline."""
    log("=" * 60)
    log("DAILY PIPELINE STARTING")
    log("=" * 60)

    steps = [
        ("Job Scraping", job_scraping),
        ("Email Finding", email_finding),
        ("Lead Scoring", lead_scoring),
        ("Dedup Cleanup", dedup_cleanup),
        ("Company Enrichment", company_enrichment),
        ("Send Alerts", send_alerts),
        ("Generate Report", generate_report),
    ]

    results = {}
    for name, func in steps:
        log(f"\n--- {name} ---")
        try:
            success = func()
            results[name] = "✅ Success" if success else "⚠️ Partial"
        except Exception as e:
            log(f"Error: {e}")
            results[name] = f"❌ Failed: {e}"

    log("\n" + "=" * 60)
    log("DAILY PIPELINE COMPLETE")
    for name, status in results.items():
        log(f"  {name}: {status}")
    log("=" * 60)

    return results


def setup_scheduled_tasks():
    """Set up Windows scheduled tasks for automation."""
    log("Setting up scheduled tasks...")

    # Clean up old tasks
    tasks_to_clean = [
        "LeadFlow_DailyPipeline",
        "LeadFlow_Scraping",
        "LeadFlow_Alerts",
        "LeadFlow_Refresh",
    ]
    for task in tasks_to_clean:
        subprocess.run(
            f'schtasks /delete /tn "{task}" /f',
            shell=True, capture_output=True
        )

    # Create new tasks
    tasks = [
        {
            "name": "LeadFlow_DailyPipeline",
            "description": "Run full daily pipeline",
            "command": f'"{PYTHON}" -m scripts.scheduler --run-now',
            "schedule": "DAILY",
            "start_time": "06:00",
        },
        {
            "name": "LeadFlow_Scraping",
            "description": "Run job scraping every 4 hours",
            "command": f'"{PYTHON}" -m scripts.unified_lead_scraper --rounds 1',
            "schedule": "HOURLY",
            "interval": 4,
        },
        {
            "name": "LeadFlow_Alerts",
            "description": "Send alerts at 9 AM daily",
            "command": f'"{PYTHON}" -m scripts.alert_system --keyword "software engineer" --hours 24',
            "schedule": "DAILY",
            "start_time": "09:00",
        },
        {
            "name": "LeadFlow_Refresh",
            "description": "Refresh proxies and enrichment weekly",
            "command": f'"{PYTHON}" -m scripts.proxy_pool --refresh',
            "schedule": "WEEKLY",
            "start_time": "02:00",
        },
    ]

    for task in tasks:
        cmd = f'schtasks /create /tn "{task["name"]}" /tr "{task["command"]}"'
        if task["schedule"] == "DAILY":
            cmd += f' /sc daily /st {task["start_time"]}'
        elif task["schedule"] == "HOURLY":
            cmd += f' /sc hourly /st {task["start_time"]} /tr {task["interval"]}'
        elif task["schedule"] == "WEEKLY":
            cmd += f' /sc weekly /d SUN /st {task["start_time"]}'

        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            log(f"✅ Created task: {task['name']}")
        else:
            log(f"⚠️ Task {task['name']}: {result.stderr[:100]}")

    log("Scheduled tasks setup complete")


def show_status():
    """Show status of scheduled tasks."""
    log("Checking scheduled tasks...")
    result = subprocess.run('schtasks /query /tn "LeadFlow_*" /fo LIST', shell=True, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    else:
        print("No LeadFlow tasks found. Run --setup to create them.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="LeadFlow Scheduler")
    parser.add_argument("--setup", action="store_true", help="Set up scheduled tasks")
    parser.add_argument("--run-now", action="store_true", help="Run daily pipeline now")
    parser.add_argument("--status", action="store_true", help="Show task status")
    parser.add_argument("--scrape", action="store_true", help="Run scraping only")
    parser.add_argument("--alerts", action="store_true", help="Send alerts only")
    parser.add_argument("--report", action="store_true", help="Generate report only")
    args = parser.parse_args()

    if args.setup:
        setup_scheduled_tasks()
    elif args.run_now:
        daily_pipeline()
    elif args.scrape:
        job_scraping()
    elif args.alerts:
        send_alerts()
    elif args.report:
        generate_report()
    elif args.status:
        show_status()
    else:
        print("LeadFlow Scheduler")
        print("  --setup     Set up Windows scheduled tasks")
        print("  --run-now   Run daily pipeline now")
        print("  --status    Show task status")
        print("  --scrape    Run scraping only")
        print("  --alerts    Send alerts only")
        print("  --report    Generate report only")


if __name__ == "__main__":
    main()
