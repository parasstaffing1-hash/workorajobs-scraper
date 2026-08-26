"""Cloudflare R2 Storage Integration for Workora Jobs.

R2 is S3-compatible, so we use boto3 to interact with it.

Setup:
1. Go to https://dash.cloudflare.com → R2 Object Storage
2. Create a bucket (e.g., 'workora-jobs')
3. Go to R2 → Manage R2 API Tokens → Create API Token
4. Copy Access Key ID and Secret Access Key
5. Set environment variables:
   R2_ACCOUNT_ID=your-cloudflare-account-id
   R2_ACCESS_KEY_ID=your-access-key-id
   R2_SECRET_ACCESS_KEY=your-secret-access-key
   R2_BUCKET_NAME=workora-jobs
   R2_PUBLIC_URL=https://pub-xxxxx.r2.dev  (optional, for public access)
"""
import os
import json
import hashlib
import io
import csv
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

# ── Configuration ─────────────────────────────────────────────

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET = os.environ.get("R2_BUCKET_NAME", "workora-jobs")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL", "")
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

# Lazy-loaded boto3 S3 client
_client = None


def get_client():
    """Get or create boto3 S3 client for R2."""
    global _client
    if _client is None:
        try:
            import boto3
        except ImportError:
            raise ImportError("Install boto3: pip install boto3")

        _client = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_KEY,
            region_name="auto",
        )
    return _client


def is_configured():
    """Check if R2 is configured."""
    return bool(R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_KEY)


# ── Low-Level Operations ─────────────────────────────────────

