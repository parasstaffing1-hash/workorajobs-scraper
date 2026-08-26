"""Google Jobs browser scraper — aggregates from multiple boards."""
from __future__ import annotations

import re
import time
from urllib.parse import quote, urljoin

from ..engine import auto_scroll, extract_jobs_from_json_ld, get_pool, goto, wait_for_any
from ..scraper import BaseBoardScraper, ScrapedJob, _clean, _extract


class GoogleJobsScraper(BaseBoardScraper):
    name = "google_jobs"
    base_url = "https://www.google.com/search"

    def get_search_urls(self) -> list[str]:
        urls = []
        for kw in self.keywords:
            encoded = quote(f"{kw} jobs")
            urls.append(
                f"{self.base_url}?q={encoded}&ibp=htl;jobs"
            )
        return urls

    def _get_wait_selectors(self) -> list[str]:
        return [
            ".iFjolb",
            ".job-listing",
            "[data-ved]",
            ".xVvCNb",
            ".wlzhwb",
        ]

    def parse_page(self, html: str, page, url: str) -> list[ScrapedJob]:
        from parsel import Selector
        sel = Selector(text=html)
        jobs = []

        # Google Jobs embeds data in structured HTML
        cards = sel.css(".iFjolb, .job-listing, .xVvCNb, [class*='resultContent']")
        if not cards:
            # Try extracting from the page's embedded data
            return self._parse_google_jobs_json(page, url)

        for card in cards:
            if len(jobs) + len(self._seen) >= self.max_items:
                break

            title = _extract(card, ".BjJfJf, .title, h3")
            if not title:
                continue

            company = _extract(card, ".vNEEBe, .company")
            location = _extract(card, ".Qk80Jf, .location")
            salary = _extract(card, ".YkS7Ve, .salary")
            posted = _extract(card, ".fBEzgb, .date")
            source = _extract(card, ".nJlQNd, .source")
            snippet = _extract(card, ".Yj480c, .snippet")

            # URL - Google Jobs links to the source
            href = _extract(card, "a@href")
            if not href or href in self._seen:
                # Use a constructed Google search URL as fallback
                href = url

            jobs.append(ScrapedJob(
                title=title,
                company=company,
                url=href,
                location=location,
                salary=salary,
                description=snippet,
                tags=[source] if source else [],
                source="google_jobs",
            ))

        # Also try JSON-LD extraction
        ld_jobs = extract_jobs_from_json_ld(html)
        for ld in ld_jobs:
            title = _clean(ld.get("title", ""))
            if not title:
                continue
            org = ld.get("hiringOrganization", {})
            company = org.get("name", "") if isinstance(org, dict) else ""
            loc = ld.get("jobLocation", {})
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr = loc.get("address", {}) if isinstance(loc, dict) else {}
            location = ""
            if isinstance(addr, dict):
                parts = [addr.get("addressLocality"), addr.get("addressRegion"),
                         addr.get("addressCountry")]
                location = ", ".join(p for p in parts if p)

            job_url = ld.get("url", url)
            if job_url in self._seen:
                continue

            jobs.append(ScrapedJob(
                title=title,
                company=company,
                url=job_url,
                location=location,
                description=_clean(ld.get("description", "")),
                source="google_jobs",
            ))

        return jobs

    def _parse_google_jobs_json(self, page, url: str) -> list[ScrapedJob]:
        """Try to extract job data from Google's embedded JS."""
        jobs = []
        try:
            # Google embeds job data in script tags
            data = page.evaluate("""
                () => {
                    const results = [];
                    document.querySelectorAll('.iFjolb, .job-listing, .xVvCNb, .resultContent, [data-ved]').forEach(el => {
                        const title = el.querySelector('.BjJfJf, h3, .title');
                        const company = el.querySelector('.vNEEBe, .company');
                        const location = el.querySelector('.Qk80Jf, .location');
                        const link = el.querySelector('a');
                        if (title) {
                            results.push({
                                title: title.textContent.trim(),
                                company: company ? company.textContent.trim() : '',
                                location: location ? location.textContent.trim() : '',
                                url: link ? link.href : '',
                            });
                        }
                    });
                    return results;
                }
            """)
            for item in (data or []):
                if item.get("title") and item.get("url") not in self._seen:
                    jobs.append(ScrapedJob(
                        title=item["title"],
                        company=item.get("company", ""),
                        url=item.get("url", ""),
                        location=item.get("location", ""),
                        source="google_jobs",
                    ))
        except Exception:
            pass
        return jobs
