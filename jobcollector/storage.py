"""SQLite storage with daily-refresh dedupe and active/expired tracking.

Semantics
---------
* Every job has a stable ``dedupe_key`` (provider id when available, otherwise a
  hash of title/company/location/url + source). Re-collecting the same job
  refreshes ``last_seen_at`` instead of inserting a duplicate.
* After each run, jobs that belong to sources seen in that run but were NOT
  seen again are marked ``is_active = 0`` (they disappeared from the board,
  i.e. are likely filled/expired). Jobs from sources skipped in a run keep
  their previous state.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  dedupe_key     TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  company        TEXT NOT NULL,
  location       TEXT NOT NULL DEFAULT '',
  url            TEXT NOT NULL DEFAULT '',
  description    TEXT NOT NULL DEFAULT '',
  tags           TEXT NOT NULL DEFAULT '[]',
  source         TEXT NOT NULL,
  source_kind    TEXT NOT NULL DEFAULT '',
  external_id    TEXT NOT NULL DEFAULT '',
  salary         TEXT NOT NULL DEFAULT '',
  posted_at      TEXT,
  first_seen_at  TEXT NOT NULL,
  last_seen_at   TEXT NOT NULL,
  is_active      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_jobs_source  ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_active  ON jobs(is_active);
-- Covering index so GROUP BY source with SUM(is_active) never touches the
-- table body (527ms -> 10ms at 100k rows; used by stats() and the dashboard).
CREATE INDEX IF NOT EXISTS idx_jobs_source_active ON jobs(source, is_active);

CREATE TABLE IF NOT EXISTS runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at   TEXT NOT NULL,
  finished_at  TEXT NOT NULL DEFAULT '',
  jobs_seen    INTEGER NOT NULL DEFAULT 0,
  jobs_new     INTEGER NOT NULL DEFAULT 0,
  jobs_expired INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS notify_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- General-purpose collection engine: RSS items, scraped pages, anything.
-- Dedupe on (source, url): re-fetching refreshes instead of duplicating.
CREATE TABLE IF NOT EXISTS items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source        TEXT NOT NULL,
  category      TEXT NOT NULL DEFAULT '',
  title         TEXT NOT NULL,
  url           TEXT NOT NULL,
  summary       TEXT NOT NULL DEFAULT '',
  content       TEXT NOT NULL DEFAULT '',
  author        TEXT NOT NULL DEFAULT '',
  tags          TEXT NOT NULL DEFAULT '[]',
  raw           TEXT NOT NULL DEFAULT '{}',
  published_at  TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  read          INTEGER NOT NULL DEFAULT 0,
  starred       INTEGER NOT NULL DEFAULT 0,
  UNIQUE(source, url)
);
CREATE INDEX IF NOT EXISTS idx_items_source  ON items(source);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);

-- Reader metadata per feed: health + subscription info for the RSS reader.
CREATE TABLE IF NOT EXISTS feeds (
  source          TEXT PRIMARY KEY,
  url             TEXT NOT NULL,
  name            TEXT NOT NULL DEFAULT '',
  category        TEXT NOT NULL DEFAULT '',
  added_at        TEXT NOT NULL DEFAULT '',
  last_fetched_at TEXT NOT NULL DEFAULT '',
  last_error      TEXT NOT NULL DEFAULT '',
  item_count      INTEGER NOT NULL DEFAULT 0
);

-- User-managed watchlist: companies and keywords tracked in the dashboard.
CREATE TABLE IF NOT EXISTS watchlist (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,             -- 'company' | 'keyword'
  value      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(kind, value)
);
"""


