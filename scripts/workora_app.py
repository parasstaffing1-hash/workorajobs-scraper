"""Workora Jobs — Complete Platform Backend.

Features:
  - User Registration & Login (session-based auth)
  - SEO-Optimized Job Listing Pages (JSON-LD structured data)
  - SEO-Optimized Company Profile Pages
  - Saved Jobs / Bookmarks
  - Job Application Tracking
  - Job Alerts (email notifications)
  - Job Recommendation Engine
  - Admin Panel
  - Employer Dashboard
  - Sitemap Generator
  - Contact Form
  - REST API endpoints

Usage:
    python -m scripts.workora_app
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form, Response, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from markupsafe import Markup
import uvicorn

# Import models
from .models import (
    get_db, init_db, create_user, authenticate, create_session,
    get_user_from_token, delete_session, save_job, unsave_job,
    get_saved_jobs, is_job_saved, get_saved_count,
    create_application, update_application, get_applications,
    get_application_stats, create_alert, get_user_alerts,
    delete_alert, get_active_alerts, match_alert_to_jobs,
    get_employer, create_employer, get_dashboard_stats,
    submit_contact, search_employers
)

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"

# Ensure dirs exist
TEMPLATE_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)
(STATIC_DIR / "css").mkdir(exist_ok=True)
(STATIC_DIR / "js").mkdir(exist_ok=True)

# ── App ───────────────────────────────────────────────────────
# Simple cache
app = FastAPI(title="Workora Jobs", version="1.0.0")
# Mount static files
STATIC_DIR = ROOT / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Template Engine (minimal Jinja2-like) ─────────────────────

def render(template_name: str, context: dict = None) -> str:
    """Simple template renderer."""
    ctx = context or {}
    ctx.setdefault("current_year", datetime.now().year)
    # Get user from cookie if available
    ctx.setdefault("user", None)
    ctx.setdefault("page_title", "Workora Jobs")
    ctx.setdefault("meta_description", "Find your dream job from 700K+ listings. Search jobs by keyword, location, salary. Get daily alerts.")
    ctx.setdefault("meta_keywords", "jobs, careers, hiring, job search, employment, work from home, remote jobs")
    ctx.setdefault("canonical_url", "/")
    ctx.setdefault("og_image", "/static/og-image.png")

    tpl_path = TEMPLATE_DIR / template_name
    if not tpl_path.exists():
        return f"<h1>Template not found: {template_name}</h1>"

    content = tpl_path.read_text(encoding="utf-8")

    # Simple template variable replacement
    for key, val in ctx.items():
        if isinstance(val, (str, int, float)):
            content = content.replace("{{" + key + "}}", str(val))
        elif isinstance(val, Markup) or (hasattr(val, '__html__')):
            content = content.replace("{{" + key + "}}", str(val))
        elif isinstance(val, (list, dict)):
            content = content.replace("{{" + key + "}}", json.dumps(val, default=str))

    # Block handling (simple)
    import re
    def render_blocks(text):
        # Handle {% if var %}...{% endif %}
        def replace_if(m):
            var_name = m.group(1).strip()
            body = m.group(2)
            else_part = m.group(3) if m.group(3) else ""
            val = ctx.get(var_name)
            if val and val != "" and val != 0:
                return body
            return else_part

        text = re.sub(
            r'\{%\s*if\s+(\w+)\s*%\}(.*?)(?:\{%\s*else\s*%\}(.*?))?\{%\s*endif\s*%\}',
            replace_if, text, flags=re.DOTALL
        )

        # Handle {% for x in list %}...{% endfor %}
        def replace_for(m):
            var = m.group(1)
            listvar = m.group(2)
            body = m.group(3)
            items = ctx.get(listvar, [])
            result = ""
            for item in items:
                line = body
                if isinstance(item, dict):
                    for k, v in item.items():
                        if isinstance(v, (str, int, float)):
                            line = line.replace("{{" + var + "." + k + "}}", str(v))
                        else:
                            line = line.replace("{{" + var + "." + k + "}}", str(v) if v else "")
                else:
                    line = line.replace("{{" + var + "}}", str(item))
                result += line
            return result

        text = re.sub(
            r'\{%\s*for\s+(\w+)\s+in\s+(\w+)\s*%\}(.*?)\{%\s*endfor\s*%\}',
            replace_for, text, flags=re.DOTALL
        )

        return text

    content = render_blocks(content)
    return content


# ── Auth Middleware ────────────────────────────────────────────
async def get_current_user(request: Request):
    token = request.cookies.get("session_token")
    if token:
        return get_user_from_token(token)
    return None


# ── Base Template ─────────────────────────────────────────────
def page(content: str, title: str = "Workora Jobs", user=None, description: str = None,
         canonical: str = "/", page_type: str = "website") -> HTMLResponse:
    desc = description or "Find your dream job from 700K+ job listings. Search by keyword, location, salary. Free daily job alerts."

    seo_meta = f"""
    <title>{html.escape(title)}</title>
    <meta name="description" content="{html.escape(desc)}">
    <meta name="keywords" content="{html.escape(desc)}">
    <link rel="canonical" href="https://workorajobs.com{canonical}">

    <!-- Open Graph -->
    <meta property="og:type" content="{page_type}">
    <meta property="og:title" content="{html.escape(title)}">
    <meta property="og:description" content="{html.escape(desc)}">
    <meta property="og:url" content="https://workorajobs.com{canonical}">
    <meta property="og:site_name" content="Workora Jobs">

    <!-- Twitter Card -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{html.escape(title)}">
    <meta name="twitter:description" content="{html.escape(desc)}">

    <!-- Favicon -->
    <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon.svg">
    <meta name="theme-color" content="#2563eb">


    """

    nav_user = ""
    if user:
        saved_count = get_saved_count(user["id"])
        nav_user = f"""
        <a href="/dashboard" class="nav-link">Dashboard</a>
        <a href="/saved" class="nav-link">Saved ({saved_count})</a>
        <a href="/applications" class="nav-link">Applications</a>
        <a href="/alerts" class="nav-link">Alerts</a>
        <a href="/logout" class="nav-btn">Logout</a>
        """
    else:
        nav_user = """
        <a href="/login" class="nav-link">Login</a>
        <a href="/register" class="nav-btn">Sign Up Free</a>
        """

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{seo_meta}
<link rel="stylesheet" href="/static/css/main.css">
</head>
<body>
<nav class="navbar">
  <div class="nav-container">
    <a href="/" class="logo">🚀 <span>Workora</span> Jobs</a>
    <div class="nav-links">
      <a href="/jobs" class="nav-link">Find Jobs</a>
      <a href="/companies" class="nav-link">Companies</a>
      <a href="/layoffs" class="nav-link">Layoff Tracker</a>
      <a href="/salary" class="nav-link">Salary Data</a>
      <a href="/about" class="nav-link">About</a>
      {nav_user}
    </div>
  </div>
</nav>
<main class="container">{content}</main>
<footer class="footer">
  <div class="footer-content">
    <div class="footer-col">
      <h4>🚀 Workora Jobs</h4>
      <p>Find your dream job from 700K+ listings. AI-powered job matching and alerts.</p>
    </div>
    <div class="footer-col">
      <h4>Quick Links</h4>
      <a href="/jobs">Browse Jobs</a>
      <a href="/companies">Companies</a>
      <a href="/salary">Salary Data</a>
      <a href="/layoffs">Layoff Tracker</a>
    </div>
    <div class="footer-col">
      <h4>For Employers</h4>
      <a href="/employer/dashboard">Post a Job</a>
      <a href="/employer/pricing">Pricing</a>
      <a href="/api/docs">API Access</a>
    </div>
    <div class="footer-col">
      <h4>Legal</h4>
      <a href="/privacy">Privacy Policy</a>
      <a href="/terms">Terms of Service</a>
      <a href="/contact">Contact Us</a>
    </div>
  </div>
  <div class="footer-bottom">
    <p>&copy; {datetime.now().year} Workora Jobs. All rights reserved. Built with ❤️</p>
  </div>
</footer>
<script src="/static/js/main.js"></script>
    <div id="cookie-consent" style="position:fixed;bottom:0;left:0;right:0;background:#1e293b;color:#e2e8f0;padding:16px 24px;display:flex;align-items:center;justify-content:space-between;z-index:9999;font-size:14px">
        <span>🍪 We use cookies to improve your experience. By continuing, you agree to our <a href="/privacy" style="color:#60a5fa">Privacy Policy</a>.</span>
        <button onclick="document.getElementById("cookie-consent").style.display="none";localStorage.setItem("cookies_accepted","1")" style="background:#2563eb;color:white;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-weight:600;margin-left:16px">Accept</button>
    </div>
    <script>if(localStorage.getItem("cookies_accepted"))document.getElementById("cookie-consent").style.display="none";</script>
</body>
</html>"""
    return HTMLResponse(content=full_html)


