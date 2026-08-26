"""Indeed.com browser scraper."""
from __future__ import annotations

import re
import time
from urllib.parse import quote, urljoin

from ..engine import auto_scroll, extract_json_ld, get_pool, goto, wait_for_any
from ..scraper import BaseBoardScraper, ScrapedJob, _clean, _extract


class IndeedScraper(BaseBoardScraper):
    name = "indeed"
    base_url = "https://www.indeed.com"

    def get_search_urls(self) -> list[str]:
        urls = []
        for kw in self.keywords:
            encoded = quote(kw)
            urls.append(
                f"{self.base_url}/jobs?q={encoded}&l=United+States&sort=date"
            )
            urls.append(
                f"{self.base_url}/jobs?q={encoded}&l=United+Kingdom&sort=date"
            )
        return urls

    def _get_wait_selectors(self) -> list[str]:
        return [
            ".job_seen_beacon",
            ".jobsearch-ResultsList",
            "[class*='jobResults']",
            ".resultContent",
        ]

    def parse_page(self, html: str, page, url: str) -> list[ScrapedJob]:
        from parsel import Selector
        sel = Selector(text=html)
        jobs = []

        # Indeed uses multiple card layouts
        cards = sel.css(".job_seen_beacon, .resultContent, [class*='jobCard']")
        if not cards:
            # Try alternative layout
            cards = sel.css("div.jobsearch-ResultsList > div")

        for card in cards:
            if len(jobs) + len(self._seen) >= self.max_items:
                break

            # Title
            title = _extract(card, ".jobTitle a@title") or _extract(card, ".jobTitle span")
            if not title:
                title = _extract(card, "h2.jobTitle, h2 a")
            if not title:
                continue

            # URL
            href = _extract(card, ".jobTitle a@href") or _extract(card, "h2 a@href")
            url_full = urljoin(self.base_url, href) if href and not href.startswith("http") else href
            if not url_full or url_full in self._seen:
                continue

            # Company
            company = _extract(card, ".companyName") or _extract(card, "[data-testid='company-name']")
            if not company:
                company = _extract(card, ".company")

            # Location
            location = _extract(card, ".companyLocation") or _extract(card, "[data-testid='text-location']")
            if not location:
                location = _extract(card, ".location")

            # Salary
            salary = _extract(card, ".salary-snippet-container, [data-testid='attribute_snippet_testid']")

            # Snippet / description
            snippet = _extract(card, ".job-snippet, .jobCardShelfContainer")

            jobs.append(ScrapedJob(
                title=title,
                company=company,
                url=url_full,
                location=location,
                description=snippet,
                salary=salary,
                source="indeed",
            ))

        return jobs

    def has_next_page(self, html: str, page) -> str | None:
        """Click or find next page URL."""
        from parsel import Selector
        sel = Selector(text=html)

        # Try next button
        next_link = sel.css("a[data-testid='pagination-page-next']::attr(href)")
        if next_link:
            return urljoin(self.base_url, next_link.get())

        # Try "Next" text link
        for a in sel.css("a"):
            text = _clean(a.root.text_content()) if a.root is not None else ""
            if text.lower() == "next":
                href = a.attrib.get("href", "")
                if href:
                    return urljoin(self.base_url, href)

        # Try clicking the next button
        try:
            btn = page.query_selector("a[data-testid='pagination-page-next']")
            if btn and btn.is_visible():
                btn.click()
                time.sleep(2)
                return page.url
        except Exception:
            pass

        return None
