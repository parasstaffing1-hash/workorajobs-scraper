"""Tests for the RSS reader: read/star state, feed discovery, HTTP API."""
import threading

import httpx

from jobcollector.feeds import discover_feeds
from jobcollector.server import make_handler
from jobcollector.storage import Store

from tests.test_engine_tools import _seed  # reuse the seed helper


def _item(source="rss:Hacker News", url="https://example.com/a", title="A post"):
    return {
        "source": source, "category": "news", "title": title, "url": url,
        "summary": "summary", "content": "", "author": "Author",
        "tags": [], "raw": {}, "published_at": "2024-05-01T10:00:00+00:00",
    }


def test_read_and_star_state_persists(tmp_path):
    store = Store(tmp_path / "r.db")
    try:
        store.upsert_item(_item())
        rows = store.reader_items()
        assert rows[0]["read"] == 0 and rows[0]["starred"] == 0

        store.mark_read("rss:Hacker News", "https://example.com/a", True)
        store.mark_starred("rss:Hacker News", "https://example.com/a", True)
        rows = store.reader_items()
        assert rows[0]["read"] == 1 and rows[0]["starred"] == 1
        assert store.unread_total() == 0

        # state survives re-upsert (refresh must not reset read/starred)
        store.upsert_item(_item())
        rows = store.reader_items()
        assert rows[0]["read"] == 1 and rows[0]["starred"] == 1

        assert store.mark_source_read() == 0  # nothing left unread
        store.mark_read("rss:Hacker News", "https://example.com/a", False)
        assert store.unread_total() == 1
        assert store.mark_source_read("rss:Hacker News") == 1
    finally:
        store.close()


def test_search_items_unread_filter(tmp_path):
    store = Store(tmp_path / "u.db")
    try:
        store.upsert_item(_item(url="https://example.com/1"))
        store.upsert_item(_item(url="https://example.com/2", title="Second"))
        store.mark_read("rss:Hacker News", "https://example.com/1", True)
        rows = store.search_items(unread_only=True)
        assert len(rows) == 1
        assert rows[0]["url"] == "https://example.com/2"
    finally:
        store.close()


def test_feed_meta_recorded(tmp_path):
    store = Store(tmp_path / "m.db")
    try:
        store.record_feed_fetch("rss:Hacker News", "https://hnrss.org/frontpage", "Hacker News", "news", 30)
        store.record_feed_fetch("rss:Hacker News", "https://hnrss.org/frontpage", "Hacker News", "news", 35)
        meta = store.feed_meta()
        assert len(meta) == 1
        assert meta[0]["item_count"] == 35
        assert meta[0]["last_fetched_at"]
        store.record_feed_fetch("rss:Bad", "https://x/bad", "Bad", "", 0, error="boom")
        bad = next(f for f in store.feed_meta() if f["source"] == "rss:Bad")
        assert bad["last_error"] == "boom"
    finally:
        store.close()


DISCOVERY_HTML = """
<html><head>
  <link rel="alternate" type="application/rss+xml" title="Main RSS" href="/rss.xml">
  <link rel="alternate" type="application/atom+xml" href="https://cdn.example.com/atom.xml">
</head><body><h1>Example</h1></body></html>
"""


def test_discover_feeds_from_link_tags():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=DISCOVERY_HTML, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    feeds = discover_feeds(client, "https://example.com/", probe=False)
    assert feeds[0]["url"] == "https://example.com/rss.xml"
    assert feeds[0]["type"].startswith("application/rss")
    assert feeds[1]["url"] == "https://cdn.example.com/atom.xml"


def test_discover_probes_common_paths():
    feed_xml = '<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title></channel></rss>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/feed":
            return httpx.Response(200, text=feed_xml, headers={"content-type": "application/rss+xml"}, request=request)
        if request.url.path == "/":
            return httpx.Response(200, text="<html><body>Blog</body></html>", request=request)
        return httpx.Response(404, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    feeds = discover_feeds(client, "https://blog.example.com/")
    assert any(f["url"].endswith("/feed") for f in feeds)
    assert feeds[0]["title"] == "Blog"


def _start_server(tmp_path, db_path):
    store = Store(db_path)
    try:
        _seed(store)
        store.upsert_item(_item(url="https://example.com/a", title="Article One"))
    finally:
        store.close()
    (tmp_path / "dashboard.html").write_text("<html>dashboard</html>", encoding="utf-8")
    from http.server import ThreadingHTTPServer

    handler = make_handler(str(db_path), tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


def test_api_jobs_and_items_endpoints(tmp_path):
    db = tmp_path / "j.db"
    httpd, port = _start_server(tmp_path, db)
    try:
        base = f"http://127.0.0.1:{port}"
        d = httpx.get(f"{base}/api/jobs").json()
        assert d["ok"] is True and d["count"] == 1
        job = d["jobs"][0]
        assert job["title"] == "Backend Engineer"
        assert job["company"] == "Acme"
        assert job["is_active"] is True
        assert set(job) >= {"title", "company", "location", "url", "source",
                            "salary", "tags", "posted_at", "is_active"}

        # active=0 includes everything; q filters
        assert httpx.get(f"{base}/api/jobs?active=0").json()["count"] == 1
        assert httpx.get(f"{base}/api/jobs?q=nomatch").json()["count"] == 0

        d = httpx.get(f"{base}/api/items").json()
        assert d["ok"] is True and d["count"] == 2
        assert any(i["title"] == "New Python release" for i in d["items"])
        assert httpx.get(f"{base}/api/items?unread=1").json()["count"] == 2
    finally:
        httpd.shutdown()


def test_server_api_roundtrip(tmp_path):
    db = tmp_path / "s.db"
    httpd, port = _start_server(tmp_path, db)
    try:
        base = f"http://127.0.0.1:{port}"
        assert httpx.get(f"{base}/api/ping").json()["ok"] is True

        data = httpx.get(f"{base}/api/data").json()
        assert data["ok"] is True
        assert "Article One" in [i["title"] for i in data["items"]]
        assert data["readerUnread"] >= 2
        assert any(f["source"] == "rss:Hacker News" for f in data["feeds"])

        # mark one read + star it, then confirm persistence via /api/data
        item = next(i for i in data["items"] if i["title"] == "Article One")
        r = httpx.post(f"{base}/api/read", json={"source": item["source"], "url": item["url"], "read": True})
        assert r.json()["ok"] is True
        r = httpx.post(f"{base}/api/star", json={"source": item["source"], "url": item["url"], "starred": True})
        assert r.json()["ok"] is True

        data2 = httpx.get(f"{base}/api/data").json()
        item2 = next(i for i in data2["items"] if i["title"] == "Article One")
        assert item2["read"] is True and item2["starred"] is True
        assert data2["readerUnread"] == data["readerUnread"] - 1

        # read_all scoped to a source
        r = httpx.post(f"{base}/api/read_all", json={"source": "rss:Hacker News"})
        assert r.json()["count"] >= 1

        # static file serving + traversal guard
        assert httpx.get(f"{base}/dashboard.html").status_code == 200
        assert httpx.get(f"{base}/../companies.example.yaml").status_code == 404
    finally:
        httpd.shutdown()
