"""Playwright browser pool with anti-detection for production scraping.

Usage::

    from jobcollector.jsrender import get_browser, render_js, render_js_multi

The browser is lazily launched on first use and reused across calls.
Call ``close_browser()`` when done (or at process exit).

Requires ``pip install playwright`` and ``playwright install chromium``.
"""
from __future__ import annotations

import atexit
import random
import time
from contextlib import suppress
from threading import Lock
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Realistic user agents (latest stable releases, 2026)
# ---------------------------------------------------------------------------
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# Stealth JS injected before every navigation to defeat bot detection.
_STEALTH_JS = """
// Overwrite navigator.webdriver so headless detection fails
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
// Fake plugins
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
// Fake languages
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
// Pass Chrome check
window.chrome = { runtime: {} };
// Pass permissions check
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);
"""


class BrowserPool:
    """Singleton browser pool — one Chromium instance, multiple pages."""

    def __init__(self):
        self._browser = None
        self._playwright = None
        self._lock = Lock()
        self._ua = random.choice(_USER_AGENTS)

    def _ensure(self):
        if self._browser and self._browser.is_connected():
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
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
            ],
        )

    def get_page(self):
        """Return a new Page with stealth patches applied."""
        with self._lock:
            try:
                self._ensure()
            except Exception:
                # Browser may be corrupted, force restart
                self.close()
                self._ensure()
            ctx = self._browser.new_context(
                user_agent=self._ua,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
                java_script_enabled=True,
                bypass_csp=True,
            )
            # Inject stealth before every navigation
            ctx.add_init_script(_STEALTH_JS)
            return ctx.new_page()

    def close(self):
        """Shut down browser + Playwright (idempotent)."""
        with self._lock:
            if self._browser:
                with suppress(Exception):
                    self._browser.close()
                self._browser = None
            if self._playwright:
                with suppress(Exception):
                    self._playwright.stop()
                self._playwright = None

    def reset(self):
        """Force-close and re-initialize the browser (e.g. after crashes)."""
        self.close()


_pool = BrowserPool()
atexit.register(_pool.close)


def get_browser_pool() -> BrowserPool:
    """Return the global BrowserPool singleton."""
    return _pool


def close_browser():
    """Shut down the global browser pool."""
    _pool.close()


def render_js(url: str, timeout_ms: int = 45_000) -> str:
    """Render a URL in headless Chromium and return the final HTML.

    Reuses the global browser pool (no browser restart per URL).
    """
    page = _pool.get_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        # Wait a bit for JS to settle (SPAs, lazy loads)
        page.wait_for_timeout(2000)
        # Auto-scroll to trigger lazy-loaded content
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)
        return page.content()
    finally:
        page.close()


def render_js_multi(
    urls: list[str],
    timeout_ms: int = 30_000,
    max_pages: int = 6,
    delay_s: float = 0.5,
) -> dict[str, str]:
    """Render multiple URLs concurrently using multiple browser tabs.

    Returns ``{url: html}`` for successfully rendered URLs.
    Opens up to ``max_pages`` tabs in parallel, rotating through the URL list.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, str] = {}
    errors: list[str] = []

    def _render_one(url: str) -> tuple[str, str]:
        page = _pool.get_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1500)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
            return url, page.content()
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            return url, ""
        finally:
            page.close()

    with ThreadPoolExecutor(max_workers=max_pages) as pool:
        futures = {pool.submit(_render_one, u): u for u in urls}
        for fut in as_completed(futures):
            url, html = fut.result()
            if html:
                results[url] = html
            if delay_s > 0:
                time.sleep(delay_s)

    return results


def scroll_to_bottom(page, max_scrolls: int = 20, wait_ms: int = 1500) -> None:
    """Auto-scroll a page to trigger lazy-loading (infinite scroll, etc.)."""
    prev_height = 0
    for _ in range(max_scrolls):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(wait_ms)
        height = page.evaluate("document.body.scrollHeight")
        if height == prev_height:
            break
        prev_height = height


def wait_for_jobs(page, selector: str = "[data-job-id], .job-card, .job-listing, li.job",
                  timeout_ms: int = 15_000) -> bool:
    """Wait for job listing elements to appear on the page."""
    try:
        page.wait_for_selector(selector, timeout=timeout_ms)
        return True
    except Exception:
        return False
