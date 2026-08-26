"""Config-driven generic web scraping engine.

Two modes per scraper (defined in ``scrapers.yaml``):

* **Row mode** — ``row_selector`` matches elements on the page that ARE items
  (e.g. ``li.athing`` on Hacker News). No detail-page fetches. Best for list
  pages where each row carries its own link and text.
* **Link mode** — ``item_selector`` matches links to detail pages, which are
  fetched (concurrently) and extracted individually.

Field extraction uses CSS selectors by default and ``xpath:...`` prefixed
selectors for XPath. JSON-LD (Article/NewsArticle/BlogPosting/JobPosting) is
preferred on detail pages, then configured ``fields``, then generic heuristics
(h1/title, meta description, trafilatura main-content extraction).
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import httpx
from parsel import Selector
from pydantic import BaseModel, Field

from .http import retry_get
from .jsrender import render_js

_MAX_CONTENT = 50_000
_JSONLD_TYPES = ("article", "newsarticle", "blogposting", "jobposting", "webpage")


class ScraperConfig(BaseModel):
    name: str
    start_url: str
    category: str = ""
    # Row mode
    row_selector: str | None = None
    # Link mode
    item_selector: str | None = None
    item_url_pattern: str | None = None
    # field name -> selector ("css" or "xpath:..."); supports "@attr" suffix for
    # attribute extraction (e.g. "time@datetime", "a@href").
    fields: dict[str, str] = Field(default_factory=dict)
    next_selector: str | None = None
    max_pages: int = 3
    max_items: int = 200
    render_js: bool = False
    domain: str | None = None
    concurrency: int = 6


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _first(selector: Selector, css: str, default: str = "") -> str:
    parts = css.split("@", 1)
    path, attr = parts[0], (parts[1] if len(parts) > 1 else None)
    if css.startswith("xpath:"):
        path, attr = css[6:], None
        nodes = selector.xpath(path)
    else:
        nodes = selector.css(path)
    if not nodes:
        return default
    node = nodes[0]
    if attr:
        return _clean(node.attrib.get(attr, ""))
    root = node.root
    if root is not None and hasattr(root, "text_content"):
        return _clean(root.text_content())
    return _clean(node.get())


def _json_ld_extract(html: str) -> dict | None:
    """Pull the first structured Article/JobPosting block from JSON-LD."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    def walk(node):
        if isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
            return None
        if isinstance(node, dict):
            t = str(node.get("@type", "")).lower()
            if any(k in t for k in _JSONLD_TYPES):
                return node
            for value in node.values():
                found = walk(value)
                if found:
                    return found
        return None

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        found = walk(data)
        if found:
            return found
    return None


def _ld_text(item: dict | None, *keys) -> str:
    if not item:
        return ""
    for key in keys:
        value = item.get(key)
        if isinstance(value, dict):
            return _clean(value.get("name") or value.get("value") or "")
        if value:
            return _clean(str(value))
    return ""


def _page_item(html: str, url: str, cfg: ScraperConfig) -> dict:
    """Extract an item from a full detail page."""
    ld = _json_ld_extract(html)
    sel = Selector(text=html)
    fields = {k: _first(sel, v) for k, v in cfg.fields.items()}

    title = fields.get("title") or _ld_text(ld, "headline", "title") or _clean(
        _first(sel, "h1") or _first(sel, "title")
    )
    summary = fields.get("summary") or _ld_text(ld, "description") or _clean(
        _first(sel, "meta[name=description]@content")
        or _first(sel, "meta[property='og:description']@content")
        or _first(sel, "p")
    )
    author = fields.get("author") or _ld_text(ld, "author") or _clean(
        _first(sel, "meta[name=author]@content") or _first(sel, ".author")
    )
    published = fields.get("published_at") or _ld_text(ld, "datePublished") or _clean(
        _first(sel, "time@datetime")
        or _first(sel, "meta[property='article:published_time']@content")
        or _first(sel, "meta[name=pubdate]@content")
    )

    content = fields.get("content")
    if not content:
        body = _ld_text(ld, "articleBody", "description")
        if not body:
            try:
                import trafilatura

                body = trafilatura.extract(
                    html, include_comments=False, include_tables=False, favor_recall=True
                ) or ""
            except Exception:
                body = ""
        if not body:
            body = _clean(_first(sel, "article") or _first(sel, "main") or _first(sel, "body"))
        content = body

    return {
        "title": (title or url)[:500],
        "summary": summary[:2000],
        "content": content[:_MAX_CONTENT],
        "author": author[:200],
        "published_at": published[:64] or None,
        "extra": fields,
    }


