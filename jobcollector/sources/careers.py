"""Generic best-effort crawler for company career pages.

Strategy
--------
1. Fetch the configured ``careers_url``.
2. Collect in-domain links whose URL or anchor text looks like a job posting
   (matches job keywords), skipping blog/news/etc. via ``exclude_keywords``.
3. Fetch each candidate page concurrently and extract structured data, with
   JSON-LD (``application/ld+json`` JobPosting) taking precedence, then HTML
   heuristics (h1 / og:title / meta location).

Per-company overrides live in the config: ``link_selector`` and/or
``link_pattern`` to target tricky layouts, ``max_pages`` to bound work, and
``use_js`` to render SPA pages with Playwright (requires the optional
``[js]`` extra and `playwright install chromium`).
"""
from __future__ import annotations

import json
import re
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Some career pages are served as XML (sitemaps, odd CMS output); we treat them
# as HTML anyway, which is fine for link extraction.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from ..http import make_client, retry_get
from ..models import CompanyConfig, Job

# httpx.Client is NOT safe to share across threads on Windows (native OpenSSL
# crash — access violation in ssl.read). Each worker thread gets its own client
# via thread-local storage; the caller's client is only used as a template.
#
# Concurrent DNS resolution also crashes on Windows (native getaddrinfo access
# violation), so hostnames are pre-resolved serially in the main thread first
# (populating the OS DNS cache); subsequent threaded connects reuse the cache.
_thread_local = threading.local()


def _thread_client(template: httpx.Client) -> httpx.Client:
    c = getattr(_thread_local, "client", None)
    if c is None:
        c = make_client(
            timeout=30.0,
            headers=dict(template.headers or {}),
        )
        _thread_local.client = c
    return c


def pre_resolve_dns(urls: list[str]) -> None:
    """Serially resolve all unique hostnames to warm the OS DNS cache.

    Windows crashes with a native access violation when many threads call
    getaddrinfo at the same time; resolving once here (single-threaded) makes
    the threaded crawl below safe because the results are cached.
    """
    import socket

    hosts = {urlparse(u).netloc for u in urls if u}
    for host in sorted(hosts):
        try:
            socket.getaddrinfo(host, None, family=socket.AF_INET)
        except OSError:
            pass

JOB_KEYWORD = re.compile(
    r"(job|career|vacanc|openings?|position|role|apply|hiring|intern|recruit)", re.IGNORECASE
)
SKIP_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".mp4",
    ".xml", ".json", ".css", ".js", ".ico",
)
# Job hubs / list pages (path ENDS with a hub segment) ranked last so detail
# pages like /careers/backend-engineer are crawled first.
HUB_PATH_KEYWORDS = re.compile(r"/(jobs?|careers?|openings?|positions?)/?$", re.IGNORECASE)

# Marketing / hub / nav pages that slip through the keyword filter when a detail
# page has no JSON-LD: their <h1>/<title> is a slogan or label, not a job title.
# Only consulted in the HTML-heuristic fallback; JSON-LD extraction is trusted.
BOILERPLATE_TITLE = re.compile(
    r"(^careers?$|^jobs?$|^open roles?$|^job openings?$|^company culture$|"
    r"^our culture$|^hiring process$|^recruitment$|^privacy notice$|"
    r"^recruitment privacy notice$|^navigation\b|^join our team$|^work with us$|"
    r"^apply now$|^early careers?$|^about us$|^current .* job openings?$|"
    r"^inclusion, diversity|^feel good about your work$|^benefits for real life$|"
    r"^meet .* teams?$|^we.?ll meet you where you are|^we hire for|"
    r"\bhiring process\b|opportunities are open|launch your career|"
    r"^why (join|work at) |^life at |^job details?$|^job posting$|^position details?$)",
    re.IGNORECASE,
)

_MAX_DESCRIPTION = 6000