# ══════════════════════════════════════════════════════════════
# PUBLIC PAGES
# ══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = await get_current_user(request)
    stats = get_dashboard_stats()

    content = f"""
    <section class="hero">
        <h1>Find Your Dream Job</h1>
        <p class="subtitle">Search {stats['total_jobs']:,}+ jobs from {stats['total_companies']:,} companies across 20+ platforms</p>
        <form action="/jobs" method="get" class="hero-search">
            <input type="text" name="q" placeholder="Job title, keyword, or company..." class="search-input-large">
            <input type="text" name="location" placeholder="City or Remote" class="search-input-medium">
            <button type="submit" class="btn btn-primary btn-large">🔍 Search Jobs</button>
        </form>
        <div class="hero-stats">
            <div class="hero-stat">
                <span class="big-number">{stats['total_jobs']:,}</span>
                <span class="stat-label">Job Listings</span>
            </div>
            <div class="hero-stat">
                <span class="big-number">{stats['total_companies']:,}</span>
                <span class="stat-label">Companies</span>
            </div>
            <div class="hero-stat">
                <span class="big-number">{stats['jobs_today']:,}</span>
                <span class="stat-label">New Today</span>
            </div>
            <div class="hero-stat">
                <span class="big-number">20+</span>
                <span class="stat-label">Job Sources</span>
            </div>
        </div>
    </section>

    <section class="section">
        <h2>🔥 Trending Skills</h2>
        <div class="tag-cloud">
            {"".join(f'<a href="/jobs?q={s["skill"]}" class="tag">{s["skill"]} <small>({s["count"]:,})</small></a>' for s in stats['top_skills'][:15])}
        </div>
    </section>

    <section class="section">
        <h2>📍 Popular Locations</h2>
        <div class="tag-cloud">
            {"".join(f'<a href="/jobs?location={l["location"]}" class="tag tag-location">{l["location"]} <small>({l["count"]:,})</small></a>' for l in stats['top_locations'][:12])}
        </div>
    </section>

    <section class="section">
        <h2>📊 Top Job Sources</h2>
        <div class="source-grid">
            {"".join(f'<div class="source-card"><strong>{s["source"]}</strong><br><span class="count">{s["count"]:,}</span> jobs</div>' for s in stats['sources'][:12])}
        </div>
    </section>

    <section class="cta-section">
        <h2>Never Miss a Job</h2>
        <p>Set up free job alerts and get notified when matching jobs are posted.</p>
        <a href="/alerts/new" class="btn btn-primary btn-large">Set Up Job Alerts →</a>
    </section>
    """
    return page(content, "Workora Jobs - Find Your Dream Job from 700K+ Listings", user)


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return """User-agent: *
Allow: /
Disallow: /api/
Disallow: /dashboard
Disallow: /saved
Disallow: /applications
Disallow: /alerts

Sitemap: https://workorajobs.com/sitemap.xml"""


@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap():
    """Dynamic sitemap for SEO."""
    urls = [
        ("https://workorajobs.com/", "1.0", "daily"),
        ("https://workorajobs.com/jobs", "0.9", "daily"),
        ("https://workorajobs.com/companies", "0.8", "weekly"),
        ("https://workorajobs.com/salary", "0.7", "weekly"),
        ("https://workorajobs.com/layoffs", "0.8", "daily"),
        ("https://workorajobs.com/about", "0.5", "monthly"),
        ("https://workorajobs.com/contact", "0.5", "monthly"),
    ]

    # Add top job pages
    with get_db() as db:
        jobs = db.execute("""
            SELECT dedupe_key, title, company, location, posted_at
            FROM jobs WHERE url IS NOT NULL AND title != ''
            ORDER BY posted_at DESC LIMIT 50000
        """).fetchall()

        for job in jobs:
            slug = re.sub(r'[^a-z0-9]+', '-', f"{job['title']}-{job['company']}".lower())[:80]
            slug = slug.strip('-')
            url = f"https://workorajobs.com/job/{slug}?id={job['dedupe_key']}"
            urls.append((url, "0.7", "weekly"))

        # Add top company pages
        companies = db.execute("""
            SELECT DISTINCT company, COUNT(*) as cnt
            FROM jobs WHERE company != '' AND company IS NOT NULL
            GROUP BY LOWER(company)
            ORDER BY cnt DESC LIMIT 5000
        """).fetchall()

        for c in companies:
            slug = re.sub(r'[^a-z0-9]+', '-', c['company'].lower()).strip('-')
            url = f"https://workorajobs.com/company/{slug}"
            urls.append((url, "0.6", "weekly"))

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for url, freq, change in urls:
        xml += f'<url><loc>{url}</loc><changefreq>{freq}</changefreq><priority>{change}</priority></url>\n'
    xml += '</urlset>'
    return PlainTextResponse(content=xml, media_type="application/xml")


# ── Jobs Pages (SEO) ──────────────────────────────────────────

