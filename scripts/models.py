"""Workora Jobs — Database models for users, saved jobs, applications, alerts, sessions, employers."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "jobs.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _get_pg_connection():
    """Get PostgreSQL connection from DATABASE_URL."""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        conn.autocommit = False
        return conn
    except ImportError:
        raise ImportError("Install psycopg2: pip install psycopg2-binary")


@contextmanager
def get_db():
    """Context manager for database connections. Uses PostgreSQL if DATABASE_URL is set."""
    if DATABASE_URL:
        conn = _get_pg_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db():
    """Create all tables if they don't exist."""
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT DEFAULT '',
            skills TEXT DEFAULT '',
            location_pref TEXT DEFAULT '',
            role TEXT DEFAULT 'user',
            is_active INTEGER DEFAULT 1,
            email_verified INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            last_login TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS saved_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            job_dedupe_key TEXT NOT NULL,
            notes TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            priority INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, job_dedupe_key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS job_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            job_dedupe_key TEXT NOT NULL,
            status TEXT DEFAULT 'saved',
            applied_at TEXT,
            interview_at TEXT,
            notes TEXT DEFAULT '',
            resume_version TEXT DEFAULT '',
            cover_letter TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, job_dedupe_key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS job_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT 'Job Alert',
            keywords TEXT DEFAULT '',
            locations TEXT DEFAULT '',
            sources TEXT DEFAULT '',
            min_salary INTEGER,
            frequency TEXT DEFAULT 'daily',
            is_active INTEGER DEFAULT 1,
            last_sent TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS employer_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            company_name TEXT NOT NULL,
            company_url TEXT DEFAULT '',
            industry TEXT DEFAULT '',
            company_size TEXT DEFAULT '',
            description TEXT DEFAULT '',
            logo_url TEXT DEFAULT '',
            headquarters TEXT DEFAULT '',
            is_verified INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS posted_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employer_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            location TEXT DEFAULT '',
            job_type TEXT DEFAULT 'full-time',
            salary_min INTEGER,
            salary_max INTEGER,
            skills_required TEXT DEFAULT '',
            remote_ok INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            application_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT,
            FOREIGN KEY (employer_id) REFERENCES employer_profiles(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS contact_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            subject TEXT DEFAULT '',
            message TEXT NOT NULL,
            read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            details TEXT DEFAULT '',
            ip_address TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
        CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_saved_jobs_user ON saved_jobs(user_id);
        CREATE INDEX IF NOT EXISTS idx_saved_jobs_key ON saved_jobs(job_dedupe_key);
        CREATE INDEX IF NOT EXISTS idx_applications_user ON job_applications(user_id);
        CREATE INDEX IF NOT EXISTS idx_applications_status ON job_applications(status);
        CREATE INDEX IF NOT EXISTS idx_alerts_user ON job_applications(user_id);
        CREATE INDEX IF NOT EXISTS idx_posted_jobs_employer ON posted_jobs(employer_id);
        CREATE INDEX IF NOT EXISTS idx_posted_jobs_active ON posted_jobs(is_active);
        CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id);
        """)


# ── Auth helpers ──────────────────────────────────────────────

def hash_password(password: str, salt: str = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(password: str, stored: str) -> bool:
    salt, h = stored.split(":", 1)
    return hashlib.sha256((salt + password).encode()).hexdigest() == h


def create_user(email: str, username: str, password: str, full_name: str = "",
                role: str = "user") -> Optional[dict]:
    with get_db() as db:
        try:
            pw_hash = hash_password(password)
            cur = db.execute(
                "INSERT INTO users (email, username, password_hash, full_name, role) "
                "VALUES (?, ?, ?, ?, ?)",
                (email.lower().strip(), username.lower().strip(), pw_hash, full_name, role)
            )
            return {"id": cur.lastrowid, "email": email, "username": username, "role": role}
        except sqlite3.IntegrityError:
            return None


def authenticate(email: str, password: str) -> Optional[dict]:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM users WHERE email = ? AND is_active = 1",
            (email.lower().strip(),)
        ).fetchone()
        if row and verify_password(password, row["password_hash"]):
            db.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (row["id"],))
            return dict(row)
    return None


def create_session(user_id: int, days: int = 30) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc).replace(
        hour=23, minute=59, second=59
    )
    # Simple expiry - days from now
    from datetime import timedelta
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    expires_str = expires.isoformat()
    with get_db() as db:
        db.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_str)
        )
    return token


def get_user_from_token(token: str) -> Optional[dict]:
    with get_db() as db:
        row = db.execute(
            "SELECT u.* FROM users u JOIN sessions s ON u.id = s.user_id "
            "WHERE s.token = ? AND s.expires_at > datetime('now')",
            (token,)
        ).fetchone()
        return dict(row) if row else None


def delete_session(token: str):
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))


# ── Saved Jobs ────────────────────────────────────────────────

def save_job(user_id: int, job_dedupe_key: str, notes: str = "", tags: str = "") -> bool:
    with get_db() as db:
        try:
            db.execute(
                "INSERT OR IGNORE INTO saved_jobs (user_id, job_dedupe_key, notes, tags) "
                "VALUES (?, ?, ?, ?)",
                (user_id, job_dedupe_key, notes, tags)
            )
            return True
        except Exception:
            return False


def unsave_job(user_id: int, job_dedupe_key: str) -> bool:
    with get_db() as db:
        db.execute(
            "DELETE FROM saved_jobs WHERE user_id = ? AND job_dedupe_key = ?",
            (user_id, job_dedupe_key)
        )
        return True


def is_job_saved(user_id: int, job_dedupe_key: str) -> bool:
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM saved_jobs WHERE user_id = ? AND job_dedupe_key = ?",
            (user_id, job_dedupe_key)
        ).fetchone()
        return row is not None


def get_saved_jobs(user_id: int, limit: int = 50, offset: int = 0):
    with get_db() as db:
        return db.execute(
            """SELECT sj.*, j.title, j.company, j.location, j.url, j.salary,
                      j.source, j.tags as job_tags
               FROM saved_jobs sj
               LEFT JOIN jobs j ON j.dedupe_key = sj.job_dedupe_key
               WHERE sj.user_id = ?
               ORDER BY sj.created_at DESC LIMIT ? OFFSET ?""",
            (user_id, limit, offset)
        ).fetchall()


def get_saved_count(user_id: int) -> int:
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM saved_jobs WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        return row[0] if row else 0


# ── Applications ──────────────────────────────────────────────

def create_application(user_id: int, job_dedupe_key: str) -> bool:
    with get_db() as db:
        try:
            # Get job info
            job = db.execute(
                "SELECT title, company FROM jobs WHERE dedupe_key = ?",
                (job_dedupe_key,)
            ).fetchone()
            title = job["title"] if job else ""
            company = job["company"] if job else ""
            db.execute(
                """INSERT OR REPLACE INTO job_applications
                   (user_id, job_dedupe_key, job_title, company, status)
                   VALUES (?, ?, ?, ?, 'applied')""",
                (user_id, job_dedupe_key, title, company)
            )
            return True
        except Exception:
            return False


def update_application(user_id: int, job_dedupe_key: str, status: str, notes: str = "") -> bool:
    with get_db() as db:
        db.execute(
            """UPDATE job_applications SET status = ?, notes = ?,
               updated_at = datetime('now')
               WHERE user_id = ? AND job_dedupe_key = ?""",
            (status, notes, user_id, job_dedupe_key)
        )
        return True


def get_applications(user_id: int, status: str = None):
    with get_db() as db:
        if status:
            return db.execute(
                """SELECT * FROM job_applications
                   WHERE user_id = ? AND status = ?
                   ORDER BY updated_at DESC""",
                (user_id, status)
            ).fetchall()
        return db.execute(
            """SELECT * FROM job_applications
               WHERE user_id = ?
               ORDER BY updated_at DESC""",
            (user_id,)
        ).fetchall()


def get_application_stats(user_id: int):
    with get_db() as db:
        rows = db.execute(
            """SELECT status, COUNT(*) as cnt
               FROM job_applications WHERE user_id = ?
               GROUP BY status""",
            (user_id,)
        ).fetchall()
        return {row["status"]: row["cnt"] for row in rows}


# ── Alerts ────────────────────────────────────────────────────

def create_alert(user_id: int, name: str, keywords: str, locations: str = "",
                 sources: str = "", min_salary: int = 0, frequency: str = "daily") -> int:
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO job_alerts
               (user_id, name, keywords, locations, sources, min_salary, frequency)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, name, keywords, locations, sources, min_salary, frequency)
        )
        return cur.lastrowid


def get_user_alerts(user_id: int):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM job_alerts WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()


def delete_alert(user_id: int, alert_id: int) -> bool:
    with get_db() as db:
        db.execute(
            "DELETE FROM job_alerts WHERE user_id = ? AND id = ?",
            (user_id, alert_id)
        )
        return True


def get_active_alerts():
    with get_db() as db:
        return db.execute(
            "SELECT * FROM job_alerts WHERE is_active = 1"
        ).fetchall()


def match_alert_to_jobs(alert):
    """Find jobs matching an alert's criteria."""
    keywords = alert["keywords"] or ""
    locations = alert["locations"] or ""
    min_salary = alert.get("min_salary") or 0

    conditions = ["is_active = 1"]
    params = []

    if keywords:
        kw_conditions = []
        for kw in keywords.split(","):
            kw = kw.strip()
            if kw:
                kw_conditions.append("(title LIKE ? OR description LIKE ? OR company LIKE ? OR tags LIKE ?)")
                like = f"%{kw}%"
                params.extend([like, like, like, like])
        if kw_conditions:
            conditions.append(f"({' OR '.join(kw_conditions)})")

    if locations:
        loc_conditions = []
        for loc in locations.split(","):
            loc = loc.strip()
            if loc:
                loc_conditions.append("location LIKE ?")
                params.append(f"%{loc}%")
        if loc_conditions:
            conditions.append(f"({' OR '.join(loc_conditions)})")

    if min_salary:
        # salary is stored as TEXT, try to extract numeric
        conditions.append("CAST(REPLACE(REPLACE(salary, '$', ''), ',', '') AS REAL) >= ?")
        params.append(min_salary)

    where = " AND ".join(conditions)

    with get_db() as db:
        return db.execute(
            f"SELECT * FROM jobs WHERE {where} ORDER BY posted_at DESC LIMIT 100",
            params
        ).fetchall()


