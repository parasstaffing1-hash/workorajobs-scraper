"""Adapters for the ATS platforms' official public (keyless) APIs.

These are the same free endpoints used by the open-source job-board-aggregator
project (Feashliaa/job-board-aggregator, MIT). Using them means structured JSON
instead of HTML scraping: titles, locations, departments, and post dates come
straight from the source.

* Greenhouse     GET  https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
* Ashby         GET  https://api.ashbyhq.com/posting-api/job-board/{slug}
* BambooHR      GET  https://{slug}.bamboohr.com/careers/list
* Lever         GET  https://api.lever.co/v0/postings/{slug}
* Workday       POST https://{company}.wd{n}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs
                 (slug format "company|wd{n}|site_id", e.g. "7eleven|wd3|7eleven")
* TCS           POST https://ibegin.tcsapps.com/candidate/api/v1/jobs/searchJ
                 (Tata Consultancy Services' "iBegin" portal; keyless JSON search)
* SmartRecruiters GET https://api.smartrecruiters.com/v1/companies/{slug}/postings
* Workable      GET  https://apply.workable.com/api/v1/widget/accounts/{slug}
* Breezy        GET  https://{slug}.breezy.hr/json?verbose=true
* Teamtailor    GET  https://{slug}.teamtailor.com/jobs.json
* HireHive      GET  https://{slug}.hirehive.com/api/v2/jobs
* Recruitee     GET  https://{slug}.recruitee.com/api/offers
* Rise          GET  https://api.joinrise.io/api/v1/jobs/public
"""
from __future__ import annotations

import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup

from ..http import retry_get, retry_post
from ..models import Job

SOURCE_KIND = "ats"
_WORKDAY_LIMIT = 20

# ------------------------------------------------------------------- TCS
# Tata Consultancy Services publishes openings through its iBegin portal
# (ibegin.tcsapps.com) — an Angular SPA backed by a keyless JSON search API.
#
# Slug format: "label" or "label|fresh_days". The API exposes no posting date,
# only an apply-by deadline, so the optional fresh_days window keeps only
# openings whose apply-by is within the next N days (and never expired ones).
# A filtered slug is a *separate source* (tcs:tcs-7) so it can run daily
# alongside a full archive without expiring it.
_TCS_BASE = "https://ibegin.tcsapps.com/candidate"
_TCS_COMPANY = "Tata Consultancy Services"
_TCS_PAGE_SIZE = 10
_TCS_DESC_CONCURRENCY = 8
_TCS_MAX_DESC = 6000
# When a fresh_days window is active, scan this many listings (newest-first)
# before filtering — comfortably covers every recently posted opening.
_TCS_SCAN_WHEN_FILTERED = 500


def _tcs_apply_by(value: str) -> datetime | None:
    """Parse "20-AUG-2026 11:59:59 PM" into a timezone-naive UTC date."""
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y %I:%M:%S %p")
    except (ValueError, AttributeError):
        return None


def _dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        # Lever createdAt is epoch ms; boards use seconds. Heuristic: ms when large.
        if value > 10_000_000_000:
            value = value / 1000
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------- Greenhouse

def _greenhouse(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
    resp = retry_get(client, url)
    resp.raise_for_status()
    jobs: list[Job] = []
    for item in resp.json().get("jobs", []):
        location = (item.get("location") or {}).get("name") or ""
        depts = [d.get("name") for d in item.get("departments") or [] if d.get("name")]
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=slug,
                url=item.get("absolute_url") or "",
                location=location,
                tags=depts,
                source=f"greenhouse:{slug}",
                source_kind=SOURCE_KIND,
                external_id=str(item.get("id") or ""),
                posted_at=_dt(item.get("updated_at")),
            )
        )
    return jobs[:limit]


# -------------------------------------------------------------------- Ashby

def _ashby(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    resp = retry_get(client, url)
    resp.raise_for_status()
    jobs: list[Job] = []
    for item in resp.json().get("jobs", []):
        comp = item.get("compensation") or {}
        tier = comp.get("compensationTier") if isinstance(comp, dict) else {}
        salary = tier.get("summary") if isinstance(tier, dict) else ""
        tags = [t for t in (item.get("department"), item.get("team")) if t]
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=slug,
                url=item.get("jobUrl") or item.get("applyUrl") or "",
                location=item.get("location") or "",
                description=(item.get("descriptionPlain") or "")[:6000],
                tags=tags,
                salary=salary or "",
                source=f"ashby:{slug}",
                source_kind=SOURCE_KIND,
                external_id=str(item.get("id") or ""),
                posted_at=_dt(item.get("publishedAt")),
            )
        )
    return jobs[:limit]


# ------------------------------------------------------------------ BambooHR