@app.get("/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request, q: str = "", location: str = "",
                    source: str = "", fresh: str = "", company: str = "",
                    page_num: int = 1, per_page: int = 20):
    user = await get_current_user(request)
    offset = (page_num - 1) * per_page

    conditions = []
    params = []

    if q:
        conditions.append("(title LIKE ? OR company LIKE ? OR description LIKE ? OR tags LIKE ?)")
        params.extend([f"%{q}%"] * 4)
    if location:
        conditions.append("location LIKE ?")
        params.append(f"%{location}%")
    if source:
        conditions.append("source = ?")
        params.append(source)
    if company:
        conditions.append("(company LIKE ?)")
        params.append(f"%{company}%")
    if fresh == "1h":
        conditions.append("(first_seen_at > datetime('now', '-1 hour'))")
    elif fresh == "24h":
        conditions.append("(first_seen_at > datetime('now', '-1 day'))")
    elif fresh == "7d":
        conditions.append("(first_seen_at > datetime('now', '-7 days'))")
    elif fresh == "30d":
        conditions.append("(first_seen_at > datetime('now', '-30 days'))")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    with get_db() as db:
        total = db.execute(f"SELECT COUNT(*) FROM jobs {where}", params).fetchone()[0]
        jobs = db.execute(
            f"SELECT * FROM jobs {where} ORDER BY posted_at DESC, first_seen_at DESC LIMIT ? OFFSET ?",
            params + [per_page, offset]
        ).fetchall()

        total_pages = max(1, math.ceil(total / per_page))

    # Build structured data for search results page
    structured_data = json.dumps({
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": f"Job Search Results{' - ' + q if q else ''}",
        "description": f"Search {total:,} jobs" + (f" for '{q}'" if q else ""),
        "url": f"https://workorajobs.com/jobs?q={q}&location={location}",
        "numberOfItems": total,
        "itemListElement": [{
            "@type": "ListItem",
            "position": i + offset + 1,
            "url": f"https://workorajobs.com/job/{re.sub(r'[^a-z0-9]+', '-', (j['title'] or '').lower())[:40]}?id={j['dedupe_key']}",
            "name": j["title"] or "Unknown",
            "hiringOrganization": {"@type": "Organization", "name": j["company"] or "Unknown"},
            "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": j["location"] or "Remote"}},
        } for i, j in enumerate(jobs[:10])]
    }, ensure_ascii=False)

    job_rows = ""
    for j in [dict(row) for row in jobs]:
        saved = is_job_saved(user["id"], j["dedupe_key"]) if user else False
        save_icon = "❤️" if saved else "🤍"
        save_class = "saved" if saved else ""
        posted = j["posted_at"][:10] if j.get("posted_at") else "Unknown"
        salary = j.get("salary", "") or ""
        tags_html = ""
        if j.get("tags"):
            tags_list = j["tags"].split(",")[:3]
            tags_html = "".join(f'<span class="tag-small">{t.strip()}</span>' for t in tags_list)

        slug = re.sub(r'[^a-z0-9]+', '-', (j["title"] or "").lower())[:40].strip('-')
        desc_preview = (j["description"] or "")[:150]
        if len(desc_preview) >= 150:
            desc_preview += "..."

        job_rows += f"""
        <div class="job-card" data-key="{j['dedupe_key']}">
            <div class="job-header">
                <h3><a href="/job/{slug}?id={j['dedupe_key']}">{html.escape(j['title'] or 'Unknown Position')}</a></h3>
                <button class="save-btn {save_class}" onclick="toggleSave('{j['dedupe_key']}')" title="Save job">{save_icon}</button>
            </div>
            <div class="job-meta">
                <span class="company">🏢 {html.escape(j['company'] or 'Unknown')}</span>
                <span class="location">📍 {html.escape(j['location'] or 'Remote')}</span>
                {f'<span class="salary">💰 {html.escape(salary)}</span>' if salary else ''}
                <span class="date">📅 {posted}</span>
                <span class="source-tag">{html.escape(j['source'] or '')}</span>
            </div>
            <p class="job-desc">{html.escape(desc_preview)}</p>
            <div class="job-tags">{tags_html}</div>
        </div>
        """

    # Pagination
    pagination = '<div class="pagination">'
    if page_num > 1:
        pagination += f'<a href="?q={q}&location={location}&source={source}&page_num={page_num-1}" class="page-btn">← Prev</a>'
    for p in range(max(1, page_num-2), min(total_pages+1, page_num+3)):
        active = "active" if p == page_num else ""
        pagination += f'<a href="?q={q}&location={location}&source={source}&page_num={p}" class="page-btn {active}">{p}</a>'
    if page_num < total_pages:
        pagination += f'<a href="?q={q}&location={location}&source={source}&page_num={page_num+1}" class="page-btn">Next →</a>'
    pagination += '</div>'

    content = f"""
    <script type="application/ld+json">{structured_data}</script>
    <section class="page-header">
        <h1>Job Search {f'— "{html.escape(q)}"' if q else ''} {f'in {html.escape(location)}' if location else ''}</h1>
        <p class="subtitle">{total:,} jobs found</p>
    </section>

    <div class="search-bar">
        <form action="/jobs" method="get" class="search-row">
            <input type="text" name="q" placeholder="Job title, keyword..." value="{html.escape(q)}">
            <input type="text" name="location" placeholder="City or Remote" value="{html.escape(location)}">
            <input type="text" name="company" placeholder="Company name" value="{html.escape(company)}">
            <select name="fresh">
                <option value="">All Time</option>
                <option value="1h" {"selected" if fresh=='1h' else ''}>Last Hour</option>
                <option value="24h" {"selected" if fresh=='24h' else ''}>Last 24 Hours</option>
                <option value="7d" {"selected" if fresh=='7d' else ''}>Last 7 Days</option>
                <option value="30d" {"selected" if fresh=='30d' else ''}>Last 30 Days</option>
            </select>
            <select name="source">
                <option value="">All Sources</option>
                <option value="linkedin">LinkedIn</option>
                <option value="indeed">Indeed</option>
                <option value="glassdoor">Glassdoor</option>
                <option value="google">Google</option>
                <option value="naukri">Naukri</option>
                <option value="greenhouse">Greenhouse</option>
                <option value="ashby">Ashby</option>
                <option value="lever">Lever</option>
                <option value="workable">Workable</option>
                <option value="simplyhired">SimplyHired</option>
            </select>
            <button type="submit" class="btn btn-primary">Search</button>
        </form>
    </div>

    <div class="job-list">
        {job_rows if job_rows else '<p class="no-results">No jobs found. Try different keywords or locations.</p>'}
    </div>

    {pagination if total_pages > 1 else ''}
    """
    return page(content, f"Job Search{' - '+q if q else ''} | Workora Jobs", user,
                canonical=f"/jobs?q={q}&location={location}")


@app.get("/job/{slug}", response_class=HTMLResponse)
async def job_detail(request: Request, slug: str, id: str = ""):
    user = await get_current_user(request)

    with get_db() as db:
        if id:
            job = db.execute("SELECT * FROM jobs WHERE dedupe_key = ?", (id,)).fetchone()
        else:
            job = None

    if not job:
        return page("<h1>Job Not Found</h1><p><a href='/jobs'>Back to Jobs</a></p>",
                    "Job Not Found | Workora Jobs")

    job = dict(job)
    saved = is_job_saved(user["id"], job["dedupe_key"]) if user else False
    posted = job.get("posted_at", "")[:10]
    salary = job.get("salary", "")
    desc_html = html.escape(job.get("description", "") or "").replace("\n", "<br>")

    # JSON-LD structured data for job
    job_structured = json.dumps({
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": job.get("title", ""),
        "description": desc_html,
        "datePosted": posted,
        "hiringOrganization": {
            "@type": "Organization",
            "name": job.get("company", "Unknown"),
        },
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": job.get("location", "Remote"),
            }
        },
        "employmentType": "FULL_TIME",
        "url": job.get("url", ""),
        "baseSalary": {"@type": "MonetaryAmount", "value": salary} if salary else None,
    }, ensure_ascii=False, default=str)

    # Find similar jobs
    similar = []
    if job.get("tags"):
        tags = job["tags"].split(",")[:2]
        with get_db() as db:
            for tag in tags:
                rows = db.execute(
                    "SELECT * FROM tags WHERE tags LIKE ? AND dedupe_key != ? LIMIT 5",
                    (f"%{tag.strip()}%", job["dedupe_key"])
                ).fetchall() if False else []
                # Simpler query
                rows = db.execute(
                    "SELECT * FROM jobs WHERE tags LIKE ? AND dedupe_key != ? LIMIT 5",
                    (f"%{tag.strip()}%", job["dedupe_key"])
                ).fetchall()
                similar.extend([dict(r) for r in rows])

    similar_html = ""
    if similar:
        similar_items = ""
        seen = set()
        for s in similar[:4]:
            if s["dedupe_key"] in seen:
                continue
            seen.add(s["dedupe_key"])
            sslug = re.sub(r'[^a-z0-9]+', '-', (s["title"] or "").lower())[:40]
            similar_items += f"""
            <div class="job-card compact">
                <h4><a href="/job/{sslug}?id={s['dedupe_key']}">{html.escape(s['title'] or '')}</a></h4>
                <span class="company">{html.escape(s['company'] or '')}</span>
                <span class="location">{html.escape(s['location'] or '')}</span>
            </div>
            """
        similar_html = f'<section class="similar-jobs"><h3>Similar Jobs</h3><div class="job-grid">{similar_items}</div></section>'

    tags_html = ""
    if job.get("tags"):
        tags_list = job["tags"].split(",")[:8]
        tags_html = "".join(f'<span class="tag">{t.strip()}</span>' for t in tags_list)

    save_btn = f'<button class="btn {"btn-danger" if saved else "btn-primary"}" onclick="toggleSave(\'{job["dedupe_key"]}\')">{("❤️ Saved" if saved else "🤍 Save Job")}</button>' if user else '<a href="/login" class="btn btn-primary">Login to Save</a>'

    apply_btn = ""
    if user:
        apply_btn = f' <a href="/apply?id={job["dedupe_key"]}" class="btn btn-success">📝 Apply Now</a>'
    elif job.get("url"):
        apply_btn = f' <a href="{html.escape(job["url"])}" target="_blank" class="btn btn-success">📝 Apply Now</a>'

    content = f"""
    <script type="application/ld+json">{job_structured}</script>
    <div class="job-detail">
        <div class="job-detail-header">
            <div>
                <h1>{html.escape(job.get('title', 'Unknown Position'))}</h1>
                <div class="job-meta-large">
                    <span class="company">🏢 {html.escape(job.get('company', 'Unknown'))}</span>
                    <span class="location">📍 {html.escape(job.get('location', 'Remote'))}</span>
                    {f'<span class="salary">💰 {html.escape(salary)}</span>' if salary else ''}
                    <span class="date">📅 Posted: {posted}</span>
                    <span class="source-tag">{html.escape(job.get('source', ''))}</span>
                </div>
                <div class="job-actions">
                    {save_btn}
                    {apply_btn}
                    {f'<a href="{html.escape(job.get("url", "#"))}" target="_blank" class="btn btn-outline">🔗 View Original</a>' if job.get("url") else ''}
                </div>
            </div>
        </div>

        <div class="job-body">
            <h2>Job Description</h2>
            <div class="description">{desc_html}</div>

            <div class="job-tags">{tags_html}</div>
        </div>

        {similar_html}
    </div>
    """

    return page(content, f"{job.get('title', 'Job')} at {job.get('company', '')} | Workora Jobs",
                user, description=f"{job.get('title', '')} at {job.get('company', '')} in {job.get('location', '')}",
                canonical=f"/job/{slug}", page_type="article")


