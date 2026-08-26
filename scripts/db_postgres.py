#!/usr/bin/env python3
"""PostgreSQL + Redis Database Layer — Production-ready concurrent access + caching.

Usage:
    export DATABASE_URL=postgresql://user:pass@localhost:5432/leadflow
    export REDIS_URL=redis://localhost:6379/0
    python -m scripts.db_postgres --init
    python -m scripts.db_postgres --migrate
    python -m scripts.db_postgres --stats
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQLITE_DB = ROOT / "jobs.db"
LOG = ROOT / "db_postgres.log"

DATABASE_URL = os.environ.get("DATABASE_URL", "")
REDIS_URL = os.environ.get("REDIS_URL", "")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── PostgreSQL Connection Pool ───────────────────────────────
_pg_pool = None

def get_pg_pool():
    global _pg_pool
    if _pg_pool and not _pg_pool.closed:
        return _pg_pool
    if not DATABASE_URL:
        return None
    try:
        import psycopg2
        from psycopg2 import pool
        _pg_pool = pool.ThreadedConnectionPool(2, 20, DATABASE_URL)
        return _pg_pool
    except Exception as e:
        log(f"PostgreSQL connection failed: {e}")
        return None


def get_pg_conn():
    pool = get_pg_pool()
    if pool:
        return pool.getconn()
    return None


def put_pg_conn(conn):
    pool = get_pg_pool()
    if pool and conn:
        pool.putconn(conn)


def pg_execute(sql, params=None):
    conn = get_pg_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()
        result = cur.fetchall() if cur.description else None
        cur.close()
        return result
    except Exception as e:
        conn.rollback()
        log(f"PG error: {e}")
        return None
    finally:
        put_pg_conn(conn)


def pg_init():
    """Initialize PostgreSQL database schema."""
    conn = get_pg_conn()
    if not conn:
        log("Cannot connect to PostgreSQL")
        return False
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                dedupe_key VARCHAR(64) PRIMARY KEY,
                title VARCHAR(500),
                company VARCHAR(300),
                location VARCHAR(300),
                url TEXT,
                description TEXT,
                tags JSONB DEFAULT '[]',
                source VARCHAR(100),
                source_kind VARCHAR(50),
                external_id VARCHAR(100),
                salary VARCHAR(50),
                posted_at TIMESTAMP,
                first_seen_at TIMESTAMP DEFAULT NOW(),
                last_seen_at TIMESTAMP DEFAULT NOW(),
                is_active BOOLEAN DEFAULT TRUE
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(LOWER(TRIM(company)));
            CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
            CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(is_active);
            CREATE INDEX IF NOT EXISTS idx_jobs_tags ON jobs USING GIN(tags);
        """)
        conn.commit()
        cur.close()
        log("PostgreSQL schema initialized")
        return True
    except Exception as e:
        log(f"PG init error: {e}")
        return False
    finally:
        put_pg_conn(conn)


def make_key(title, company):
    raw = f"{(title or '').lower().strip()}|{(company or '').lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


