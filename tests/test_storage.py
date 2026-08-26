from datetime import datetime, timedelta, timezone

import pytest

from jobcollector.models import Job
from jobcollector.storage import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def _job(title="Engineer", source="remotive", ext="1"):
    return Job(title=title, company="Acme", url=f"https://x/{ext}", external_id=ext, source=source)


def test_upsert_new_then_refresh(store):
    assert store.upsert(_job()) is True
    assert store.upsert(_job()) is False  # same key -> refresh, not duplicate
    rows = store.search(limit=10)
    assert len(rows) == 1


def test_expiry_only_touches_seen_sources(store):
    store.upsert(_job(ext="1", source="remotive"))
    store.upsert(_job(ext="2", source="rss:GitLab"))
    cutoff = datetime.now(timezone.utc) + timedelta(seconds=1)
    expired = store.expire_older_than(cutoff, sources=["remotive"])
    assert expired == 1
    rows = store.search(active_only=True, limit=10)
    assert len(rows) == 1
    assert rows[0]["source"] == "rss:GitLab"


def test_search_filters(store):
    store.upsert(_job(title="Backend Engineer", ext="1"))
    store.upsert(_job(title="Data Scientist", ext="2"))
    hits = store.search("backend")
    assert len(hits) == 1
    assert hits[0]["title"] == "Backend Engineer"


def test_stats_counts(store):
    store.upsert(_job(ext="1"))
    store.upsert(_job(title="Scientist", ext="2"))
    stats = store.stats()
    assert stats["total"] == 2
    assert stats["active"] == 2
