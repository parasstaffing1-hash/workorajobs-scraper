"""Migrate SQLite jobs.db to PostgreSQL (Supabase).

Usage:
  set DATABASE_URL=postgresql://user:pass@host:5432/dbname
  python -m scripts.migrate_to_postgres

Migrates in batches of 500 rows for memory efficiency.
"""
import os
import sys
import sqlite3
import time

DB_URL = os.environ.get("DATABASE_URL", "")
BATCH = 500

def main():
    if not DB_URL:
        print("ERROR: Set DATABASE_URL env var first")
        print("  set DATABASE_URL=postgresql://user:pass@host:5432/dbname")
        return

    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        print("Installing psycopg2-binary...")
        os.system("pip install psycopg2-binary")
        import psycopg2
        from psycopg2.extras import execute_values

    pg = psycopg2.connect(DB_URL)
    pg.autocommit = False
    pc = pg.cursor()

    # Create tables in PostgreSQL
    pc.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id SERIAL PRIMARY KEY,
        title TEXT,
        company TEXT,
        location TEXT,
        description TEXT,
        salary_min REAL,
        salary_max REAL,
        job_type TEXT,
        url TEXT,
        source TEXT,
        skills TEXT,
        tags TEXT,
        company_url TEXT,
        remote INTEGER DEFAULT 0,
        date_posted TEXT,
        date_scraped TEXT,
        dedupe_key TEXT UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
    CREATE INDEX IF NOT EXISTS idx_jobs_location ON jobs(location);
    CREATE INDEX IF NOT EXISTS idx_jobs_date ON jobs(date_posted);
    CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
    CREATE INDEX IF NOT EXISTS idx_jobs_dedup ON jobs(dedupe_key);
    """)
    pg.commit()

    # Read from SQLite
    sqlite_db = os.path.join(os.path.dirname(__file__), "..", "jobs.db")
    sc = sqlite3.connect(sqlite_db)
    sc.row_factory = sqlite3.Row

    total = sc.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"SQLite has {total:,} jobs. Migrating in batches of {BATCH}...")

    # Get all data
    rows = sc.execute(
        "SELECT title,company,location,description,salary_min,salary_max,"
        "job_type,url,source,skills,tags,company_url,remote,date_posted,"
        "date_scraped,dedupe_key FROM jobs"
    ).fetchall()

    migrated = 0
    skipped = 0
    start = time.time()

    for i in range(0, len(rows), BATCH):
        batch = rows[i:i+BATCH]
        values = []
        for r in batch:
            values.append(tuple(
                None if v == '' else v for v in [r['title'], r['company'], r['location'],
                r['description'], r['salary_min'], r['salary_max'], r['job_type'],
                r['url'], r['source'], r['skills'], r['tags'], r['company_url'],
                r['remote'], r['date_posted'], r['date_scraped'], r['dedupe_key']]
            ))

        try:
            execute_values(pc, """INSERT INTO jobs
                (title,company,location,description,salary_min,salary_max,
                 job_type,url,source,skills,tags,company_url,remote,
                 date_posted,date_scraped,dedupe_key)
                VALUES %s ON CONFLICT (dedupe_key) DO NOTHING""", values)
            pg.commit()
            migrated += len(batch) - 0
        except Exception as e:
            pg.rollback()
            skipped += len(batch)
            print(f"  Batch error: {e}")

        elapsed = time.time() - start
        rate = migrated / max(elapsed, 1) * 60
        pct = (i + len(batch)) / total * 100
        print(f"  {pct:.0f}% - {migrated:,}/{total:,} migrated ({rate:.0f}/min) - {skipped} skipped", end='\r')

    print(f"\nDone! Migrated {migrated:,} jobs to PostgreSQL in {time.time()-start:.0f}s")

    # Create user/alert tables too
    pc.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT DEFAULT '',
        skills TEXT DEFAULT '',
        location_pref TEXT DEFAULT '',
        role TEXT DEFAULT 'user',
        is_active BOOLEAN DEFAULT TRUE,
        email_verified BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW(),
        last_login TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT NOW(),
        expires_at TIMESTAMP NOT NULL
    );

    CREATE TABLE IF NOT EXISTS saved_jobs (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        job_id INTEGER REFERENCES jobs(id),
        job_slug TEXT,
        notes TEXT DEFAULT '',
        priority INTEGER DEFAULT 0,
        tags TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(user_id, job_slug)
    );

    CREATE TABLE IF NOT EXISTS applications (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        job_id INTEGER REFERENCES jobs(id),
        job_slug TEXT,
        job_title TEXT,
        company TEXT,
        status TEXT DEFAULT 'saved',
        notes TEXT DEFAULT '',
        applied_at TIMESTAMP,
        updated_at TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS job_alerts (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        name TEXT DEFAULT '',
        keywords TEXT NOT NULL,
        location TEXT DEFAULT '',
        min_salary REAL,
        frequency TEXT DEFAULT 'daily',
        is_active BOOLEAN DEFAULT TRUE,
        last_sent TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS employers (
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        company_name TEXT NOT NULL,
        company_url TEXT,
        industry TEXT,
        company_size TEXT,
        description TEXT DEFAULT '',
        logo_url TEXT,
        is_verified BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS posted_jobs (
        id SERIAL PRIMARY KEY,
        employer_id INTEGER REFERENCES employers(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        location TEXT DEFAULT '',
        job_type TEXT DEFAULT 'full-time',
        salary_min REAL,
        salary_max REAL,
        skills TEXT DEFAULT '',
        is_remote BOOLEAN DEFAULT FALSE,
        is_active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    );
    """)
    pg.commit()
    print("All tables created.")

    pg.close()
    sc.close()


if __name__ == "__main__":
    main()