def migrate_sqlite_to_pg(limit=100000):
    """Migrate data from SQLite to PostgreSQL."""
    if not DATABASE_URL:
        log("DATABASE_URL not set")
        return False

    sqlite_conn = sqlite3.connect(str(SQLITE_DB))
    sqlite_conn.row_factory = sqlite3.Row

    total = sqlite_conn.execute("SELECT COUNT(*) FROM jobs WHERE is_active = 1").fetchone()[0]
    log(f"Migrating {total} jobs from SQLite to PostgreSQL...")

    pg_init()

    offset = 0
    migrated = 0
    batch_size = 1000

    while offset < total:
        rows = sqlite_conn.execute(
            "SELECT * FROM jobs WHERE is_active = 1 LIMIT ? OFFSET ?",
            (batch_size, offset)
        ).fetchall()

        for row in rows:
            key = row["dedupe_key"] or make_key(row["title"], row["company"])
            try:
                pg_execute(
                    """INSERT INTO jobs (dedupe_key, title, company, location, url,
                       description, tags, source, source_kind, external_id,
                       salary, posted_at, first_seen_at, last_seen_at, is_active)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (dedupe_key) DO NOTHING""",
                    (key, row["title"], row["company"], row["location"],
                     row["url"], row["description"], json.dumps(json.loads(row["tags"]) if row["tags"] else []),
                     row["source"], row["source_kind"], row["external_id"],
                     row["salary"], row["posted_at"], row["first_seen_at"],
                     row["last_seen_at"], bool(row["is_active"]))
                )
                migrated += 1
            except Exception as e:
                log(f"Migration error: {e}")

        offset += batch_size
        if migrated % 10000 == 0:
            log(f"  Migrated {migrated}/{total}...")

    sqlite_conn.close()
    log(f"Migration complete: {migrated} jobs migrated")
    return True


# ── Redis Cache ──────────────────────────────────────────────
_redis_client = None

def get_redis():
    global _redis_client
    if _redis_client:
        return _redis_client
    if not REDIS_URL:
        return None
    try:
        import redis
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        _redis_client.ping()
        return _redis_client
    except Exception as e:
        log(f"Redis connection failed: {e}")
        return None


def cache_get(key, default=None):
    r = get_redis()
    if not r:
        return default
    try:
        val = r.get(f"leadflow:{key}")
        if val:
            return json.loads(val)
        return default
    except:
        return default


def cache_set(key, value, ttl=3600):
    r = get_redis()
    if not r:
        return
    try:
        r.setex(f"leadflow:{key}", ttl, json.dumps(value))
    except:
        pass


def cache_delete(key):
    r = get_redis()
    if r:
        try:
            r.delete(f"leadflow:{key}")
        except:
            pass


def cache_dedup_check(dedupe_key):
    """Check if a job is already in the dedup set."""
    r = get_redis()
    if not r:
        return False
    try:
        return r.sismember("leadflow:dedup_set", dedupe_key)
    except:
        return False


def cache_dedup_add(dedupe_key):
    """Add a job to the dedup set."""
    r = get_redis()
    if not r:
        return
    try:
        r.sadd("leadflow:dedup_set", dedupe_key)
    except:
        pass


def get_stats():
    """Get database statistics."""
    stats = {}

    # SQLite stats
    try:
        conn = sqlite3.connect(str(SQLITE_DB))
        stats["sqlite_total"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        stats["sqlite_active"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_active = 1").fetchone()[0]
        stats["sqlite_companies"] = conn.execute(
            "SELECT COUNT(DISTINCT LOWER(TRIM(company))) FROM jobs WHERE company != ''"
        ).fetchone()[0]
        conn.close()
    except:
        stats["sqlite_total"] = 0

    # PostgreSQL stats
    if DATABASE_URL:
        try:
            result = pg_execute("SELECT COUNT(*) FROM jobs")
            if result:
                stats["pg_total"] = result[0][0]
            result = pg_execute("SELECT COUNT(*) FROM jobs WHERE is_active = TRUE")
            if result:
                stats["pg_active"] = result[0][0]
        except:
            stats["pg_total"] = "Not connected"

    # Redis stats
    r = get_redis()
    if r:
        try:
            stats["redis_dedup_count"] = r.scard("leadflow:dedup_set")
            stats["redis_memory"] = r.info("memory").get("used_memory_human", "N/A")
        except:
            stats["redis_status"] = "Not connected"

    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--limit", type=int, default=100000)
    args = parser.parse_args()

    if args.init:
        pg_init()
    elif args.migrate:
        migrate_sqlite_to_pg(args.limit)
    elif args.stats:
        stats = get_stats()
        for k, v in stats.items():
            print(f"  {k}: {v}")
    else:
        print("Use --init, --migrate, or --stats")


if __name__ == "__main__":
    main()
