"""General-purpose RSS/Atom ingestion for the items engine.

Unlike ``sources.rss`` (which maps feeds to job postings), this module ingests
*any* feed — news, blogs, podcasts, changelogs — into the generic ``items``
table, with categories and tags, for use by the rest of the engine.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from .http import retry_get

_MAX_ITEMS = 500
_MAX_CONTENT = 50_000

# Common feed paths probed when a page doesn't advertise a feed link.
PROBE_PATHS = ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml", "/feeds", "/feed/")


def discover_feeds(client: httpx.Client, url: str, probe: bool = True) -> list[dict]:
    """Find RSS/Atom feeds for a website URL.

    Looks for ``<link rel=alternate type=application/rss+xml>`` first, then
    probes well-known feed paths (e.g. /feed, /rss.xml) and validates any hit
    with feedparser. Returns ``[{title, url, type}]``.
    """
    resp = retry_get(client, url)
    resp.raise_for_status()
    base = str(resp.url).rstrip("/")
    soup = BeautifulSoup(resp.text, "lxml")
    found: list[dict] = []
    seen: set[str] = set()

    for link in soup.find_all("link", href=True):
        rel = link.get("rel") or []
        if isinstance(rel, str):
            rel = rel.split()
        if "alternate" not in [r.lower() for r in rel]:
            continue
        typ = (link.get("type") or "").lower()
        if not ("rss" in typ or "atom" in typ or "xml" in typ):
            continue
        href = urljoin(base, link.get("href") or "")
        if href in seen:
            continue
        seen.add(href)
        found.append({
            "title": (link.get("title") or "").strip() or urlparse(href).netloc,
            "url": href,
            "type": typ or "feed",
        })

    if probe and not found:
        for path in PROBE_PATHS:
            candidate = urljoin(base + "/", path.lstrip("/"))
            try:
                probe_resp = client.get(candidate, follow_redirects=True, timeout=20)
            except httpx.HTTPError:
                continue
            if probe_resp.status_code != 200:
                continue
            ctype = probe_resp.headers.get("content-type", "").lower()
            if "xml" not in ctype and not probe_resp.text.lstrip().startswith(("<?xml", "<rss", "<feed")):
                continue
            parsed = feedparser.parse(probe_resp.text)
            if not (parsed.get("entries") or parsed.feed.get("title")):
                continue
            final = str(probe_resp.url)
            if final in seen:
                continue
            seen.add(final)
            found.append({
                "title": parsed.feed.get("title") or urlparse(final).netloc,
                "url": final,
                "type": "feed (probed)",
            })
    return found


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                return None
    return None


def _strip_html(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def fetch_feed_items(client: httpx.Client, feed: dict, limit: int = _MAX_ITEMS) -> list[dict]:
    """Fetch one feed and return normalized item dicts for the items table."""
    url = feed["url"]
    resp = retry_get(client, url)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    if parsed.get("bozo") and not parsed.get("entries"):
        raise ValueError(f"Unparseable feed {url}: {parsed.get('bozo_exception')}")
    name = feed.get("name") or parsed.feed.get("title") or urlparse(url).netloc
    category = feed.get("category", "")
    feed_tags = [t for t in (feed.get("tags") or []) if t]

    items: list[dict] = []
    for entry in parsed.entries[:limit]:
        title = (entry.get("title") or "").strip()
        link = entry.get("link") or ""
        if not title or not link:
            continue
        summary = _strip_html(entry.get("summary") or "")
        content_parts = []
        for c in entry.get("content") or []:
            text = _strip_html(c.get("value") or "")
            if text:
                content_parts.append(text)
        content = "\n\n".join(content_parts) or summary
        tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]
        tags = list(dict.fromkeys(feed_tags + [t for t in tags if t]))[:20]
        published = _entry_time(entry)
        items.append(
            {
                "source": f"rss:{name}",
                "category": category,
                "title": title,
                "url": link,
                "summary": summary[:2000],
                "content": content[:_MAX_CONTENT],
                "author": entry.get("author") or "",
                "tags": tags,
                "raw": {
                    "feed": url,
                    "id": entry.get("id") or entry.get("guid") or "",
                    "updated": entry.get("updated"),
                },
                "published_at": published.isoformat() if published else None,
            }
        )
    return items
