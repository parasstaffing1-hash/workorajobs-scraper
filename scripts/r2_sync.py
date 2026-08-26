#!/usr/bin/env python3
"""R2 Sync - Automatically sync jobs between local SQLite and Cloudflare R2.

This module runs as part of the scheduler and:
1. After each scrape, syncs new jobs to R2
2. On startup, syncs any jobs from R2 to local SQLite
3. Periodically exports CSV reports to R2
4. Backs up the database to R2 daily

Usage:
    python -m scripts.r2_sync sync
    python -m scripts.r2_sync backup
    python -m scripts.r2_sync export
    python -m scripts.r2_sync status
"""
import os
import sys
import json
import sqlite3
import shutil
import gzip
from datetime import datetime, timedelta
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.r2_storage import (
    is_configured, get_client, upload_bytes, upload_string,
    download_bytes, download_json, list_keys, save_stats, load_stats,
    save_job_to_r2, load_job_from_r2, get_all_job_keys_from_r2,
    save_jobs_batch, export_daily_report, sync_sqlite_to_r2, sync_r2_to_sqlite
)

DB_PATH = Path(__file__).resolve().parent.parent / "jobs.db"


def get_db_stats():
    """Get current database statistics."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        stats = {
            "total_jobs": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "fresh_24h": conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE first_seen_at > datetime('now', '-1 day')"
            ).fetchone()[0],
            "fresh_7d": conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE first_seen_at > datetime('now', '-7 days')"
            ).fetchone()[0],
            "companies": conn.execute(
                "SELECT COUNT(DISTINCT LOWER(company)) FROM jobs"
            ).fetchone()[0],
        }

        # Top sources
        stats["sources"] = []
        for row in conn.execute(
            "SELECT source, COUNT(*) as cnt FROM jobs GROUP BY source ORDER BY cnt DESC LIMIT 20"
        ):
            stats["sources"].append({"source": row[0], "count": row[1]})

        conn.close()
        return stats
    except Exception as e:
        print(f"Error getting stats: {e}")
        return {}


def sync_new_to_r2():
    """Sync only new jobs (not already in R2) to R2."""
    if not is_configured():
        print("R2 not configured. Skipping sync.")
        return 0

    print("Syncing new jobs to R2...")
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # Get existing keys from R2
    existing = set(get_all_job_keys_from_r2())
    print(f"  R2 has {len(existing)} jobs, local has {total}")

    # Find new jobs
    new_keys = []
    for row in conn.execute("SELECT dedupe_key FROM jobs"):
        if row[0] not in existing:
            new_keys.append(row[0])

    if not new_keys:
        print("  No new jobs to sync")
        return 0

    print(f"  Syncing {len(new_keys)} new jobs...")

    synced = 0
    for i, key in enumerate(new_keys):
        row = conn.execute("SELECT * FROM jobs WHERE dedupe_key = ?", (key,)).fetchone()
        if row:
            job = dict(row)
            save_job_to_r2(job)
            synced += 1

        if (i + 1) % 100 == 0:
            print(f"  Progress: {i+1}/{len(new_keys)}", end="\r")

    conn.close()

    # Update stats
    stats = get_db_stats()
    stats["last_sync_to_r2"] = datetime.utcnow().isoformat()
    stats["jobs_synced_to_r2"] = synced
    save_stats(stats)

    print(f"\n  Synced {synced} new jobs to R2")
    return synced


def sync_from_r2():
    """Sync jobs from R2 to local SQLite."""
    if not is_configured():
        print("R2 not configured. Skipping sync.")
        return 0

    print("Syncing from R2 to local...")
    count = sync_r2_to_sqlite(str(DB_PATH))
    return count


def backup_database():
    """Backup SQLite database to R2 as compressed file."""
    if not is_configured():
        print("R2 not configured. Skipping backup.")
        return False

    print("Backing up database to R2...")

    if not DB_PATH.exists():
        print(f"  Database not found: {DB_PATH}")
        return False

    # Compress the database
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backups/jobs_{timestamp}.db.gz"

    with open(DB_PATH, "rb") as f_in:
        with gzip.open(f"/tmp/jobs_backup.gz", "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    with open("/tmp/jobs_backup.gz", "rb") as f:
        data = f.read()

    success = upload_bytes(backup_name, data, "application/gzip")

    # Also save as "latest"
    upload_bytes("backups/latest.db.gz", data, "application/gzip")

    # Clean up
    os.remove("/tmp/jobs_backup.gz")

    if success:
        stats = load_stats() or {}
        stats["last_backup"] = datetime.utcnow().isoformat()
        stats["backup_size_mb"] = round(len(data) / (1024 * 1024), 2)
        save_stats(stats)
        print(f"  Backup saved: {backup_name} ({len(data) // 1024}KB)")

    return success


def export_csv():
    """Export jobs to CSV and upload to R2."""
    if not is_configured():
        print("R2 not configured. Skipping export.")
        return False

    print("Exporting jobs to CSV...")

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cursor = conn.execute(
        """SELECT title, company, location, url, description, salary,
                  source, tags, posted_at
           FROM jobs WHERE is_active = 1
           ORDER BY posted_at DESC"""
    )

    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["title", "company", "location", "url", "description",
                     "salary", "source", "tags", "posted_at"])

    count = 0
    for row in cursor:
        writer.writerow([str(c) if c else "" for c in row])
        count += 1

    conn.close()

    csv_data = output.getvalue().encode("utf-8")
    timestamp = datetime.utcnow().strftime("%Y%m%d")
    filename = f"exports/jobs_{timestamp}.csv"

    upload_bytes(filename, csv_data, "text/csv")
    upload_bytes("exports/latest.csv", csv_data, "text/csv")

    print(f"  Exported {count} jobs to {filename} ({len(csv_data) // 1024}KB)")
    return True


def export_daily():
    """Export daily stats report to R2."""
    if not is_configured():
        return False

    stats = get_db_stats()
    return export_daily_report(
        total=stats.get("total_jobs", 0),
        fresh_24h=stats.get("fresh_24h", 0),
        fresh_7d=stats.get("fresh_7d", 0),
        companies=stats.get("companies", 0),
        sources=stats.get("sources", []),
    )


def show_status():
    """Show current sync status."""
    if not is_configured():
        print("R2 is NOT configured")
        print("Set environment variables:")
        print("  R2_ACCOUNT_ID")
        print("  R2_ACCESS_KEY_ID")
        print("  R2_SECRET_ACCESS_KEY")
        print("  R2_BUCKET_NAME")
        return

    print("R2 Status: CONFIGURED ✓")
    print(f"Bucket: {__import__('scripts.r2_storage', fromlist=['R2_BUCKET']).R2_BUCKET}")

    # Local stats
    local = get_db_stats()
    print(f"\nLocal DB:")
    print(f"  Jobs: {local.get('total_jobs', 0):,}")
    print(f"  Companies: {local.get('companies', 0):,}")
    print(f"  Fresh 24h: {local.get('fresh_24h', 0):,}")
    print(f"  Fresh 7d: {local.get('fresh_7d', 0):,}")

    # R2 stats
    r2_stats = load_stats()
    if r2_stats:
        print(f"\nR2 Stats:")
        print(f"  Total synced: {r2_stats.get('total_synced', 'N/A')}")
        print(f"  Last sync: {r2_stats.get('last_sync_to_r2', 'N/A')}")
        print(f"  Last backup: {r2_stats.get('last_backup', 'N/A')}")
        print(f"  Backup size: {r2_stats.get('backup_size_mb', 'N/A')} MB")

    # R2 files
    keys = list_keys()
    print(f"\nR2 Files: {len(keys)} total")
    for prefix in ["jobs/dedupe/", "backups/", "exports/", "stats/"]:
        count = len([k for k in keys if k.startswith(prefix)])
        if count > 0:
            print(f"  {prefix}: {count}")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m scripts.r2_sync sync      - Sync new jobs to R2")
        print("  python -m scripts.r2_sync download  - Sync R2 jobs to local")
        print("  python -m scripts.r2_sync backup    - Backup database to R2")
        print("  python -m scripts.r2_sync export    - Export jobs to CSV on R2")
        print("  python -m scripts.r2_sync status    - Show sync status")
        return

    cmd = sys.argv[1].lower()

    if cmd == "sync":
        sync_new_to_r2()
    elif cmd == "download":
        sync_from_r2()
    elif cmd == "backup":
        backup_database()
    elif cmd == "export":
        export_csv()
    elif cmd == "status":
        show_status()
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
