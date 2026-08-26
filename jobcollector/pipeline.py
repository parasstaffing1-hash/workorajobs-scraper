"""Run the daily collection pipeline: fetch sources -> store -> expire -> report."""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import httpx

from .http import make_client
from .models import Job, RunReport, SourceConfig
from .sources import ats as ats_html
from .sources import ats_api
from .sources import boards as boards_source
from .sources import careers as careers_source
from .sources import rss as rss_source
from .storage import Store

# httpx.Client is NOT safe to share across threads on Windows (native OpenSSL
# crash — access violation in ssl.read). Each worker thread gets its own client;
# the caller's client is used only as a template for headers/timeouts.
_pipeline_local = threading.local()


def _thread_client(template: httpx.Client) -> httpx.Client:
    c = getattr(_pipeline_local, "client", None)
    if c is None:
        c = make_client(
            timeout=30.0,
            headers=dict(template.headers or {}),
        )
        _pipeline_local.client = c
    return c

SOURCE_GROUPS = {
    "board": "public job-board APIs",
    "ats": "Greenhouse / Lever career boards",
    "rss": "RSS feeds",
    "careers": "company career-page crawler",
}

# (kind, config list) pairs, in display order. Every kind is dispatched through
# ats_api with its slugs; each slug becomes its own source entry.
_ATS_KINDS = [
    ("greenhouse", "greenhouse"),
    ("ashby", "ashby"),
    ("bamboohr", "bamboohr"),
    ("lever", "lever"),
    ("workday", "workday"),
    ("tcs", "tcs"),
    ("smartrecruiters", "smartrecruiters"),
    ("workable", "workable"),
    ("breezy", "breezy"),
    ("teamtailor", "teamtailor"),
    ("hirehive", "hirehive"),
    ("recruitee", "recruitee"),
    ("rise", "rise"),
    ("adzuna", "adzuna"),
    ("usajobs", "usajobs"),
    ("jobvite", "jobvite"),
    ("icims", "icims"),
    ("yc", "yc"),
    ("linkedin", "linkedin"),
    ("themuse", "themuse"),
    ("workingnomads", "workingnomads"),
    ("reed", "reed"),
    ("rippling", "rippling"),
    ("personio", "personio"),
    ("jazzhr", "jazzhr"),
    ("paylocity", "paylocity"),
    ("freshteam", "freshteam"),
    ("fountain", "fountain"),
    ("deel", "deel"),
    ("phenom", "phenom"),
    ("jobscore", "jobscore"),
    ("talentlyft", "talentlyft"),
    ("crelate", "crelate"),
    ("recruiterflow", "recruiterflow"),
    ("ismartrecruit", "ismartrecruit"),
]


def collect(
    config: SourceConfig,
    store: Store,
    sources: tuple[str, ...] = ("board", "ats", "rss", "careers"),
    limit_per_source: int = 200,
    concurrency: int = 8,
    use_js: bool = False,
) -> RunReport:
    started = datetime.now(timezone.utc)
    report = RunReport(started_at=started)
    seen_sources: set[str] = set()

    def ats_fetch(kind: str, slug: str) -> list[Job]:
        try:
            return ats_api.fetch_ats_api(_thread_client(client), kind, slug, limit_per_source, api_keys=config.api_keys)
        except httpx.HTTPError:
            # Some companies expose only HTML boards (e.g. old Greenhouse
            # tenants without the boards API); fall back to the scraper.
            if kind == "greenhouse":
                return ats_html.fetch_ats(_thread_client(client), "greenhouse", slug, limit_per_source)
            raise

    with make_client() as client:
        tasks: list[tuple[str, Callable[[], list[Job]]]] = []

        if "board" in sources:
            report.groups_run.append("board")
            for name in config.boards:
                tasks.append((f"board:{name}", lambda n=name: boards_source.fetch_board(_thread_client(client), n, limit_per_source)))

        if "ats" in sources:
            report.groups_run.append("ats")
            for kind, attr in _ATS_KINDS:
                for slug in getattr(config, attr):
                    tasks.append((f"{kind}:{slug}", lambda k=kind, s=slug: ats_fetch(k, s)))

        if "rss" in sources:
            report.groups_run.append("rss")
            for feed in config.rss_feeds:
                name = feed.get("name") or feed.get("url")
                tasks.append((f"rss:{name}", lambda f=feed: rss_source.fetch_feed(_thread_client(client), f, limit_per_source)))

        crawler = None
        if "careers" in sources and config.companies:
            report.groups_run.append("careers")
            crawler = careers_source.CareersCrawler(client, concurrency=concurrency, use_js=use_js)
            tasks.append(("careers", lambda: crawler.crawl(config.companies)))

        # Fetch every source concurrently (httpx.Client is thread-safe); the
        # single SQLite connection is only touched on this thread afterwards.
        results: list[tuple[str, list[Job], str | None]] = []
        if concurrency > 1 and len(tasks) > 1:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {pool.submit(fn): label for label, fn in tasks}
                for fut in as_completed(futures):
                    label = futures[fut]
                    try:
                        results.append((label, fut.result(), None))
                    except Exception as exc:
                        results.append((label, [], str(exc)))
        else:
            for label, fn in tasks:
                try:
                    results.append((label, fn(), None))
                except Exception as exc:
                    results.append((label, [], str(exc)))

        for label, jobs, err in results:
            if err is not None:
                report.errors.append(f"{label}: {err}")
                continue
            report.sources.append(label)
            # Batched upsert: one transaction per source instead of one per job
            # (a ~100x write-speed win at 100k-row scale; see `jobcollect stress`).
            report.jobs_new += store.upsert_many(jobs)
            report.jobs_seen += len(jobs)
            seen_sources.update(j.source for j in jobs)

        if crawler is not None:
            report.errors.extend(f"careers: {e}" for e in crawler.errors)
            # A crawl that finds nothing still counts as having checked the
            # company: stale postings (e.g. hub pages filtered as junk) must be
            # eligible for expiry even when zero jobs were upserted.
            for company in config.companies:
                seen_sources.add(f"careers:{company.name}")

    report.jobs_expired = store.expire_older_than(started, seen_sources)
    report.finished_at = datetime.now(timezone.utc)
    store.record_run(started, report.jobs_seen, report.jobs_new, report.jobs_expired)
    return report