# ── Dashboard Stats ───────────────────────────────────────────

def get_dashboard_stats():
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        companies = db.execute(
            "SELECT COUNT(DISTINCT LOWER(company)) FROM jobs"
        ).fetchone()[0]
        jobs_today = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE date(first_seen_at) = date('now')"
        ).fetchone()[0]
        jobs_this_week = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE first_seen_at > datetime('now', '-7 days')"
        ).fetchone()[0]

        # Sources breakdown
        sources = []
        for row in db.execute(
            "SELECT source, COUNT(*) as cnt FROM jobs GROUP BY source ORDER BY cnt DESC LIMIT 20"
        ):
            sources.append({"source": row[0], "count": row[1]})

        # Top skills from tags
        skills = []
        try:
            for row in db.execute(
                "SELECT tags FROM jobs WHERE tags IS NOT NULL AND tags != ''"
            ).fetchall():
                try:
                    tags = json.loads(row[0]) if row[0] else []
                    if isinstance(tags, list):
                        for t in tags:
                            if t and t != "[]":
                                skills.append(t)
                except (json.JSONDecodeError, TypeError):
                    pass
            from collections import Counter
            skill_counts = Counter(skills).most_common(20)
        except Exception:
            skill_counts = []

        # Top locations
        locations = []
        for row in db.execute(
            "SELECT location, COUNT(*) as cnt FROM jobs "
            "WHERE location != '' AND location IS NOT NULL "
            "GROUP BY location ORDER BY cnt DESC LIMIT 20"
        ):
            locations.append({"location": row[0], "count": row[1]})

        # User stats
        try:
            total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total_saved = db.execute("SELECT COUNT(*) FROM saved_jobs").fetchone()[0]
            total_alerts = db.execute("SELECT COUNT(*) FROM job_alerts").fetchone()[0]
            total_applications = db.execute("SELECT COUNT(*) FROM job_applications").fetchone()[0]
        except Exception:
            total_users = total_saved = total_alerts = total_applications = 0

        return {
            "total_jobs": total,
            "total_companies": companies,
            "jobs_today": jobs_today,
            "jobs_this_week": jobs_this_week,
            "sources": sources,
            "top_skills": [{"skill": s, "count": c} for s, c in skill_counts],
            "top_locations": locations,
            "total_users": total_users,
            "total_saved": total_saved,
            "total_alerts": total_alerts,
            "total_applications": total_applications,
        }


