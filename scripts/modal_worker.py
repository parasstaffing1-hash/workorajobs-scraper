"""Stateless workers used by the Modal 24/7 scraper deployment.

The workers intentionally avoid SQLite and long-lived local files. Every run:
1. loads a small slice of input,
2. scrapes with bounded concurrency,
3. writes one gzip-compressed JSONL object to S3-compatible storage,
4. exits so memory is reclaimed.
"""
from __future__ import annotations

import asyncio
import gc
import gzip
import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

COMPANIES_FILE = Path(os.environ.get("COMPANIES_FILE", "/root/data/companies_10k.json"))
DEFAULT_MAX_JOBS = int(os.environ.get("MAX_JOBS_PER_COMPANY", "50"))
DEFAULT_CONCURRENCY = int(os.environ.get("ATS_CONCURRENCY", "10"))


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> Any:
    """Convert pandas/numpy/date-ish values to JSON-safe primitives."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        try:
            return _clean(value.item())
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _dedupe_key(job: dict[str, Any]) -> str:
    raw = "|".join(
        str(job.get(k, "") or "").strip().lower()
        for k in ("title", "company", "location", "url")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _load_companies() -> list[dict[str, Any]]:
    with COMPANIES_FILE.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"{COMPANIES_FILE} must contain a JSON list")
    return data


def _s3_client():
    import boto3

    access_key = os.getenv("S3_ACCESS_KEY_ID", "")
    secret_key = os.getenv("S3_SECRET_ACCESS_KEY", "")
    bucket = os.getenv("S3_BUCKET", "")
    if not access_key or not secret_key or not bucket:
        raise RuntimeError(
            "Missing S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY or S3_BUCKET in "
            "the Modal secret 'workorajobs-runtime'"
        )

    kwargs: dict[str, Any] = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": os.getenv("S3_REGION", "us-east-1"),
    }
    endpoint = os.getenv("S3_ENDPOINT_URL", "").strip()
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    session_token = os.getenv("S3_SESSION_TOKEN", "").strip()
    if session_token:
        kwargs["aws_session_token"] = session_token

    return boto3.client("s3", **kwargs), bucket


def upload_records(
    records: list[dict[str, Any]],
    *,
    source: str,
    shard_name: str,
) -> dict[str, Any]:
    """Write a compact current snapshot and, optionally, a dated archive copy."""
    client, bucket = _s3_client()
    prefix = os.getenv("S3_OBJECT_PREFIX", "workorajobs").strip("/")
    lines = b"\n".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        for row in records
    )
    if lines:
        lines += b"\n"
    payload = gzip.compress(lines, compresslevel=6)

    current_key = f"{prefix}/current/{source}/{shard_name}.jsonl.gz"
    client.put_object(
        Bucket=bucket,
        Key=current_key,
        Body=payload,
        ContentType="application/x-ndjson",
        ContentEncoding="gzip",
        CacheControl="no-cache",
        Metadata={
            "record-count": str(len(records)),
            "updated-at": _utcnow(),
        },
    )

    archive_key = None
    if os.getenv("S3_ARCHIVE_DAILY", "0").lower() in {"1", "true", "yes"}:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d/%H%M")
        archive_key = f"{prefix}/archive/{stamp}/{source}/{shard_name}.jsonl.gz"
        client.put_object(
            Bucket=bucket,
            Key=archive_key,
            Body=payload,
            ContentType="application/x-ndjson",
            ContentEncoding="gzip",
            Metadata={"record-count": str(len(records))},
        )

    return {
        "bucket": bucket,
        "key": current_key,
        "archive_key": archive_key,
        "records": len(records),
        "compressed_bytes": len(payload),
    }


def _ats_target(company: dict[str, Any]) -> tuple[str, str] | None:
    ats = str(company.get("ats", "") or "").strip().lower()
    slug = str(company.get("slug", "") or "").strip()

    if ats == "greenhouse":
        url = company.get("greenhouse_url") or (
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
            if slug
            else ""
        )
        return (ats, str(url)) if url else None
    if ats == "lever":
        url = company.get("lever_url") or (
            f"https://api.lever.co/v0/postings/{slug}?mode=json" if slug else ""
        )
        return (ats, str(url)) if url else None
    if ats == "smartrecruiters":
        url = company.get("smartrecruiters_url") or (
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
            if slug
            else ""
        )
        return (ats, str(url)) if url else None
    return None


def _normalize_ats_jobs(
    company: dict[str, Any],
    ats: str,
    payload: Any,
) -> list[dict[str, Any]]:
    slug = str(company.get("slug", "") or "")
    company_name = str(company.get("name", "") or slug)
    scraped_at = _utcnow()
    jobs: list[dict[str, Any]] = []

    if ats == "greenhouse":
        rows = payload.get("jobs", []) if isinstance(payload, dict) else []
        for row in rows[:DEFAULT_MAX_JOBS]:
            location = row.get("location") or {}
            job = {
                "title": row.get("title", ""),
                "company": company_name,
                "location": location.get("name", "Remote") if isinstance(location, dict) else str(location),
                "url": row.get("absolute_url", ""),
                "description": str(row.get("content", "") or "")[:2000],
                "source": f"greenhouse:{slug}",
                "source_kind": "ats",
                "external_id": str(row.get("id", "") or ""),
                "posted_at": row.get("updated_at", ""),
                "scraped_at": scraped_at,
            }
            job["dedupe_key"] = _dedupe_key(job)
            jobs.append(job)

    elif ats == "lever":
        rows = payload if isinstance(payload, list) else []
        for row in rows[:DEFAULT_MAX_JOBS]:
            cats = row.get("categories") or {}
            job = {
                "title": row.get("text", ""),
                "company": company_name,
                "location": cats.get("location", "Remote") if isinstance(cats, dict) else "Remote",
                "url": row.get("hostedUrl", ""),
                "description": str(row.get("descriptionPlain", "") or "")[:2000],
                "source": f"lever:{slug}",
                "source_kind": "ats",
                "external_id": str(row.get("id", "") or ""),
                "posted_at": str(row.get("createdAt", "") or ""),
                "scraped_at": scraped_at,
            }
            job["dedupe_key"] = _dedupe_key(job)
            jobs.append(job)

    elif ats == "smartrecruiters":
        rows = payload.get("content", []) if isinstance(payload, dict) else []
        for row in rows[:DEFAULT_MAX_JOBS]:
            loc = row.get("location") or {}
            if isinstance(loc, dict):
                location = ", ".join(
                    part for part in [str(loc.get("city", "") or ""), str(loc.get("country", "") or "")]
                    if part
                ) or "Remote"
            else:
                location = str(loc) or "Remote"
            job = {
                "title": row.get("name", ""),
                "company": company_name,
                "location": location,
                "url": row.get("ref", ""),
                "description": "",
                "source": f"smartrecruiters:{slug}",
                "source_kind": "ats",
                "external_id": str(row.get("id", "") or ""),
                "posted_at": row.get("releasedDate", ""),
                "scraped_at": scraped_at,
            }
            job["dedupe_key"] = _dedupe_key(job)
            jobs.append(job)

    return jobs


async def _fetch_ats_company(client, semaphore, company: dict[str, Any]):
    target = _ats_target(company)
    if not target:
        return [], "unsupported"

    ats, url = target
    async with semaphore:
        for attempt in range(2):
            try:
                response = await client.get(url, timeout=15)
                if response.status_code == 200:
                    return _normalize_ats_jobs(company, ats, response.json()), "ok"
                if response.status_code == 429 and attempt == 0:
                    await asyncio.sleep(1.5)
                    continue
                return [], f"http_{response.status_code}"
            except Exception as exc:
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                return [], f"error:{type(exc).__name__}"
    return [], "error"


async def _run_ats_async(companies: list[dict[str, Any]]):
    import httpx

    limits = httpx.Limits(
        max_connections=max(DEFAULT_CONCURRENCY * 2, 10),
        max_keepalive_connections=max(DEFAULT_CONCURRENCY, 5),
    )
    headers = {
        "User-Agent": os.getenv(
            "SCRAPER_USER_AGENT",
            "Mozilla/5.0 (compatible; WorkoraJobsBot/1.0; +https://workorajobs.com)",
        )
    }
    semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY)
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        limits=limits,
        timeout=15,
    ) as client:
        return await asyncio.gather(
            *(_fetch_ats_company(client, semaphore, company) for company in companies)
        )


def run_ats_shard(start: int, batch_size: int = 110) -> dict[str, Any]:
    companies = _load_companies()
    total = len(companies)
    if total == 0:
        raise RuntimeError("No companies found")

    start %= total
    end = min(start + max(batch_size, 1), total)
    shard = companies[start:end]
    results = asyncio.run(_run_ats_async(shard))

    deduped: dict[str, dict[str, Any]] = {}
    status_counts: dict[str, int] = {}
    for jobs, status in results:
        status_counts[status] = status_counts.get(status, 0) + 1
        for job in jobs:
            deduped[job["dedupe_key"]] = job

    records = list(deduped.values())
    storage = upload_records(
        records,
        source="ats",
        shard_name=f"companies-{start:05d}",
    )
    next_cursor = 0 if end >= total else end

    return {
        "source": "ats",
        "started_at": start,
        "companies_processed": len(shard),
        "total_companies": total,
        "next_cursor": next_cursor,
        "jobs": len(records),
        "statuses": status_counts,
        "storage": storage,
        "finished_at": _utcnow(),
    }


def _jobspy_records(frame, *, keyword: str, location: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if frame is None or getattr(frame, "empty", False):
        return records

    for raw in frame.to_dict(orient="records"):
        location_parts = [
            _clean(raw.get("city")),
            _clean(raw.get("state")),
            _clean(raw.get("country")),
        ]
        location_text = ", ".join(str(v) for v in location_parts if v) or location or "Remote"
        min_amount = _clean(raw.get("min_amount"))
        max_amount = _clean(raw.get("max_amount"))
        interval = _clean(raw.get("interval"))
        currency = _clean(raw.get("currency"))
        salary_bits = [v for v in (min_amount, max_amount, currency, interval) if v not in (None, "")]
        job = {
            "title": _clean(raw.get("title")) or "",
            "company": _clean(raw.get("company")) or "",
            "location": location_text,
            "url": _clean(raw.get("job_url")) or _clean(raw.get("job_url_direct")) or "",
            "description": str(_clean(raw.get("description")) or "")[:2000],
            "source": f"jobspy:{_clean(raw.get('site')) or 'unknown'}",
            "source_kind": "jobspy",
            "external_id": _clean(raw.get("id")) or "",
            "salary": " | ".join(str(v) for v in salary_bits),
            "posted_at": _clean(raw.get("date_posted")) or "",
            "search_keyword": keyword,
            "search_location": location,
            "scraped_at": _utcnow(),
        }
        job["dedupe_key"] = _dedupe_key(job)
        records.append(job)
    return records


def run_jobboard_shard(query_start: int, query_count: int = 6) -> dict[str, Any]:
    from jobspy import scrape_jobs

    try:
        from scripts.job_keywords import ALL_KEYWORDS, ALL_LOCATIONS
    except ImportError:
        from job_keywords import ALL_KEYWORDS, ALL_LOCATIONS

    keywords = list(ALL_KEYWORDS)
    locations = list(ALL_LOCATIONS)
    if not keywords or not locations:
        raise RuntimeError("JobSpy keywords/locations are empty")

    total_queries = len(keywords) * len(locations)
    query_start %= total_queries
    sites = [
        s.strip()
        for s in os.getenv("JOBSPY_SITES", "indeed,linkedin").split(",")
        if s.strip()
    ]
    results_wanted = int(os.getenv("JOBSPY_RESULTS_WANTED", "20"))
    hours_old = int(os.getenv("JOBSPY_HOURS_OLD", "72"))
    proxies = [
        p.strip()
        for p in os.getenv("JOBSPY_PROXIES", "").split(",")
        if p.strip()
    ]
    country_indeed = os.getenv("JOBSPY_COUNTRY_INDEED", "").strip()

    deduped: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    processed = 0

    for offset in range(min(query_count, total_queries)):
        q = (query_start + offset) % total_queries
        keyword = keywords[(q // len(locations)) % len(keywords)]
        location = locations[q % len(locations)]
        kwargs: dict[str, Any] = {
            "site_name": sites,
            "search_term": keyword,
            "location": location,
            "results_wanted": results_wanted,
            "hours_old": hours_old,
        }
        if proxies:
            kwargs["proxies"] = proxies
        if country_indeed:
            kwargs["country_indeed"] = country_indeed

        try:
            frame = scrape_jobs(**kwargs)
            for job in _jobspy_records(frame, keyword=keyword, location=location):
                deduped[job["dedupe_key"]] = job
            processed += 1
            del frame
            gc.collect()
        except Exception as exc:
            failures.append(
                {
                    "keyword": str(keyword),
                    "location": str(location),
                    "error": f"{type(exc).__name__}: {str(exc)[:180]}",
                }
            )

    records = list(deduped.values())
    storage = upload_records(
        records,
        source="jobboards",
        shard_name=f"queries-{query_start:06d}",
    )
    next_cursor = (query_start + min(query_count, total_queries)) % total_queries

    return {
        "source": "jobboards",
        "query_start": query_start,
        "queries_processed": processed,
        "queries_attempted": min(query_count, total_queries),
        "total_query_space": total_queries,
        "next_cursor": next_cursor,
        "jobs": len(records),
        "failures": failures[:20],
        "storage": storage,
        "finished_at": _utcnow(),
    }


def send_heartbeat(url: str) -> None:
    if not url:
        return
    import httpx

    try:
        httpx.get(url, timeout=10)
    except Exception:
        # A monitoring outage must never fail a successful scrape.
        pass
