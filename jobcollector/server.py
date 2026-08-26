"""Local reader server: serves the dashboard plus a small JSON API.

Keeps read/star state in SQLite and extracts full article text on demand, so
the dashboard's Reader page behaves like a real feed reader. Zero new
dependencies: stdlib ``http.server`` + the engine's existing libs.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from .storage import Store

_UA = "Mozilla/5.0 (JobCollector reader; +https://github.com/Feashliaa/job-collector)"
_CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
_READER_LIMIT = 1000


def _extract_fulltext(url: str) -> str:
    """Fetch a page and pull out the main article text (trafilatura)."""
    import trafilatura

    resp = httpx.get(url, follow_redirects=True, timeout=40, headers={"User-Agent": _UA})
    resp.raise_for_status()
    text = trafilatura.extract(resp.text, include_comments=False, include_tables=False)
    if not text:
        raise ValueError("No extractable article content found.")
    return text[:60_000]


def make_handler(db_path: str, root: Path):
    root = root.resolve()

    class Handler(BaseHTTPRequestHandler):
        # Quiet by default; the CLI prints the startup URL itself.
        def log_message(self, fmt, *args):  # noqa: D102
            pass

        def _send(self, code: int, body, ctype: str = "application/json") -> None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            elif isinstance(body, str):
                data = body.encode("utf-8")
            else:
                data = body
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_body(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0:
                return {}
            raw = self.rfile.read(n) or b"{}"
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}

        # ------------------------------------------------------------- routes

        # ---------------------------------------------- machine-readable data

        def _qs(self, key: str, default: str = "") -> str:
            values = parse_qs(urlparse(self.path).query).get(key)
            return values[0] if values else default

        def _api_jobs(self) -> None:
            """GET /api/jobs?active=1&limit=5000&offset=10000&q=python&source=greenhouse"""
            try:
                limit = min(int(self._qs("limit", "1000")), 10_000)
            except ValueError:
                limit = 1000
            try:
                offset = max(int(self._qs("offset", "0")), 0)
            except ValueError:
                offset = 0
            active = self._qs("active", "1").lower() not in ("0", "false", "no")
            store = Store(db_path)
            try:
                rows = store.search(
                    query=self._qs("q"), source=self._qs("source"),
                    active_only=active, limit=limit, offset=offset,
                )
                jobs = []
                for r in rows:
                    try:
                        tags = " | ".join(json.loads(r["tags"] or "[]"))
                    except (ValueError, TypeError):
                        tags = ""
                    jobs.append({
                        "title": r["title"],
                        "company": r["company"],
                        "location": r["location"],
                        "url": r["url"],
                        "source": r["source"],
                        "source_kind": r["source_kind"],
                        "salary": r["salary"],
                        "tags": tags,
                        "description": (r["description"] or "")[:2000],
                        "posted_at": r["posted_at"],
                        "first_seen_at": r["first_seen_at"],
                        "last_seen_at": r["last_seen_at"],
                        "is_active": bool(r["is_active"]),
                    })
            finally:
                store.close()
            self._send(200, {"ok": True, "count": len(jobs), "jobs": jobs})

        def _api_items(self) -> None:
            """GET /api/items?limit=500&offset=0&category=news&unread=1"""
            try:
                limit = min(int(self._qs("limit", "500")), 5_000)
            except ValueError:
                limit = 500
            try:
                offset = max(int(self._qs("offset", "0")), 0)
            except ValueError:
                offset = 0
            unread = self._qs("unread", "0").lower() in ("1", "true", "yes")
            store = Store(db_path)
            try:
                rows = store.search_items(
                    query=self._qs("q"), category=self._qs("category"),
                    source=self._qs("source"), unread_only=unread, limit=limit,
                    offset=offset,
                )
                items = [{
                    "title": r["title"],
                    "category": r["category"],
                    "source": r["source"],
                    "url": r["url"],
                    "summary": (r["summary"] or "")[:500],
                    "author": r["author"],
                    "published_at": r["published_at"],
                    "read": bool(r["read"]),
                    "starred": bool(r["starred"]),
                } for r in rows]
            finally:
                store.close()
            self._send(200, {"ok": True, "count": len(items), "items": items})

        def _api_data(self) -> None:
            store = Store(db_path)
            try:
                items = store.reader_items(limit=_READER_LIMIT)
                feeds = store.feed_meta()
                unread = store.unread_totals()
                by_source = store.items_stats()["by_source"]
                feed_by_source = {f["source"]: f for f in feeds}
                reader_feeds = []
                for src, total in by_source.items():
                    meta = feed_by_source.get(src, {})
                    reader_feeds.append({
                        "source": src,
                        "name": meta.get("name") or src.split(":", 1)[-1],
                        "category": meta.get("category") or ("" if src.startswith("rss:") else "scraped"),
                        "url": meta.get("url", ""),
                        "unread": unread.get(src, 0),
                        "total": total,
                    })
                reader_feeds.sort(key=lambda f: (-f["unread"], f["name"].lower()))
                payload = {
                    "ok": True,
                    "generated": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
                    "readerUnread": sum(unread.values()),
                    "feeds": reader_feeds,
                    "items": [{
                        "title": r["title"],
                        "category": r["category"],
                        "url": r["url"],
                        "source": r["source"],
                        "summary": (r["summary"] or "")[:500],
                        "content": (r["content"] or "")[:60_000],
                        "author": r["author"],
                        "published_at": r["published_at"],
                        "read": bool(r["read"]),
                        "starred": bool(r["starred"]),
                        "full": (r["content"] or "") != (r["summary"] or ""),
                    } for r in items],
                }
            finally:
                store.close()
            self._send(200, payload)

        def _api_watchlist(self) -> None:
            store = Store(db_path)
            try:
                items = store.watchlist_all()
                counts = store.watchlist_counts()
                for it in items:
                    it["count"] = counts.get(it["id"], 0)
                return self._send(200, {"ok": True, "items": items})
            finally:
                store.close()

        def _do_post(self, path: str) -> None:
            body = self._read_body()
            store = Store(db_path)
            try:
                if path == "/api/watchlist":
                    kind, value = body.get("kind", ""), (body.get("value") or "").strip()
                    if kind not in ("company", "keyword") or not value:
                        return self._send(400, {"error": "kind must be company|keyword and value non-empty"})
                    item = store.watchlist_add(kind, value)
                    item["count"] = store.count_matches(kind, item["value"])
                    return self._send(200, {"ok": True, "item": item})
                if path == "/api/watchlist/bulk":
                    kind, values = body.get("kind", ""), body.get("values")
                    if kind not in ("company", "keyword") or not isinstance(values, list):
                        return self._send(400, {"error": "kind must be company|keyword and values a list"})
                    clean = [str(v).strip() for v in values if str(v).strip()]
                    if not clean:
                        return self._send(400, {"error": "values empty"})
                    res = store.watchlist_bulk_add(kind, clean)
                    return self._send(200, {"ok": True, **res})
                if path == "/api/watchlist_delete":
                    try:
                        wid = int(body.get("id", 0))
                    except (TypeError, ValueError):
                        return self._send(400, {"error": "invalid id"})
                    return self._send(200, {"ok": store.watchlist_delete(wid)})
                if path == "/api/read":
                    store.mark_read(body.get("source", ""), body.get("url", ""), bool(body.get("read", True)))
                    return self._send(200, {"ok": True})
                if path == "/api/star":
                    store.mark_starred(body.get("source", ""), body.get("url", ""), bool(body.get("starred", True)))
                    return self._send(200, {"ok": True})
                if path == "/api/read_all":
                    n = store.mark_source_read(body.get("source") or "")
                    return self._send(200, {"ok": True, "count": n})
                if path == "/api/fulltext":
                    source, url = body.get("source", ""), body.get("url", "")
                    if not url or not re.match(r"^https?://", url):
                        return self._send(400, {"error": "invalid url"})
                    try:
                        content = _extract_fulltext(url)
                    except Exception as exc:  # network / extraction failure
                        return self._send(502, {"error": str(exc)})
                    store.store_fulltext(source, url, content)
                    return self._send(200, {"ok": True, "content": content})
                self._send(404, {"error": "not found"})
            finally:
                store.close()

        def _serve_static(self, path: str) -> None:
            rel = path.lstrip("/") or "dashboard.html"
            target = (root / rel).resolve()
            if not str(target).startswith(str(root)) or not target.is_file():
                return self._send(404, {"error": "not found"})
            ctype = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
            self._send(200, target.read_bytes(), ctype)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/ping":
                return self._send(200, {"ok": True})
            if path == "/api/data":
                return self._api_data()
            if path == "/api/jobs":
                return self._api_jobs()
            if path == "/api/items":
                return self._api_items()
            if path == "/api/watchlist":
                return self._api_watchlist()
            self._serve_static(path)

        def do_POST(self) -> None:  # noqa: N802
            self._do_post(urlparse(self.path).path)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8600, root: str | Path = ".") -> None:
    handler = make_handler(db_path, Path(root))
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.serve_forever()
