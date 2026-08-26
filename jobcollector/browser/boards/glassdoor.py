"""Glassdoor browser scraper."""
from __future__ import annotations

import re
import time
from urllib.parse import quote, urljoin

from ..engine import auto_scroll, get_pool, goto, wait_for_any
from ..scraper import BaseBoardScraper, ScrapedJob, _clean, _extract


class GlassdoorScraper(BaseBoardScraper):
    name = "glassdoor"
    base_url = "https://www.glassdoor.com"

    def get_search_urls(self) -> list[str]:
        urls = []
        for kw in self.keywords:
            slug = kw.replace(" ", "-").lower()
            urls.append(
                f"{self.base_url}/Job/{slug}-jobs-SRCH_KO0,{len(kw)}.htm"
            )
        return urls

    def _get_wait_selectors(self) -> list[str]:
        return [
            "[data-test='job-link']",
            ".JobCard_jobCardContainer__arQkV",
            "[class*='JobCard']",
            ".job-listing",
        ]

    def parse_page(self, html: str, page, url: str) -> list[ScrapedJob]:
        from parsel import Selector
        sel = Selector(text=html)
        jobs = []

        # Glassdoor uses data-test attributes
        cards = sel.css(
            "[data-test='job-link'], "
            "[class*='JobCard'], "
            ".job-listing, "
            ".EmployerCard_employerCard__n3Mj4"
        )
        if not cards:
            # Try to find job cards by structure
            cards = sel.css("li[id*='job-']")

        for card in cards:
            if len(jobs) + len(self._seen) >= self.max_items:
                break

            # Title — Glassdoor uses various layouts
            title = _extract(card, "[data-test='job-title']")
            if not title:
                title = _extract(card, "a[data-test='job-link']")
            if not title:
                title = _extract(card, "h2, h3, [class*='title']")
            if not title:
                continue

            # URL
            href = _extract(card, "a[data-test='job-link']@href")
            if not href:
                href = _extract(card, "a@href")
            if href and not href.startswith("http"):
                href = urljoin(self.base_url, href)
            if not href or href in self._seen:
                continue

            # Company
            company = _extract(card, "[data-test='employer-short-name']")
            if not company:
                company = _extract(card, "[class*='employer'], [class*='company']")

            # Location
            location = _extract(card, "[data-test='emp-location']")
            if not location:
                location = _extract(card, "[class*='location']")

            # Salary
            salary = _extract(card, "[class*='salary'], [data-test*='salary']")

            # Rating
            rating = _extract(card, "[class*='rating']")

            jobs.append(ScrapedJob(
                title=title,
                company=company,
                url=href,
                location=location,
                salary=salary,
                source="glassdoor",
            ))

        return jobs

    def has_next_page(self, html: str, page) -> str | None:
        from parsel import Selector
        sel = Selector(text=html)
        next_link = sel.css("a[data-test='pagination-next']::attr(href)")
        if next_link:
            return urljoin(self.base_url, next_link.get())

        try:
            btn = page.query_selector("button[aria-label='Next']")
            if btn and btn.is_visible():
                btn.click()
                time.sleep(2)
                return page.url
        except Exception:
            pass
        return None
