"""Scrapers for ATS-hosted career boards (server-rendered, no API key needed).

* Greenhouse: the public embed page at
  https://boards.greenhouse.io/embed/job_board?for={slug} returns plain HTML
  with one ``div.opening`` per posting.
* Lever: https://jobs.lever.co/{slug} returns HTML with one
  ``a.posting-title`` per posting.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from ..http import retry_get
from ..models import Job


def _gh_id(url: str) -> str:
    """External id from the gh_jid query param or the last URL path segment."""
    qs = parse_qs(urlparse(url).query)
    if qs.get("gh_jid"):
        return qs["gh_jid"][0]
    return url.rstrip("/").rsplit("/", 1)[-1]


def _greenhouse_slug(client: httpx.Client, slug: str, limit: int = 200) -> list[Job]:
    url = f"https://boards.greenhouse.io/embed/job_board?for={slug}"
    resp = retry_get(client, url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    jobs: list[Job] = []

    # Current Greenhouse embed template: <tr class="job-post"> with the title
    # in <p class="body body--medium"> and location in <p class="body__secondary">.
    for row in soup.select("tr.job-post"):
        link = row.find("a", href=True)
        if not link:
            continue
        title_el = row.select_one("p.body--medium") or link
        loc_el = row.select_one("p.body__secondary")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        title = re.sub(r"\s*(New|Hot|Featured)\s*$", "", title, flags=re.IGNORECASE).strip()
        href = link["href"]
        full_url = href if href.startswith("http") else f"https://boards.greenhouse.io{href}"
        jobs.append(
            Job(
                title=title,
                company=slug,
                url=full_url,
                location=loc_el.get_text(" ", strip=True) if loc_el else "",
                source=f"greenhouse:{slug}",
                source_kind="ats",
                external_id=_gh_id(full_url),
            )
        )

    # Legacy template: <div class="opening"> with .title / .location / .department.
    for opening in soup.select("div.opening"):
        link = opening.find("a", href=True)
        if not link:
            continue
        title_el = opening.select_one(".title")
        loc_el = opening.select_one(".location")
        dept_el = opening.select_one(".department")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title:  # fall back to the link text minus location bits
            title = link.get_text(" ", strip=True)
        location = loc_el.get_text(" ", strip=True) if loc_el else ""
        tags = [dept_el.get_text(" ", strip=True)] if dept_el and dept_el.get_text(strip=True) else []
        href = link["href"]
        full_url = href if href.startswith("http") else f"https://boards.greenhouse.io{href}"
        jobs.append(
            Job(
                title=title,
                company=slug,
                url=full_url,
                location=location,
                tags=tags,
                source=f"greenhouse:{slug}",
                source_kind="ats",
                external_id=_gh_id(full_url),
            )
        )
    return jobs[:limit]


def _lever_slug(client: httpx.Client, slug: str, limit: int = 200) -> list[Job]:
    url = f"https://jobs.lever.co/{slug}"
    resp = retry_get(client, url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    jobs: list[Job] = []
    for posting in soup.select("a.posting-title"):
        href = posting.get("href")
        if not href:
            continue
        title_el = posting.select_one("h5")
        loc_el = posting.select_one("h6")
        title = title_el.get_text(" ", strip=True) if title_el else posting.get_text(" ", strip=True)
        location = loc_el.get_text(" ", strip=True) if loc_el else ""
        full_url = href if href.startswith("http") else f"https://jobs.lever.co{href}"
        jobs.append(
            Job(
                title=title,
                company=slug,
                url=full_url,
                location=location,
                source=f"lever:{slug}",
                source_kind="ats",
                external_id=full_url.rsplit("/", 1)[-1],
            )
        )
    return jobs[:limit]


def fetch_ats(client: httpx.Client, kind: str, slug: str, limit: int = 200) -> list[Job]:
    """Fetch one ATS board. kind is 'greenhouse' or 'lever'."""
    if kind == "greenhouse":
        return _greenhouse_slug(client, slug, limit=limit)
    if kind == "lever":
        return _lever_slug(client, slug, limit=limit)
    raise KeyError(f"Unknown ATS kind: {kind}")
