#!/usr/bin/env python3
"""Company Profiles — Dedicated page per company with all jobs, salary, tech stack.

Usage:
    python -m scripts.company_profiles --list
    python -m scripts.company_profiles --profile "stripe"
    python -m scripts.company_profiles --export
"""
from __future__ import annotations
import json, os, re, sqlite3, time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
PROFILES_DIR = ROOT / "company_profiles"
LOG = ROOT / "company_profiles.log"


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def normalize_company(name):
    if not name:
        return ""
    return name.lower().strip().replace(" ", "").replace(".", "").replace(",", "")


def get_company_profile(company_name):
    """Get detailed profile for a company."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    # Get all jobs for this company
    jobs = conn.execute(
        "SELECT * FROM jobs WHERE LOWER(TRIM(company)) = LOWER(?) AND is_active = 1 "
        "ORDER BY first_seen_at DESC",
        (company_name,)
    ).fetchall()

    if not jobs:
        # Try partial match
        jobs = conn.execute(
            "SELECT * FROM jobs WHERE LOWER(TRIM(company)) LIKE LOWER(?) AND is_active = 1 "
            "ORDER BY first_seen_at DESC",
            (f"%{company_name}%",)
        ).fetchall()

    conn.close()

    if not jobs:
        return None

    job_dicts = [dict(j) for j in jobs]

    # Calculate stats
    total_jobs = len(job_dicts)

    # Locations
    locations = Counter()
    for job in job_dicts:
        loc = job.get("location", "") or "Unknown"
        locations[loc] += 1

    # Job titles
    titles = Counter()
    for job in job_dicts:
        title = job.get("title", "") or "Unknown"
        titles[title] += 1

    # Salary data
    salaries = []
    for job in job_dicts:
        if job.get("salary"):
            try:
                nums = re.findall(r'[\d,]+', str(job["salary"]))
                if nums:
                    salary = float(nums[0].replace(",", ""))
                    if salary > 1000:
                        salaries.append(salary)
            except:
                pass

    # Tech stack
    tech_skills = Counter()
    skill_keywords = [
        "python", "javascript", "typescript", "react", "node", "angular", "vue",
        "java", "go", "rust", "c++", "c#", "ruby", "php", "swift", "kotlin",
        "django", "flask", "fastapi", "spring", "express", "nextjs",
        "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
        "aws", "azure", "gcp", "docker", "kubernetes", "terraform",
        "machine learning", "deep learning", "tensorflow", "pytorch",
    ]

    for job in job_dicts:
        text = f"{job.get('title', '')} {job.get('description', '')}".lower()
        for skill in skill_keywords:
            if skill in text:
                tech_skills[skill] += 1

    # Sources
    sources = Counter()
    for job in job_dicts:
        src = job.get("source", "") or "Unknown"
        sources[src] += 1

    # Date range
    dates = []
    for job in job_dicts:
        if job.get("first_seen_at"):
            dates.append(job["first_seen_at"])

    # Hiring velocity (jobs per day)
    if dates:
        try:
            dates.sort()
            first = datetime.fromisoformat(dates[0].replace("Z", "+00:00"))
            last = datetime.fromisoformat(dates[-1].replace("Z", "+00:00"))
            days = max((last - first).days, 1)
            velocity = total_jobs / days
        except:
            velocity = 0
    else:
        velocity = 0

    return {
        "company": company_name,
        "total_jobs": total_jobs,
        "locations": dict(locations.most_common(10)),
        "top_titles": dict(titles.most_common(10)),
        "salary": {
            "average": round(sum(salaries) / len(salaries)) if salaries else None,
            "min": min(salaries) if salaries else None,
            "max": max(salaries) if salaries else None,
            "count": len(salaries),
        },
        "tech_stack": dict(tech_skills.most_common(15)),
        "sources": dict(sources),
        "hiring_velocity": round(velocity, 1),
        "first_seen": dates[0] if dates else None,
        "last_seen": dates[-1] if dates else None,
        "jobs": job_dicts[:20],
    }


def list_companies(min_jobs=5):
    """List all companies with at least min_jobs."""
    conn = get_db()
    rows = conn.execute(
        "SELECT company, COUNT(*) as cnt FROM jobs "
        "WHERE is_active = 1 AND company != '' "
        "GROUP BY LOWER(TRIM(company)) "
        "HAVING cnt >= ? ORDER BY cnt DESC",
        (min_jobs,)
    ).fetchall()
    conn.close()
    return [{"company": r[0], "jobs": r[1]} for r in rows]


def generate_profile_html(profile):
    """Generate HTML for a company profile."""
    salary = profile["salary"]
    salary_str = f"${salary['average']:,}" if salary.get("average") else "N/A"

    tech_list = ", ".join(list(profile["tech_stack"].keys())[:10])
    loc_list = ", ".join(list(profile["locations"].keys())[:5])

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{profile['company']} - Company Profile</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #0f172a; color: #e2e8f0; }}
h1 {{ color: #3b82f6; }}
.grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin: 20px 0; }}
.card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 15px; }}
.card .label {{ color: #94a3b8; font-size: 12px; text-transform: uppercase; }}
.card .value {{ font-size: 24px; font-weight: bold; color: #38bdf8; margin-top: 5px; }}
table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
th, td {{ padding: 8px; border-bottom: 1px solid #334155; text-align: left; font-size: 13px; }}
th {{ color: #94a3b8; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; background: #334155; color: #e2e8f0; font-size: 11px; margin: 2px; }}
</style>
</head>
<body>
<h1>{profile['company']}</h1>
<p>Total Open Positions: {profile['total_jobs']} | Hiring Velocity: {profile['hiring_velocity']:.1f} jobs/day</p>

<div class="grid">
  <div class="card"><div class="label">Total Jobs</div><div class="value">{profile['total_jobs']}</div></div>
  <div class="card"><div class="label">Average Salary</div><div class="value">{salary_str}</div></div>
  <div class="card"><div class="label">Hiring Velocity</div><div class="value">{profile['hiring_velocity']:.1f}/day</div></div>
</div>

<h2>📍 Locations</h2>
<p>{loc_list or 'Various locations'}</p>

<h2>🛠️ Tech Stack</h2>
<p>{tech_list or 'Not specified'}</p>

<h2>💼 Top Job Titles</h2>
<table>
<tr><th>Title</th><th>Count</th></tr>
{''.join(f"<tr><td>{t}</td><td>{c}</td></tr>" for t, c in list(profile['top_titles'].items())[:10])}
</table>

<h2>💰 Salary Range</h2>
<p>Min: ${salary.get('min', 0):,.0f} | Avg: ${salary.get('average', 0):,.0f} | Max: ${salary.get('max', 0):,.0f}</p>

<h2>📋 Recent Jobs</h2>
<table>
<tr><th>Title</th><th>Location</th><th>Posted</th></tr>
{''.join(f"<tr><td><a href='{j.get('url', '#')}' style='color:#3b82f6'>{j.get('title', '')}</a></td><td>{j.get('location', '')}</td><td>{(j.get('posted_at', '') or '')[:10]}</td></tr>" for j in profile['jobs'][:15])}
</table>

<p style="color:#64748b;font-size:12px;margin-top:40px">Generated by LeadFlow Company Profiles</p>
</body>
</html>"""
    return html


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--profile", help="Company name")
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--min-jobs", type=int, default=5)
    args = parser.parse_args()

    if args.list:
        companies = list_companies(args.min_jobs)
        print(f"\nCompanies with {args.min_jobs}+ jobs:")
        for c in companies[:50]:
            print(f"  {c['company']}: {c['jobs']} jobs")

    elif args.profile:
        profile = get_company_profile(args.profile)
        if profile:
            if args.export:
                PROFILES_DIR.mkdir(exist_ok=True)
                html = generate_profile_html(profile)
                filepath = PROFILES_DIR / f"{args.profile.lower()}.html"
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"Profile exported to: {filepath}")
            else:
                print(json.dumps(profile, indent=2, default=str))
        else:
            print(f"Company '{args.profile}' not found")

    elif args.export:
        companies = list_companies(args.min_jobs)
        PROFILES_DIR.mkdir(exist_ok=True)
        for c in companies[:100]:
            try:
                profile = get_company_profile(c["company"])
                if profile:
                    html = generate_profile_html(profile)
                    filepath = PROFILES_DIR / f"{normalize_company(c['company'])}.html"
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(html)
            except:
                pass
        print(f"Exported {min(100, len(companies))} company profiles")


if __name__ == "__main__":
    main()
