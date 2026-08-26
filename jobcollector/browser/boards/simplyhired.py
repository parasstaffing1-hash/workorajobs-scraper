"""SimplyHired job aggregator browser scraper."""
from __future__ import annotations

import re
import time
from urllib.parse import quote, urljoin

from ..engine import auto_scroll, get_pool, goto, wait_for_any
from ..scraper import BaseBoardScraper, ScrapedJob, _clean, _extract


class SimplyHiredScraper(BaseBoardScraper):
    name = "simplyhired"
    base_url = "https://www.simplyhired.com"

    def get_search_urls(self) -> list[str]:
        urls = []
        for kw in self.keywords:
            slug = kw.replace(" ", "-").lower()
            urls.append(f"{self.base_url}/search?q={quote(kw)}&l=United+States&fdb=7")
        return urls

    def _get_wait_selectors(self) -> list[str]:
        return [
            "[data-testid='searchSerpJob']",
            ".SerpJob",
            ".css-0",
        ]

    def parse_page(self, html: str, page, url: str) -> list[ScrapedJob]:
        from parsel import Selector
        sel = Selector(text=html)
        jobs = []

        cards = sel.css(
            "[data-testid='searchSerpJob'], "
            ".SerpJob, "
            ".jobposting"
        )

        for card in cards:
            if len(jobs) + len(self._seen) >= self.max_items:
                break

            title = _extract(card, "h2 a, [class*='postingJobTitle'] a")
            if not title:
                continue

            href = _extract(card, "h2 a@href, [class*='postingJobTitle'] a@href")
            if not href or href in self._seen:
                continue
            if not href.startswith("http"):
                href = urljoin(self.base_url, href)

            company = _extract(card, "[class*='company'], .company")
            location = _extract(card, "[class*='location'], .location")
            salary = _extract(card, "[class*='salary'], .salary")

            jobs.append(ScrapedJob(
                title=title,
                company=company,
                url=href,
                location=location,
                salary=salary,
                source="simplyhired",
            ))

        return jobs