def upload_bytes(key: str, data: bytes, content_type: str = "application/json") -> bool:
    """Upload bytes to R2."""
    if not is_configured():
        return False
    try:
        client = get_client()
        client.put_object(
            Bucket=R2_BUCKET,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return True
    except Exception as e:
        print(f"R2 upload error: {e}")
        return False


def upload_string(key: str, text: str, content_type: str = "application/json") -> bool:
    """Upload string as file to R2."""
    return upload_bytes(key, text.encode("utf-8"), content_type)


def download_bytes(key: str) -> Optional[bytes]:
    """Download file from R2."""
    if not is_configured():
        return None
    try:
        client = get_client()
        response = client.get_object(Bucket=R2_BUCKET, Key=key)
        return response["Body"].read()
    except Exception as e:
        print(f"R2 download error: {e}")
        return None


def download_json(key: str) -> Optional[dict]:
    """Download and parse JSON file from R2."""
    data = download_bytes(key)
    if data:
        return json.loads(data.decode("utf-8"))
    return None


def delete_object(key: str) -> bool:
    """Delete file from R2."""
    if not is_configured():
        return False
    try:
        client = get_client()
        client.delete_object(Bucket=R2_BUCKET, Key=key)
        return True
    except Exception as e:
        print(f"R2 delete error: {e}")
        return False


def list_keys(prefix: str = "") -> List[str]:
    """List all keys with given prefix."""
    if not is_configured():
        return []
    try:
        client = get_client()
        keys = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=R2_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys
    except Exception as e:
        print(f"R2 list error: {e}")
        return []


def get_public_url(key: str) -> str:
    """Get public URL for a file."""
    if R2_PUBLIC_URL:
        return f"{R2_PUBLIC_URL}/{key}"
    return ""


# ── Job Storage ───────────────────────────────────────────────

def save_jobs_batch(jobs: List[Dict], batch_id: str = None) -> bool:
    """Save a batch of jobs to R2 as JSON.

    Each batch is stored as:
      jobs/batches/{batch_id}.json

    The batch contains:
      - jobs: list of job dicts
      - count: number of jobs
      - timestamp: when batch was created
      - dedupe_keys: list of dedupe keys for quick lookup
    """
    if not is_configured():
        return False

    if batch_id is None:
        batch_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    batch_data = {
        "batch_id": batch_id,
        "count": len(jobs),
        "timestamp": datetime.utcnow().isoformat(),
        "dedupe_keys": [j.get("dedupe_key", "") for j in jobs],
        "jobs": jobs,
    }

    key = f"jobs/batches/{batch_id}.json"
    return upload_string(key, json.dumps(batch_data, default=str))


def load_jobs_batch(batch_id: str) -> Optional[List[Dict]]:
    """Load a job batch from R2."""
    key = f"jobs/batches/{batch_id}.json"
    data = download_json(key)
    if data:
        return data.get("jobs", [])
    return None


def list_job_batches() -> List[str]:
    """List all batch IDs."""
    keys = list_keys("jobs/batches/")
    return [k.split("/")[-1].replace(".json", "") for k in keys]


def get_all_job_keys_from_r2() -> List[str]:
    """Get all dedupe_keys of jobs stored in R2."""
    keys = list_keys("jobs/dedupe/")
    return [k.split("/")[-1].replace(".json", "") for k in keys]


def save_job_to_r2(job: Dict) -> bool:
    """Save a single job to R2, indexed by dedupe_key."""
    if not is_configured():
        return False

    dedupe_key = job.get("dedupe_key", "")
    if not dedupe_key:
        return False

    key = f"jobs/dedupe/{dedupe_key}.json"
    return upload_string(key, json.dumps(job, default=str))


def load_job_from_r2(dedupe_key: str) -> Optional[Dict]:
    """Load a single job from R2."""
    key = f"jobs/dedupe/{dedupe_key}.json"
    return download_json(key)


def save_stats(stats: dict) -> bool:
    """Save scraper stats to R2."""
    if not is_configured():
        return False
    return upload_string("stats/latest.json", json.dumps(stats, default=str, indent=2))


def load_stats() -> Optional[dict]:
    """Load latest scraper stats from R2."""
    return download_json("stats/latest.json")


def export_jobs_csv(jobs: List[Dict], filename: str = None) -> bool:
    """Export jobs to CSV and upload to R2."""
    if not is_configured():
        return False

    if filename is None:
        filename = f"exports/jobs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"

    # Create CSV in memory
    output = io.StringIO()
    if jobs:
        fieldnames = jobs[0].keys()
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(jobs)

    csv_data = output.getvalue().encode("utf-8")
    return upload_bytes(filename, csv_data, "text/csv")


def export_daily_report(total: int, fresh_24h: int, fresh_7d: int,
                        companies: int, sources: List[dict]) -> bool:
    """Export daily report to R2."""
    if not is_configured():
        return False

    report = {
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "timestamp": datetime.utcnow().isoformat(),
        "stats": {
            "total_jobs": total,
            "fresh_24h": fresh_24h,
            "fresh_7d": fresh_7d,
            "companies": companies,
        },
        "sources": sources[:20],
        "report_url": f"https://workorajobs.com/api/stats",
    }

    key = f"reports/{datetime.utcnow().strftime('%Y-%m-%d')}.json"
    return upload_string(key, json.dumps(report, default=str, indent=2))


# ── Sync Operations ──────────────────────────────────────────

def sync_sqlite_to_r2(sqlite_path: str, batch_size: int = 1000) -> int:
    """Sync SQLite database to R2.

    Returns number of jobs synced.
    """
    import sqlite3

    if not is_configured():
        print("R2 not configured, skipping sync")
        return 0

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"Syncing {total:,} jobs to R2...")

    # Check what's already in R2
    existing_keys = set(get_all_job_keys_from_r2())
    print(f"Already in R2: {len(existing_keys)} jobs")

    # Get all jobs
    cursor = conn.execute(
        "SELECT * FROM jobs WHERE dedupe_key NOT IN ({})".format(
            ",".join("?" * len(existing_keys)) if existing_keys else "''"
        ),
        list(existing_keys)
    )

    synced = 0
    batch = []

    for row in cursor:
        job = dict(row)
        batch.append(job)

        if len(batch) >= batch_size:
            for j in batch:
                save_job_to_r2(j)
            synced += len(batch)
            print(f"  Synced {synced}/{total}", end="\r")
            batch = []

    # Sync remaining
    if batch:
        for j in batch:
            save_job_to_r2(j)
        synced += len(batch)

    # Save batch index
    if synced > 0:
        save_stats({
            "total_synced": synced + len(existing_keys),
            "new_synced": synced,
            "timestamp": datetime.utcnow().isoformat(),
        })

    conn.close()
    print(f"\nSynced {synced} new jobs to R2")
    return synced