# ── Company Pages (SEO) ──────────────────────────────────────

@app.get("/companies", response_class=HTMLResponse)
async def companies_list(request: Request, q: str = "", page_num: int = 1, per_page: int = 50):
    user = await get_current_user(request)
    offset = (page_num - 1) * per_page

    with get_db() as db:
        if q:
            total = db.execute(
                "SELECT COUNT(DISTINCT LOWER(company)) FROM jobs WHERE company LIKE ?",
                (f"%{q}%",)
            ).fetchone()[0]
            companies = db.execute(
                """SELECT LOWER(company) as slug, company, COUNT(*) as job_count,
                   GROUP_CONCAT(DISTINCT location) as locations
                   FROM jobs WHERE company LIKE ? AND company != ''
                   GROUP BY LOWER(company) ORDER BY job_count DESC LIMIT ? OFFSET ?""",
                (f"%{q}%", per_page, offset)
            ).fetchall()
        else:
            total = db.execute(
                "SELECT COUNT(DISTINCT LOWER(company)) FROM jobs WHERE company != ''"
            ).fetchone()[0]
            companies = db.execute(
                """SELECT LOWER(company) as slug, company, COUNT(*) as job_count,
                   GROUP_CONCAT(DISTINCT location) as locations
                   FROM jobs WHERE company != ''
                   GROUP BY LOWER(company) ORDER BY job_count DESC LIMIT ? OFFSET ?""",
                (per_page, offset)
            ).fetchall()

    total_pages = max(1, math.ceil(total / per_page))

    cards = ""
    for c in companies:
        locs = (c["locations"] or "Various")[:80]
        slug = re.sub(r'[^a-z0-9]+', '-', c["slug"]).strip('-')
        cards += f"""
        <a href="/company/{slug}?name={html.escape(c['company'])}" class="company-card">
            <h3>{html.escape(c['company'])}</h3>
            <p class="count">{c['job_count']:,} open positions</p>
            <p class="locations">📍 {html.escape(locs)}</p>
        </a>
        """

    return page(f"""
    <section class="page-header">
        <h1>Companies Hiring Now</h1>
        <p class="subtitle">{total:,} companies with open positions</p>
    </section>
    <div class="search-bar">
        <form action="/companies" method="get" class="search-row">
            <input type="text" name="q" placeholder="Search company..." value="{html.escape(q)}">
            <button type="submit" class="btn btn-primary">Search</button>
        </form>
    </div>
    <div class="company-grid">{cards if cards else '<p>No companies found.</p>'}</div>
    """, f"Companies{' - '+q if q else ''} | Workora Jobs", user,
        canonical=f"/companies?q={q}")


@app.get("/company/{name}", response_class=HTMLResponse)
async def company_detail(request: Request, name: str, url_name: str = ""):
    user = await get_current_user(request)
    display_name = url_name or name

    with get_db() as db:
        # Find all jobs for this company
        rows = db.execute(
            "SELECT * FROM jobs WHERE LOWER(company) = LOWER(?) ORDER BY posted_at DESC LIMIT 200",
            (display_name,)
        ).fetchall()
        if not rows:
            # Try fuzzy match
            rows = db.execute(
                "SELECT * FROM jobs WHERE company LIKE ? ORDER BY posted_at DESC LIMIT 200",
                (f"%{display_name}%",)
            ).fetchall()

        if not rows:
            return page(f"<h1>Company not found</h1><p><a href='/companies'>Browse companies</a></p>",
                        "Company Not Found | Workora Jobs")

        display_name = rows[0]["company"]
        job_count = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE LOWER(company) = LOWER(?)",
            (display_name,)
        ).fetchone()[0]

        locations = db.execute(
            "SELECT location, COUNT(*) as cnt FROM jobs "
            "WHERE LOWER(company) = LOWER(?) AND location != '' "
            "GROUP BY location ORDER BY cnt DESC LIMIT 10",
            (display_name,)
        ).fetchall()

        all_tags = db.execute(
            "SELECT tags FROM jobs WHERE LOWER(company) = LOWER(?) AND tags != ''",
            (display_name,)
        ).fetchall()

    tag_count = {}
    for row in all_tags:
        for t in row[0].split(","):
            t = t.strip().lower()
            if t:
                tag_count[t] = tag_count.get(t, 0) + 1
    top_tags = sorted(tag_count.items(), key=lambda x: x[1], reverse=True)[:15]

    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

    # JSON-LD for company
    company_json = json.dumps({
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": display_name,
        "url": f"https://workorajobs.com/company/{slug}",
    })

    job_rows = ""
    for j in rows[:50]:
        posted = j["posted_at"][:10] if j.get("posted_at") else ""
        jslug = re.sub(r'[^a-z0-9]+', '-', (j["title"] or "").lower())[:40]
        salary = j.get("salary", "")
        job_rows += f"""
        <tr>
            <td><a href="/job/{jslug}?id={j['dedupe_key']}">{html.escape(j['title'] or '')}</a></td>
            <td>{html.escape(j['location'] or '')}</td>
            <td>{html.escape(salary) if salary else '—'}</td>
            <td>{posted}</td>
        </tr>
        """

    return page(f"""
    <script type="application/ld+json">{company_json}</script>
    <section class="page-header">
        <h1>{html.escape(display_name)}</h1>
        <p class="subtitle">{job_count:,} open positions</p>
    </section>

    <div class="company-detail">
        <div class="company-stats">
            <div class="stat-card"><div class="label">Jobs</div><div class="value">{job_count}</div></div>
            <div class="stat-card"><div class="label">Locations</div><div class="value">{len(locations)}</div></div>
            <div class="stat-card"><div class="label">Top Skills</div><div class="value">{len(top_tags)}</div></div>
        </div>

        <div class="company-locations">
            <h3>📍 Locations</h3>
            {"".join(f'<span class="tag">{l["location"]} ({l["count"]})</span>' for l in locations)}
        </div>

        <div class="company-skills">
            <h3>🛠️ Required Skills</h3>
            <div class="tag-cloud">
                {"".join(f'<a href="/jobs?q={t[0]}&location={display_name}" class="tag">{t[0]} <small>({t[1]})</small></a>' for t in top_tags)}
            </div>
        </div>

        <h3>💼 Open Positions</h3>
        <table class="job-table">
            <thead><tr><th>Title</th><th>Location</th><th>Salary</th><th>Posted</th></tr></thead>
            <tbody>{job_rows}</tbody>
        </table>
    </div>
    """, f"{display_name} Jobs | Workora Jobs", user,
        description=f"{job_count} open positions at {display_name}. Find your next career opportunity.",
        canonical=f"/company/{slug}")


