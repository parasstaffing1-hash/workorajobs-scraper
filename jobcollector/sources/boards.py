"""Adapters for public job-board APIs that need no API key.

All of these are free, open, and documented:
* Remotive   https://remotive.com/remote-jobs  -> GET https://remotive.com/api/remote-jobs
* RemoteOK   https://remoteok.com              -> GET https://remoteok.com/api
* Arbeitnow  https://www.arbeitnow.com         -> GET https://www.arbeitnow.com/api/job-board-api
* Jobicy     https://jobicy.com                -> GET https://jobicy.com/api/v2/remote-jobs
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from ..http import retry_get
from ..models import Job

BOARDS = {
    "remotive": "https://remotive.com/api/remote-jobs",
    "remoteok": "https://remoteok.com/api",
    "arbeitnow": "https://www.arbeitnow.com/api/job-board-api",
    "jobicy": "https://jobicy.com/api/v2/remote-jobs?count=50",
    "weworkremotely": "https://weworkremotely.com/remote-jobs.json",
    "trulyremote": "https://trulyremote.co/api/jobs",
    "honeypot": "https://api.honeypot.io/v2/jobs",
}


def _clean_html(html: str, limit: int = 4000) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    return text[:limit]


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):  # epoch seconds (RemoteOK)
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _remotive(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        tags = list(item.get("tags") or [])
        if item.get("category"):
            tags.append(item["category"])
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=item.get("company_name") or "",
                url=item.get("url") or "",
                location=item.get("candidate_required_location") or "",
                description=_clean_html(item.get("description") or ""),
                tags=[t for t in tags if t],
                source="remotive",
                source_kind="board",
                external_id=str(item.get("id") or ""),
                posted_at=_parse_dt(item.get("publication_date")),
            )
        )
    return jobs


def _remoteok(data: list) -> list[Job]:
    jobs = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        if not item.get("position") or not item.get("company"):
            continue  # RemoteOK sometimes includes malformed/premium stubs
        jobs.append(
            Job(
                title=item.get("position") or "",
                company=item.get("company") or "",
                url=item.get("url") or "",
                location=item.get("location") or "Remote",
                description=_clean_html(item.get("description") or ""),
                tags=list(item.get("tags") or []),
                source="remoteok",
                source_kind="board",
                external_id=str(item.get("id") or item.get("slug") or ""),
                posted_at=_parse_dt(item.get("date")),
            )
        )
    return jobs


def _arbeitnow(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("data", []):
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=item.get("company_name") or "",
                url=item.get("url") or "",
                location=item.get("location") or "",
                description=_clean_html(item.get("description") or ""),
                tags=list(item.get("tags") or []),
                source="arbeitnow",
                source_kind="board",
                external_id=str(item.get("slug") or item.get("id") or ""),
                posted_at=_parse_dt(item.get("created_at")),
            )
        )
    return jobs


def _normalize_tags(tags) -> list[str]:
    """Jobicy sometimes returns tags as nested lists; flatten and drop junk."""
    out: list[str] = []
    for tag in tags or []:
        if isinstance(tag, list):
            out.extend(_normalize_tags(tag))
        elif isinstance(tag, str) and tag.strip():
            out.append(tag.strip())
    return out


def _jobicy(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        tags = _normalize_tags(
            [item.get("jobType"), item.get("jobLevel"), item.get("jobIndustry")]
        )
        jobs.append(
            Job(
                title=item.get("jobTitle") or "",
                company=item.get("companyName") or "",
                url=item.get("url") or "",
                location=item.get("jobGeo") or "",
                description=_clean_html(item.get("jobDescription") or ""),
                tags=tags,
                source="jobicy",
                source_kind="board",
                external_id=str(item.get("id") or ""),
                posted_at=_parse_dt(item.get("pubDate")),
            )
        )
    return jobs


def _weworkremotely(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        tags = list(item.get("tags") or [])
        jobs.append(
            Job(
                title=item.get("name") or "",
                company=item.get("company_name") or "",
                url=item.get("url") or "",
                location=item.get("location") or "Remote",
                description=_clean_html(item.get("description") or ""),
                tags=tags,
                source="weworkremotely",
                source_kind="board",
                external_id=str(item.get("id") or ""),
                posted_at=_parse_dt(item.get("created_at")),
            )
        )
    return jobs


def _trulyremote(data: dict) -> list[Job]:
    jobs = []
    items = data.get("jobs") or data.get("results") or data.get("data") or []
    if isinstance(data, list):
        items = data
    for item in items:
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=item.get("company") or item.get("company_name") or "",
                url=item.get("url") or item.get("link") or "",
                location=item.get("location") or "Remote",
                description=_clean_html(item.get("description") or ""),
                tags=list(item.get("tags") or []),
                source="trulyremote",
                source_kind="board",
                external_id=str(item.get("id") or ""),
                posted_at=_parse_dt(item.get("posted_at") or item.get("created_at")),
            )
        )
    return jobs


def _honeypot(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("jobs") or data.get("data") or []:
        company = item.get("company") or {}
        comp_name = company.get("name") if isinstance(company, dict) else str(company or "")
        loc = item.get("city") or ""
        country = item.get("country") or ""
        location = ", ".join(p for p in [loc, country] if p)
        jobs.append(
            Job(
                title=item.get("name") or "",
                company=comp_name,
                url=item.get("url") or "",
                location=location or "Remote",
                description=_clean_html(item.get("description") or ""),
                tags=list(item.get("tags") or []),
                salary=item.get("salary_range") or "",
                source="honeypot",
                source_kind="board",
                external_id=str(item.get("id") or ""),
                posted_at=_parse_dt(item.get("created_at")),
            )
        )
    return jobs


def _github_jobs(data: dict) -> list[Job]:
    """Parse GitHub Issues labeled 'job' as job listings."""
    jobs = []
    for item in data.get("items", []):
        repo_url = item.get("repository_url") or ""
        repo_parts = repo_url.rstrip("/").split("/")
        org = repo_parts[-2] if len(repo_parts) >= 2 else ""
        repo = repo_parts[-1] if repo_parts else ""
        labels = [l.get("name", "") for l in item.get("labels", [])]
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=org,
                url=item.get("html_url") or "",
                location="",
                description=_clean_html(item.get("body") or ""),
                tags=labels,
                source="github_jobs",
                source_kind="board",
                external_id=str(item.get("id") or ""),
                posted_at=_parse_dt(item.get("created_at")),
            )
        )
    return jobs


# ---- Keyed boards (require API keys) ---------------------------------------

# SerpApi — aggregates Indeed, Glassdoor, LinkedIn, ZipRecruiter, Dice, Monster
# via Google Jobs. Free tier: 100 searches/month. Paid: $50-75/month.
def _serpapi(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("jobs_results", []):
        posted_at = None
        if item.get("detected_extensions", {}).get("posted_at"):
            try:
                posted_at = datetime.fromisoformat(
                    item["detected_extensions"]["posted_at"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass
        ext = item.get("detected_extensions") or {}
        salary = ext.get("salary") or ""
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=item.get("company_name") or "",
                url=item.get("share_link") or item.get("related_links", [{}])[0].get("link") if item.get("related_links") else "",
                location=item.get("location") or "",
                description=_clean_html(item.get("description") or ""),
                salary=salary,
                tags=[t for t in [ext.get("schedule_type"), ext.get("work_from_home") and "remote"] if t],
                source="serpapi",
                source_kind="board",
                external_id=item.get("job_id") or "",
                posted_at=posted_at,
            )
        )
    return jobs


# Jooble — aggregates from 50+ job boards. Free API (limited calls).
def _jooble(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("jobs", []):
        posted_at = None
        if item.get("date"):
            try:
                posted_at = datetime.fromisoformat(item["date"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=item.get("company") or "",
                url=item.get("link") or "",
                location=item.get("location") or "",
                description=_clean_html(item.get("snippet") or ""),
                salary=item.get("salary") or "",
                source="jooble",
                source_kind="board",
                external_id=item.get("id") or "",
                posted_at=posted_at,
            )
        )
    return jobs


# Findwork — curated developer job board. Free API.
def _findwork(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("results", []):
        jobs.append(
            Job(
                title=item.get("role") or "",
                company=item.get("company_name") or "",
                url=item.get("url") or "",
                location=item.get("location") or "",
                description=_clean_html(item.get("text") or ""),
                tags=list(item.get("keywords") or []) + list(item.get("employment_types") or []),
                source="findwork",
                source_kind="board",
                external_id=str(item.get("id") or ""),
                posted_at=_parse_dt(item.get("date_posted")),
            )
        )
    return jobs


# Reed UK — UK's largest job board. Free API (needs key).
def _reed_uk(data: dict) -> list[Job]:
    jobs = []
    for item in data.get("results", []):
        posted_at = None
        if item.get("datePosted"):
            try:
                posted_at = datetime.fromisoformat(item["datePosted"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        salary_min = item.get("minimumSalary") or ""
        salary_max = item.get("maximumSalary") or ""
        salary = ""
        if salary_min and salary_max:
            salary = f"{salary_min}-{salary_max}"
        elif salary_min:
            salary = str(salary_min)
        jobs.append(
            Job(
                title=item.get("jobTitle") or "",
                company=item.get("employerName") or "",
                url=item.get("jobUrl") or "",
                location=item.get("locationName") or "",
                description=_clean_html(item.get("jobDescription") or ""),
                salary=salary,
                tags=[t for t in [item.get("contractType"), item.get("hours")] if t],
                source="reed_uk",
                source_kind="board",
                external_id=str(item.get("jobId") or ""),
                posted_at=posted_at,
            )
        )
    return jobs


PARSERS = {
    "remotive": _remotive,
    "remoteok": _remoteok,
    "arbeitnow": _arbeitnow,
    "jobicy": _jobicy,
    "weworkremotely": _weworkremotely,
    "trulyremote": _trulyremote,
    "honeypot": _honeypot,
    "github_jobs": _github_jobs,
    "findwork": _findwork,
    "jooble": _jooble,
    "serpapi": _serpapi,
    "reed_uk": _reed_uk,
}


# Boards that accept a server-side keyword search param (verified live).
# Boards not listed here (arbeitnow, remoteok, jobicy) have no search param —
# the keyword is applied client-side to the fetched feed instead.
KEYWORD_PARAM = {
    "remotive": "search",
}


def _matches_keyword(job: Job, keyword: str) -> bool:
    """Case-insensitive keyword match across the fields we can see."""
    kw = keyword.lower()
    haystack = " ".join(
        [job.title, job.company, job.location, job.description, " ".join(job.tags)]
    ).lower()
    return kw in haystack


def fetch_board(client: httpx.Client, name: str, limit: int = 200, api_keys: dict | None = None) -> list[Job]:
    """Fetch one board by name. Raises KeyError for unknown boards.

    ``name`` may carry a keyword: "arbeitnow|python" narrows the pull to
    matching postings. Boards with a real search param (remotive) filter
    server-side; the rest are filtered client-side on the fetched feed. The
    source label stays the plain board name, so keyword entries merge into
    the same source.
    """
    keys = api_keys or {}
    parts = name.split("|", 1)
    board = parts[0]
    raw_keywords = parts[1] if len(parts) > 1 else ""
    # Comma-separated keywords are match-any: "arbeitnow|python,react" pulls
    # jobs mentioning python OR react with a single feed fetch.
    keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]

    # GitHub Jobs uses a different API endpoint
    if board == "github_jobs":
        query = raw_keywords or "job label:job"
        url = f"https://api.github.com/search/issues?q={quote(query)}+is:issue&per_page={min(limit, 100)}"
        resp = retry_get(client, url)
        resp.raise_for_status()
        return PARSERS[board](resp.json())[:limit]

    # SerpApi — needs API key + search engine + location
    if board == "serpapi":
        api_key = keys.get("serpapi_key") or ""
        if not api_key:
            raise KeyError("serpapi requires 'serpapi_key' in api_keys")
        query = raw_keywords or "software engineer"
        url = (
            f"https://serpapi.com/search.json?engine=google_jobs&q={quote(query)}"
            f"&api_key={api_key}&hl=en&gl=us&num={min(limit, 100)}"
        )
        resp = retry_get(client, url)
        resp.raise_for_status()
        return PARSERS[board](resp.json())[:limit]

    # Jooble — needs API key
    if board == "jooble":
        api_key = keys.get("jooble_key") or ""
        if not api_key:
            raise KeyError("jooble requires 'jooble_key' in api_keys")
        query = raw_keywords or "software engineer"
        url = f"https://jooble.org/api/"
        resp = client.post(url, json={
            "keywords": query,
            "page": 1,
            "count": min(limit, 100),
        }, headers={"Authorization": api_key, "Content-Type": "application/json"}, timeout=15)
        resp.raise_for_status()
        return PARSERS[board](resp.json())[:limit]

    # Findwork — free API (optional key for higher rate limits)
    if board == "findwork":
        url = "https://findwork.dev/api/jobs/"
        headers = {}
        fk = keys.get("findwork_key")
        if fk:
            headers["Authorization"] = fk
        resp = retry_get(client, url, headers=headers)
        resp.raise_for_status()
        return PARSERS[board](resp.json())[:limit]

    # Reed UK — needs API key
    if board == "reed_uk":
        api_key = keys.get("reed_api_key") or ""
        if not api_key:
            raise KeyError("reed_uk requires 'reed_api_key' in api_keys")
        query = raw_keywords or "software engineer"
        url = f"https://www.reed.co.uk/api/1.0/search?keywords={quote(query)}&resultsToTake={min(limit, 100)}"
        resp = client.get(url, headers={"Authorization": f"Basic {api_key}"}, timeout=15)
        resp.raise_for_status()
        return PARSERS[board](resp.json())[:limit]

    # Free boards (no API key needed)
    url = BOARDS[board]
    if keywords and board in KEYWORD_PARAM:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{KEYWORD_PARAM[board]}={quote(raw_keywords)}"
    resp = retry_get(client, url)
    resp.raise_for_status()
    jobs = PARSERS[board](resp.json())
    if keywords and board not in KEYWORD_PARAM:
        jobs = [j for j in jobs if any(_matches_keyword(j, k) for k in keywords)]
    return jobs[:limit]
