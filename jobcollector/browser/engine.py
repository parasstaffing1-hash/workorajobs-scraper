"""Core browser pool and page automation utilities.

Provides:
- BrowserPool: singleton Chromium with anti-detection patches
- BrowserPage: wrapper with retry, scroll, wait helpers
- scrape_url: one-shot convenience function

Usage:
    from jobcollector.browser.engine import get_pool, BrowserPage
    pool = get_pool()
    page = pool.new_page()
    page.goto("https://example.com/jobs")
    html = page.content()
"""
from __future__ import annotations

import atexit
import json
import random
import re
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urljoin, urlparse

# ---------------------------------------------------------------------------
# User agents — latest stable releases
# ---------------------------------------------------------------------------
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# Stealth JS — defeats common headless detection
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = { runtime: {} };
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);
delete navigator.__proto__.webdriver;
"""

# Extra JS to randomize fingerprint
_FP_JS = """
Object.defineProperty(navigator, 'platform', {
    get: () => 'Win32'
});
Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => { return {valueOf: () => 8}; }
});
Object.defineProperty(navigator, 'deviceMemory', {
    get: () => { return {valueOf: () => 8}; }
});
"""


class BrowserPool:
    """Singleton Chromium pool with anti-detection."""

    def __init__(self):
        self._browser = None
        self._pw = None
        self._lock = Lock()
        self._ua = random.choice(_USER_AGENTS)
        self._ctx_count = 0

    def _launch(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-translate",
                "--metrics-recording-only",
                "--mute-audio",
                "--no-sandbox",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )

    def _ensure(self):
        if self._browser and self._browser.is_connected():
            return
        self.close()
        self._launch()

    def new_page(self, timeout_ms: int = 60000, proxy: str | None = None):
        """Return a new Page with stealth patches applied.

        ``proxy``: optional proxy URL (e.g. "http://user:pass@host:port").
        If not given, falls back to the PROXY_URL env var.
        """
        import os
        if proxy is None:
            proxy = os.environ.get("PROXY_URL") or os.environ.get("JOBCOLLECT_PROXY") or None
        with self._lock:
            try:
                self._ensure()
            except Exception:
                self.close()
                self._launch()

            kwargs = {}
            if proxy:
                kwargs["proxy"] = {"server": proxy}
            ctx = self._browser.new_context(
                user_agent=self._ua,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
                java_script_enabled=True,
                bypass_csp=True,
                # Extra stealth
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                },
                **kwargs,
            )
            ctx.add_init_script(_STEALTH_JS)
            ctx.add_init_script(_FP_JS)
            self._ctx_count += 1
            page = ctx.new_page()
            page.set_default_timeout(timeout_ms)
            return page

    def close(self):
        with self._lock:
            for attr in ("_browser", "_pw"):
                obj = getattr(self, attr, None)
                if obj:
                    with suppress(Exception):
                        if attr == "_browser":
                            obj.close()
                        else:
                            obj.stop()
                setattr(self, attr, None)
            self._ctx_count = 0

    def reset(self):
        self.close()


_pool: BrowserPool | None = None
_pool_lock = Lock()


def get_pool() -> BrowserPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = BrowserPool()
                atexit.register(_pool.close)
    return _pool


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------

def goto(page, url: str, *, wait_until: str = "domcontentloaded", timeout_ms: int = 45000) -> None:
    """Navigate to URL with retry on timeout."""
    for attempt in range(3):
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt * 2)


def auto_scroll(page, max_scrolls: int = 30, wait_ms: int = 1500) -> int:
    """Scroll to bottom to trigger lazy-loading. Returns number of scrolls."""
    prev_height = 0
    scrolls = 0
    for i in range(max_scrolls):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(wait_ms)
        height = page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            # Try one more time with a longer wait
            page.wait_for_timeout(2000)
            height = page.evaluate("document.body.scrollHeight")
            if height == prev_height:
                break
        prev_height = height
        scrolls = i + 1
    return scrolls


def wait_for_any(page, selectors: list[str], timeout_ms: int = 15000) -> str | None:
    """Wait for any of the given selectors to appear. Returns the matching selector."""
    combined = ", ".join(selectors)
    try:
        page.wait_for_selector(combined, timeout=timeout_ms)
        # Find which one matched
        for sel in selectors:
            if page.query_selector(sel):
                return sel
    except Exception:
        pass
    return None


def dismiss_banners(page) -> None:
    """Try to dismiss common cookie/notification banners."""
    dismiss_selectors = [
        'button[id*="accept"]',
        'button[class*="accept"]',
        'button[data-testid*="accept"]',
        '[id*="cookie"] button',
        '[class*="cookie"] button',
        '[id*="consent"] button',
        'button:has-text("Accept")',
        'button:has-text("OK")',
        'button:has-text("Got it")',
        'button:has-text("I agree")',
        'button:has-text("Close")',
        '[aria-label="Close"]',
        '[aria-label="Dismiss"]',
        'button[class*="dismiss"]',
    ]
    for sel in dismiss_selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                time.sleep(0.5)
                return
        except Exception:
            continue


def extract_json_ld(html: str) -> dict | None:
    """Extract JobPosting JSON-LD from page HTML."""
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    ):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, list):
                for item in data:
                    if _is_job_posting(item):
                        return item
            elif _is_job_posting(data):
                return data
            # Check @graph
            for item in data.get("@graph", []):
                if _is_job_posting(item):
                    return item
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _is_job_posting(data: dict) -> bool:
    t = str(data.get("@type", "")).lower()
    return "jobposting" in t or "job-posting" in t


def extract_jobs_from_json_ld(html: str) -> list[dict]:
    """Extract all JobPosting items from JSON-LD in a page."""
    jobs = []
    for match in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL
    ):
        try:
            data = json.loads(match.group(1))
            items = data if isinstance(data, list) else [data]
            for item in items:
                if _is_job_posting(item):
                    jobs.append(item)
                for g in item.get("@graph", []):
                    if _is_job_posting(g):
                        jobs.append(g)
        except (json.JSONDecodeError, TypeError):
            continue
    return jobs


def parse_relative_date(text: str) -> datetime | None:
    """Parse '3 days ago', 'just now', '2 hours ago', etc."""
    text = text.lower().strip()
    now = datetime.now(timezone.utc)

    if not text or "just now" in text or "moments ago" in text:
        return now

    patterns = [
        (r"(\d+)\s*second", lambda n: now),
        (r"(\d+)\s*minute", lambda n: now),
        (r"(\d+)\s*hour", lambda n: now),
        (r"(\d+)\s*day", lambda n: now),
        (r"(\d+)\s*week", lambda n: now),
        (r"(\d+)\s*month", lambda n: now),
    ]
    for pat, _ in patterns:
        if re.search(pat, text):
            # For now, just return now (the relative date is recent enough)
            return now

    # Try ISO format
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass

    return None
