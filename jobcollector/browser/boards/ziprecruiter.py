"""ZipRecruiter browser scraper."""
from __future__ import annotations

import re
import time
from urllib.parse import quote, urljoin

from ..engine import auto_scroll, get_pool, goto, wait_for_any
from ..scraper import BaseBoardScraper, ScrapedJob, _clean, _extract


class ZipRecruiterScraper(BaseBoardScraper):
    name = "ziprecruiter"
    base_url = "https://www.ziprecruiter.com"

    def get_search_urls(self) -> list[str]:
        urls = []
        for kw in self.keywords:
            encoded = quote(kw)
            urls.append(f"{self.base_url}/jobs-search?search={encoded}&location=United+States&days=7")
        return urls

    def _get_wait_selectors(self) -> list[str]:
        return [
            ".job_content",
            "[class*='job-card']",
            ".job-result",
            "article",
        ]

    def parse_page(self, html: str, page, url: str) -> list[ScrapedJob]:
        from parsel import Selector
        sel = Selector(text=html)
        jobs = []

        cards = sel.css(
            ".job_content, "
            "[class*='job-card'], "
            ".job-result, "
            "article.job_result"
        )

        for card in cards:
            if len(jobs) + len(self._seen) >= self.max_items:
                break

            title = _extract(card, "h2 a, .job_title a, [class*='title'] a")
            if not title:
                continue

            href = _extract(card, "h2 a@href, .job_title a@href, [class*='title'] a@href")
            if not href or href in self._seen:
                continue

            company = _extract(card, ".company_name, [class*='company']")
            location = _extract(card, ".location, [class*='location']")
            salary = _extract(card, ".salary, [class*='salary']")
            snippet = _extract(card, ".job_snippet, [class*='snippet']")

            jobs.append(ScrapedJob(
                title=title,
                company=company,
                url=href,
                location=location,
                salary=salary,
                description=snippet,
                source="ziprecruiter",
            ))

        return jobs