def _row_item(row_sel: Selector, page_url: str, cfg: ScraperConfig) -> dict:
    """Extract an item from a single row element (row mode)."""
    fields = {k: _first(row_sel, v) for k, v in cfg.fields.items()}
    link = _first(row_sel, "a@href")
    title = fields.get("title") or _clean(_first(row_sel, "h1") or _first(row_sel, "h2")
                                          or _first(row_sel, "h3") or _first(row_sel, "a")
                                          or _first(row_sel, "title"))
    summary = fields.get("summary") or _clean(_first(row_sel, "p") or _first(row_sel, "span"))
    author = fields.get("author") or _clean(_first(row_sel, ".author") or _first(row_sel, "a[rel=author]"))
    published = fields.get("published_at") or _clean(
        _first(row_sel, "time@datetime") or _first(row_sel, "time")
    )
    url = urljoin(page_url, link) if link else page_url
    return {
        "title": (title or url)[:500],
        "summary": summary[:2000],
        "content": summary[:_MAX_CONTENT],
        "author": author[:200],
        "published_at": published[:64] or None,
        "url": url,
        "extra": fields,
    }


def _fetch_html(client: httpx.Client, url: str, render: bool) -> str:
    if render:
        return render_js(url)
    resp = retry_get(client, url)
    resp.raise_for_status()
    return resp.text


class Scraper:
    def __init__(self, cfg: ScraperConfig, client: httpx.Client | None = None):
        self.cfg = cfg
        self.client = client or httpx.Client(follow_redirects=True, timeout=30)
        self.errors: list[str] = []

    def run(self) -> list[dict]:
        if self.cfg.row_selector:
            return self._run_rows()
        if self.cfg.item_selector:
            return self._run_links()
        raise ValueError(f"Scraper {self.cfg.name!r} needs row_selector or item_selector")

    def _allowed(self, url: str) -> bool:
        if not self.cfg.domain:
            return True
        return urlparse(url).netloc.lower() == self.cfg.domain.lower()

    def _next_page(self, html: str, page_url: str) -> str | None:
        if not self.cfg.next_selector:
            return None
        sel = Selector(text=html)
        href = _first(sel, f"{self.cfg.next_selector}@href")
        if not href:
            return None
        url = urljoin(page_url, href)
        return url if self._allowed(url) else None

    # ------------------------------------------------------------- row mode

    def _run_rows(self) -> list[dict]:
        items: list[dict] = []
        page_url = self.cfg.start_url
        for _ in range(self.cfg.max_pages):
            html = _fetch_html(self.client, page_url, self.cfg.render_js)
            sel = Selector(text=html)
            rows = sel.css(self.cfg.row_selector)
            for row in rows:
                item = _row_item(row, page_url, self.cfg)
                if item["title"] and item["title"] != page_url:
                    items.append(item)
                    if len(items) >= self.cfg.max_items:
                        return items
            page_url = self._next_page(html, page_url) or ""
            if not page_url:
                break
        return items

    # ------------------------------------------------------------ link mode

    def _run_links(self) -> list[dict]:
        links: list[str] = []
        page_url = self.cfg.start_url
        pattern = re.compile(self.cfg.item_url_pattern) if self.cfg.item_url_pattern else None
        for _ in range(self.cfg.max_pages):
            html = _fetch_html(self.client, page_url, self.cfg.render_js)
            sel = Selector(text=html)
            for node in sel.css(self.cfg.item_selector):
                href = _attr_from_node(node) or _first(node, "a@href")
                if not href:
                    continue
                url = urljoin(page_url, href)
                if not self._allowed(url) or url.rstrip("/") == page_url.rstrip("/"):
                    continue
                if pattern and not pattern.search(url):
                    continue
                if url not in links:
                    links.append(url)
            page_url = self._next_page(html, page_url) or ""
            if not page_url:
                break
        links = links[: self.cfg.max_items]

        items: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.cfg.concurrency) as pool:
            futures = {
                pool.submit(self._fetch_detail, url): url for url in links
            }
            for fut in as_completed(futures):
                url = futures[fut]
                try:
                    item = fut.result()
                except Exception as exc:
                    self.errors.append(f"{url}: {exc}")
                    continue
                if item:
                    items.append(item)
        return items

    def _fetch_detail(self, url: str) -> dict | None:
        html = _fetch_html(self.client, url, self.cfg.render_js)
        item = _page_item(html, url, self.cfg)
        if not item["title"]:
            return None
        item["url"] = url
        return item


def _attr_from_node(node) -> str:
    """Best-effort href from an element that may itself be the link."""
    return _clean(node.attrib.get("href", ""))


def run_scraper(cfg: ScraperConfig, store, client: httpx.Client | None = None, limit: int | None = None) -> tuple[int, int, list[str]]:
    """Run one scraper and persist items. Returns (seen, new, errors)."""
    if limit:
        cfg.max_items = min(cfg.max_items, limit)
    scraper = Scraper(cfg, client=client)
    items = scraper.run()
    seen = new = 0
    for item in items:
        record = {
            "source": f"scrape:{cfg.name}",
            "category": cfg.category,
            "title": item["title"],
            "url": item["url"],
            "summary": item["summary"],
            "content": item["content"],
            "author": item["author"],
            "tags": list(item["extra"].values())[:10],
            "raw": {"scraper": cfg.name, "fields": item["extra"], "start_url": cfg.start_url},
            "published_at": item["published_at"],
        }
        if store.upsert_item(record):
            new += 1
        seen += 1
    return seen, new, scraper.errors
