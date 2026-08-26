"""SQL analytics over the jobs and items tables via duckdb.

DuckDB attaches the SQLite database read-only, so you can run arbitrary SQL
across both tables (``j.jobs``, ``j.items``) with all of DuckDB's SQL power —
window functions, aggregates, JSON, and more — without touching the data.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

REPORTS = {
    "jobs_by_source": "SELECT source, COUNT(*) AS total, SUM(is_active) AS active FROM j.jobs GROUP BY source ORDER BY total DESC",
    "jobs_by_company": "SELECT company, COUNT(*) AS total, SUM(is_active) AS active FROM j.jobs GROUP BY company ORDER BY total DESC LIMIT 20",
    "items_by_category": "SELECT category, COUNT(*) AS n FROM j.items GROUP BY category ORDER BY n DESC",
    "items_by_source": "SELECT source, COUNT(*) AS n FROM j.items GROUP BY source ORDER BY n DESC",
    "latest_jobs": "SELECT title, company, location, source FROM j.jobs WHERE is_active = 1 ORDER BY COALESCE(posted_at, first_seen_at) DESC LIMIT 15",
    "latest_items": "SELECT title, category, source FROM j.items ORDER BY COALESCE(published_at, first_seen_at) DESC LIMIT 15",
}


def _attach(con: duckdb.DuckDBPyConnection, db_path: str) -> None:
    path = str(Path(db_path).resolve()).replace("\\", "/")
    try:
        con.execute(f"ATTACH '{path}' AS j (TYPE sqlite, READ_ONLY)")
    except duckdb.Error:
        con.execute("INSTALL sqlite; LOAD sqlite;")
        con.execute(f"ATTACH '{path}' AS j (TYPE sqlite, READ_ONLY)")


def run_analysis(db_path: str, sql: str | None = None, report: str = "jobs_by_source", limit: int | None = None) -> tuple[str, list[str], list[tuple]]:
    """Run a report (or a custom SQL string) over the database.

    Returns (report_name, columns, rows).
    """
    if report not in REPORTS:
        raise KeyError(f"Unknown report {report!r}. Available: {', '.join(REPORTS)}")
    query = sql or REPORTS[report]
    name = report if not sql else "custom"
    if limit and "LIMIT" not in query.upper():
        query = query.rstrip(";").rstrip() + f" LIMIT {limit}"
    con = duckdb.connect()
    try:
        _attach(con, db_path)
        result = con.execute(query).fetchall()
        cols = [d[0] for d in con.description]
        return name, cols, result
    finally:
        con.close()
