"""Collect jobs from RSS/Atom feeds.

Many company career sites (GitLab, Django, We Work Remotely, ...) publish job
feeds. We fetch the feed over HTTP ourselves and parse with feedparser so the
source stays testable and shares the retry/polite-UA machinery.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from ..http import retry_get
from ..models import Job

_MAX_FEED_ITEMS = 200


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                return None
    return None


def fetch_feed(client: httpx.Client, feed: dict, limit: int = _MAX_FEED_ITEMS) -> list[Job]:
    url = feed["url"]
    resp = retry_get(client, url)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    if parsed.get("bozo") and not parsed.get("entries"):
        raise ValueError(f"Unparseable feed {url}: {parsed.get('bozo_exception')}")
    feed_name = feed.get("name") or parsed.feed.get("title") or urlparse(url).netloc
    default_company = feed.get("company") or feed_name
    jobs: list[Job] = []
    for entry in parsed.entries[:limit]:
        title = (entry.get("title") or "").strip()
        link = entry.get("link") or ""
        if not title or not link:
            continue
        summary = BeautifulSoup(entry.get("summary") or "", "lxml").get_text(" ", strip=True)
        tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]
        jobs.append(
            Job(
                title=title,
                company=entry.get("author") or default_company,
                url=link,
                description=summary[:4000],
                tags=[t for t in tags if t],
                source=f"rss:{feed_name}",
                source_kind="rss",
                external_id=entry.get("id") or entry.get("guid") or link,
                posted_at=_entry_time(entry),
            )
        )
    return jobs