# ── Saved Jobs ────────────────────────────────────────────────

@app.get("/saved", response_class=HTMLResponse)
async def saved_page(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    saved = get_saved_jobs(user["id"], limit=100)
    items = ""
    for s in saved:
        priority_badge = {0: "", 1: "⭐", 2: "✅ Applied"}.get(s.get("priority", 0), "")
        slug = re.sub(r'[^a-z0-9]+', '-', (s.get("title") or "job").lower())[:40]
        items += f"""
        <div class="job-card compact">
            <div class="job-header">
                <h4><a href="/job/{slug}?id={s['job_dedupe_key']}">{html.escape(s.get('title') or 'Unknown')}</a></h4>
                <button class="btn btn-sm" onclick="toggleSave('{s['job_dedupe_key']}')">❌ Remove</button>
            </div>
            <div class="job-meta">
                <span>{html.escape(s.get('company') or '')}</span>
                <span>{html.escape(s.get('location') or '')}</span>
                <span>{s.get('posted_at', '')[:10]}</span>
                {priority_badge}
            </div>
            {f'<p class="notes">{html.escape(s.get("notes",""))}</p>' if s.get("notes") else ''}
        </div>
        """

    return page(f"""
    <section class="page-header">
        <h1>❤️ Saved Jobs ({len(saved)})</h1>
    </section>
    <div class="job-list">
        {items if items else '<p>No saved jobs yet. <a href="/jobs">Browse jobs</a> to start saving.</p>'}
    </div>
    """, "Saved Jobs | Workora Jobs", user)


@app.post("/api/save-job")
async def api_save_job(request: Request, job_key: str = Form(...), notes: str = Form("")):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Login required"}, 401)
    save_job(user["id"], job_key, notes)
    return JSONResponse({"ok": True, "saved": True})


@app.post("/api/unsave-job")
async def api_unsave_job(request: Request, job_key: str = Form(...)):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Login required"}, 401)
    unsave_job(user["id"], job_key)
    return JSONResponse({"ok": True, "saved": False})


# ── Job Applications ──────────────────────────────────────────

@app.get("/applications", response_class=HTMLResponse)
async def applications_page(request: Request, status: str = ""):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    apps = get_applications(user["id"], status=status if status else None)
    stats = get_application_stats(user["id"])

    status_tabs = ""
    for s_name, s_label, s_color in [
        ("", "All", "#6b7280"),
        ("saved", "Saved", "#3b82f6"),
        ("applied", "Applied", "#10b981"),
        ("interview", "Interview", "#f59e0b"),
        ("offer", "Offer", "#22c55e"),
        ("rejected", "Rejected", "#ef4444"),
    ]:
        count = stats.get(s_name, 0) if s_name else sum(stats.values())
        active = "active" if (status == s_name or (not status and not s_name)) else ""
        status_tabs += f'<a href="/applications?status={s_name}" class="tab {active}">{s_label} ({count})</a>'

    rows = ""
    for a in apps:
        slug = re.sub(r'[^a-z0-9]+', '-', (a.get("title") or "job").lower())[:40]
        rows += f"""
        <tr>
            <td><a href="/job/{slug}?id={a['job_dedupe_key']}">{html.escape(a.get('title') or '')}</a></td>
            <td>{html.escape(a.get('company') or '')}</td>
            <td><span class="status-badge status-{a['status']}">{a['status'].title()}</span></td>
            <td>{(a.get('applied_at') or a.get('created_at',''))[:10]}</td>
            <td>
                <select onchange="updateStatus('{a['job_dedupe_key']}', this.value)">
                    {"".join(f'<option value="{s}" {"selected" if s==a["status"] else ""}>{s.title()}</option>' for s in ['saved','applied','interview','offer','rejected','withdrawn'])}
                </select>
            </td>
        </tr>
        """

    return page(f"""
    <section class="page-header">
        <h1>📋 My Applications</h1>
        <div class="tabs">{status_tabs}</div>
    </section>
    <table class="job-table">
        <thead><tr><th>Job</th><th>Company</th><th>Status</th><th>Date</th><th>Update</th></tr></thead>
        <tbody>{rows if rows else '<tr><td colspan="5">No applications yet.</td></tr>'}</tbody>
    </table>
    """, "My Applications | Workora Jobs", user)


@app.post("/api/apply")
async def api_apply(request: Request, job_key: str = Form(...),
                    notes: str = Form(""), resume: str = Form(""),
                    cover_letter: str = Form("")):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Login required"}, 401)
    create_application(user["id"], job_key, notes, resume, cover_letter)
    return JSONResponse({"ok": True})


@app.post("/api/update-application")
async def api_update_application(request: Request, job_key: str = Form(...),
                                  status: str = Form(...)):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"error": "Login required"}, 401)
    update_application(user["id"], job_key, status)
    return JSONResponse({"ok": True})


# ── Job Alerts ────────────────────────────────────────────────

