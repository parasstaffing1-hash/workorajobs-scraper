"""Tests for the Companies & Keywords watchlist (storage + server API)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from jobcollector.models import Job
from jobcollector.server import make_handler
from jobcollector.storage import Store

from tests.test_reader import _start_server


def _seed_job(store: Store, title: str, company: str, tags: list[str], active: bool = True) -> None:
    now = datetime.now(timezone.utc)
    ext = f"{company}:{title}".lower().replace(" ", "-")
    job = Job(
        title=title, company=company, url=f"https://a.com/{ext}",
        external_id=ext, source="remotive", first_seen_at=now,
        description="We need Python skills", tags=tags,
    )
    store.upsert(job)
    if not active:
        store.conn.execute("UPDATE jobs SET is_active = 0 WHERE dedupe_key = ?", (job.dedupe_key,))
        store.conn.commit()


# ------------------------------------------------------------------ storage

def test_watchlist_add_list_dedupe_delete(tmp_path):
    store = Store(tmp_path / "w.db")
    try:
        item = store.watchlist_add("company", "  Stripe  ")
        assert item["kind"] == "company" and item["value"] == "Stripe"
        # duplicate add returns the same row, does not create a second
        again = store.watchlist_add("company", "Stripe")
        assert again["id"] == item["id"]
        assert len(store.watchlist_all()) == 1

        store.watchlist_add("keyword", "python")
        store.watchlist_add("keyword", "rust")
        rows = store.watchlist_all()
        assert [(r["kind"], r["value"]) for r in rows] == [
            ("company", "Stripe"), ("keyword", "python"), ("keyword", "rust"),
        ]

        assert store.watchlist_delete(item["id"]) is True
        assert store.watchlist_delete(item["id"]) is False
        assert len(store.watchlist_all()) == 2
    finally:
        store.close()


def test_watchlist_add_invalid_raises(tmp_path):
    store = Store(tmp_path / "w.db")
    try:
        with pytest.raises(ValueError):
            store.watchlist_add("company", "   ")
        with pytest.raises(ValueError):
            store.watchlist_add("bogus", "x")
    finally:
        store.close()


def test_count_matches_company_and_keyword(tmp_path):
    store = Store(tmp_path / "c.db")
    try:
        _seed_job(store, "Backend Engineer", "Stripe", ["python"])
        _seed_job(store, "Data Scientist", "Stripe", ["python"])
        _seed_job(store, "Rust Compiler Dev", "Mozilla", ["rust"])
        _seed_job(store, "Python Platform Engineer", "Acme", ["python"], active=False)

        assert store.count_matches("company", "Stripe") == 2
        assert store.count_matches("company", "acme") == 0      # inactive excluded
        assert store.count_matches("company", "Moz") == 1       # substring match
        # description mentions Python on all 4 seeded jobs; inactive excluded -> 3
        assert store.count_matches("keyword", "python") == 3
        assert store.count_matches("keyword", "rust") == 1   # only the tags match
        assert store.count_matches("keyword", "nomatch") == 0
    finally:
        store.close()


def test_watchlist_bulk_add_dedupes_and_counts(tmp_path):
    store = Store(tmp_path / "b.db")
    try:
        store.watchlist_add("company", "Stripe")
        res = store.watchlist_bulk_add("company", ["Stripe", "Mozilla", "  airbnb  ", "", "Airbnb"])
        assert res == {"added": 2, "existing": 1}  # Stripe dup, Airbnb deduped in-input
        rows = [r["value"] for r in store.watchlist_all()]
        assert rows == ["Airbnb", "Mozilla", "Stripe"]

        res2 = store.watchlist_bulk_add("keyword", [])
        assert res2 == {"added": 0, "existing": 0}
    finally:
        store.close()


def test_watchlist_counts_matches_per_item_counts(tmp_path):
    store = Store(tmp_path / "n.db")
    try:
        _seed_job(store, "Backend Engineer", "Stripe", ["python"])
        _seed_job(store, "Data Scientist", "Stripe", ["python"])
        _seed_job(store, "Rust Compiler Dev", "Mozilla", ["rust"])
        _seed_job(store, "Python Platform Engineer", "Acme", ["python"], active=False)
        c1 = store.watchlist_add("company", "Stripe")
        c2 = store.watchlist_add("company", "Mozilla")
        k1 = store.watchlist_add("keyword", "python")

        counts = store.watchlist_counts()
        assert counts == {c1["id"]: 2, c2["id"]: 1, k1["id"]: 3}
        # consistent with per-item semantics
        assert counts[c1["id"]] == store.count_matches("company", "Stripe")
        assert counts[k1["id"]] == store.count_matches("keyword", "python")

        # empty watchlist -> empty dict (no crash)
        empty = Store(tmp_path / "e.db")
        try:
            assert empty.watchlist_counts() == {}
        finally:
            empty.close()
    finally:
        store.close()


# ------------------------------------------------------------ server API


def test_watchlist_bulk_api(tmp_path):
    db = tmp_path / "sb.db"
    httpd, port = _start_server(tmp_path, db)
    try:
        base = f"http://127.0.0.1:{port}"
        r = httpx.post(f"{base}/api/watchlist/bulk",
                       json={"kind": "company", "values": ["Stripe", "Mozilla", "Stripe"]})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["added"] == 2 and r.json()["existing"] == 0

        d = httpx.get(f"{base}/api/watchlist").json()
        assert [i["value"] for i in d["items"]] == ["Mozilla", "Stripe"]

        # invalid requests
        assert httpx.post(f"{base}/api/watchlist/bulk",
                          json={"kind": "nope", "values": ["x"]}).status_code == 400
        assert httpx.post(f"{base}/api/watchlist/bulk",
                          json={"kind": "company", "values": []}).status_code == 400
        assert httpx.post(f"{base}/api/watchlist/bulk",
                          json={"kind": "company", "values": "notalist"}).status_code == 400
    finally:
        httpd.shutdown()


def test_watchlist_import_cli(tmp_path):
    from click.testing import CliRunner

    from jobcollector.cli import cli

    csv_file = tmp_path / "companies.csv"
    csv_file.write_text("rank,company,employees\n1,Stripe,8000\n2,Mozilla,3000\n3,Stripe,8000\n",
                        encoding="utf-8")
    db = tmp_path / "imp.db"

    runner = CliRunner()
    res = runner.invoke(cli, ["watchlist-import", str(csv_file), "--db", str(db)])
    assert res.exit_code == 0, res.output
    assert "Imported 2 companys" in res.output  # Stripe deduped in-input

    store = Store(db)
    try:
        rows = [r["value"] for r in store.watchlist_all()]
        assert rows == ["Mozilla", "Stripe"]
    finally:
        store.close()

    # missing file errors cleanly
    res = runner.invoke(cli, ["watchlist-import", str(tmp_path / "nope.csv"), "--db", str(db)])
    assert res.exit_code != 0


def test_watchlist_api_roundtrip(tmp_path):
    db = tmp_path / "s.db"
    httpd, port = _start_server(tmp_path, db)
    try:
        base = f"http://127.0.0.1:{port}"

        # empty at first
        d = httpx.get(f"{base}/api/watchlist").json()
        assert d["ok"] is True and d["items"] == []

        # add a company + a keyword
        r = httpx.post(f"{base}/api/watchlist", json={"kind": "company", "value": "Stripe"})
        assert r.json()["ok"] is True
        item = r.json()["item"]
        assert item["value"] == "Stripe" and item["count"] == 0

        r = httpx.post(f"{base}/api/watchlist", json={"kind": "keyword", "value": "python"})
        assert r.json()["ok"] is True

        # invalid input rejected
        assert httpx.post(f"{base}/api/watchlist", json={"kind": "nope", "value": "x"}).status_code == 400
        assert httpx.post(f"{base}/api/watchlist", json={"kind": "company", "value": "  "}).status_code == 400

        d = httpx.get(f"{base}/api/watchlist").json()
        kinds = {i["kind"] for i in d["items"]}
        assert kinds == {"company", "keyword"}

        # delete via API
        assert httpx.post(f"{base}/api/watchlist_delete", json={"id": item["id"]}).json()["ok"] is True
        d = httpx.get(f"{base}/api/watchlist").json()
        assert all(i["id"] != item["id"] for i in d["items"])
    finally:
        httpd.shutdown()
