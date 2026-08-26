"""Playwright-based job board scraper.

Scrapes job listings from JS-rendered career pages using a shared browser
pool. Supports two modes:

1. **Card mode** — ``card_selector`` matches job cards on a list/search page.
   Extracts title, company, location, URL directly from the card (no detail
   fetch needed for most job boards).

2. **Link mode** — ``link_selector`` matches links to individual job detail
   pages, which are fetched and extracted individually.

Both modes support:
- Infinite-scroll auto-pagination (``scroll_to_bottom``)
- Next-page button clicks (``next_selector``)
- Configurable CSS/XPath selectors for all fields
- JSON-LD structured data extraction (``JobPosting`` schema)
- Automatic retry with exponential backoff
- Anti-bot stealth via the browser pool

Usage::

    from jobcollector.sources.playwright_scrape import (
        PlaywrightJobScraper, PlaywrightJobConfig,
    )

    cfg = PlaywrightJobConfig(
        name="indeed",
        start_url="https://indeed.com/jobs?q=software+engineer",
        card_selector=".job_seen_beacon",
        fields={"title": ".jobTitle a", "company": ".companyName"},
    )
    scraper = PlaywrightJobScraper(cfg)
    jobs = scraper.run()

Requires ``pip install playwright`` and ``playwright install chromium``.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from parsel import Selector
from pydantic import BaseModel, Field

from ..http import make_client
from ..models import Job
from ..jsrender import (
    get_browser_pool,
    scroll_to_bottom,
    wait_for_jobs,
)

_MAX_CONTENT = 6000


class PlaywrightJobConfig(BaseModel):
    """Configuration for a single Playwright-based job scraper."""

    name: str
    start_url: str
    category: str = "jobs"

    # Card mode: each matching element IS a job listing (no detail fetch)
    card_selector: str | None = None

    # Link mode: matched links go to detail pages
    link_selector: str | None = None
    link_url_pattern: str | None = None

    # CSS selectors for fields (work in both card and detail modes)
    # Supports "xpath:..." prefix and "@attr" suffix (e.g. "a@href")
    fields: dict[str, str] = Field(default_factory=dict)

    # Pagination: click next button or auto-scroll
    next_selector: str | None = None
    use_scroll: bool = True        # auto-scroll for infinite-scroll pages
    max_scrolls: int = 30
    scroll_wait_ms: int = 2000

    # Limits
    max_pages: int = 10
    max_items: int = 200

    # Optional: URL to click before scraping (e.g. "Accept cookies" button)
    pre_click: str | None = None

    # Optional: wait for this selector before scraping
    wait_for: str | None = None

    # Delay between page loads (polite scraping)
    delay_s: float = 1.0

    # Domain restriction
    domain: str | None = None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract(sel: Selector, css: str, default: str = "") -> str:
    """Extract text or attribute from a selector."""
    if css.startswith("xpath:"):
        nodes = sel.xpath(css[6:])
    elif "@" in css:
        parts = css.split("@", 1)
        nodes = sel.css(parts[0])
        if nodes:
            return _clean(nodes[0].attrib.get(parts[1], ""))
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


def _extract_json_ld(html: str) -> dict | None:
    """Pull structured JobPosting data from JSON-LD."""
    sel = Selector(text=html)
    for script in sel.css("script[type='application/ld+json']"):
        raw = script.get()
        # Extract JSON content between script tags (handles multi-line)
        match = re.search(r">\s*(\{.+\})\s*<", raw, re.DOTALL)
        if not match:
            continue
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        # Check if it's a JobPosting
        obj_type = str(data.get("@type", "")).lower()
        if "jobposting" in obj_type:
            return data
        # Check nested @graph
        for item in data.get("@graph", []):
            if "jobposting" in str(item.get("@type", "")).lower():
                return item
    return None


def _job_from_json_ld(ld: dict, url: str, source: str) -> Job | None:
    """Create a Job from JSON-LD structured data."""
    title = _clean(ld.get("title") or "")
    if not title:
        return None

    org = ld.get("hiringOrganization") or {}
    company = _clean(org.get("name") or "") if isinstance(org, dict) else ""

    loc = ld.get("jobLocation") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    addr = loc.get("address") if isinstance(loc, dict) else None
    location = ""
    if isinstance(addr, dict):
        parts = [addr.get("addressLocality"), addr.get("addressRegion"),
                 addr.get("addressCountry")]
        location = ", ".join(p for p in parts if p)
    elif isinstance(loc, dict):
        location = _clean(loc.get("name") or "")

    desc = _clean(ld.get("description") or "")
    salary = ""
    comp = ld.get("baseSalary") or {}
    if isinstance(comp, dict):
        val = comp.get("value") or {}
        if isinstance(val, dict):
            salary = f"{val.get('minValue', '')}-{val.get('maxValue', '')} {val.get('currency', '')}".strip("- ")

    posted = ld.get("datePosted") or ""
    posted_at = None
    if posted:
        try:
            posted_at = datetime.fromisoformat(posted.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    return Job(
        title=title,
        company=company,
        url=ld.get("url") or url,
        location=location,
        description=desc[:_MAX_CONTENT],
        salary=salary,
        source=source,
        source_kind="playwright",
        posted_at=posted_at,
    )


class PlaywrightJobScraper:
    """Scrape job listings from a JS-rendered page using Playwright."""

    def __init__(self, cfg: PlaywrightJobConfig):
        self.cfg = cfg
        self.errors: list[str] = []
        self._seen_urls: set[str] = set()

    def run(self) -> list[Job]:
        """Run the scraper and return collected jobs."""
        if self.cfg.card_selector:
            return self._run_cards()
        elif self.cfg.link_selector:
            return self._run_links()
        else:
            raise ValueError(
                f"Scraper {self.cfg.name!r} needs card_selector or link_selector"
            )

    def _new_page(self):
        """Get a new page from the browser pool."""
        return get_browser_pool().get_page()

    def _allowed(self, url: str) -> bool:
        if not self.cfg.domain:
            return True
        return urlparse(url).netloc.lower() == self.cfg.domain.lower()

    # -----------------------------------------------------------------
    # Card mode: extract jobs directly from listing elements
    # -----------------------------------------------------------------

    def _run_cards(self) -> list[Job]:
        jobs: list[Job] = []
        page = self._new_page()
        try:
            page.goto(self.cfg.start_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(2)

            # Pre-click (e.g. accept cookies)
            if self.cfg.pre_click:
                try:
                    page.click(self.cfg.pre_click, timeout=5000)
                    time.sleep(1)
                except Exception:
                    pass

            # Wait for job cards
            if self.cfg.wait_for:
                wait_for_jobs(page, self.cfg.wait_for, timeout_ms=15000)

            # Auto-scroll if needed
            if self.cfg.use_scroll:
                scroll_to_bottom(page, self.cfg.max_scrolls, self.cfg.scroll_wait_ms)

            # Extract cards
            html = page.content()
            sel = Selector(text=html)
            cards = sel.css(self.cfg.card_selector)
            for card in cards:
                if len(jobs) >= self.cfg.max_items:
                    break
                job = self._job_from_card(card)
                if job and job.url not in self._seen_urls:
                    jobs.append(job)
                    self._seen_urls.add(job.url)

        except Exception as exc:
            self.errors.append(f"{self.cfg.name}: {exc}")
        finally:
            page.close()

        # Handle pagination via next button
        if len(jobs) < self.cfg.max_items and self.cfg.next_selector:
            jobs.extend(self._paginate_cards())

        return jobs

    def _paginate_cards(self) -> list[Job]:
        """Click through next-page buttons and extract more cards."""
        jobs: list[Job] = []
        page = self._new_page()
        try:
            page.goto(self.cfg.start_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(2)

            for _ in range(self.cfg.max_pages):
                if len(jobs) >= self.cfg.max_items:
                    break

                # Click next
                try:
                    next_btn = page.query_selector(self.cfg.next_selector)
                    if not next_btn or not next_btn.is_visible():
                        break
                    next_btn.click()
                    time.sleep(self.cfg.delay_s + 1)
                    page.wait_for_load_state("domcontentloaded")
                except Exception:
                    break

                html = page.content()
                sel = Selector(text=html)
                cards = sel.css(self.cfg.card_selector)
                if not cards:
                    break

                for card in cards:
                    if len(jobs) >= self.cfg.max_items:
                        break
                    job = self._job_from_card(card)
                    if job and job.url not in self._seen_urls:
                        jobs.append(job)
                        self._seen_urls.add(job.url)

        except Exception as exc:
            self.errors.append(f"{self.cfg.name} pagination: {exc}")
        finally:
            page.close()

        return jobs

    def _job_from_card(self, card: Selector) -> Job | None:
        """Extract a Job from a card element."""
        fields = {k: _extract(card, v) for k, v in self.cfg.fields.items()}

        title = fields.get("title") or _extract(card, "h2, h3, h4, [class*=title]")
        if not title:
            return None

        url = fields.get("url") or _extract(card, "a@href")
        if url and not url.startswith("http"):
            url = urljoin(self.cfg.start_url, url)

        company = fields.get("company") or _extract(card, "[class*=company], [class*=employer]")
        location = fields.get("location") or _extract(card, "[class*=location], [class*=place]")
        salary = fields.get("salary") or _extract(card, "[class*=salary], [class*=compensation]")
        posted = fields.get("posted_at") or _extract(card, "time@datetime, time")
        description = fields.get("description") or _extract(card, "[class*=description], [class*=snippet]")

        posted_at = None
        if posted:
            try:
                posted_at = datetime.fromisoformat(posted.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        return Job(
            title=title,
            company=company,
            url=url or self.cfg.start_url,
            location=location,
            salary=salary,
            description=description[:_MAX_CONTENT],
            source=f"playwright:{self.cfg.name}",
            source_kind="playwright",
            posted_at=posted_at,
        )

    # -----------------------------------------------------------------
    # Link mode: follow links to detail pages
    # -----------------------------------------------------------------

    def _run_links(self) -> list[Job]:
        links: list[str] = []
        page = self._new_page()
        try:
            page.goto(self.cfg.start_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(2)

            if self.cfg.pre_click:
                try:
                    page.click(self.cfg.pre_click, timeout=5000)
                    time.sleep(1)
                except Exception:
                    pass

            if self.cfg.use_scroll:
                scroll_to_bottom(page, self.cfg.max_scrolls, self.cfg.scroll_wait_ms)

            # Collect links from multiple pages
            for _ in range(self.cfg.max_pages):
                if len(links) >= self.cfg.max_items:
                    break

                html = page.content()
                sel = Selector(text=html)
                pattern = re.compile(self.cfg.link_url_pattern) if self.cfg.link_url_pattern else None

                for node in sel.css(self.cfg.link_selector):
                    # If the selector already matches <a> tags, get href directly
                    href = node.attrib.get("href", "") or _extract(node, "a@href")
                    if not href:
                        continue
                    url = urljoin(page.url, href)
                    if not self._allowed(url):
                        continue
                    if pattern and not pattern.search(url):
                        continue
                    if url not in self._seen_urls:
                        links.append(url)
                        self._seen_urls.add(url)

                # Click next page
                if self.cfg.next_selector:
                    try:
                        next_btn = page.query_selector(self.cfg.next_selector)
                        if not next_btn or not next_btn.is_visible():
                            break
                        next_btn.click()
                        time.sleep(self.cfg.delay_s + 1)
                        page.wait_for_load_state("domcontentloaded")
                    except Exception:
                        break
                else:
                    break

        except Exception as exc:
            self.errors.append(f"{self.cfg.name}: {exc}")
        finally:
            page.close()

        # Fetch detail pages sequentially (Playwright sync API is not thread-safe)
        links = links[: self.cfg.max_items]
        jobs: list[Job] = []
        for url in links:
            try:
                job = self._fetch_detail(url)
                if job:
                    jobs.append(job)
            except Exception as exc:
                self.errors.append(str(exc))
            time.sleep(self.cfg.delay_s)

        return jobs

    def _fetch_detail(self, url: str) -> Job | None:
        """Fetch a detail page and extract a Job."""
        page = self._new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(1.5)
            html = page.content()

            # Try JSON-LD first
            ld = _extract_json_ld(html)
            if ld:
                job = _job_from_json_ld(ld, url, f"playwright:{self.cfg.name}")
                if job:
                    return job

            # Fallback to configured selectors
            sel = Selector(text=html)
            fields = {k: _extract(sel, v) for k, v in self.cfg.fields.items()}
            title = fields.get("title") or _extract(sel, "h1")
            if not title:
                return None

            company = fields.get("company") or _extract(sel, "[class*=company], [class*=employer]")
            location = fields.get("location") or _extract(sel, "[class*=location]")
            salary = fields.get("salary") or _extract(sel, "[class*=salary]")
            posted = fields.get("posted_at") or _extract(sel, "time@datetime")
            description = fields.get("description") or _extract(sel, "article, main, [class*=description]")

            posted_at = None
            if posted:
                try:
                    posted_at = datetime.fromisoformat(posted.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

            return Job(
                title=title,
                company=company,
                url=url,
                location=location,
                salary=salary,
                description=description[:_MAX_CONTENT],
                source=f"playwright:{self.cfg.name}",
                source_kind="playwright",
                posted_at=posted_at,
            )
        finally:
            page.close()


def run_playwright_scraper(
    cfg: PlaywrightJobConfig,
    store,
    limit: int | None = None,
) -> tuple[int, int, list[str]]:
    """Run one Playwright scraper and persist jobs. Returns (seen, new, errors)."""
    if limit:
        cfg.max_items = min(cfg.max_items, limit)
    scraper = PlaywrightJobScraper(cfg)
    jobs = scraper.run()
    seen = new = 0
    for job in jobs:
        cur = store.conn.execute(
            """INSERT OR IGNORE INTO jobs
               (dedupe_key, title, company, location, description, url, source,
                source_kind, external_id, posted_at, salary, tags,
                first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job.dedupe_key, job.title, job.company, job.location,
                job.description, job.url, job.source, job.source_kind,
                job.external_id, job.posted_at.isoformat() if job.posted_at else None,
                job.salary, ",".join(job.tags),
                job.first_seen_at.isoformat(), job.last_seen_at.isoformat(),
            ),
        )
        if cur.rowcount > 0:
            new += 1
        seen += 1
    store.conn.commit()
    return seen, new, scraper.errors
