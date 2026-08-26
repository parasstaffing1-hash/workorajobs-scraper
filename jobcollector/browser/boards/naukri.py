"""Naukri.com India job board browser scraper."""
from __future__ import annotations

import re
import time
from urllib.parse import quote, urljoin

from ..engine import auto_scroll, get_pool, goto, wait_for_any
from ..scraper import BaseBoardScraper, ScrapedJob, _clean, _extract


class NaukriScraper(BaseBoardScraper):
    name = "naukri"
    base_url = "https://www.naukri.com"

    def get_search_urls(self) -> list[str]:
        urls = []
        for kw in self.keywords:
            slug = kw.replace(" ", "-").lower()
            urls.append(f"{self.base_url}/{slug}-jobs?experience=0to5&sort=date")
        return urls

    def _get_wait_selectors(self) -> list[str]:
        return [
            ".srp-cardTuple",
            ".tuple-wrapper",
            "[class*='jobTuple']",
            ".jobTuple",
        ]

    def parse_page(self, html: str, page, url: str) -> list[ScrapedJob]:
        from parsel import Selector
        sel = Selector(text=html)
        jobs = []

        cards = sel.css(
            ".srp-cardTuple, "
            ".tuple-wrapper, "
            "[class*='jobTuple'], "
            ".jobTuple"
        )

        for card in cards:
            if len(jobs) + len(self._seen) >= self.max_items:
                break

            title = _extract(card, ".title, [class*='title'] a, .desEmp a")
            if not title:
                continue

            href = _extract(card, ".title@href, [class*='title'] a@href")
            if not href or href in self._seen:
                continue
            if not href.startswith("http"):
                href = urljoin(self.base_url, href)

            company = _extract(card, ".compName, .companyName, [class*='company']")
            location = _extract(card, ".locWdth, .location, [class*='location']")
            salary = _extract(card, ".salary, [class*='salary']")
            posted = _extract(card, ".date, [class*='posted']")

            tags = []
            for tag_el in card.css(".skill, [class*='skill']"):
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
                source="naukri",
            ))

        return jobs

    def has_next_page(self, html: str, page) -> str | None:
        from parsel import Selector
        sel = Selector(text=html)
        next_link = sel.css("a[title='Next']::attr(href), .pagination-next a::attr(href)")
        if next_link:
            href = next_link.get()
            if not href.startswith("http"):
                href = urljoin(self.base_url, href)
            return href
        return None
