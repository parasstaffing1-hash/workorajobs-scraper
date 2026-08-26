"""LinkedIn public job search browser scraper."""
from __future__ import annotations

import re
import time
from urllib.parse import quote, urljoin

from ..engine import auto_scroll, get_pool, goto, wait_for_any
from ..scraper import BaseBoardScraper, ScrapedJob, _clean, _extract


class LinkedInScraper(BaseBoardScraper):
    name = "linkedin"
    base_url = "https://www.linkedin.com"

    def get_search_urls(self) -> list[str]:
        urls = []
        for kw in self.keywords:
            encoded = quote(kw)
            urls.append(
                f"{self.base_url}/jobs/search/?keywords={encoded}&location=United+States&sortBy=DD"
            )
            urls.append(
                f"{self.base_url}/jobs/search/?keywords={encoded}&location=United+Kingdom&sortBy=DD"
            )
        return urls

    def _get_wait_selectors(self) -> list[str]:
        return [
            ".base-card",
            ".job-search-card",
            ".jobs-search-results__list-item",
            "[class*='job-card']",
        ]

    def parse_page(self, html: str, page, url: str) -> list[ScrapedJob]:
        from parsel import Selector
        sel = Selector(text=html)
        jobs = []

        # LinkedIn card selectors
        cards = sel.css(
            ".base-card, "
            ".job-search-card, "
            ".jobs-search-results__list-item"
        )

        for card in cards:
            if len(jobs) + len(self._seen) >= self.max_items:
                break

            # Title
            title = _extract(card, ".base-search-card__title") or _extract(card, ".job-search-card__title")
            if not title:
                continue

            # URL
            href = _extract(card, ".base-card__full-link@href") or _extract(card, "a.job-search-card__logo-link@href")
            if not href or href in self._seen:
                continue
            # Clean tracking params
            href = re.sub(r'\?.*$', '', href)

            # Company
            company = _extract(card, ".base-search-card__subtitle") or _extract(card, ".job-search-card__company-name")

            # Location
            location = _extract(card, ".job-search-card__location") or _extract(card, ".job-search-card__bullet")

            # Posted time
            posted_text = _extract(card, "time@datetime") or _extract(card, ".job-search-card__listdate")

            jobs.append(ScrapedJob(
                title=title,
                company=company,
                url=href,
                location=location,
                source="linkedin",
            ))

        return jobs

    def has_next_page(self, html: str, page) -> str | None:
        """LinkedIn uses JS pagination — click next button."""
        try:
            btn = page.query_selector("button[aria-label='View next page']")
            if btn and btn.is_visible():
                btn.click()
                time.sleep(2)
                return page.url
        except Exception:
            pass

        # Check for "See more jobs" button
        try:
            btn = page.query_selector("button.jobs-search-results__list-expand-btn")
            if btn and btn.is_visible():
                btn.click()
                time.sleep(2)
                return page.url
        except Exception:
            pass

        return None