@app.get("/alerts", response_class=HTMLResponse)
async def alerts_page(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    alerts = get_user_alerts(user["id"])
    items = ""
    for a in alerts:
        items += f"""
        <div class="alert-card">
            <div class="alert-header">
                <strong>{html.escape(a['name'])}</strong>
                <span class="badge {'badge-green' if a['is_active'] else 'badge-red'}">
                    {'Active' if a['is_active'] else 'Paused'}
                </span>
            </div>
            <div class="alert-details">
                <span>Keywords: {html.escape(a.get('keywords') or 'Any')}</span>
                <span>Location: {html.escape(a.get('locations') or 'Any')}</span>
                <span>Frequency: {a['frequency']}</span>
                {f'<span>Min Salary: ${a["min_salary"]:,}</span>' if a.get('min_salary') else ''}
            </div>
            <div class="alert-actions">
                <a href="/alerts/{a['id']}/test" class="btn btn-sm">Test Now</a>
                <form method="post" action="/alerts/{a['id']}/delete" style="display:inline">
                    <button type="submit" class="btn btn-sm btn-danger">Delete</button>
                </form>
            </div>
        </div>
        """

    return page(f"""
    <section class="page-header">
        <h1>🔔 Job Alerts ({len(alerts)})</h1>
        <a href="/alerts/new" class="btn btn-primary">+ New Alert</a>
    </section>
    <div class="alerts-list">
        {items if items else '<p>No alerts set. <a href="/alerts/new">Create your first alert</a> to get notified about matching jobs.</p>'}
    </div>
    """, "Job Alerts | Workora Jobs", user)


@app.get("/alerts/new", response_class=HTMLResponse)
async def new_alert_page(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    return page(f"""
    <section class="page-header">
        <h1>🔔 Create Job Alert</h1>
    </section>
    <form method="post" action="/alerts/create" class="form-card">
        <div class="form-group">
            <label>Alert Name</label>
            <input type="text" name="name" placeholder="e.g., React Developer in NYC" required>
        </div>
        <div class="form-group">
            <label>Keywords (comma-separated)</label>
            <input type="text" name="keywords" placeholder="e.g., python, react, frontend">
        </div>
        <div class="form-group">
            <label>Locations (comma-separated)</label>
            <input type="text" name="locations" placeholder="e.g., New York, Remote, Bangalore">
        </div>
        <div class="form-group">
            <label>Minimum Salary ($)</label>
            <input type="number" name="min_salary" placeholder="e.g., 80000">
        </div>
        <div class="form-group">
            <label>Frequency</label>
            <select name="frequency">
                <option value="daily">Daily (9:00 AM)</option>
                <option value="realtime">Real-time</option>
            </select>
        </div>
        <button type="submit" class="btn btn-primary">Create Alert</button>
    </form>
    """, "Create Alert | Workora Jobs", user)


@app.post("/alerts/create")
async def create_alert_action(request: Request, name: str = Form(""),
                               keywords: str = Form(""), locations: str = Form(""),
                               min_salary: int = Form(0), frequency: str = Form("daily")):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    create_alert(user["id"], name, keywords, locations, "", min_salary, frequency)
    return RedirectResponse("/alerts")


@app.post("/alerts/{alert_id}/delete")
async def delete_alert_action(alert_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    delete_alert(alert_id, user["id"])
    return RedirectResponse("/alerts")


@app.get("/alerts/{alert_id}/test", response_class=HTMLResponse)
async def test_alert(alert_id: int, request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    alerts = get_user_alerts(user["id"])
    alert = next((a for a in alerts if a["id"] == alert_id), None)
    if not alert:
        return RedirectResponse("/alerts")

    matches = match_alert_to_jobs(alert)

    items = ""
    for m in matches[:20]:
        slug = re.sub(r'[^a-z0-9]+', '-', (m.get("title") or "").lower())[:40]
        items += f"""
        <div class="job-card compact">
            <h4><a href="/job/{slug}?id={m['dedupe_key']}">{html.escape(m.get('title', ''))}</a></h4>
            <span>{html.escape(m.get('company', ''))} — {html.escape(m.get('location', ''))}</span>
        </div>
        """

    return page(f"""
    <section class="page-header">
        <h1>🔔 Alert: {html.escape(alert['name'])}</h1>
        <p>Found {len(matches)} matching jobs from the last 24 hours</p>
        <a href="/alerts" class="btn btn-outline">← Back to Alerts</a>
    </section>
    <div class="job-list">
        {items if items else '<p>No matching jobs found for this alert criteria in the last 24 hours.</p>'}
    </div>
    """, f"Alert: {alert['name']} | Workora Jobs", user)


# ── Auth Pages ────────────────────────────────────────────────

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    user = await get_current_user(request)
    if user:
        return RedirectResponse("/dashboard")
    return page("""
    <section class="auth-page">
        <div class="auth-card">
            <h1>Create Your Account</h1>
            <p class="subtitle">Start tracking jobs and get alerts</p>
            <form method="post" action="/register" class="auth-form">
                <div class="form-group">
                    <label>Full Name</label>
                    <input type="text" name="full_name" placeholder="John Doe" required>
                </div>
                <div class="form-group">
                    <label>Username</label>
                    <input type="text" name="username" placeholder="johndoe" required minlength="3">
                </div>
                <div class="form-group">
                    <label>Email</label>
                    <input type="email" name="email" placeholder="you@example.com" required>
                </div>
                <div class="form-group">
                    <label>Password</label>
                    <input type="password" name="password" placeholder="Min 6 characters" required minlength="6">
                </div>
                <button type="submit" class="btn btn-primary btn-full">Create Account</button>
            </form>
            <p class="auth-switch">Already have an account? <a href="/login">Login</a></p>
        </div>
    </section>
    """, "Sign Up | Workora Jobs")


@app.post("/register")
async def register_action(response: Response, full_name: str = Form(""),
                           username: str = Form(""), email: str = Form(""),
                           password: str = Form("")):
    if not email or not password or not username:
        raise HTTPException(400, "Missing required fields")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    user = create_user(email, username, password, full_name)
    if not user:
        raise HTTPException(409, "Email or username already exists")

    token = create_session(user["id"])
    # Send welcome email (non-blocking)
    try:
        from .email_sender import send_welcome_email
        import threading
        threading.Thread(target=send_welcome_email, args=(email, username), daemon=True).start()
    except Exception:
        pass
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("session_token", token, max_age=30*24*3600, httponly=True)
    return response


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    user = await get_current_user(request)
    if user:
        return RedirectResponse("/dashboard")

    error_html = f'<div class="alert alert-error">{html.escape(error)}</div>' if error else ""

    return page(f"""
    <section class="auth-page">
        <div class="auth-card">
            <h1>Login to Workora</h1>
            <p class="subtitle">Access your saved jobs and alerts</p>
            {error_html}
            <form method="post" action="/login" class="auth-form">
                <div class="form-group">
                    <label>Email</label>
                    <input type="email" name="email" placeholder="you@example.com" required>
                </div>
                <div class="form-group">
                    <label>Password</label>
                    <input type="password" name="password" placeholder="Your password" required>
                </div>
                <button type="submit" class="btn btn-primary btn-full">Login</button>
            </form>
            <p class="auth-switch">Don't have an account? <a href="/register">Sign Up</a></p>
        </div>
    </section>
    """, "Login | Workora Jobs")


@app.post("/login")
async def login_action(response: Response, email: str = Form(""),
                        password: str = Form("")):
    user = authenticate(email, password)
    if not user:
        return RedirectResponse("/login?error=Invalid+email+or+password", status_code=303)

    token = create_session(user["id"])
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("session_token", token, max_age=30*24*3600, httponly=True)
    return response


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session_token")
    if token:
        delete_session(token)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("session_token")
    return response


# ── Dashboard (Logged in) ─────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def user_dashboard(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    saved = get_saved_jobs(user["id"], limit=10)
    apps = get_applications(user["id"], limit=10)
    stats = get_application_stats(user["id"])
    alerts = get_user_alerts(user["id"])
    saved_count = get_saved_count(user["id"])

    recent_saved = ""
    for s in saved[:5]:
        slug = re.sub(r'[^a-z0-9]+', '-', (s.get("title") or "job").lower())[:40]
        recent_saved += f"""
        <div class="list-item">
            <a href="/job/{slug}?id={s['job_dedupe_key']}">{html.escape(s.get('title', ''))}</a>
            <span>{html.escape(s.get('company', ''))}</span>
        </div>
        """

    recent_apps = ""
    for a in apps[:5]:
        recent_apps += f"""
        <div class="list-item">
            <span>{html.escape(a.get('title', ''))}</span>
            <span class="status-badge status-{a['status']}">{a['status'].title()}</span>
        </div>
        """

    return page(f"""
    <section class="page-header">
        <h1>Welcome, {html.escape(user.get('full_name') or user['username'])}! 👋</h1>
    </section>

    <div class="dashboard-grid">
        <div class="dash-card">
            <h3>❤️ Saved Jobs</h3>
            <div class="big-number">{saved_count}</div>
            <a href="/saved" class="btn btn-sm">View All</a>
        </div>
        <div class="dash-card">
            <h3>📋 Applications</h3>
            <div class="big-number">{stats.get('applied', 0)}</div>
            <a href="/applications" class="btn btn-sm">View All</a>
        </div>
        <div class="dash-card">
            <h3>📞 Interviews</h3>
            <div class="big-number">{stats.get('interview', 0)}</div>
        </div>
        <div class="dash-card">
            <h3>🔔 Alerts</h3>
            <div class="big-number">{len(alerts)}</div>
            <a href="/alerts" class="btn btn-sm">Manage</a>
        </div>
    </div>

    <div class="dashboard-section">
        <h2>Recently Saved</h2>
        {recent_saved if recent_saved else '<p>No saved jobs yet. <a href="/jobs">Browse jobs</a></p>'}
    </div>

    <div class="dashboard-section">
        <h2>Recent Applications</h2>
        {recent_apps if recent_apps else '<p>No applications yet. Start applying!</p>'}
    </div>
    """, "My Dashboard | Workora Jobs", user)


# ── Salary Data (SEO) ────────────────────────────────────────

@app.get("/salary", response_class=HTMLResponse)
async def salary_page(request: Request, q: str = "", location: str = ""):
    user = await get_current_user(request)

    with get_db() as db:
        q_filter = ""
        params = []
        if q:
            q_filter = "AND (title LIKE ? OR tags LIKE ?)"
            params = [f"%{q}%", f"%{q}%"]
        if location:
            q_filter += " AND location LIKE ?"
            params.append(f"%{location}%")

        skills_data = db.execute(f"""
            SELECT tags, CAST(salary AS REAL) as sal
            FROM jobs
            WHERE salary IS NOT NULL AND salary != ''
            AND CAST(salary AS REAL) > 10000
            AND CAST(salary AS REAL) < 500000
            {q_filter}
            LIMIT 50000
        """, params).fetchall()

        # Aggregate by skill
        skill_salaries = {}
        for row in skills_data:
            for tag in (row["tags"] or "").split(","):
                tag = tag.strip().lower()
                if tag and row["sal"]:
                    if tag not in skill_salaries:
                        skill_salaries[tag] = []
                    skill_salaries[tag].append(row["sal"])

        stats = []
        for skill, sals in sorted(skill_salaries.items(), key=lambda x: -len(x[1])):
            if len(sals) >= 5:
                sals_sorted = sorted(sals)
                stats.append({
                    "skill": skill,
                    "count": len(sals),
                    "avg": int(sum(sals) / len(sals)),
                    "median": int(sals_sorted[len(sals_sorted) // 2]),
                    "min": int(sals_sorted[0]),
                    "max": int(sals_sorted[-1]),
                })

    skill_rows = ""
    for s in sorted(stats, key=lambda x: -x["count"])[:30]:
        skill_rows += f"""
        <tr>
            <td><a href="/jobs?q={s['skill']}">{html.escape(s['skill'])}</a></td>
            <td>{s['count']:,}</td>
            <td>${s['avg']:,}</td>
            <td>${s['median']:,}</td>
            <td>${s['min']:,}</td>
            <td>${s['max']:,}</td>
        </tr>
        """

    return page(f"""
    <section class="page-header">
        <h1>💰 Salary Data by Skill</h1>
        <p class="subtitle">Average salaries from {len(skills_data):,} job listings with salary info</p>
    </section>

    <div class="search-bar">
        <form action="/salary" method="get" class="search-row">
            <input type="text" name="q" placeholder="Filter by skill..." value="{html.escape(q)}">
            <input type="text" name="location" placeholder="Filter by location..." value="{html.escape(location)}">
            <button type="submit" class="btn btn-primary">Search</button>
        </form>
    </div>

    <table class="job-table">
        <thead><tr><th>Skill</th><th>Jobs</th><th>Avg Salary</th><th>Median</th><th>Min</th><th>Max</th></tr></thead>
        <tbody>{skill_rows}</tbody>
    </table>
    """, f"Salary Data{' - '+q if q else ''} | Workora Jobs", user,
        canonical=f"/salary?q={q}")


# ── Layoff Tracker (SEO) ─────────────────────────────────────

@app.get("/layoffs", response_class=HTMLResponse)
async def layoffs_page(request: Request):
    user = await get_current_user(request)

    layoff_path = ROOT / "layoffs.db"
    layoff_rows_html = ""
    total_affected = 0

    if layoff_path.exists():
        try:
            lconn = sqlite3.connect(str(layoff_path))
            lconn.row_factory = sqlite3.Row
            layoffs = lconn.execute(
                "SELECT * FROM layoffs ORDER BY date DESC LIMIT 100"
            ).fetchall()
            for lay in layoffs:
                affected = lay["affected_count"] if "affected_count" in lay.keys() else 0
                total_affected += affected
                layoff_rows_html += f"""
                <tr>
                    <td>{html.escape(str(lay['company']))}</td>
                    <td>{affected:,}</td>
                    <td>{html.escape(str(lay.get('date', '')))}</td>
                    <td>{html.escape(str(lay.get('industry', '')))}</td>
                    <td>{html.escape(str(lay.get('source', '')))}</td>
                </tr>
                """
            lconn.close()
        except Exception:
            pass

    return page(f"""
    <section class="page-header">
        <h1>🔥 Tech Layoff Tracker</h1>
        <p class="subtitle">Real-time layoff news and data — {total_affected:,} employees affected</p>
    </section>
    <table class="job-table">
        <thead><tr><th>Company</th><th>Employees Affected</th><th>Date</th><th>Industry</th><th>Source</th></tr></thead>
        <tbody>{layoff_rows_html if layoff_rows_html else '<tr><td colspan="5">No layoff data available.</td></tr>'}</tbody>
    </table>
    """, "Tech Layoff Tracker | Workora Jobs", user,
        description="Real-time tech layoff tracker. See which companies are laying off employees.")


# ── About / Contact ──────────────────────────────────────────

@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    user = await get_current_user(request)
    stats = get_dashboard_stats()
    return page(f"""
    <section class="page-header">
        <h1>About Workora Jobs</h1>
    </section>
    <div class="content-page">
        <p>Workora Jobs is an AI-powered job aggregator that collects job listings from 20+ platforms
        including LinkedIn, Indeed, Glassdoor, Google Jobs, Naukri, and 1,000+ company career pages
        powered by ATS platforms like Greenhouse, Ashby, Lever, and more.</p>

        <h2>Our Mission</h2>
        <p>We believe job seekers deserve access to <strong>all available opportunities</strong> in one place.
        No more jumping between 10 different job boards. Workora aggregates {stats['total_jobs']:,}+ job listings
        from {stats['total_companies']:,}+ companies and makes them searchable with AI-powered matching.</p>

        <h2>Features</h2>
        <ul>
            <li>🔍 <strong>Universal Search</strong> — Search across all job platforms at once</li>
            <li>🔔 <strong>Smart Alerts</strong> — Get notified when matching jobs are posted</li>
            <li>📊 <strong>Salary Data</strong> — Know your market value by skill and location</li>
            <li>🏢 <strong>Company Profiles</strong> — See all open positions at any company</li>
            <li>🔥 <strong>Layoff Tracker</strong> — Stay informed about industry changes</li>
            <li>💾 <strong>Job Tracker</strong> — Save, organize, and track your applications</li>
        </ul>

        <h2>Data Sources</h2>
        <p>We collect data from {len(stats['sources'])} sources including LinkedIn, Indeed, Glassdoor,
        Google Jobs, Naukri, ZipRecruiter, SimplyHired, Dice, and direct ATS integrations with
        Greenhouse, Lever, Ashby, SmartRecruiters, and Workable.</p>

        <h2>Contact Us</h2>
        <p>Have questions or feedback? <a href="/contact">Contact us here</a>.</p>
    </div>
    """, "About Workora Jobs | Find Your Dream Job", user)


@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    user = await get_current_user(request)
    return page(f"""
    <section class="page-header">
        <h1>Contact Us</h1>
    </section>
    <form method="post" action="/contact/submit" class="form-card" style="max-width:600px;">
        <div class="form-group">
            <label>Name</label>
            <input type="text" name="name" required value="{html.escape(user.get('full_name','')) if user else ''}">
        </div>
        <div class="form-group">
            <label>Email</label>
            <input type="email" name="email" required value="{html.escape(user.get('email','')) if user else ''}">
        </div>
        <div class="form-group">
            <label>Subject</label>
            <input type="text" name="subject" placeholder="How can we help?">
        </div>
        <div class="form-group">
            <label>Message</label>
            <textarea name="message" rows="5" required></textarea>
        </div>
        <button type="submit" class="btn btn-primary">Send Message</button>
    </form>
    """, "Contact Us | Workora Jobs", user)


@app.post("/contact/submit")
async def contact_submit(name: str = Form(""), email: str = Form(""),
                          subject: str = Form(""), message: str = Form("")):
    submit_contact(name, email, subject, message)
    return RedirectResponse("/contact?sent=1")


# ── Employer Dashboard ────────────────────────────────────────

@app.get("/employer/dashboard", response_class=HTMLResponse)
async def employer_dashboard(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    employer = get_employer(user_id=user["id"])
    if not employer:
        return page("""
        <section class="page-header">
            <h1>💼 Employer Dashboard</h1>
        </section>
        <div class="form-card">
            <h2>Register as an Employer</h2>
            <p>Post jobs and manage applicants on Workora Jobs.</p>
            <form method="post" action="/employer/register" class="auth-form">
                <div class="form-group">
                    <label>Company Name</label>
                    <input type="text" name="company_name" required>
                </div>
                <div class="form-group">
                    <label>Company Website</label>
                    <input type="url" name="company_url" placeholder="https://company.com">
                </div>
                <div class="form-group">
                    <label>Industry</label>
                    <input type="text" name="industry" placeholder="e.g., Technology, Healthcare">
                </div>
                <div class="form-group">
                    <label>Company Size</label>
                    <select name="company_size">
                        <option>1-10</option>
                        <option>11-50</option>
                        <option>51-200</option>
                        <option>201-1000</option>
                        <option selected>1000+</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Description</label>
                    <textarea name="description" rows="4"></textarea>
                </div>
                <button type="submit" class="btn btn-primary">Register Company</button>
            </form>
        </div>
        """, "Employer Dashboard | Workora Jobs", user)

    return page(f"""
    <section class="page-header">
        <h1>💼 {html.escape(employer['company_name'])} Dashboard</h1>
    </section>
    <div class="dashboard-grid">
        <div class="dash-card">
            <h3>📋 Active Jobs</h3>
            <div class="big-number">0</div>
            <a href="/employer/post-job" class="btn btn-primary">Post New Job</a>
        </div>
    </div>
    <p>Employer dashboard is being developed. Contact us for early access.</p>
    """, f"{employer['company_name']} Dashboard | Workora Jobs", user)


@app.post("/employer/register")
async def employer_register(request: Request, company_name: str = Form(""),
                             company_url: str = Form(""), industry: str = Form(""),
                             company_size: str = Form(""), description: str = Form("")):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    create_employer(user["id"], company_name, company_url, industry, company_size, description)
    return RedirectResponse("/employer/dashboard")


# ── Post Job (Employer) ───────────────────────────────────────

@app.get("/employer/post-job", response_class=HTMLResponse)
async def post_job_page(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    employer = get_employer(user_id=user['id'])
    if not employer:
        return RedirectResponse("/employer/dashboard")
    return page(f"""
    <section class="page-header">
        <h1>Post a New Job</h1>
        <p class="subtitle">{html.escape(employer['company_name'])}</p>
    </section>
    <form method="post" action="/employer/post-job/submit" class="form-card" style="max-width:700px">
        <div class="form-group">
            <label>Job Title *</label>
            <input type="text" name="title" placeholder="e.g. Senior Software Engineer" required>
        </div>
        <div class="form-group">
            <label>Job Description *</label>
            <textarea name="description" rows="8" placeholder="Describe the role, requirements, benefits..." required></textarea>
        </div>
        <div class="form-group">
            <label>Location</label>
            <input type="text" name="location" placeholder="e.g. Remote, New York, Bangalore">
        </div>
        <div class="form-group">
            <label>Job Type</label>
            <select name="job_type">
                <option value="full-time">Full Time</option>
                <option value="part-time">Part Time</option>
                <option value="contract">Contract</option>
                <option value="internship">Internship</option>
                <option value="freelance">Freelance</option>
            </select>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div class="form-group">
                <label>Salary Min ($)</label>
                <input type="number" name="salary_min" placeholder="50000">
            </div>
            <div class="form-group">
                <label>Salary Max ($)</label>
                <input type="number" name="salary_max" placeholder="120000">
            </div>
        </div>
        <div class="form-group">
            <label>Skills Required (comma-separated)</label>
            <input type="text" name="skills" placeholder="Python, React, AWS, Docker">
        </div>
        <div class="form-group">
            <label>
                <input type="checkbox" name="remote_ok" value="1" style="width:auto;margin-right:8px">
                Remote OK
            </label>
        </div>
        <button type="submit" class="btn btn-primary btn-full">Post Job</button>
    </form>
    """, f"Post Job | {employer['company_name']} | Workora Jobs", user)


@app.post("/employer/post-job/submit")
async def post_job_submit(request: Request, title: str = Form(""), description: str = Form(""),
                           location: str = Form(""), job_type: str = Form("full-time"),
                           salary_min: int = Form(0), salary_max: int = Form(0),
                           skills: str = Form(""), remote_ok: bool = Form(False)):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    employer = get_employer(user_id=user['id'])
    if not employer:
        return RedirectResponse("/employer/dashboard")
    from .models import post_job
    post_job(employer['id'], title, description, location, job_type,
             salary_min, salary_max, skills, remote_ok)
    return RedirectResponse("/employer/dashboard")


# ── N8n Webhook ───────────────────────────────────────────────

@app.post("/api/webhook/n8n")
async def n8n_webhook(request: Request):
    """n8n daily trigger endpoint for 9 AM workflow."""
    stats = get_dashboard_stats()
    active_alerts = get_active_alerts()

    results = []
    for alert in active_alerts[:50]:
        matches = match_alert_to_jobs(alert)
        if matches:
            results.append({
                "alert_id": alert["id"],
                "user": alert["username"],
                "email": alert["email"],
                "keyword": alert["keywords"],
                "new_jobs": len(matches),
                "top_jobs": [{"title": m["title"], "company": m["company"]}
                            for m in matches[:5]],
            })

    return {
        "status": "ok",
        "stats": {
            "total_jobs": stats["total_jobs"],
            "new_today": stats["jobs_today"],
            "new_this_week": stats["jobs_this_week"],
        },
        "alerts_processed": len(results),
        "alerts_with_matches": len([r for r in results if r["new_jobs"] > 0]),
        "results": results,
    }


# --- API Endpoints ---
@app.get("/api/health")
async def api_health():
    try:
        from scripts.models import get_db
        with get_db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        return {"status": "ok", "total_jobs": total}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/api/stats")
async def api_stats():
    return get_dashboard_stats()

@app.get("/api/jobs")
async def api_jobs(q: str = "", location: str = "", source: str = "", limit: int = 50, offset: int = 0):
    conditions, params = [], []
    if q:
        conditions.append("(title LIKE ? OR company LIKE ? OR tags LIKE ?)")
        params.extend([f"%{q}%"] * 3)
    if location:
        conditions.append("location LIKE ?")
        params.append(f"%{location}%")
    if source:
        conditions.append("source = ?")
        params.append(source)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    with get_db() as db:
        total = db.execute(f"SELECT COUNT(*) FROM jobs {where}", params).fetchone()[0]
        rows = db.execute(f"SELECT * FROM jobs {where} ORDER BY posted_at DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    return {"total": total, "jobs": [dict(r) for r in rows]}

# ── Catch-all 404 ─────────────────────────────────────────────

@app.get("/{path:path}", response_class=HTMLResponse)
async def catch_all(path: str):
    return page(
        '<div style="text-align:center;padding:80px 0"><h1>404</h1><p>Page not found</p>'
        '<a href="/" class="btn btn-primary">Go Home</a></div>',
        "Page Not Found | Workora Jobs"
    )


# ── Main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("Workora Jobs starting on http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
