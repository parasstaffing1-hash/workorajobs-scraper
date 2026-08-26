"""Core data models for the job collector."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    """A single job posting collected from any source."""

    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    # Human-readable source id, e.g. "remotive", "rss:GitLab", "careers:Mozilla".
    source: str = "unknown"
    # One of: board | rss | careers | ats
    source_kind: str = "board"
    external_id: str = ""
    posted_at: datetime | None = None
    salary: str = ""
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
    is_active: bool = True

    @property
    def dedupe_key(self) -> str:
        """Stable unique key used to dedupe across daily runs."""
        if self.external_id:
            return f"{self.source}:{self.external_id}"
        raw = "|".join(
            str(p).strip().lower() for p in (self.title, self.company, self.location, self.url)
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"{self.source}:{digest}"


class CompanyConfig(BaseModel):
    """A company whose career page(s) should be crawled."""

    name: str
    careers_url: str
    # Restrict link-following to these hosts. Defaults to the careers_url host.
    domain: str | None = None
    # Optional CSS selector for job links (e.g. "a[href*='/jobs/']"). When set, the
    # generic keyword filter is bypassed (use link_pattern if you still need one).
    link_selector: str | None = None
    # Optional regex matched against candidate hrefs.
    link_pattern: str | None = None
    # Substrings that disqualify a link (blog posts, news, etc.).
    exclude_keywords: list[str] = Field(
        default_factory=lambda: ["blog", "news", "event", "about", "press", "faq", "contact"]
    )
    # Max number of job-detail pages fetched per company per run.
    max_pages: int = 40
    # Render the page with Playwright (requires `pip install jobcollector[js]`).
    use_js: bool = False


class SourceConfig(BaseModel):
    """Top-level YAML configuration."""

    boards: list[str] = Field(default_factory=list)
    rss_feeds: list[dict[str, str]] = Field(default_factory=list)
    # Company slugs on ATS-hosted career boards (official public APIs).
    greenhouse: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)
    bamboohr: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    # Workday slugs use the format "company|wd{n}|site_id", e.g. "7eleven|wd3|7eleven".
    workday: list[str] = Field(default_factory=list)
    # TCS slugs are just labels (the official iBegin portal is keyless); the
    # company name and endpoint are fixed inside the adapter.
    tcs: list[str] = Field(default_factory=list)
    # Additional keyless ATS platforms (all official public job APIs).
    smartrecruiters: list[str] = Field(default_factory=list)
    workable: list[str] = Field(default_factory=list)
    breezy: list[str] = Field(default_factory=list)
    teamtailor: list[str] = Field(default_factory=list)
    hirehive: list[str] = Field(default_factory=list)
    recruitee: list[str] = Field(default_factory=list)
    rise: list[str] = Field(default_factory=list)
    # Adzuna slugs are country codes (us, gb, de, ...); needs api_keys below.
    adzuna: list[str] = Field(default_factory=list)
    # USAJobs (usajobs.gov); needs api_keys below. Slug is ignored (single board).
    usajobs: list[str] = Field(default_factory=list)
    # Jobvite slugs are the company name on jobs.jobvite.com (e.g. "carfax").
    jobvite: list[str] = Field(default_factory=list)
    # iCIMS slugs are the tenant on careers-{tenant}.icims.com (e.g. "here").
    icims: list[str] = Field(default_factory=list)
    # YC slugs are company slugs on ycombinator.com/companies/{slug} (server-
    # rendered pages embed the company's current jobs as JSON). Generated from
    # the YC directory by scripts/build_yc_companies.py + generate_yc_config.py.
    yc: list[str] = Field(default_factory=list)
    # LinkedIn guest-search queries: "keywords" | "keywords|location" |
    # "keywords|location|days". Unofficial endpoint — see the adapter docstring
    # for the ToS/robustness caveats.
    linkedin: list[str] = Field(default_factory=list)
    # The Muse (keyless public JSON API). Slug is ignored (one aggregate board).
    themuse: list[str] = Field(default_factory=list)
    # Working Nomads (keyless remote-jobs API). Slug is ignored.
    workingnomads: list[str] = Field(default_factory=list)
    # Reed UK (keyed; needs reed_api_key). Slugs are search keywords.
    reed: list[str] = Field(default_factory=list)
    # Additional ATS platforms (2026 additions).
    rippling: list[str] = Field(default_factory=list)
    personio: list[str] = Field(default_factory=list)
    jazzhr: list[str] = Field(default_factory=list)
    paylocity: list[str] = Field(default_factory=list)
    freshteam: list[str] = Field(default_factory=list)
    fountain: list[str] = Field(default_factory=list)
    deel: list[str] = Field(default_factory=list)
    phenom: list[str] = Field(default_factory=list)
    jobscore: list[str] = Field(default_factory=list)
    talentlyft: list[str] = Field(default_factory=list)
    crelate: list[str] = Field(default_factory=list)
    recruiterflow: list[str] = Field(default_factory=list)
    ismartrecruit: list[str] = Field(default_factory=list)
    # Optional API keys for keyed sources, e.g.
    #   api_keys:
    #     adzuna_app_id: "..."      # free: developer.adzuna.com
    #     adzuna_api_key: "..."
    #     usajobs_api_key: "..."    # free: developer.usajobs.gov
    #     usajobs_user_agent: "you@example.com"
    # Values also fall back to env vars ADZUNA_APP_ID / ADZUNA_API_KEY /
    # USAJOBS_API_KEY / USAJOBS_USER_AGENT.
    api_keys: dict[str, str] = Field(default_factory=dict)
    companies: list[CompanyConfig] = Field(default_factory=list)

    @property
    def company_names(self) -> list[str]:
        return [c.name for c in self.companies]


class RunReport(BaseModel):
    """Summary of one collection pass."""

    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    sources: list[str] = Field(default_factory=list)
    groups_run: list[str] = Field(default_factory=list)
    jobs_seen: int = 0
    jobs_new: int = 0
    jobs_expired: int = 0
    errors: list[str] = Field(default_factory=list)
