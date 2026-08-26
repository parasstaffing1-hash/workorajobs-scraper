"""Dice.com tech job board browser scraper."""
from __future__ import annotations

import re
import time
from urllib.parse import quote, urljoin

from ..engine import auto_scroll, get_pool, goto, wait_for_any
from ..scraper import BaseBoardScraper, ScrapedJob, _clean, _extract


class DiceScraper(BaseBoardScraper):
    name = "dice"
    base_url = "https://www.dice.com"

    def get_search_urls(self) -> list[str]:
        urls = []
        for kw in self.keywords:
            encoded = quote(kw)
            urls.append(f"{self.base_url}/jobs?q={encoded}&country=United+States&sort=date")
        return urls

    def _get_wait_selectors(self) -> list[str]:
        return [
            ".card-body",
            "[class*='job-card']",
            ".job-tile",
            "dhi-search-card",
        ]

    def parse_page(self, html: str, page, url: str) -> list[ScrapedJob]:
        from parsel import Selector
        sel = Selector(text=html)
        jobs = []

        cards = sel.css(
            ".card-body, "
            "[class*='job-card'], "
            "dhi-search-card, "
            ".job-tile"
        )

        for card in cards:
            if len(jobs) + len(self._seen) >= self.max_items:
                break

            title = _extract(card, "h5 a, .card-title a, [class*='title'] a")
            if not title:
                continue

            href = _extract(card, "h5 a@href, .card-title a@href")
            if not href or href in self._seen:
                continue
            if not href.startswith("http"):
                href = urljoin(self.base_url, href)

            company = _extract(card, ".company-name, [class*='company']")
            location = _extract(card, ".card-location, [class*='location']")
            salary = _extract(card, ".salary, [class*='salary']")
            posted = _extract(card, ".posted-date, [class*='posted']")

            tags = []
            for tag_el in card.css(".badge, [class*='skill']"):
                tag = _clean(tag_el.root.text_content()) if tag_el.root is not None else ""
                if tag:
                    tags.append(tag)

            jobs.append(ScrapedJob(
                title=title,
                company=company,
                url=href,
                location=location,
                salary=salary,
                tags=tags,
                source="dice",
            ))

        return jobs

    def has_next_page(self, html: str, page) -> str | None:
        from parsel import Selector
        sel = Selector(text=html)
        next_link = sel.css("a[aria-label='Next Page']::attr(href), .pagination-next a::attr(href)")
        if next_link:
            return urljoin(self.base_url, next_link.get())
        return None