# ── Employer ──────────────────────────────────────────────────

def get_employer(user_id: int):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM employer_profiles WHERE user_id = ?",
            (user_id,)
        ).fetchone()


def create_employer(user_id: int, company_name: str, company_url: str = "",
                    industry: str = "", company_size: str = "",
                    description: str = "", headquarters: str = "") -> int:
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO employer_profiles
               (user_id, company_name, company_url, industry, company_size, description, headquarters)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, company_name, company_url, industry, company_size, description, headquarters)
        )
        return cur.lastrowid


def search_employers(query: str = "", limit: int = 50):
    with get_db() as db:
        if query:
            like = f"%{query}%"
            return db.execute(
                """SELECT * FROM employer_profiles
                   WHERE company_name LIKE ? OR industry LIKE ?
                   ORDER BY company_name LIMIT ?""",
                (like, like, limit)
            ).fetchall()
        return db.execute(
            "SELECT * FROM employer_profiles ORDER BY company_name LIMIT ?",
            (limit,)
        ).fetchall()


def submit_contact(name: str, email: str, subject: str, message: str):
    with get_db() as db:
        db.execute(
            "INSERT INTO contact_submissions (name, email, subject, message) VALUES (?, ?, ?, ?)",
            (name, email, subject, message)
        )


def get_jobs_search(query: str = "", location: str = "", company: str = "",
                    source: str = "", freshness: str = "", page: int = 1,
                    per_page: int = 20):
    """Search jobs with filters. Returns (jobs, total_count)."""
    conditions = ["1=1"]
    params = []

    if query:
        for q in query.split(","):
            q = q.strip()
            if q:
                conditions.append("(title LIKE ? OR description LIKE ? OR company LIKE ? OR tags LIKE ?)")
                like = f"%{q}%"
                params.extend([like, like, like, like])

    if location:
        for loc in location.split(","):
            loc = loc.strip()
            if loc:
                conditions.append("location LIKE ?")
                params.append(f"%{loc}%")

    if company:
        conditions.append("LOWER(company) LIKE LOWER(?)")
        params.append(f"%{company}%")

    if source:
        conditions.append("source LIKE ?")
        params.append(f"%{source}%")

    if freshness:
        if freshness == "1h":
            conditions.append("first_seen_at > datetime('now', '-1 hours')")
        elif freshness == "24h":
            conditions.append("first_seen_at > datetime('now', '-1 day')")
        elif freshness == "7d":
            conditions.append("first_seen_at > datetime('now', '-7 days')")
        elif freshness == "30d":
            conditions.append("first_seen_at > datetime('now', '-30 days')")

    where = " AND ".join(conditions)
    offset = (page - 1) * per_page

    with get_db() as db:
        total = db.execute(f"SELECT COUNT(*) FROM jobs WHERE {where}", params).fetchone()[0]
        jobs = db.execute(
            f"SELECT * FROM jobs WHERE {where} ORDER BY posted_at DESC, first_seen_at DESC LIMIT ? OFFSET ?",
            params + [per_page, offset]
        ).fetchall()
        return jobs, total


# Initialize on import
init_db()
