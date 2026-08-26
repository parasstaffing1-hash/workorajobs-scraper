"""Regression tests for the collect pipeline's source-seen / expiry semantics."""
from datetime import datetime, timedelta, timezone

from jobcollector.models import CompanyConfig, Job, SourceConfig
from jobcollector.pipeline import collect
from jobcollector.storage import Store


def _stale_job(source: str, ext: str) -> Job:
    old = datetime.now(timezone.utc) - timedelta(days=2)
    return Job(
        title="Engineer", company="Acme", url=f"https://x/{ext}",
        external_id=ext, source=source, first_seen_at=old, last_seen_at=old,
    )


def test_careers_crawl_finding_nothing_still_expires_stale(tmp_path, monkeypatch):
    """A careers crawl that returns zero jobs must still count as having checked
    the company, so stale (e.g. junk hub-page) postings get expired."""
    store = Store(tmp_path / "t.db")
    try:
        store.upsert(_stale_job("careers:Acme", "1"))

        class EmptyCrawler:
            def __init__(self, *a, **k):
                pass

            errors: list[str] = []

            def crawl(self, companies):
                return []

        import jobcollector.pipeline as pipeline
        monkeypatch.setattr(pipeline.careers_source, "CareersCrawler", EmptyCrawler)

        config = SourceConfig(companies=[CompanyConfig(name="Acme", careers_url="https://acme.com/careers")])
        report = collect(config, store, sources=("careers",))
        assert report.jobs_seen == 0
        assert report.jobs_expired == 1
        assert store.search(active_only=True, limit=10) == []
    finally:
        store.close()


def test_careers_crawl_with_jobs_keeps_them_active(tmp_path, monkeypatch):
    store = Store(tmp_path / "t.db")
    try:
        store.upsert(_stale_job("careers:Acme", "1"))

        class ReturningCrawler:
            def __init__(self, *a, **k):
                pass

            errors: list[str] = []

            def crawl(self, companies):
                # a freshly-seen job (last_seen_at defaults to now)
                return [Job(title="Engineer", company="Acme", url="https://x/1",
                            external_id="1", source="careers:Acme")]

        import jobcollector.pipeline as pipeline
        monkeypatch.setattr(pipeline.careers_source, "CareersCrawler", ReturningCrawler)

        config = SourceConfig(companies=[CompanyConfig(name="Acme", careers_url="https://acme.com/careers")])
        report = collect(config, store, sources=("careers",))
        assert report.jobs_seen == 1
        assert report.jobs_expired == 0
        assert len(store.search(active_only=True, limit=10)) == 1
    finally:
        store.close()
