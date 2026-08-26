from datetime import datetime, timedelta, timezone

from jobcollector.analyze import run_analysis
from jobcollector.dedupe import find_duplicate_clusters
from jobcollector.models import Job
from jobcollector.notify import build_digest, run_notify
from jobcollector.report import render_dashboard
from jobcollector.storage import Store


def _seed(store: Store) -> None:
    now = datetime.now(timezone.utc)
    store.upsert(Job(
        title="Backend Engineer", company="Acme", url="https://a.com/1",
        external_id="1", source="remotive", first_seen_at=now,
    ))
    item = {
        "source": "rss:Hacker News",
        "category": "news",
        "title": "New Python release",
        "url": "https://example.com/py",
        "summary": "It's faster.",
        "content": "",
        "author": "Guido",
        "tags": ["python"],
        "raw": {},
        "published_at": "2024-05-01T10:00:00+00:00",
    }
    store.upsert_item(item)


def test_digest_includes_jobs_and_items(tmp_path):
    store = Store(tmp_path / "t.db")
    try:
        _seed(store)
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        msg = build_digest(store, since, top=5)
        assert "**Jobs**" in msg
        assert "Backend Engineer" in msg
        assert "**Engine items**" in msg
        assert "New Python release" in msg
        # nothing newer than the cutoff -> empty
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        msg2 = build_digest(store, future, top=5)
        assert "new: 0" in msg2
    finally:
        store.close()


def test_run_notify_dry_run_does_not_update_state(tmp_path):
    store = Store(tmp_path / "t.db")
    try:
        _seed(store)
        message, errors = run_notify(store, [], dry_run=True)
        assert errors == []
        assert "Backend Engineer" in message
        assert store.get_notify_state("last_notify") == ""
    finally:
        store.close()


def test_dedupe_finds_cross_source_duplicates(tmp_path):
    store = Store(tmp_path / "d.db")
    try:
        store.upsert(Job(title="Senior Rust Engineer", company="Ferris Co", url="https://x/1",
                         external_id="1", source="remotive"))
        store.upsert(Job(title="Senior Rust Engineer", company="Ferris Co", url="https://x/2",
                         external_id="2", source="remoteok"))
        store.upsert(Job(title="Data Scientist", company="Other", url="https://x/3",
                         external_id="3", source="jobicy"))
        clusters = find_duplicate_clusters(store, threshold=85)
        assert len(clusters) == 1
        assert len(clusters[0]) == 2
        sources = {r["source"] for r in clusters[0]}
        assert sources == {"remotive", "remoteok"}
    finally:
        store.close()


def test_analyze_reports_over_sqlite(tmp_path):
    db = tmp_path / "a.db"
    store = Store(db)
    try:
        _seed(store)
    finally:
        store.close()
    name, cols, rows = run_analysis(str(db), report="jobs_by_source")
    assert name == "jobs_by_source"
    assert ("remotive", 1, 1) in rows
    # custom SQL
    name2, cols2, rows2 = run_analysis(str(db), sql="SELECT COUNT(*) AS n FROM j.items")
    assert rows2 == [(1,)]
    assert cols2 == ["n"]


def test_dashboard_watchlist_payload(tmp_path):
    db = tmp_path / "w.db"
    store = Store(db)
    try:
        _seed(store)
        store.watchlist_add("company", "Acme")
        store.watchlist_add("keyword", "python")
        out = tmp_path / "dash.html"
        render_dashboard(store, out, limit=50)
        html = out.read_text(encoding="utf-8")
        assert '"watchlist": [{"id": 1, "kind": "company", "value": "Acme", "count": 1}' in html
        # the seeded job has no python text anywhere -> keyword count is 0
        assert '"kind": "keyword", "value": "python", "count": 0}' in html
    finally:
        store.close()


def test_dashboard_includes_items(tmp_path):
    db = tmp_path / "r.db"
    store = Store(db)
    try:
        _seed(store)
        out = tmp_path / "dash.html"
        n = render_dashboard(store, out, limit=50)
        html = out.read_text(encoding="utf-8")
        assert n == 1
        assert 'id="nav"' in html          # Notion-style sidebar
        assert "Engine Items" in html
        assert "renderBoard" in html        # board view
        assert '"category": "news"' in html
        assert "renderCompanies" in html    # companies & keywords page
        assert "🏢" in html                  # nav item
    finally:
        store.close()
