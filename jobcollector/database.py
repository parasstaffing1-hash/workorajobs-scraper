"""Production database layer — supports PostgreSQL (primary) and SQLite (fallback).

Usage:
    from jobcollector.database import get_db, DB_URL
    
    db = get_db()
    jobs = db.fetch_jobs(keyword="python", location="Remote", limit=100)
    db.insert_jobs([...])
    db.close()
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# ── Configuration ──────────────────────────────────────────────
DB_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{ROOT / 'jobs.db'}"
)
USE_POSTGRES = DB_URL.startswith("postgresql")

# ── PostgreSQL implementation ──────────────────────────────────
_pg_pool = None
_pg_lock = threading.Lock()


def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        import psycopg_pool
        _pg_pool = psycopg_pool.ConnectionPool(
            DB_URL,
            min_size=2,
            max_size=20,
            kwargs={"autocommit": False},
        )
    return _pg_pool


def _pg_init_schema():
    """Create tables if they don't exist."""
    pool = _get_pg_pool()
    with pool.connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id SERIAL PRIMARY KEY,
                dedupe_key VARCHAR(64) UNIQUE NOT NULL,
                title TEXT NOT NULL,
                company TEXT DEFAULT '',
                location TEXT DEFAULT '',
                description TEXT DEFAULT '',
                url TEXT NOT NULL,
                source TEXT DEFAULT '',
                source_kind TEXT DEFAULT '',
                external_id TEXT DEFAULT '',
                posted_at TEXT DEFAULT NULL,
                salary TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                first_seen_at TEXT DEFAULT NULL,
                last_seen_at TEXT DEFAULT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(dedupe_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id SERIAL PRIMARY KEY,
                key VARCHAR(64) UNIQUE NOT NULL,
                name TEXT DEFAULT '',
                tier TEXT DEFAULT 'free',
                rate_limit INT DEFAULT 60,
                created_at TEXT DEFAULT NULL,
                last_used_at TEXT DEFAULT NULL,
                active BOOLEAN DEFAULT TRUE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id SERIAL PRIMARY KEY,
                started_at TEXT DEFAULT NULL,
                ended_at TEXT DEFAULT NULL,
                total_new INT DEFAULT 0,
                total_scraped INT DEFAULT 0,
                source TEXT DEFAULT '',
                status TEXT DEFAULT 'running'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS proxy_config (
                id SERIAL PRIMARY KEY,
                proxy_url TEXT NOT NULL,
                proxy_type TEXT DEFAULT 'http',
                active BOOLEAN DEFAULT TRUE,
                last_used_at TEXT DEFAULT NULL,
                fail_count INT DEFAULT 0,
                avg_latency_ms INT DEFAULT 0
            )
        """)
        conn.commit()
    return True


class PostgresDB:
    """Async-capable PostgreSQL database interface."""

    def __init__(self):
        self.pool = _get_pg_pool()
        _pg_init_schema()

    def _hash(self, url: str, title: str, company: str) -> str:
        raw = f"{url.strip().lower()}|{title.strip().lower()}|{company.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def insert_jobs(self, jobs: list[dict]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        new = 0
        with self.pool.connection() as conn:
            for j in jobs:
                url = j.get("url", "").strip()
                title = j.get("title", "").strip()
                company = j.get("company", "").strip()
                if not url or not title:
                    continue
                key = self._hash(url, title, company)
                try:
                    conn.execute(
                        """INSERT INTO jobs
                           (dedupe_key, title, company, location, description, url,
                            source, source_kind, external_id, posted_at, salary, tags,
                            first_seen_at, last_seen_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (dedupe_key) DO UPDATE SET last_seen_at = %s""",
                        (key, title, company, j.get("location", ""),
                         j.get("desc", "")[:500], url, j.get("source", ""),
                         j.get("source_kind", ""), j.get("id", ""),
                         j.get("posted"), j.get("salary", ""),
                         j.get("tags", ""), now, now, now)
                    )
                    new += 1
                except Exception:
                    pass
            conn.commit()
        return new

    def count(self) -> int:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
            return row[0] if row else 0

    def count_fresh(self, days: int = 7) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE first_seen_at >= %s", (cutoff,)
            ).fetchone()
            return row[0] if row else 0

    def fetch_jobs(self, keyword: str = None, company: str = None,
                   location: str = None, source: str = None,
                   source_kind: str = None, days: int = 7,
                   limit: int = 100, offset: int = 0,
                   sort: str = "newest") -> tuple[list[dict], int]:
        conditions = ["1=1"]
        params: list[Any] = []

        if keyword:
            conditions.append(
                "(title ILIKE %s OR company ILIKE %s OR description ILIKE %s)"
            )
            like = f"%{keyword}%"
            params.extend([like, like, like])
        if company:
            conditions.append("LOWER(company) LIKE %s")
            params.append(f"%{company.lower()}%")
        if location:
            conditions.append("LOWER(location) LIKE %s")
            params.append(f"%{location.lower()}%")
        if source:
            conditions.append("source = %s")
            params.append(source)
        if source_kind:
            conditions.append("source_kind = %s")
            params.append(source_kind)
        if days and days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            conditions.append("first_seen_at >= %s")
            params.append(cutoff)

        where = " AND ".join(conditions)
        order = "first_seen_at DESC" if sort == "newest" else "first_seen_at ASC"

        with self.pool.connection() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM jobs WHERE {where}", params
            ).fetchone()[0]

            rows = conn.execute(
                f"""SELECT title, company, location, url, source, source_kind,
                           posted_at, salary, description, first_seen_at
                    FROM jobs WHERE {where}
                    ORDER BY {order} LIMIT %s OFFSET %s""",
                params + [limit, offset]
            ).fetchall()

        jobs = [
            {
                "title": r[0], "company": r[1], "location": r[2],
                "url": r[3], "source": r[4], "source_kind": r[5],
                "posted_at": r[6], "salary": r[7],
                "description": (r[8] or "")[:300], "first_seen_at": r[9],
            }
            for r in rows
        ]
        return jobs, total

    def get_stats(self) -> dict:
        with self.pool.connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

            source_kinds = {}
            for row in conn.execute(
                "SELECT source_kind, COUNT(*) FROM jobs GROUP BY source_kind ORDER BY COUNT(*) DESC"
            ):
                source_kinds[row[0] or "unknown"] = row[1]

            top_sources = {}
            for row in conn.execute(
                "SELECT source, COUNT(*) FROM jobs GROUP BY source ORDER BY COUNT(*) DESC LIMIT 20"
            ):
                top_sources[row[0]] = row[1]

            companies = conn.execute(
                "SELECT COUNT(DISTINCT LOWER(TRIM(company))) FROM jobs WHERE company != ''"
            ).fetchone()[0]

            urls = conn.execute("SELECT COUNT(DISTINCT url) FROM jobs").fetchone()[0]

            daily = {}
            for row in conn.execute("""
                SELECT date(first_seen_at) as day, COUNT(*) as cnt
                FROM jobs WHERE first_seen_at >= date(now() - interval '7 days')
                GROUP BY day ORDER BY day
            """):
                daily[str(row[0])] = row[1]

            return {
                "total_jobs": total,
                "unique_companies": companies,
                "unique_urls": urls,
                "source_kinds": source_kinds,
                "top_sources": top_sources,
                "daily_last_7d": daily,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def get_sources(self) -> list[dict]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT source, source_kind, COUNT(*) FROM jobs GROUP BY source ORDER BY COUNT(*) DESC"
            ).fetchall()
            return [{"source": r[0], "kind": r[1], "count": r[2]} for r in rows]

    def close(self):
        if _pg_pool:
            _pg_pool.close()


# ── SQLite fallback ────────────────────────────────────────────
class SQLiteDB:
    """SQLite fallback for local development."""

    def __init__(self, db_path: str = None):
        self.path = db_path or str(ROOT / "jobs.db")
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.lock = threading.Lock()

    def _hash(self, url, title, company):
        raw = f"{url.strip().lower()}|{title.strip().lower()}|{company.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def insert_jobs(self, jobs):
        new = 0
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            for j in jobs:
                url = j.get("url", "").strip()
                title = j.get("title", "").strip()
                company = j.get("company", "").strip()
                if not url or not title:
                    continue
                key = self._hash(url, title, company)
                try:
                    cur = self.conn.execute(
                        "INSERT OR IGNORE INTO jobs "
                        "(dedupe_key,title,company,location,description,url,"
                        "source,source_kind,external_id,posted_at,salary,tags,"
                        "first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (key, title, company, j.get("location", ""),
                         j.get("desc", "")[:500], url, j.get("source", ""),
                         j.get("source_kind", ""), j.get("id", ""),
                         j.get("posted"), j.get("salary", ""),
                         j.get("tags", ""), now, now))
                    if cur.rowcount > 0:
                        new += 1
                except Exception:
                    pass
            if new > 0:
                self.conn.commit()
        return new

    def count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def count_fresh(self, days=7):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE first_seen_at >= ?", (cutoff,)
            ).fetchone()[0]

    def fetch_jobs(self, keyword=None, company=None, location=None,
                   source=None, source_kind=None, days=7,
                   limit=100, offset=0, sort="newest"):
        conditions = ["1=1"]
        params = []

        if keyword:
            conditions.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like, like])
        if company:
            conditions.append("LOWER(company) LIKE ?")
            params.append(f"%{company.lower()}%")
        if location:
            conditions.append("LOWER(location) LIKE ?")
            params.append(f"%{location.lower()}%")
        if source:
            conditions.append("source = ?")
            params.append(source)
        if source_kind:
            conditions.append("source_kind = ?")
            params.append(source_kind)
        if days and days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            conditions.append("first_seen_at >= ?")
            params.append(cutoff)

        where = " AND ".join(conditions)
        order = "first_seen_at DESC" if sort == "newest" else "first_seen_at ASC"

        with self.lock:
            total = self.conn.execute(
                f"SELECT COUNT(*) FROM jobs WHERE {where}", params
            ).fetchone()[0]

            rows = self.conn.execute(
                f"""SELECT title, company, location, url, source, source_kind,
                           posted_at, salary, description, first_seen_at
                    FROM jobs WHERE {where}
                    ORDER BY {order} LIMIT ? OFFSET ?""",
                params + [limit, offset]
            ).fetchall()

        jobs = [
            {"title": r[0], "company": r[1], "location": r[2],
             "url": r[3], "source": r[4], "source_kind": r[5],
             "posted_at": r[6], "salary": r[7],
             "description": (r[8] or "")[:300], "first_seen_at": r[9]}
            for r in rows
        ]
        return jobs, total

    def get_stats(self):
        with self.lock:
            total = self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

            source_kinds = {}
            for row in self.conn.execute(
                "SELECT source_kind, COUNT(*) FROM jobs GROUP BY source_kind ORDER BY COUNT(*) DESC"
            ):
                source_kinds[row[0] or "unknown"] = row[1]

            top_sources = {}
            for row in self.conn.execute(
                "SELECT source, COUNT(*) FROM jobs GROUP BY source ORDER BY COUNT(*) DESC LIMIT 20"
            ):
                top_sources[row[0]] = row[1]

            companies = self.conn.execute(
                "SELECT COUNT(DISTINCT LOWER(TRIM(company))) FROM jobs WHERE company != ''"
            ).fetchone()[0]
            urls = self.conn.execute("SELECT COUNT(DISTINCT url) FROM jobs").fetchone()[0]

            daily = {}
            for row in self.conn.execute("""
                SELECT date(first_seen_at) as day, COUNT(*) as cnt
                FROM jobs WHERE first_seen_at >= date('now', '-7 days')
                GROUP BY day ORDER BY day
            """):
                daily[row[0]] = row[1]

            return {
                "total_jobs": total,
                "unique_companies": companies,
                "unique_urls": urls,
                "source_kinds": source_kinds,
                "top_sources": top_sources,
                "daily_last_7d": daily,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def get_sources(self):
        with self.lock:
            rows = self.conn.execute(
                "SELECT source, source_kind, COUNT(*) FROM jobs GROUP BY source ORDER BY COUNT(*) DESC"
            ).fetchall()
            return [{"source": r[0], "kind": r[1], "count": r[2]} for r in rows]

    def close(self):
        self.conn.close()


# ── Factory ────────────────────────────────────────────────────
_db_instance = None
_db_lock = threading.Lock()


def get_db():
    global _db_instance
    if _db_instance is not None:
        return _db_instance
    with _db_lock:
        if _db_instance is not None:
            return _db_instance
        if USE_POSTGRES:
            _db_instance = PostgresDB()
            print(f"[DB] Connected to PostgreSQL")
        else:
            _db_instance = SQLiteDB()
            print(f"[DB] Using SQLite fallback: {DB_URL}")
        return _db_instance