def _bamboohr(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    url = f"https://{slug}.bamboohr.com/careers/list"
    resp = retry_get(client, url)
    resp.raise_for_status()
    if "json" not in resp.headers.get("content-type", ""):
        # BambooHR serves a marketing HTML page for non-existent tenants.
        return []
    jobs: list[Job] = []
    for item in resp.json().get("result", []):
        loc = item.get("location") or {}
        if isinstance(loc, dict):
            parts = [loc.get("city"), loc.get("state")]
            location = ", ".join(p for p in parts if p)
        else:
            location = str(loc)
        jobs.append(
            Job(
                title=item.get("jobOpeningName") or "",
                company=slug,
                url=f"https://{slug}.bamboohr.com/careers/{item.get('id')}",
                location=location,
                source=f"bamboohr:{slug}",
                source_kind=SOURCE_KIND,
                external_id=str(item.get("id") or ""),
            )
        )
    return jobs[:limit]


# -------------------------------------------------------------------- Lever

def _lever(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    url = f"https://api.lever.co/v0/postings/{slug}"
    resp = retry_get(client, url)
    resp.raise_for_status()
    jobs: list[Job] = []
    for item in resp.json():
        categories = item.get("categories") or {}
        location = categories.get("location") or ""
        all_locs = categories.get("allLocations") or []
        if isinstance(all_locs, list) and all_locs:
            location = ", ".join(str(x) for x in all_locs)
        tags = [t for t in (categories.get("team"), categories.get("commitment")) if t]
        jobs.append(
            Job(
                title=item.get("text") or "",
                company=slug,
                url=item.get("hostedUrl") or "",
                location=location,
                description=(item.get("descriptionPlain") or "")[:6000],
                tags=tags,
                source=f"lever:{slug}",
                source_kind=SOURCE_KIND,
                external_id=str(item.get("id") or ""),
                posted_at=_dt(item.get("createdAt")),
            )
        )
    return jobs[:limit]


# ------------------------------------------------------------------- Workday

def _parse_workday_posted_on(text: str) -> datetime | None:
    """Convert Workday's relative string ('Posted 2 Days Ago') to a datetime."""
    if not text:
        return None
    t = text.strip().lower()
    today = datetime.now(timezone.utc).date()
    if "today" in t:
        return datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    m = re.search(r"(\d+)\s+day", t)
    if m:
        return datetime.combine(today - timedelta(days=int(m.group(1))), datetime.min.time(), tzinfo=timezone.utc)
    m = re.search(r"(\d+)\s+week", t)
    if m:
        return datetime.combine(today - timedelta(weeks=int(m.group(1))), datetime.min.time(), tzinfo=timezone.utc)
    m = re.search(r"(\d+)\s+month", t)
    if m:
        return datetime.combine(today - timedelta(days=int(m.group(1)) * 30), datetime.min.time(), tzinfo=timezone.utc)
    return None


def _workday(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    parts = slug.split("|")
    if len(parts) != 3:
        raise ValueError(f"Workday slug must be 'company|wd{{n}}|site_id', got {slug!r}")
    company, wd, site_id = parts
    base = f"https://{company}.wd{wd[2:]}.myworkdayjobs.com"
    api_url = f"{base}/wday/cxs/{company}/{site_id}/jobs"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": base,
        "Referer": f"{base}/{site_id}",
    }
    jobs: list[Job] = []
    offset = 0
    while offset < limit:
        resp = retry_post(
            client, api_url,
            json={"appliedFacets": {}, "limit": _WORKDAY_LIMIT, "offset": offset, "searchText": ""},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings") or []
        for item in postings:
            path = item.get("externalPath") or ""
            jobs.append(
                Job(
                    title=item.get("title") or "",
                    company=company,
                    url=f"{base}/{site_id}{path}",
                    location=item.get("locationsText") or "",
                    source=f"workday:{company}",
                    source_kind=SOURCE_KIND,
                    external_id=item.get("bulletFields", [""])[0] if item.get("bulletFields") else path,
                    posted_at=_parse_workday_posted_on(item.get("postedOn")),
                )
            )
        total = data.get("total", 0)
        offset += _WORKDAY_LIMIT
        if offset >= total or not postings:
            break
    return jobs[:limit]


# ------------------------------------------------------------------- TCS

def _tcs_search_payload(page: int) -> dict:
    return {
        "jobCity": None,
        "jobSkill": None,
        "pageNumber": str(page),
        "userText": "",
        "jobTitleOrder": None,
        "jobCityOrder": None,
        "jobFunctionOrder": None,
        "jobExperienceOrder": None,
        "applyByOrder": None,
        "regular": True,
        "walkin": True,
    }


def _tcs_description(client: httpx.Client, job_id: str, headers: dict) -> str:
    """Fetch the full description for one iBegin job (extra API call)."""
    try:
        resp = retry_post(
            client, f"{_TCS_BASE}/api/v1/job/desc",
            json={"jobId": job_id[:-1] if job_id.endswith(("J", "W")) else job_id},
            headers=headers,
        )
        resp.raise_for_status()
        d = resp.json().get("data") or {}
    except (httpx.HTTPError, ValueError):
        return ""
    parts = [
        d.get("description") or "",
        (d.get("qualifications") or "") and f"Qualifications: {d['qualifications']}",
        (d.get("role") or "") and f"Role: {d['role']}",
        (d.get("experience") or "") and f"Experience: {d['experience']}",
        (d.get("applyby") or "") and f"Apply by: {d['applyby']}",
    ]
    return "\n\n".join(p for p in parts if p)[:_TCS_MAX_DESC]


def _tcs_tags(skills: str) -> list[str]:
    seen: list[str] = []
    for s in re.split(r"[,|]", skills or ""):
        s = s.strip()
        if s and s not in seen:
            seen.append(s)
    return seen[:10]


def _tcs(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch TCS openings via the iBegin search API, 10 per page.

    ``slug`` may carry a fresh-days window ("tcs|7"): only openings whose
    apply-by date falls within the next 7 days are kept. The source label is
    derived from the slug ("tcs|7" -> ``tcs:tcs-7``), so a filtered feed is
    a distinct source that never expires a full archive.
    """
    label = slug.replace("|", "-")
    fresh_days = None
    if "|" in slug:
        try:
            fresh_days = int(slug.split("|", 1)[1])
        except ValueError:
            pass

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": _TCS_BASE,
        "Referer": f"{_TCS_BASE}/jobs",
    }
    search_url = f"{_TCS_BASE}/api/v1/jobs/searchJ"
    # With a fresh window, scan deeper so we don't miss in-window postings that
    # sit beyond the caller's per-source limit (results come back newest-first).
    scan_limit = max(limit, _TCS_SCAN_WHEN_FILTERED) if fresh_days is not None else limit
    raw: list[dict] = []
    page = 1
    while len(raw) < scan_limit:
        resp = retry_post(client, search_url, json=_tcs_search_payload(page), headers=headers)
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        postings = data.get("jobs") or []
        raw.extend(postings)
        total = int(data.get("totalJobs") or 0)
        if not postings or len(raw) >= total or page > 500:
            break
        page += 1
    raw = raw[:scan_limit]

    now = datetime.now()
    kept: list[tuple[int, dict]] = []
    for i, item in enumerate(raw):
        apply_by = _tcs_apply_by(item.get("applyByDate") or "")
        if apply_by is not None and apply_by < now:
            continue  # application window already closed
        if (
            fresh_days is not None
            and apply_by is not None
            and apply_by > now + timedelta(days=fresh_days)
        ):
            continue  # outside the fresh window
        kept.append((i, item))
    raw = [item for _, item in kept]

    # Enrich survivors with full descriptions concurrently (best-effort, like
    # the careers crawler fetching detail pages).
    descs: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=_TCS_DESC_CONCURRENCY) as pool:
        futs = {
            pool.submit(_tcs_description, client, item.get("id") or "", headers): i
            for i, item in enumerate(raw)
        }
        for fut in as_completed(futs):
            descs[futs[fut]] = fut.result()

    jobs: list[Job] = []
    for i, item in enumerate(raw):
        job_id = item.get("id") or ""
        jobs.append(
            Job(
                title=(item.get("jobTitle") or "").strip(),
                company=_TCS_COMPANY,
                url=f"{_TCS_BASE}/jobs/{job_id}",
                location=(item.get("location") or "").strip(),
                description=descs.get(i, ""),
                tags=_tcs_tags(item.get("skills")),
                source=f"tcs:{label}",
                source_kind=SOURCE_KIND,
                external_id=job_id,
            )
        )
    return jobs


# ------------------------------------------------------------ SmartRecruiters

def _smartrecruiters(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    resp = retry_get(client, url, params={"limit": min(limit, 100)})
    resp.raise_for_status()
    data = resp.json()
    jobs: list[Job] = []
    for item in data.get("content") or []:
        loc = item.get("location") or {}
        location = loc.get("fullLocation") or loc.get("city") or ""
        if isinstance(location, list):
            location = ", ".join(str(x) for x in location)
        dept = item.get("department") or {}
        func = item.get("function") or {}
        emp = item.get("typeOfEmployment") or {}
        def _lbl(v):
            return (v or {}).get("label") if isinstance(v, dict) else (str(v) if v else None)
        tags = [t for t in (_lbl(dept), _lbl(func), _lbl(emp)) if t]
        jobs.append(
            Job(
                title=item.get("name") or "",
                company=slug,
                url=f"https://jobs.smartrecruiters.com/{slug}/{item.get('refNumber') or item.get('id') or ''}",
                location=str(location or ""),
                tags=tags,
                source=f"smartrecruiters:{slug}",
                source_kind=SOURCE_KIND,
                external_id=item.get("refNumber") or item.get("id") or "",
                posted_at=_dt(item.get("releasedDate")),
            )
        )
    return jobs[:limit]


# ------------------------------------------------------------------ Workable

def _workable(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    resp = retry_get(client, url)
    resp.raise_for_status()
    data = resp.json()
    jobs: list[Job] = []
    for item in data.get("jobs") or []:
        parts = [item.get("city") or "", item.get("state") or "", item.get("country") or ""]
        location = ", ".join(p for p in parts if p)
        tags = [item.get("department")] if item.get("department") else []
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=item.get("company_name") or slug,
                url=item.get("shortlink") or item.get("url") or "",
                location=location,
                tags=tags,
                source=f"workable:{slug}",
                source_kind=SOURCE_KIND,
                external_id=item.get("shortcode") or item.get("code") or "",
                posted_at=_dt(item.get("published_on")),
            )
        )
    return jobs[:limit]


# -------------------------------------------------------------------- Breezy

def _breezy(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    url = f"https://{slug}.breezy.hr/json?verbose=true"
    resp = retry_get(client, url)
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list):
        return []
    jobs: list[Job] = []
    for item in items:
        loc = item.get("location") or {}
        if isinstance(loc, dict):
            location = loc.get("name") or ""
        else:
            location = str(loc or "")
        comp = item.get("company") or {}
        comp_name = comp.get("name") if isinstance(comp, dict) else str(comp or "")
        emp_type = item.get("type") or {}
        tags = [t for t in (item.get("department"), emp_type.get("name") if isinstance(emp_type, dict) else emp_type) if t]
        jobs.append(
            Job(
                title=item.get("name") or "",
                company=comp_name or slug,
                url=item.get("url") or f"https://{slug}.breezy.hr/p/{item.get('friendly_id')}",
                location=str(location or ""),
                description=(item.get("description") or "")[:6000],
                tags=tags,
                source=f"breezy:{slug}",
                source_kind=SOURCE_KIND,
                external_id=str(item.get("id") or item.get("friendly_id") or ""),
                posted_at=_dt(item.get("published_date")),
            )
        )
    return jobs[:limit]


# ---------------------------------------------------------------- Teamtailor

def _teamtailor(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    url = f"https://{slug}.teamtailor.com/jobs.json"
    resp = retry_get(client, url)
    resp.raise_for_status()
    data = resp.json()
    jobs: list[Job] = []
    for item in data.get("items") or []:
        jp = item.get("_jobposting") or {}
        org = jp.get("hiringOrganization") or {}
        comp_name = org.get("name") if isinstance(org, dict) else str(org or "")
        loc_raw = jp.get("jobLocation") or jp.get("location") or ""
        loc_parts: list[str] = []
        for loc in loc_raw if isinstance(loc_raw, list) else [loc_raw]:
            if not isinstance(loc, dict):
                continue
            addr = loc.get("address") or {}
            if isinstance(addr, dict):
                loc_parts.extend(
                    str(v) for v in [addr.get("addressLocality"), addr.get("addressRegion")] if v
                )
            else:
                loc_parts.append(str(loc.get("name") or ""))
        location = ", ".join(dict.fromkeys(p for p in loc_parts if p))
        tags = [jp.get("employmentType")] if jp.get("employmentType") else []
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=comp_name or slug,
                url=item.get("url") or "",
                location=str(location or ""),
                description=(item.get("content_html") or "")[:6000],
                tags=tags,
                source=f"teamtailor:{slug}",
                source_kind=SOURCE_KIND,
                external_id=str(item.get("id") or ""),
                posted_at=_dt(item.get("date_published")),
            )
        )
    return jobs[:limit]


# ----------------------------------------------------------------- HireHive

def _hirehive(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    url = f"https://{slug}.hirehive.com/api/v2/jobs"
    resp = retry_get(client, url, params={"page_size": min(limit, 30)})
    resp.raise_for_status()
    data = resp.json()
    jobs: list[Job] = []
    for item in data.get("items") or []:
        loc_parts = [item.get("location") or "", item.get("state_code") or ""]
        loc_parts = [p for p in loc_parts if p]
        country = (item.get("country") or {}).get("name") if isinstance(item.get("country"), dict) else item.get("country")
        if country:
            loc_parts.append(country)
        tags = [item.get("category")] if item.get("category") else []
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=slug,
                url=item.get("hosted_url") or "",
                location=", ".join(dict.fromkeys(loc_parts)),
                description=((item.get("description") or {}).get("text") or "")[:6000],
                tags=tags,
                source=f"hirehive:{slug}",
                source_kind=SOURCE_KIND,
                external_id=str(item.get("id") or ""),
                posted_at=_dt(item.get("published_date")),
            )
        )
    return jobs[:limit]


# ---------------------------------------------------------------- Recruitee

def _recruitee(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    url = f"https://{slug}.recruitee.com/api/offers"
    resp = retry_get(client, url)
    resp.raise_for_status()
    data = resp.json()
    jobs: list[Job] = []
    for item in data.get("offers") or []:
        loc_parts = [item.get("city") or "", item.get("state_name") or "", item.get("country") or ""]
        loc_parts = [p for p in loc_parts if p]
        if item.get("remote"):
            loc_parts.append("Remote")
        tags = [t for t in (item.get("department"), item.get("category_code")) if t]
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=item.get("company_name") or slug,
                url=item.get("careers_url") or item.get("careers_apply_url") or "",
                location=", ".join(dict.fromkeys(loc_parts)),
                description=(item.get("description") or "")[:6000],
                tags=tags,
                source=f"recruitee:{slug}",
                source_kind=SOURCE_KIND,
                external_id=str(item.get("id") or item.get("guid") or ""),
                posted_at=_dt(item.get("published_at")),
            )
        )
    return jobs[:limit]


# --------------------------------------------------------------------- Rise

def _rise(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    url = "https://api.joinrise.io/api/v1/jobs/public"
    jobs: list[Job] = []
    page = 1
    while len(jobs) < limit:
        resp = retry_get(client, url, params={"page": page, "limit": min(50, limit - len(jobs)), "sort": "desc", "sortedBy": "createdAt"})
        resp.raise_for_status()
        result = (resp.json() or {}).get("result") or {}
        batch = result.get("jobs") or []
        for item in batch:
            owner = item.get("owner") or {}
            jobs.append(
                Job(
                    title=item.get("title") or "",
                    company=owner.get("companyName") or slug or "",
                    url=item.get("url") or "",
                    location=item.get("locationAddress") or "",
                    tags=[item.get("type")] if item.get("type") else [],
                    source="rise:public",
                    source_kind=SOURCE_KIND,
                    external_id=str(item.get("_id") or ""),
                    posted_at=_dt(item.get("createdAt")),
                )
            )
        if not batch:
            break
        page += 1
    return jobs[:limit]


# -------------------------------------------------------------- Adzuna
# Adzuna aggregates jobs across 20+ countries and is the single biggest
# keyless-ish source here. It needs a free API key (developer.adzuna.com):
# ~1,000 calls/month, each returning up to 50 results. The slug is a country
# code ("us", "gb", "de", ...) so every country is its own source and each
# gets up to ``limit`` jobs per run.


def _adzuna_keys(keys: dict | None) -> tuple[str, str]:
    app_id = (keys or {}).get("adzuna_app_id") or os.environ.get("ADZUNA_APP_ID", "")
    api_key = (keys or {}).get("adzuna_api_key") or os.environ.get("ADZUNA_API_KEY", "")
    if not app_id or not api_key:
        raise RuntimeError(
            "Adzuna needs adzuna_app_id + adzuna_api_key (config api_keys section "
            "or ADZUNA_APP_ID/ADZUNA_API_KEY env vars). Register a free key at "
            "https://developer.adzuna.com"
        )
    return app_id, api_key


def _adzuna(client: httpx.Client, slug: str, limit: int, keys: dict | None = None) -> list[Job]:
    app_id, api_key = _adzuna_keys(keys)
    # Slug is "country" or "country|keyword" — the keyword becomes Adzuna's
    # `what` search term, so adzuna: ["us|software-engineer"] scrapes matching
    # jobs across the US.
    parts = slug.split("|", 1)
    country = parts[0]
    keyword = parts[1] if len(parts) > 1 else ""
    jobs: list[Job] = []
    page = 1
    while len(jobs) < limit:
        resp = retry_get(
            client,
            f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}",
            params={
                "app_id": app_id,
                "app_key": api_key,
                "what": keyword,
                "results_per_page": min(50, limit - len(jobs)),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        for item in results:
            area = (item.get("location") or {}).get("area") or []
            company = (item.get("company") or {}).get("display_name") or slug
            cat = (item.get("category") or {}).get("label") or ""
            salary = ""
            smin, smax = item.get("salary_min"), item.get("salary_max")
            if smin and smax:
                salary = f"{float(smin):,.0f} - {float(smax):,.0f} {item.get('salary_currency') or ''}".strip()
            jobs.append(
                Job(
                    title=item.get("title") or "",
                    company=company,
                    url=item.get("redirect_url") or "",
                    location=", ".join(str(x) for x in area if x),
                    description=_html_to_text(item.get("description") or "")[:6000],
                    tags=[cat] if cat else [],
                    salary=salary,
                    source=f"adzuna:{slug}",
                    source_kind=SOURCE_KIND,
                    external_id=str(item.get("id") or ""),
                    posted_at=_dt(item.get("created")),
                )
            )
        count = int(data.get("count") or 0)
        if not results or len(jobs) >= count or page >= 50:
            break
        page += 1
    return jobs[:limit]


# --------------------------------------------------------------- USAJobs
# The U.S. federal government's official jobs board. Free API key from
# developer.usajobs.gov; every request needs the key plus a User-Agent that
# looks like an email address. ~50-100k live federal postings, 500 per page.


def _usajobs_keys(keys: dict | None) -> tuple[str, str]:
    api_key = (keys or {}).get("usajobs_api_key") or os.environ.get("USAJOBS_API_KEY", "")
    ua = (keys or {}).get("usajobs_user_agent") or os.environ.get("USAJOBS_USER_AGENT", "")
    if not api_key:
        raise RuntimeError(
            "USAJobs needs usajobs_api_key (config api_keys section or "
            "USAJOBS_API_KEY env var). Register a free key at https://developer.usajobs.gov"
        )
    return api_key, ua or "jobcollector@example.com"


def _usajobs(client: httpx.Client, slug: str, limit: int, keys: dict | None = None) -> list[Job]:
    api_key, ua = _usajobs_keys(keys)
    headers = {"Host": "data.usajobs.gov", "User-Agent": ua, "Authorization-Key": api_key}
    # slug can carry a keyword: "public" (all federal) or "software engineer" etc.
    keyword = slug.strip()
    jobs: list[Job] = []
    page = 1
    while len(jobs) < limit:
        params: dict = {"ResultsPerPage": min(500, limit - len(jobs)), "Page": page}
        if keyword and keyword != "public":
            params["Keyword"] = keyword
        resp = retry_get(
            client,
            "https://data.usajobs.gov/api/search",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
        result = resp.json().get("SearchResult") or {}
        items = result.get("SearchResultItems") or []
        for item in items:
            d = item.get("MatchedObjectDescriptor") or {}
            dept = d.get("DepartmentName") or ""
            org = d.get("OrganizationName") or ""
            company = dept or "U.S. Government"
            if org and org != dept:
                company = f"{dept} · {org}" if dept else org
            rem = d.get("PositionRemuneration") or []
            salary = ""
            if rem:
                r0 = rem[0]
                lo, hi = r0.get("MinimumRange"), r0.get("MaximumRange")
                if lo and hi:
                    salary = f"${float(lo):,.0f} - ${float(hi):,.0f} {r0.get('RateIntervalCode') or ''}".strip()
            summary = d.get("JobSummary") or ""
            quals = d.get("QualificationSummary") or ""
            desc = "\n\n".join(p for p in (summary, quals) if p)
            jobs.append(
                Job(
                    title=d.get("PositionTitle") or "",
                    company=company,
                    url=d.get("PositionURI") or "",
                    location=d.get("PositionLocationDisplay") or "",
                    description=desc[:6000],
                    tags=[org] if org and org != company else [],
                    salary=salary,
                    source="usajobs",
                    source_kind=SOURCE_KIND,
                    external_id=str(d.get("PositionID") or ""),
                    posted_at=_dt(d.get("PublicationStartDate")),
                )
            )
        count = int(result.get("SearchResultCount") or 0)
        if not items or len(jobs) >= count or page >= 20:
            break
        page += 1
    return jobs[:limit]


# ---------------------------------------------------------------- Jobvite
# jobs.jobvite.com/{company}/jobs is fully server-rendered: every opening is a
# <li> with a link to /{company}/job/{id}. No JSON API, but the HTML is stable
# and parseable without a key (verified live against carfax).


def _jobvite(client: httpx.Client, slug: str, limit: int, keys: dict | None = None) -> list[Job]:
    url = f"https://jobs.jobvite.com/{slug}/jobs"
    resp = retry_get(client, url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    jobs: list[Job] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="/job/"]'):
        href = a.get("href") or ""
        m = re.search(r"/job/([A-Za-z0-9]+)", href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        title = " ".join(a.get_text(" ", strip=True).split())
        if not title:
            continue
        loc_el = None
        for parent in (a.parent, a.parent.parent if a.parent else None):
            if parent is None:
                continue
            loc_el = parent.select_one(".jv-featured-job-location")
            if loc_el is None:
                loc_el = parent.select_one("[class*='location']")
            if loc_el:
                break
        location = " ".join(loc_el.get_text(" ", strip=True).split()) if loc_el else ""
        jobs.append(
            Job(
                title=title,
                company=slug,
                url=f"https://jobs.jobvite.com{href}",
                location=location,
                source=f"jobvite:{slug}",
                source_kind=SOURCE_KIND,
                external_id=m.group(1),
            )
        )
    return jobs[:limit]


# ---------------------------------------------------------------- iCIMS
# careers-{tenant}.icims.com/jobs/search renders job rows server-side (no JSON
# API). Some tenants gate automated clients with a bot-check — those return []
# here and work from a normal network. Parsing is defensive: any unexpected
# markup yields an empty list rather than a crash.


def _icims(client: httpx.Client, slug: str, limit: int, keys: dict | None = None) -> list[Job]:
    base = f"https://careers-{slug}.icims.com/jobs/search"
    jobs: list[Job] = []
    seen: set[str] = set()
    page = 0
    while len(jobs) < limit and page < 20:
        params = {"ss": 1, "searchRelation": "keywordAll", "searchText": ""}
        if page:
            params["pr"] = page
        resp = retry_get(client, base, params=params)
        if resp.status_code in (403, 405, 429):
            return jobs
        resp.raise_for_status()
        if "Human Verification" in resp.text[:2000]:
            return jobs
        soup = BeautifulSoup(resp.text, "lxml")
        links = soup.select('a[href*="/jobs/"][href$="/job"]')
        if not links:
            break
        for a in links:
            href = a.get("href") or ""
            m = re.search(r"/jobs/(\d+)/job", href)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            title = " ".join(a.get_text(" ", strip=True).split())
            if not title:
                continue
            li = a.find_parent("li") or a.parent
            loc_el = li.select_one(".iCIMS_JobLocation, [class*='location']") if li else None
            cat_el = li.select_one(".iCIMS_JobCategory, [class*='category']") if li else None
            location = " ".join(loc_el.get_text(" ", strip=True).split()) if loc_el else ""
            cat = " ".join(cat_el.get_text(" ", strip=True).split()) if cat_el else ""
            jobs.append(
                Job(
                    title=title,
                    company=slug,
                    url=f"https://careers-{slug}.icims.com{href}",
                    location=location,
                    tags=[cat] if cat else [],
                    source=f"icims:{slug}",
                    source_kind=SOURCE_KIND,
                    external_id=m.group(1),
                )
            )
        page += 1
    return jobs[:limit]


def _html_to_text(html: str) -> str:
    """Strip HTML tags to plain text (used by HTML-heavy sources)."""
    if not html:
        return ""
    return re.sub(r"\s+", " ", BeautifulSoup(html, "lxml").get_text(" ", strip=True)).strip()


# ---------------------------------------------------------------------- YC
# YC-funded startups. ycombinator.com/companies/{slug} is fully server-rendered
# (an Inertia app) and embeds the company's current job postings as JSON in the
# ``data-page`` attribute — keyless, no Playwright needed. The slug list is
# generated from the directory (scripts/build_yc_companies.py + generate_yc_config.py)
# and only hiring companies are configured, so non-hiring ones aren't crawled.
_YC_BASE = "https://www.ycombinator.com/companies"


def _yc(client: httpx.Client, slug: str, limit: int, keys: dict | None = None) -> list[Job]:
    resp = retry_get(client, f"{_YC_BASE}/{slug}")
    if resp.status_code == 404:
        return []  # not in the public directory anymore
    resp.raise_for_status()
    m = re.search(r'data-page="([^"]*)"', resp.text) or re.search(r"data-page='([^']*)'", resp.text)
    if not m:
        return []
    try:
        data = json.loads(html.unescape(m.group(1)))
    except ValueError:
        return []
    props = data.get("props") or {}
    company = props.get("company") or {}
    name = company.get("name") or slug
    jobs: list[Job] = []
    for jp in props.get("jobPostings") or []:
        title = (jp.get("title") or "").strip()
        if not title:
            continue
        rel = jp.get("url") or ""
        # Job URLs come as absolute paths (e.g. "/companies/stripe/jobs/...").
        url = f"https://www.ycombinator.com{rel}" if rel.startswith("/") else rel
        salary = jp.get("salaryRange") or ""
        if jp.get("equityRange"):
            salary = f"{salary} | equity {jp['equityRange']}".strip(" |")
        jobs.append(
            Job(
                title=title,
                company=name,
                url=url,
                location=jp.get("location") or "",
                tags=[jp.get("prettyRole")] if jp.get("prettyRole") else [],
                salary=salary,
                source=f"yc:{slug}",
                source_kind=SOURCE_KIND,
                external_id=str(jp.get("id") or ""),
                posted_at=_dt(jp.get("createdAt")),
            )
        )
    return jobs[:limit]


# --------------------------------------------------------------- LinkedIn
# Unofficial, keyless guest-search endpoint (linkedin.com/jobs-guest/jobs/api/...).
# Returns public job-search results as HTML cards (no login). Two honest caveats:
#   * It's an internal endpoint — LinkedIn may change or block it at any time,
#     and scraping it sits outside LinkedIn's Terms of Service (use at your own
#     risk; keep volume low and polite).
#   * Guest access shows a limited window of results with no full descriptions
#     (detail pages are auth-walled).
# Slug format: "keywords" or "keywords|location" or "keywords|location|days".
_LINKEDIN_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def _parse_linkedin_relative(text: str) -> datetime | None:
    """Parse '1 week ago' / '30+ days ago' into a naive UTC date."""
    if not text:
        return None
    t = text.lower()
    today = datetime.now(timezone.utc).date()
    m = re.search(r"(\d+)\s*\+?\s*day", t)
    if m:
        return datetime.combine(today - timedelta(days=int(m.group(1))), datetime.min.time(), tzinfo=timezone.utc)
    m = re.search(r"(\d+)\s*\+?\s*week", t)
    if m:
        return datetime.combine(today - timedelta(weeks=int(m.group(1))), datetime.min.time(), tzinfo=timezone.utc)
    m = re.search(r"(\d+)\s*\+?\s*month", t)
    if m:
        return datetime.combine(today - timedelta(days=int(m.group(1)) * 30), datetime.min.time(), tzinfo=timezone.utc)
    if "today" in t:
        return datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    return None


def _linkedin(client: httpx.Client, slug: str, limit: int, keys: dict | None = None) -> list[Job]:
    parts = slug.split("|")
    keywords = parts[0]
    location = parts[1] if len(parts) > 1 else ""
    days = parts[2] if len(parts) > 2 else ""
    f_tpr = f"r{int(days) * 86400}" if days else ""
    jobs: list[Job] = []
    start = 0
    while len(jobs) < limit and start < limit * 2:
        resp = retry_get(
            client,
            _LINKEDIN_SEARCH,
            params={"keywords": keywords, "location": location, "f_TPR": f_tpr, "start": start},
        )
        if resp.status_code == 429:
            # LinkedIn rate-limits the guest endpoint hard (often after ~100
            # results); stop paginating and keep what we have.
            break
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select("div[class*=job-search-card]")
        for card in cards:
            title = card.select_one(".base-search-card__title")
            comp = card.select_one(".base-search-card__subtitle")
            loc_el = card.select_one(".base-search-card__metadata")
            date_el = card.select_one("time")
            a = card.select_one("a[href]")
            urn = card.get("data-entity-urn") or ""
            jid = urn.rsplit(":", 1)[-1] if urn else ""
            location_txt = (loc_el.get_text(" ", strip=True) if loc_el else "")
            # The metadata line mixes location with 'Actively Hiring' badges.
            location_txt = re.sub(r"\s*(Actively Hiring|Be an early applicant)\s*", " ", location_txt).strip()
            jobs.append(
                Job(
                    title=title.get_text(strip=True) if title else "",
                    company=comp.get_text(strip=True) if comp else slug,
                    url=a.get("href") or f"https://www.linkedin.com/jobs/view/{jid}",
                    location=location_txt,
                    source=f"linkedin:{keywords or 'jobs'}",
                    source_kind=SOURCE_KIND,
                    external_id=jid,
                    posted_at=_parse_linkedin_relative(date_el.get_text(strip=True) if date_el else ""),
                )
            )
        if not cards:
            break
        start += len(cards)
    return jobs[:limit]


# --------------------------------------------------------------- The Muse
# Public, keyless jobs API (documented): https://www.themuse.com/api/public/jobs
# The Muse aggregates job postings from thousands of companies (US-heavy).
_THEMUSE = "https://www.themuse.com/api/public/jobs"


def _themuse(client: httpx.Client, slug: str, limit: int, keys: dict | None = None) -> list[Job]:
    jobs: list[Job] = []
    page = 1
    while len(jobs) < limit and page <= 50:
        resp = retry_get(client, _THEMUSE, params={"page": page})
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        for item in results:
            company = (item.get("company") or {}).get("name") or ""
            locs = [l.get("name") for l in item.get("locations") or [] if l.get("name")]
            levels = [l.get("name") for l in item.get("levels") or [] if l.get("name")]
            cats = [c.get("name") for c in item.get("categories") or [] if c.get("name")]
            refs = item.get("refs") or {}
            jobs.append(
                Job(
                    title=item.get("name") or "",
                    company=company,
                    url=refs.get("landing_page") or "",
                    location=", ".join(dict.fromkeys(locs)),
                    tags=[t for t in (levels + cats) if t],
                    source="themuse",
                    source_kind=SOURCE_KIND,
                    external_id=str(item.get("id") or ""),
                    posted_at=_dt(item.get("publication_date")),
                )
            )
        if not results or page >= int(data.get("page_count") or page):
            break
        page += 1
    return jobs[:limit]


# ----------------------------------------------------------- Working Nomads
# Keyless API for remote jobs: https://www.workingnomads.com/api/exposed_jobs/
_WORKINGNOMADS = "https://www.workingnomads.com/api/exposed_jobs/"


def _workingnomads(client: httpx.Client, slug: str, limit: int, keys: dict | None = None) -> list[Job]:
    resp = retry_get(client, _WORKINGNOMADS)
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list):
        return []
    jobs: list[Job] = []
    for item in items:
        tags = [t.strip() for t in (item.get("tags") or "").split(",") if t.strip()]
        if item.get("category_name"):
            tags.insert(0, item.get("category_name"))
        jobs.append(
            Job(
                title=item.get("title") or "",
                company=item.get("company_name") or "",
                url=item.get("url") or "",
                location=item.get("location") or "Remote",
                description=_html_to_text(item.get("description") or "")[:6000],
                tags=tags[:10],
                source="workingnomads",
                source_kind=SOURCE_KIND,
                external_id=item.get("id") or item.get("url") or "",
                posted_at=_dt(item.get("pub_date")),
            )
        )
    return jobs[:limit]


# -------------------------------------------------------------------- Reed
# The UK's biggest job board. Free API key from https://www.reed.co.uk/api
# (self-service); the key is sent as HTTP Basic auth (username=key, no
# password). Slug = search keyword.
_REED_API = "https://www.reed.co.uk/api/1.0/search"


def _reed_keys(keys: dict | None) -> str:
    api_key = (keys or {}).get("reed_api_key") or os.environ.get("REED_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "Reed needs reed_api_key (config api_keys section or REED_API_KEY env var). "
            "Free key: https://www.reed.co.uk/api"
        )
    return api_key


def _reed(client: httpx.Client, slug: str, limit: int, keys: dict | None = None) -> list[Job]:
    import base64

    api_key = _reed_keys(keys)
    auth = base64.b64encode(f"{api_key}:".encode()).decode()
    jobs: list[Job] = []
    page = 1
    while len(jobs) < limit and page <= 20:
        resp = retry_get(
            client,
            _REED_API,
            params={"q": slug, "resultsToTake": min(100, limit - len(jobs)), "page": page},
            headers={"Authorization": f"Basic {auth}"},
        )
        if resp.status_code in (401, 403):
            break
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        for item in results:
            salary = item.get("salary") or ""
            jobs.append(
                Job(
                    title=item.get("jobTitle") or "",
                    company=item.get("employerName") or "",
                    url=item.get("jobUrl") or "",
                    location=item.get("locationName") or "",
                    description=((item.get("description") or "")[:6000]),
                    tags=[item.get("applicationType")] if item.get("applicationType") else [],
                    salary=salary,
                    source=f"reed:{slug}",
                    source_kind=SOURCE_KIND,
                    external_id=str(item.get("jobId") or ""),
                    posted_at=_dt(item.get("postedDate")),
                )
            )
        if not results:
            break
        page += 1
    return jobs[:limit]


# --- Rippling (growing fast, many companies use it now) ---

def _rippling(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from Rippling ATS (boardsapi.rippling.com)."""
    url = f"https://boardsapi.rippling.com/api/v1/companies/{slug}/jobs"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("jobs", []) or data.get("results", []) or []:
        loc = item.get("location") or {}
        location = loc.get("display_name") if isinstance(loc, dict) else str(loc or "")
        dept = item.get("department") or ""
        jobs.append(Job(
            title=item.get("title") or "",
            company=item.get("company_name") or slug,
            url=item.get("absolute_url") or item.get("url") or "",
            location=location,
            description=_html_to_text(item.get("description") or ""),
            tags=[dept] if dept else [],
            source="rippling",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# --- Personio (big in EU/UK) ---

def _personio(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from Personio ATS (careers page API)."""
    url = f"https://{slug}.personio.de/api/v1/positions"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("data", []):
        jobs.append(Job(
            title=item.get("name") or "",
            company=item.get("department") or slug,
            url=item.get("url") or "",
            location=item.get("office") or "",
            description=_html_to_text(item.get("job_description") or ""),
            tags=[t for t in [item.get("employment_type"), item.get("schedule")] if t],
            source="personio",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# --- JazzHR (SMBs) ---

def _jazzhr(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from JazzHR API (api.jazz.co)."""
    url = f"https://api.jazz.co/v1/jobs?company={slug}&limit={min(limit, 100)}"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("data", []):
        jobs.append(Job(
            title=item.get("title") or "",
            company=item.get("company_name") or slug,
            url=item.get("url") or "",
            location=item.get("city") or "",
            description=_html_to_text(item.get("description") or ""),
            tags=[item.get("department_name") or ""] ,
            source="jazzhr",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# --- Paylocity (US SMBs) ---

def _paylocity(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from Paylocity career sites."""
    url = f"https://www.paylocity.com/careers/{slug}/jobs/"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        return []
    jobs = []
    for card in soup.select(".job-card, [data-job-id], .career-listing"):
        title_el = card.select_one(".job-title, h3, h2, a")
        if not title_el:
            continue
        link = card.select_one("a")
        href = link.get("href", "") if link else ""
        jobs.append(Job(
            title=title_el.get_text(strip=True),
            company=slug,
            url=href if href.startswith("http") else f"https://www.paylocity.com{href}",
            location=(card.select_one(".job-location, .location") or type("", (), {"get_text": lambda s, **kw: ""})).get_text(strip=True),
            description="",
            source="paylocity",
            source_kind="ats",
        ))
    return jobs[:limit]


# --- Freshteam (Freshworks) ---

def _freshteam(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from Freshteam ATS."""
    url = f"https://{slug}.freshteam.com/jobs.json"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("job_postings", []):
        jobs.append(Job(
            title=item.get("title") or "",
            company=item.get("department") or slug,
            url=item.get("url") or "",
            location=item.get("location") or "",
            description=_html_to_text(item.get("description") or ""),
            tags=[item.get("employment_type") or ""],
            source="freshteam",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# --- Fountain (hourly workforce) ---

def _fountain(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from Fountain ATS."""
    url = f"https://api.fountain.com/v2/companies/{slug}/jobs?limit={min(limit, 100)}"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            title=item.get("title") or "",
            company=item.get("company_name") or slug,
            url=item.get("url") or "",
            location=item.get("location") or "",
            description=_html_to_text(item.get("description") or ""),
            tags=[item.get("employment_type") or ""],
            source="fountain",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# --- Deel (global remote) ---

def _deel(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from Deel's job board."""
    url = f"https://api.deel.com/v1/job-boards/{slug}/jobs?limit={min(limit, 100)}"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            title=item.get("title") or "",
            company=item.get("company_name") or slug,
            url=item.get("url") or "",
            location=item.get("location") or "",
            description=_html_to_text(item.get("description") or ""),
            tags=[item.get("employment_type") or ""],
            source="deel",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# --- Phenom (enterprise AI) ---

def _phenom(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from Phenom ATS."""
    url = f"https://{slug}.phenom.com/api/v1/jobs?limit={min(limit, 100)}"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            title=item.get("title") or "",
            company=item.get("company_name") or slug,
            url=item.get("url") or "",
            location=item.get("location") or "",
            description=_html_to_text(item.get("description") or ""),
            tags=[item.get("department") or ""],
            source="phenom",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# --- JobScore (SMBs) ---

def _jobscore(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from JobScore ATS."""
    url = f"https://api.jobscore.com/v1/companies/{slug}/jobs?limit={min(limit, 100)}"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            title=item.get("title") or "",
            company=item.get("company_name") or slug,
            url=item.get("url") or "",
            location=item.get("location") or "",
            description=_html_to_text(item.get("description") or ""),
            tags=[item.get("department") or ""],
            source="jobscore",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# --- TalentLyft (SMBs) ---

def _talentlyft(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from TalentLyft ATS."""
    url = f"https://{slug}.talentlyft.com/api/jobs?limit={min(limit, 100)}"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            title=item.get("title") or "",
            company=item.get("company_name") or slug,
            url=item.get("url") or "",
            location=item.get("location") or "",
            description=_html_to_text(item.get("description") or ""),
            tags=[item.get("department") or ""],
            source="talentlyft",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# --- Crelate (agencies) ---

def _crelate(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from Crelate ATS."""
    url = f"https://{slug}.crelate.com/api/jobs?limit={min(limit, 100)}"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            title=item.get("title") or "",
            company=item.get("company_name") or slug,
            url=item.get("url") or "",
            location=item.get("location") or "",
            description=_html_to_text(item.get("description") or ""),
            tags=[item.get("department") or ""],
            source="crelate",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# --- Recruiterflow (agencies) ---

def _recruiterflow(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from Recruiterflow ATS."""
    url = f"https://{slug}.recruiterflow.com/api/jobs?limit={min(limit, 100)}"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            title=item.get("title") or "",
            company=item.get("company_name") or slug,
            url=item.get("url") or "",
            location=item.get("location") or "",
            description=_html_to_text(item.get("description") or ""),
            tags=[item.get("department") or ""],
            source="recruiterflow",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# --- iSmartRecruit (agencies) ---

def _ismartrecruit(client: httpx.Client, slug: str, limit: int) -> list[Job]:
    """Fetch jobs from iSmartRecruit ATS."""
    url = f"https://{slug}.ismartrecruit.com/api/jobs?limit={min(limit, 100)}"
    try:
        resp = client.get(url, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            title=item.get("title") or "",
            company=item.get("company_name") or slug,
            url=item.get("url") or "",
            location=item.get("location") or "",
            description=_html_to_text(item.get("description") or ""),
            tags=[item.get("department") or ""],
            source="ismartrecruit",
            source_kind="ats",
            external_id=str(item.get("id") or ""),
        ))
    return jobs[:limit]


# ----------------------------------------------------------------- dispatch

ADAPTERS = {
    "greenhouse": _greenhouse,
    "ashby": _ashby,
    "bamboohr": _bamboohr,
    "lever": _lever,
    "workday": _workday,
    "tcs": _tcs,
    "smartrecruiters": _smartrecruiters,
    "workable": _workable,
    "breezy": _breezy,
    "teamtailor": _teamtailor,
    "hirehive": _hirehive,
    "recruitee": _recruitee,
    "rise": _rise,
    "adzuna": _adzuna,
    "usajobs": _usajobs,
    "jobvite": _jobvite,
    "icims": _icims,
    "yc": _yc,
    "linkedin": _linkedin,
    "themuse": _themuse,
    "workingnomads": _workingnomads,
    "reed": _reed,
    "rippling": _rippling,
    "personio": _personio,
    "jazzhr": _jazzhr,
    "paylocity": _paylocity,
    "freshteam": _freshteam,
    "fountain": _fountain,
    "deel": _deel,
    "phenom": _phenom,
    "jobscore": _jobscore,
    "talentlyft": _talentlyft,
    "crelate": _crelate,
    "recruiterflow": _recruiterflow,
    "ismartrecruit": _ismartrecruit,
}

# Kinds that take a (client, slug, limit, keys) signature.
_KEYED = {"adzuna", "usajobs", "reed"}


def fetch_ats_api(
    client: httpx.Client,
    kind: str,
    slug: str,
    limit: int = 200,
    api_keys: dict | None = None,
) -> list[Job]:
    """Fetch one ATS board via its official public API."""
    if kind not in ADAPTERS:
        raise KeyError(f"Unknown ATS API kind: {kind} (available: {', '.join(ADAPTERS)})")
    fn = ADAPTERS[kind]
    if kind in _KEYED:
        return fn(client, slug, limit, api_keys or {})
    return fn(client, slug, limit)