def sync_r2_to_sqlite(sqlite_path: str) -> int:
    """Sync jobs from R2 to local SQLite.

    Returns number of jobs synced.
    """
    import sqlite3

    if not is_configured():
        print("R2 not configured, skipping sync")
        return 0

    conn = sqlite3.connect(sqlite_path)
    cursor = conn.cursor()

    # Get existing keys
    existing = set()
    for row in cursor.execute("SELECT dedupe_key FROM jobs"):
        existing.add(row[0])

    # Get all keys from R2
    r2_keys = get_all_job_keys_from_r2()
    print(f"Found {len(r2_keys)} jobs in R2, {len(existing)} locally")

    new_count = 0
    for i, key in enumerate(r2_keys):
        if key not in existing:
            job = load_job_from_r2(key)
            if job:
                try:
                    cursor.execute(
                        """INSERT OR IGNORE INTO jobs
                           (dedupe_key, title, company, location, url, description,
                            tags, source, source_kind, external_id, salary,
                            posted_at, first_seen_at, last_seen_at, is_active)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (job.get("dedupe_key"), job.get("title"), job.get("company"),
                         job.get("location", ""), job.get("url", ""),
                         job.get("description", ""), job.get("tags", "[]"),
                         job.get("source", ""), job.get("source_kind", ""),
                         job.get("external_id", ""), job.get("salary", ""),
                         job.get("posted_at"), job.get("first_seen_at", ""),
                         job.get("last_seen_at", ""), 1)
                    )
                    new_count += 1
                except Exception as e:
                    pass

        if (i + 1) % 100 == 0:
            print(f"  Processed {i+1}/{len(r2_keys)}", end="\r")

    conn.commit()
    conn.close()

    print(f"\nSynced {new_count} new jobs from R2 to SQLite")
    return new_count


# ── Search ────────────────────────────────────────────────────

def search_jobs_r2(query: str = "", location: str = "",
                   source: str = "", limit: int = 20) -> List[Dict]:
    """Search jobs in R2. Note: R2 doesn't support SQL queries,
    so we load batches and filter in memory. For large datasets,
    use SQLite instead.

    This is for backup/quick access only.
    """
    if not is_configured():
        return []

    # For small searches, load all and filter
    # For production, use SQLite for search
    all_keys = list_keys("jobs/dedupe/")
    results = []

    for key in all_keys[:1000]:  # Limit for performance
        job = download_json(key)
        if not job:
            continue

        match = True
        if query:
            q = query.lower()
            if not any(q in (job.get(f, "") or "").lower()
                       for f in ["title", "company", "description", "tags"]):
                match = False
        if location and location.lower() not in (job.get("location", "") or "").lower():
            match = False
        if source and source != job.get("source", ""):
            match = False

        if match:
            results.append(job)
            if len(results) >= limit:
                break

    return results


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if not is_configured():
        print("R2 not configured!")
        print("Set these environment variables:")
        print("  R2_ACCOUNT_ID=your-cloudflare-account-id")
        print("  R2_ACCESS_KEY_ID=your-access-key-id")
        print("  R2_SECRET_ACCESS_KEY=your-secret-access-key")
        print("  R2_BUCKET_NAME=workora-jobs")
        print("  R2_PUBLIC_URL=https://pub-xxxxx.r2.dev (optional)")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m scripts.r2_storage sync-to-r2    # Sync SQLite to R2")
        print("  python -m scripts.r2_storage sync-from-r2  # Sync R2 to SQLite")
        print("  python -m scripts.r2_storage stats         # Show R2 stats")
        print("  python -m scripts.r2_storage list          # List R2 files")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "sync-to-r2":
        db_path = sys.argv[2] if len(sys.argv) > 2 else "jobs.db"
        sync_sqlite_to_r2(db_path)

    elif cmd == "sync-from-r2":
        db_path = sys.argv[2] if len(sys.argv) > 2 else "jobs.db"
        sync_r2_to_sqlite(db_path)

    elif cmd == "stats":
        stats = load_stats()
        if stats:
            print(json.dumps(stats, indent=2))
        else:
            print("No stats found in R2")

    elif cmd == "list":
        keys = list_keys()
        print(f"Total files: {len(keys)}")
        for k in keys[:20]:
            print(f"  {k}")
        if len(keys) > 20:
            print(f"  ... and {len(keys) - 20} more")
