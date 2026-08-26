"""Tests for the stress-test tooling: batched ingest, offset paging, generators."""
import csv
from pathlib import Path
from datetime import datetime, timedelta, timezone

from jobcollector.models import Job
from jobcollector.storage import Store
from jobcollector.stress import generate_jobs, run_stress, _write_sheets_csv


def _job(i: int, source: str = "stress:greenhouse:acme") -> Job:
    return Job(
        title=f"Engineer {i}", company="Acme", url=f"https://x/{i}",
        external_id=f"{i}", source=source, tags=["python"],
    )


def test_upsert_many_counts_new_and_refreshes(tmp_path):
    store = Store(tmp_path / "b.db")
    try:
        jobs = [_job(i) for i in range(500)]
        assert store.upsert_many(jobs) == 500
        assert store.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 500
        # re-upsert refreshes, adds nothing new
        assert store.upsert_many(jobs) == 0
        assert store.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 500
        # mixed batch: 100 new + 500 existing
        mixed = jobs + [_job(i, source="stress:ashby:acme") for i in range(100, 200)]
        assert store.upsert_many(mixed) == 100
        # existing rows stay active after refresh
        assert store.conn.execute("SELECT COUNT(*) FROM jobs WHERE is_active = 1").fetchone()[0] == 600
    finally:
        store.close()


def test_search_offset_paging_covers_all(tmp_path):
    store = Store(tmp_path / "p.db")
    try:
        store.upsert_many([_job(i) for i in range(250)])
        seen: set[str] = set()
        for off in range(0, 250, 100):
            rows = store.search(limit=100, offset=off)
            assert len(rows) == min(100, 250 - off)
            seen.update(r["url"] for r in rows)
        assert len(seen) == 250
        # beyond the end returns nothing
        assert store.search(limit=100, offset=999) == []
    finally:
        store.close()


def test_generate_jobs_all_posted_last_24h_deterministic():
    now = datetime(2026, 8, 16, 9, 0, tzinfo=timezone.utc)
    a = generate_jobs(200, now, seed=7)
    b = generate_jobs(200, now, seed=7)
    assert [j.dedupe_key for j in a] == [j.dedupe_key for j in b]
    assert len({j.dedupe_key for j in a}) == 200  # fully unique
    lo, hi = now - timedelta(days=1), now + timedelta(seconds=1)
    assert all(lo <= j.posted_at <= hi for j in a)
    assert all(j.title and j.company and j.url for j in a)
    assert all(j.source.startswith("stress:") for j in a)


def test_sheets_csv_contract(tmp_path):
    store = Store(tmp_path / "s.db")
    try:
        store.upsert_many([_job(i) for i in range(5)])
        out = tmp_path / "sheet.csv"
        n = _write_sheets_csv(store, out, rows=5)
        assert n == 5
        with out.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames == ["title", "company", "location", "salary", "source",
                                         "posted_at", "url", "tags", "is_active"]
            rows = list(reader)
        assert len(rows) == 5
        assert rows[0]["tags"] == "python"
        assert rows[0]["is_active"] == "1"
    finally:
        store.close()


def test_mini_stress_run_end_to_end(tmp_path):
    """A tiny full run: every phase executes and the verdict is computable."""
    results = run_stress(
        rows=600, db_path=tmp_path / "mini.db", out_csv=tmp_path / "mini.csv",
        results_json=tmp_path / "mini.json", seed=3, per_row_sample=20,
        api_workers=4, api_rounds=3,
    )
    m = results["metrics"]
    assert m["rows"] == 600
    assert m["integrity"]["row_count"] is True
    assert m["integrity"]["unique_keys"] is True
    assert m["integrity"]["posted_last_24h"] is True
    assert m["integrity"]["api_paging_full"] is True
    assert m["integrity"]["sheets_row_count"] is True
    assert set(results["passed"]) >= {"integrity", "bulk_rate", "search_p95", "api_p95"}
    assert Path(tmp_path / "mini.json").exists()
