"""Migrate SQLite jobs.db to PostgreSQL for production scaling.

Usage:
    export DATABASE_URL=postgresql://user:pass@localhost:5432/workora
    python -m scripts.db_migrate
"""
from __future__ import annotations
import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "jobs.db"
BATCH_SIZE = 1000

def migrate_sqlite_to_postgres():
    """Migrate all tables from SQLite to PostgreSQL."""
    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        print("Installing psycopg2-binary...")
        import subprocess
        subprocess.run(["pip", "install", "psycopg2-binary"], check=True)
        import psycopg2
        from psycopg2.extras import execute_values

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: Set DATABASE_URL environment variable")
        print("  export DATABASE_URL=postgresql://user:pass@localhost:5432/workora")
        return

    print(f"Connecting to PostgreSQL: {db_url[:30]}...")
    pg = psycopg2.connect(db_url)
    pg.autocommit = False
    cur = pg.cursor()

    # Create tables
    cur.execute("DROP TABLE IF EXISTS jobs CASCADE")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            dedupe_key TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT,
            description TEXT,
            tags TEXT,
            source TEXT,
            source_kind TEXT,
            external_id TEXT,
            salary TEXT,
            posted_at TEXT,
            first_seen_at TEXT,
            last_seen_at TEXT,
            is_active INTEGER DEFAULT 1
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_location ON jobs(location)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_posted ON jobs(posted_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(is_active)")
    print("Created indexes...")

    # Migrate from SQLite
    print(f"Reading from SQLite: {DB_PATH}")
    sqlite = sqlite3.connect(str(DB_PATH))
    sqlite.row_factory = sqlite3.Row
    total = sqlite.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"Total rows to migrate: {total:,}")

    cursor = sqlite.execute("SELECT * FROM jobs")
    count = 0
    batch = []
    start = time.time()

    for row in cursor:
        batch.append((
            row["dedupe_key"], row["title"], row["company"], row["location"],
            row["url"], row["description"], row["tags"], row["source"],
            row["source_kind"], row["external_id"], row["salary"],
            row["posted_at"], row["first_seen_at"], row["last_seen_at"],
            row["is_active"] or 1
        ))

        if len(batch) >= BATCH_SIZE:
            execute_values(cur, """
                INSERT INTO jobs (dedupe_key, title, company, location, url,
                    description, tags, source, source_kind, external_id,
                    salary, posted_at, first_seen_at, last_seen_at, is_active)
                VALUES %s ON CONFLICT (dedupe_key) DO NOTHING
            """, batch, page_size=BATCH_SIZE)
            pg.commit()
            count += len(batch)
            elapsed = time.time() - start
            rate = count / elapsed if elapsed > 0 else 0
            pct = (count / total) * 100 if total > 0 else 100
            print(f"  Migrated {count:,}/{total:,} ({pct:.1f}%) at {rate:.0f} rows/sec")
            batch = []

    if batch:
        execute_values(cur, """
            INSERT INTO jobs (dedupe_key, title, company, location, url,
                description, tags, source, source_kind, external_id,
                salary, posted_at, first_seen_at, last_seen_at, is_active)
            VALUES %s ON CONFLICT (dedupe_key) DO NOTHING
        """, batch, page_size=BATCH_SIZE)
        pg.commit()
        count += len(batch)

    sqlite.close()
    elapsed = time.time() - start
    print(f"\nMigration complete: {count:,} rows in {elapsed:.1f}s ({count/elapsed:.0f} rows/sec)")
    print(f"PostgreSQL database ready at: {db_url[:30]}...")

    pg.close()


if __name__ == "__main__":
    migrate_sqlite_to_postgres()