class CareersCrawler:
    def __init__(self, client: httpx.Client, concurrency: int = 8, use_js: bool = False):
        self.client = client
        self.concurrency = concurrency
        self.use_js = use_js
        self.errors: list[str] = []

    # ------------------------------------------------------------ public API

    def crawl(self, companies: list[CompanyConfig]) -> list[Job]:
        # Companies are crawled concurrently (each company's detail pages already
        # use their own inner pool). With hundreds of companies — many of them
        # unreachable and eating retries — sequential crawling would take hours;
        # parallelism keeps a full top-1000 pass to minutes.
        #
        # Windows note: warm the OS DNS cache serially first, otherwise many
        # threads calling getaddrinfo simultaneously crash the process with a
        # native access violation (also seen in socket.create_connection).
        pre_resolve_dns([c.careers_url for c in companies])
        jobs: list[Job] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {
                pool.submit(self._crawl_company, company): company for company in companies
            }
            for fut in as_completed(futures):
                company = futures[fut]
                try:
                    jobs.extend(fut.result())
                except Exception as exc:  # one bad company must not kill the run
                    self.errors.append(f"{company.name}: {exc}")
        return jobs

    # ------------------------------------------------------------- internals

    def _fetch(self, url: str) -> httpx.Response:
        # Per-thread client — httpx.Client shared across threads crashes on
        # Windows (native access violation in ssl). DNS is pre-resolved by
        # crawl() before the pool starts, so socket.connect is thread-safe.
        client = _thread_client(self.client)
        resp = retry_get(client, url)
        resp.raise_for_status()
        return resp

    def _html(self, url: str, use_js: bool) -> str:
        if use_js:
            return self._render_js(url)
        return self._fetch(url).text

    def _render_js(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Playwright is not installed. Run `pip install 'jobcollector[js]'` "
                "and `playwright install chromium`."
            ) from exc
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=45_000)
                return page.content()
            finally:
                browser.close()

    def _crawl_company(self, company: CompanyConfig) -> list[Job]:
        try:
            html = self._html(company.careers_url, company.use_js or self.use_js)
        except httpx.HTTPError as exc:
            self.errors.append(f"{company.name}: {company.careers_url}: {exc}")
            return []
        soup = BeautifulSoup(html, "lxml")
        candidates = self._candidate_links(soup, company.careers_url, company)
        jobs: list[Job] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {
                pool.submit(self._fetch_job_page, url, company): url for url in candidates
            }
            for fut in as_completed(futures):
                url = futures[fut]
                try:
                    job = fut.result()
                except Exception as exc:  # one bad page must not kill the run
                    self.errors.append(f"{company.name}: {url}: {exc}")
                    continue
                if job is not None:
                    jobs.append(job)
        return jobs

    def _candidate_links(self, soup: BeautifulSoup, base_url: str, company: CompanyConfig) -> list[str]:
        allowed_hosts = {urlparse(base_url).netloc.lower()}
        if company.domain:
            allowed_hosts.add(company.domain.lower())
        pattern = re.compile(company.link_pattern, re.IGNORECASE) if company.link_pattern else None

        anchors = (
            soup.select(company.link_selector)
            if company.link_selector
            else soup.find_all("a", href=True)
        )
        links: set[str] = set()
        for anchor in anchors:
            href = urljoin(base_url, anchor.get("href") or "")
            if not href.startswith(("http://", "https://")):
                continue
            parsed = urlparse(href)
            if parsed.netloc.lower() not in allowed_hosts:
                continue
            path = parsed.path.lower()
            if path.endswith(SKIP_EXTENSIONS) or not path:
                continue
            text = anchor.get_text(" ", strip=True)
            if company.link_selector is None and pattern is None:
                if not (JOB_KEYWORD.search(path) or JOB_KEYWORD.search(text)):
                    continue
            if pattern and not pattern.search(href):
                continue
            low = href.lower()
            if any(k in low for k in company.exclude_keywords):
                continue
            if href.rstrip("/") == base_url.rstrip("/"):
                continue
            links.add(href)
        # Cap the work per company; prefer links that look most like detail pages.
        ranked = sorted(
            links,
            key=lambda u: (bool(HUB_PATH_KEYWORDS.search(u)), len(u)),
        )
        return ranked[: company.max_pages]

    def _fetch_job_page(self, url: str, company: CompanyConfig) -> Job | None:
        html = self._html(url, company.use_js or self.use_js)
        job = extract_job(html, url, company.name)
        if not job or not job.title:
            return None
        return job


# --------------------------------------------------------------- extraction

def _json_ld_items(soup: BeautifulSoup) -> list[dict]:
    """Walk application/ld+json blocks and yield JobPosting dicts."""
    found: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if str(node.get("@type", "")).lower() == "jobposting":
                found.append(node)
            for value in node.values():
                walk(value)

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        walk(data)
    return found


def _ld_location(item: dict) -> str:
    loc = item.get("jobLocation") or {}
    if isinstance(loc, dict):
        addr = loc.get("address") or loc
        if isinstance(addr, dict):
            parts = [addr.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
            return ", ".join(p for p in parts if p)
        return str(addr)
    return str(loc) if loc else ""


def _head_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    tag = soup.find("title")
    if tag and tag.get_text(strip=True):
        return tag.get_text(" ", strip=True)
    return ""


def _meta_location(soup: BeautifulSoup) -> str:
    for attrs in ({"name": "job-location"}, {"property": "job-location"}, {"itemprop": "jobLocation"}):
        el = soup.find("meta", attrs=attrs)
        if el and el.get("content"):
            return el["content"].strip()
    el = soup.find(attrs={"itemprop": "jobLocation"})
    if el:
        return el.get_text(" ", strip=True)
    return ""


def _body_text(soup: BeautifulSoup, limit: int = _MAX_DESCRIPTION) -> str:
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return main.get_text(" ", strip=True)[:limit]


def extract_job(html: str, page_url: str, company: str) -> Job | None:
    """Extract a Job from a job-detail page. Returns None if nothing usable."""
    soup = BeautifulSoup(html, "lxml")
    ld = _json_ld_items(soup)
    if ld:
        item = ld[0]
        title = item.get("title") or _head_title(soup)
        org = item.get("hiringOrganization") or {}
        org_name = org.get("name") if isinstance(org, dict) else str(org)
        salary = ""
        base = item.get("baseSalary") or {}
        if isinstance(base, dict):
            value = base.get("value") or {}
            if isinstance(value, dict):
                amount = value.get("value")
                currency = value.get("currency") or base.get("currency") or ""
                salary = f"{currency} {amount}".strip() if amount else ""
        posted = None
        raw_date = item.get("datePosted")
        if raw_date:
            try:
                posted = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
            except ValueError:
                posted = None
        return Job(
            title=(title or "").strip(),
            company=org_name or company,
            url=page_url,
            location=_ld_location(item),
            description=(item.get("description") or "")[:_MAX_DESCRIPTION],
            salary=salary,
            source=f"careers:{company}",
            source_kind="careers",
            external_id=page_url,
            posted_at=posted,
        )

    title = _head_title(soup)
    if not title or BOILERPLATE_TITLE.search(title):
        return None
    return Job(
        title=title,
        company=company,
        url=page_url,
        location=_meta_location(soup),
        description=_body_text(soup),
        source=f"careers:{company}",
        source_kind="careers",
        external_id=page_url,
    )
