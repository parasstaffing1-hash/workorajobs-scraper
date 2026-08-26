import httpx
import pytest

from jobcollector.feeds import fetch_feed_items
from jobcollector.scrape import ScraperConfig, run_scraper
from jobcollector.storage import Store

FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Tech News</title>
  <item>
    <title>New Python release</title>
    <link>https://example.com/py</link>
    <guid>g1</guid>
    <author>Guido</author>
    <pubDate>Wed, 01 May 2024 10:00:00 GMT</pubDate>
    <description>&lt;p&gt;It's faster.&lt;/p&gt;</description>
    <category>python</category>
  </item>
</channel></rss>
"""

LISTING_HTML = """
<html><body>
<table>
<tr class="athing"><td><span class="titleline"><a href="https://news.example.com/1">Story One</a></span></td></tr>
<tr class="athing"><td><span class="titleline"><a href="https://news.example.com/2">Story Two</a></span></td></tr>
<tr><td><a class="morelink" href="?p=2">More</a></td></tr>
</table>
</body></html>
"""

PAGE2_HTML = """
<html><body><table>
<tr class="athing"><td><span class="titleline"><a href="https://news.example.com/3">Story Three</a></span></td></tr>
</table></body></html>
"""

DETAIL_LIST = """
<html><body>
<a href="/news/2026/1/">First Post</a>
<a href="/news/2026/2/">Second Post</a>
</body></html>
"""

DETAIL_1 = """
<html><head><title>First Post | Python.org</title>
<meta property="og:description" content="A great post."></head>
<body><h1>First Post</h1><time datetime="2026-05-01T10:00:00Z">May 1</time>
<article>Lots of interesting content here.</article></body></html>
"""

DETAIL_2 = """
<html><head><title>Second Post | Python.org</title></head>
<body><h1>Second Post</h1><time datetime="2026-05-02T10:00:00Z">May 2</time>
<article>More content.</article></body></html>
"""


def _client(routes: dict):
    """MockTransport routed on the exact request URL (trailing-slash tolerant)."""
    normalized = {str(k).rstrip("/") or "/": v for k, v in routes.items()}

    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url).rstrip("/") or "/"
        body = normalized.get(key)
        if body is None:
            return httpx.Response(404, request=request)
        return httpx.Response(200, text=body, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------- feeds

def test_feed_items_ingestion():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=FEED, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        items = fetch_feed_items(client, {"name": "Tech News", "url": "https://example.com/feed", "category": "news"})
    assert len(items) == 1
    item = items[0]
    assert item["source"] == "rss:Tech News"
    assert item["category"] == "news"
    assert item["title"] == "New Python release"
    assert item["summary"] == "It's faster."
    assert item["author"] == "Guido"
    assert item["tags"] == ["python"]
    assert item["published_at"]


# ------------------------------------------------------------------ storage

def test_items_storage_dedupe(tmp_path):
    store = Store(tmp_path / "t.db")
    try:
        item = {
            "source": "rss:Tech News",
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
        assert store.upsert_item(item) is True
        item["summary"] = "Updated"
        assert store.upsert_item(item) is False  # same source+url -> refresh
        rows = store.search_items()
        assert len(rows) == 1
        assert rows[0]["summary"] == "Updated"
        cats = store.items_stats()
        assert cats["total"] == 1
        assert cats["by_category"]["news"] == 1
    finally:
        store.close()


# ------------------------------------------------------------------ scraping

def test_scrape_row_mode(tmp_path):
    routes = {"https://news.example.com/": LISTING_HTML, "https://news.example.com/?p=2": PAGE2_HTML}
    store = Store(tmp_path / "s.db")
    try:
        with _client(routes) as client:
            cfg = ScraperConfig(
                name="hn",
                start_url="https://news.example.com/",
                category="news",
                row_selector="tr.athing",
                fields={"title": "span.titleline a", "url": "span.titleline a@href"},
                next_selector="a.morelink",
                max_pages=2,
            )
            seen, new, errors = run_scraper(cfg, store, client=client)
        assert seen == 3
        assert new == 3
        assert errors == []
        rows = store.search_items()
        assert len(rows) == 3
        titles = {r["title"] for r in rows}
        assert titles == {"Story One", "Story Two", "Story Three"}
    finally:
        store.close()


def test_scrape_link_mode(tmp_path):
    store = Store(tmp_path / "s.db")
    try:
        with _client({"https://www.python.org/blogs/": DETAIL_LIST,
                      "https://www.python.org/news/2026/1/": DETAIL_1,
                      "https://www.python.org/news/2026/2/": DETAIL_2}) as client:
            cfg = ScraperConfig(
                name="python",
                start_url="https://www.python.org/blogs/",
                category="blog",
                item_selector="a",
                item_url_pattern="/news/",
                fields={"title": "h1", "content": "article", "published_at": "time@datetime"},
                domain="www.python.org",
                concurrency=2,
            )
            seen, new, errors = run_scraper(cfg, store, client=client)
        assert seen == 2
        assert new == 2
        rows = store.search_items()
        assert {r["title"] for r in rows} == {"First Post", "Second Post"}
        first = next(r for r in rows if r["title"] == "First Post")
        assert first["summary"] == "A great post."  # og:description fallback
        assert first["published_at"] == "2026-05-01T10:00:00Z"
    finally:
        store.close()


def test_scraper_requires_mode():
    cfg = ScraperConfig(name="x", start_url="https://x.com")
    store = Store(":memory:")
    with _client({}) as client:
        with pytest.raises(ValueError):
            run_scraper(cfg, store, client=client)
    store.close()
