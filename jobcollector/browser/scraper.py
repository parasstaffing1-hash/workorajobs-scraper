"""Base class for all browser-based job scrapers."""
from __future__ import annotations

import contextlib
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin

from parsel import Selector

from .engine import (
    auto_scroll,
    dismiss_banners,
    extract_json_ld,
    extract_jobs_from_json_ld,
    get_pool,
    goto,
    parse_relative_date,
    wait_for_any,
)


@dataclass
class ScrapedJob:
    title: str = ""
    company: str = ""
    url: str = ""
    location: str = ""
    description: str = ""
    salary: str = ""
    tags: list[str] = field(default_factory=list)
    posted_at: datetime | None = None
    source: str = ""
    source_kind: str = "browser"
    external_id: str = ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract(sel: Selector, css: str, default: str = "") -> str:
    if css.startswith("xpath:"):
        nodes = sel.xpath(css[6:])
    elif "@" in css and not css.startswith("::"):
        # Support both "a@href" and "a::attr(href)" syntax
        parts = css.split("@", 1)
        css_sel = parts[0]
        attr = parts[1]
        nodes = sel.css(f"{css_sel}::attr({attr})")
        if nodes:
            return _clean(nodes.get())
        # Fallback: get the element and extract attribute
        nodes = sel.css(css_sel)
        if nodes:
            return _clean(nodes[0].attrib.get(attr, ""))
        return default
    elif "::attr(" in css:
        # parsel native attr syntax
        nodes = sel.css(css)
        if nodes:
            return _clean(nodes.get())
        return default
    else:
        nodes = sel.css(css)
    if not nodes:
        return default
    node = nodes[0]
    root = node.root
    if root is not None and hasattr(root, "text_content"):
        return _clean(root.text_content())
    return _clean(node.get())


class BaseBoardScraper(ABC):
    """Base class for scraping a single job board."""

    name: str = ""
    base_url: str = ""

    def __init__(self, keywords: list[str] | None = None, max_items: int = 200, delay_s: float = 1.5):
        self.keywords = keywords or ["software engineer"]
        self.max_items = max_items
        self.delay_s = delay_s
        self.errors: list[str] = []
        self._seen: set[str] = set()

    @abstractmethod
    def get_search_urls(self) -> list[str]:
        """Return list of search URLs to scrape."""
        ...

    @abstractmethod
    def parse_page(self, html: str, page, url: str) -> list[ScrapedJob]:
        """Parse a page of results. Return list of ScrapedJob."""
        ...

    def has_next_page(self, html: str, page) -> str | None:
        """Return URL of next page, or None."""
        return None

    def run(self) -> list[ScrapedJob]:
        """Run the scraper using the global browser pool."""
        pool = get_pool()
        browser = None  # pool creates browsers internally
        return self._run_internal(None)

    def run_with_browser(self, browser) -> list[ScrapedJob]:
        """Run the scraper with an externally provided Playwright browser."""
        return self._run_internal(browser)

    def _run_internal(self, browser) -> list[ScrapedJob]:
        """Core run logic."""
        all_jobs: list[ScrapedJob] = []

        for search_url in self.get_search_urls():
            if len(all_jobs) >= self.max_items:
                break

            # Create a fresh context per search URL
            if browser:
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                    extra_http_headers={
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                ctx.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    window.chrome = { runtime: {} };
                """)
                pg = ctx.new_page()
            else:
                pg = get_pool().new_page()

            try:
                goto(pg, search_url, timeout_ms=45000)
                time.sleep(2)
                dismiss_banners(pg)

                # Scroll to load content
                auto_scroll(pg, max_scrolls=15, wait_ms=1500)

                # Wait for job listings
                wait_for_any(pg, self._get_wait_selectors(), timeout_ms=10000)

                # Parse multiple pages
                page_url = search_url
                for page_num in range(10):
                    if len(all_jobs) >= self.max_items:
                        break

                    html = pg.content()
                    jobs = self.parse_page(html, pg, page_url)

                    for job in jobs:
                        if job.url and job.url not in self._seen:
                            job.source = self.name
                            all_jobs.append(job)
                            self._seen.add(job.url)

                    # Check for next page
                    next_url = self.has_next_page(html, pg)
                    if not next_url or len(all_jobs) >= self.max_items:
                        break

                    time.sleep(self.delay_s)
                    goto(pg, next_url, timeout_ms=30000)
                    time.sleep(1.5)
                    auto_scroll(pg, max_scrolls=5, wait_ms=1000)

            except Exception as exc:
                self.errors.append(f"{self.name}: {search_url}: {exc}")
            finally:
                with contextlib.suppress(Exception):
                    pg.close()
                with contextlib.suppress(Exception):
                    ctx.close()

            time.sleep(self.delay_s)

        return all_jobs[:self.max_items]

    def _get_wait_selectors(self) -> list[str]:
        return [
            "[data-test='job-link']",
            ".job_seen_beacon",
            ".base-card",
            ".job-card",
            ".job-listing",
            "li.result",
            "[class*='job-card']",
            "[class*='JobCard']",
            "article",
            "tr.athing",
        ]