def _dt(v: datetime) -> str:
    return v.astimezone(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after the original schema (idempotent)."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(items)")}
        if "read" not in cols:
            self.conn.execute("ALTER TABLE items ADD COLUMN read INTEGER NOT NULL DEFAULT 0")
        if "starred" not in cols:
            self.conn.execute("ALTER TABLE items ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")
        if "idx_items_read" not in {
            r["name"] for r in self.conn.execute("PRAGMA index_list(items)")
        }:
            self.conn.execute("CREATE INDEX idx_items_read ON items(read)")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert(self, job: Job) -> bool:
        """Insert a new job or refresh an existing one. Returns True if new."""
        is_new = self.conn.execute(
            "SELECT 1 FROM jobs WHERE dedupe_key = ?", (job.dedupe_key,)
        ).fetchone() is None
        self.conn.execute(
            """
            INSERT INTO jobs (dedupe_key, title, company, location, url, description, tags,
                              source, source_kind, external_id, salary, posted_at,
                              first_seen_at, last_seen_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(dedupe_key) DO UPDATE SET
              title = excluded.title,
              company = excluded.company,
              location = excluded.location,
              url = excluded.url,
              description = excluded.description,
              tags = excluded.tags,
              posted_at = excluded.posted_at,
              salary = excluded.salary,
              last_seen_at = excluded.last_seen_at,
              is_active = 1
            """,
            (
                job.dedupe_key,
                job.title,
                job.company,
                job.location,
                job.url,
                job.description,
                json.dumps(job.tags),
                job.source,
                job.source_kind,
                job.external_id,
                job.salary,
                job.posted_at.isoformat() if job.posted_at else None,
                _dt(job.first_seen_at),
                _dt(job.last_seen_at),
            ),
        )
        self.conn.commit()
        return is_new

    def upsert_many(self, jobs: Iterable[Job]) -> int:
        """Bulk-insert/refresh many jobs in one transaction. Returns count of new.

        ~100x faster than calling :meth:`upsert` per row (one SELECT + one
        executemany + one commit instead of a commit per job). Same semantics:
        existing rows are refreshed and re-activated.
        """
        batch = list(jobs)
        if not batch:
            return 0
        keys = [j.dedupe_key for j in batch]
        # SQLite's bind-variable limit is far below 100k, so probe existence in
        # chunks (900 vars per chunk stays well under the default limit).
        existing: set[str] = set()
        for i in range(0, len(keys), 900):
            chunk = keys[i:i + 900]
            placeholders = ",".join("?" for _ in chunk)
            existing.update(
                r[0]
                for r in self.conn.execute(
                    f"SELECT dedupe_key FROM jobs WHERE dedupe_key IN ({placeholders})", chunk
                )
            )
        rows = [
            (
                j.dedupe_key,
                j.title,
                j.company,
                j.location,
                j.url,
                j.description,
                json.dumps(j.tags),
                j.source,
                j.source_kind,
                j.external_id,
                j.salary,
                j.posted_at.isoformat() if j.posted_at else None,
                _dt(j.first_seen_at),
                _dt(j.last_seen_at),
            )
            for j in batch
        ]
        # Chunked executemany — a single huge batch crashes Windows SQLite with
        # a native access violation (observed at ~10k+ rows on Python 3.13).
        _SQL = """
            INSERT INTO jobs (dedupe_key, title, company, location, url, description, tags,
                              source, source_kind, external_id, salary, posted_at,
                              first_seen_at, last_seen_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(dedupe_key) DO UPDATE SET
              title = excluded.title,
              company = excluded.company,
              location = excluded.location,
              url = excluded.url,
              description = excluded.description,
              tags = excluded.tags,
              posted_at = excluded.posted_at,
              salary = excluded.salary,
              last_seen_at = excluded.last_seen_at,
              is_active = 1
        """
        for i in range(0, len(rows), 500):
            self.conn.executemany(_SQL, rows[i:i + 500])
        self.conn.commit()
        return sum(k not in existing for k in keys)

    def expire_older_than(self, cutoff: datetime, sources: Iterable[str]) -> int:
        """Deactivate jobs from the given sources not seen since `cutoff`."""
        srcs = list(sources)
        if not srcs:
            return 0
        placeholders = ",".join("?" for _ in srcs)
        cur = self.conn.execute(
            f"UPDATE jobs SET is_active = 0 WHERE is_active = 1 "
            f"AND source IN ({placeholders}) AND last_seen_at < ?",
            [*srcs, _dt(cutoff)],
        )
        self.conn.commit()
        return cur.rowcount

    def record_run(self, started_at: datetime, seen: int, new: int, expired: int) -> None:
        self.conn.execute(
            "INSERT INTO runs (started_at, finished_at, jobs_seen, jobs_new, jobs_expired) "
            "VALUES (?, ?, ?, ?, ?)",
            (_dt(started_at), _dt(datetime.now(timezone.utc)), seen, new, expired),
        )
        self.conn.commit()

    # --------------------------------------------------------------- notify state

    def get_notify_state(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM notify_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_notify_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO notify_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ------------------------------------------------------------ items engine

    def upsert_item(self, item: dict) -> bool:
        """Insert a generic engine item or refresh an existing one. True if new."""
        is_new = self.conn.execute(
            "SELECT 1 FROM items WHERE source = ? AND url = ?",
            (item["source"], item["url"]),
        ).fetchone() is None
        now = _dt(datetime.now(timezone.utc))
        self.conn.execute(
            """
            INSERT INTO items (source, category, title, url, summary, content, author,
                              tags, raw, published_at, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, url) DO UPDATE SET
              category = excluded.category,
              title = excluded.title,
              summary = excluded.summary,
              content = excluded.content,
              author = excluded.author,
              tags = excluded.tags,
              raw = excluded.raw,
              published_at = excluded.published_at,
              last_seen_at = excluded.last_seen_at
            """,
            (
                item["source"],
                item.get("category", ""),
                item["title"],
                item["url"],
                item.get("summary", ""),
                item.get("content", ""),
                item.get("author", ""),
                json.dumps(item.get("tags", [])),
                json.dumps(item.get("raw", {}), ensure_ascii=False),
                item.get("published_at"),
                now,
                now,
            ),
        )
        self.conn.commit()
        return is_new

    def search_items(
        self,
        query: str = "",
        category: str = "",
        source: str = "",
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        sql = "SELECT * FROM items WHERE 1 = 1"
        params: list = []
        if query:
            sql += " AND (title LIKE ? OR summary LIKE ? OR content LIKE ? OR author LIKE ?)"
            like = f"%{query}%"
            params += [like, like, like, like]
        if category:
            sql += " AND category = ?"
            params.append(category)
        if source:
            sql += " AND source LIKE ?"
            params.append(f"%{source}%")
        if unread_only:
            sql += " AND read = 0"
        sql += " ORDER BY COALESCE(published_at, first_seen_at) DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- reader state

    def mark_read(self, source: str, url: str, read: bool = True) -> None:
        self.conn.execute(
            "UPDATE items SET read = ? WHERE source = ? AND url = ?",
            (1 if read else 0, source, url),
        )
        self.conn.commit()

    def mark_starred(self, source: str, url: str, starred: bool = True) -> None:
        self.conn.execute(
            "UPDATE items SET starred = ? WHERE source = ? AND url = ?",
            (1 if starred else 0, source, url),
        )
        self.conn.commit()

    def mark_source_read(self, source: str = "") -> int:
        """Mark all unread items read; optionally restricted to one source."""
        if source:
            cur = self.conn.execute("UPDATE items SET read = 1 WHERE read = 0 AND source = ?", (source,))
        else:
            cur = self.conn.execute("UPDATE items SET read = 1 WHERE read = 0")
        self.conn.commit()
        return cur.rowcount

    def unread_totals(self) -> dict[str, int]:
        return {
            r["source"]: r["n"]
            for r in self.conn.execute("SELECT source, COUNT(*) AS n FROM items WHERE read = 0 GROUP BY source")
        }

    def unread_total(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM items WHERE read = 0").fetchone()["n"]

    def reader_items(self, limit: int = 1000) -> list[dict]:
        return self.search_items(limit=limit)

    def store_fulltext(self, source: str, url: str, content: str) -> bool:
        cur = self.conn.execute(
            "UPDATE items SET content = ? WHERE source = ? AND url = ?", (content, source, url)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---------------------------------------------------------- feed metadata

    def record_feed_fetch(self, source: str, url: str, name: str, category: str,
                          item_count: int, error: str = "") -> None:
        now = _dt(datetime.now(timezone.utc))
        self.conn.execute(
            """INSERT INTO feeds (source, url, name, category, added_at, last_fetched_at, last_error, item_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET
                 url = excluded.url,
                 name = excluded.name,
                 category = excluded.category,
                 last_fetched_at = excluded.last_fetched_at,
                 last_error = excluded.last_error,
                 item_count = excluded.item_count""",
            (source, url, name, category, now, now, error, item_count),
        )
        self.conn.commit()

    def feed_meta(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM feeds ORDER BY name COLLATE NOCASE")
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ watchlist

    def watchlist_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM watchlist ORDER BY kind, value COLLATE NOCASE"
        )
        return [dict(r) for r in rows]

    def watchlist_add(self, kind: str, value: str) -> dict:
        value = value.strip()
        if not value or kind not in ("company", "keyword"):
            raise ValueError(f"Invalid watchlist entry: kind={kind!r} value={value!r}")
        self.conn.execute(
            "INSERT INTO watchlist (kind, value, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(kind, value) DO NOTHING",
            (kind, value, _dt(datetime.now(timezone.utc))),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM watchlist WHERE kind = ? AND value = ?", (kind, value)
        ).fetchone()
        return dict(row)

    def watchlist_delete(self, wid: int) -> bool:
        cur = self.conn.execute("DELETE FROM watchlist WHERE id = ?", (wid,))
        self.conn.commit()
        return cur.rowcount > 0

    def watchlist_bulk_add(self, kind: str, values: list[str]) -> dict:
        """Insert many watchlist entries in a single transaction.

        Returns {"added": n_new, "existing": n_dups}.
        """
        seen = {v for v in values if v}
        now = _dt(datetime.now(timezone.utc))
        added = 0
        with self.conn:
            for v in sorted(seen):
                cur = self.conn.execute(
                    "INSERT INTO watchlist (kind, value, created_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(kind, value) DO NOTHING",
                    (kind, v, now),
                )
                added += cur.rowcount
        return {"added": added, "existing": len(seen) - added}

    def watchlist_counts(self) -> dict[int, int]:
        """Active-job match counts for every watchlist row, in one pass.

        Avoids one LIKE query per row (matters at 1000+ watchlist entries):
        loads active jobs once and matches in memory.
        """
        items = self.watchlist_all()
        counts: dict[int, int] = {w["id"]: 0 for w in items}
        if not items:
            return counts
        jobs = self.conn.execute(
            "SELECT company, title, description, tags FROM jobs WHERE is_active = 1"
        ).fetchall()
        companies = [(j["company"] or "").lower() for j in jobs]
        keywords = [
            " ".join((j["title"] or "", j["company"] or "",
                      j["description"] or "", j["tags"] or "")).lower()
            for j in jobs
        ]
        for w in items:
            v = w["value"].lower()
            hay = companies if w["kind"] == "company" else keywords
            counts[w["id"]] = sum(1 for h in hay if v in h)
        return counts

    def count_matches(self, kind: str, value: str) -> int:
        """Active jobs matching a watchlist entry (company name or keyword)."""
        like = f"%{value}%"
        if kind == "company":
            n = self.conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE is_active = 1 AND company LIKE ?",
                (like,),
            ).fetchone()["n"]
        else:
            n = self.conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE is_active = 1 AND "
                "(title LIKE ? OR company LIKE ? OR description LIKE ? OR tags LIKE ?)",
                (like, like, like, like),
            ).fetchone()["n"]
        return n

    def items_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
        by_source = {
            r["source"]: r["n"]
            for r in self.conn.execute(
                "SELECT source, COUNT(*) AS n FROM items GROUP BY source ORDER BY n DESC"
            )
        }
        by_category = {
            r["category"] or "(none)": r["n"]
            for r in self.conn.execute(
                "SELECT category, COUNT(*) AS n FROM items GROUP BY category ORDER BY n DESC"
            )
        }
        return {"total": total, "by_source": by_source, "by_category": by_category}

    # ------------------------------------------------------------------ queries

    def search(
        self,
        query: str = "",
        location: str = "",
        source: str = "",
        active_only: bool = True,
        limit: int = 50,
        since: datetime | None = None,
        offset: int = 0,
    ) -> list[dict]:
        sql = "SELECT * FROM jobs WHERE 1 = 1"
        params: list = []
        if query:
            sql += " AND (title LIKE ? OR company LIKE ? OR description LIKE ? OR tags LIKE ?)"
            like = f"%{query}%"
            params += [like, like, like, like]
        if location:
            sql += " AND location LIKE ?"
            params.append(f"%{location}%")
        if source:
            sql += " AND source = ?"
            params.append(source)
        if active_only:
            sql += " AND is_active = 1"
        if since:
            sql += " AND posted_at >= ?"
            params.append(_dt(since))
        sql += " ORDER BY COALESCE(posted_at, first_seen_at) DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        active = self.conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE is_active = 1"
        ).fetchone()["n"]
        by_source = {
            r["source"]: {"active": r["active"], "total": r["total"]}
            for r in self.conn.execute(
                "SELECT source, SUM(is_active) AS active, COUNT(*) AS total "
                "FROM jobs GROUP BY source ORDER BY total DESC"
            )
        }
        return {"total": total, "active": active, "by_source": by_source}

    def export(self, out_path: str | Path, fmt: str, query: str = "", active_only: bool = True) -> int:
        rows = self.search(query=query, active_only=active_only, limit=100000)
        path = Path(out_path)
        if fmt == "jsonl":
            with path.open("w", encoding="utf-8") as fh:
                for r in rows:
                    r["tags"] = json.loads(r["tags"])
                    fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        else:  # csv
            if not rows:
                path.write_text("", encoding="utf-8")
            else:
                cols = [c for c in rows[0]]
                with path.open("w", encoding="utf-8", newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
                    writer.writeheader()
                    for r in rows:
                        r["tags"] = "|".join(json.loads(r["tags"]))
                        writer.writerow(r)
        return len(rows)
