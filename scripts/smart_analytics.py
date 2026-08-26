#!/usr/bin/env python3
"""Smart Analytics — Salary benchmarking, market trends, hiring velocity, remote/visa detection.

Usage:
    python -m scripts.smart_analytics --salary
    python -m scripts.smart_analytics --trends
    python -m scripts.smart_analytics --hiring-velocity
    python -m scripts.smart_analytics --all
"""
from __future__ import annotations
import json, os, re, sqlite3, time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "smart_analytics.log"

# Remote keywords
REMOTE_KEYWORDS = [
    "remote", "work from home", "wfh", "home office", "distributed",
    "anywhere", "global", "worldwide", "telecommute", "telecommuting",
    "fully remote", "100% remote", "remote-first", "remote friendly",
]

ONSITE_KEYWORDS = [
    "on-site", "onsite", "in-office", "in office", "on site",
    "office based", "office-based", "hybrid", "relocation",
]

# Visa keywords
VISA_KEYWORDS = [
    "visa", "visa sponsorship", "sponsor visa", "h1b", "h-1b", "h1-b",
    "work permit", "work authorization", "authorized to work",
    "green card", "permanent residency", "sponsorship available",
    "will sponsor", "visa support", "immigration",
    "opt", "cpt", "stem opt", "ead",
]


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def classify_remote(title, description, location):
    """Classify a job as remote/hybrid/onsite."""
    text = f"{title} {description} {location}".lower()

    for kw in REMOTE_KEYWORDS:
        if kw in text:
            return "remote"

    for kw in ONSITE_KEYWORDS:
        if kw in text:
            return "onsite" if "hybrid" not in text else "hybrid"

    if "remote" in (location or "").lower():
        return "remote"

    return "unknown"


def detect_visa(title, description):
    """Check if a job mentions visa sponsorship."""
    text = f"{title} {description}".lower()
    for kw in VISA_KEYWORDS:
        if kw in text:
            return True
    return False


def analyze_salary():
    """Analyze salary data across jobs."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    jobs = conn.execute(
        "SELECT title, company, location, salary, description, source "
        "FROM jobs WHERE is_active = 1 AND salary != '' AND salary IS NOT NULL"
    ).fetchall()

    conn.close()

    salaries = []
    by_title = defaultdict(list)
    by_location = defaultdict(list)

    for job in jobs:
        try:
            salary_str = str(job["salary"])
            # Extract numeric value
            nums = re.findall(r'[\d,]+', salary_str.replace(",", ""))
            if nums:
                salary = float(nums[0])
                if salary > 1000:  # Assume yearly
                    salaries.append(salary)
                    title_key = job["title"].lower()[:30]
                    by_title[title_key].append(salary)
                    loc = job["location"] or "Unknown"
                    by_location[loc].append(salary)
        except:
            pass

    if not salaries:
        return {"message": "No salary data available"}

    # Calculate stats
    avg_salary = sum(salaries) / len(salaries)
    min_salary = min(salaries)
    max_salary = max(salaries)
    sorted_salaries = sorted(salaries)
    median_salary = sorted_salaries[len(sorted_salaries) // 2]

    # Top paying titles
    top_titles = sorted(by_title.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True)[:10]

    # Top paying locations
    top_locations = sorted(by_location.items(), key=lambda x: sum(x[1]) / len(x[1]), reverse=True)[:10]

    return {
        "total_with_salary": len(salaries),
        "average": round(avg_salary),
        "median": round(median_salary),
        "min": round(min_salary),
        "max": round(max_salary),
        "top_paying_titles": [
            {"title": t, "avg": round(sum(s) / len(s)), "count": len(s)}
            for t, s in top_titles
        ],
        "top_paying_locations": [
            {"location": l, "avg": round(sum(s) / len(s)), "count": len(s)}
            for l, s in top_locations
        ],
    }


def analyze_market_trends(days=30):
    """Analyze market trends over time."""
    conn = get_db()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT title, description, tags, first_seen_at FROM jobs "
        "WHERE is_active = 1 AND first_seen_at >= ?",
        (cutoff,)
    ).fetchall()

    conn.close()

    # Extract skills
    skill_counts = Counter()
    for row in rows:
        text = f"{row[0]} {row[1]}".lower()
        # Common tech skills
        skills = [
            "python", "javascript", "typescript", "react", "node",
            "angular", "vue", "java", "go", "rust", "c++", "c#",
            "ruby", "php", "swift", "kotlin", "django", "flask",
            "fastapi", "spring", "express", "nextjs", "postgresql",
            "mysql", "mongodb", "redis", "aws", "azure", "gcp",
            "docker", "kubernetes", "terraform", "devops", "ci/cd",
            "machine learning", "deep learning", "tensorflow", "pytorch",
            "data science", "sql", "spark", "kafka", "graphql", "linux",
        ]
        for skill in skills:
            if skill in text:
                skill_counts[skill] += 1

    # Jobs per day
    daily_counts = Counter()
    for row in rows:
        if row[3]:
            try:
                day = row[3][:10]
                daily_counts[day] += 1
            except:
                pass

    return {
        "period_days": days,
        "total_jobs": len(rows),
        "avg_daily": round(len(rows) / max(days, 1)),
        "top_skills": [
            {"skill": s, "count": c}
            for s, c in skill_counts.most_common(20)
        ],
        "daily_trend": dict(sorted(daily_counts.items())),
    }


def analyze_hiring_velocity(days=30):
    """Analyze which companies are hiring fastest."""
    conn = get_db()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT company, COUNT(*) as cnt FROM jobs "
        "WHERE is_active = 1 AND company != '' AND first_seen_at >= ? "
        "GROUP BY LOWER(TRIM(company)) ORDER BY cnt DESC LIMIT 50",
        (cutoff,)
    ).fetchall()

    conn.close()

    companies = []
    for row in rows:
        velocity = row[1] / max(days, 1)
        if velocity >= 1:  # At least 1 job per day
            companies.append({
                "company": row[0],
                "total_jobs": row[1],
                "jobs_per_day": round(velocity, 1),
                "velocity_label": (
                    "🔥 Hyper-growth" if velocity >= 10 else
                    "🚀 Fast growing" if velocity >= 5 else
                    "📈 Growing" if velocity >= 2 else
                    "📊 Active"
                ),
            })

    return {
        "period_days": days,
        "hiring_companies": companies,
        "total_companies": len(companies),
    }


def analyze_remote():
    """Analyze remote vs onsite job distribution."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    jobs = conn.execute(
        "SELECT title, description, location FROM jobs WHERE is_active = 1"
    ).fetchall()

    conn.close()

    remote_count = 0
    onsite_count = 0
    hybrid_count = 0
    unknown_count = 0

    for job in jobs:
        classification = classify_remote(
            job["title"], job["description"], job["location"]
        )
        if classification == "remote":
            remote_count += 1
        elif classification == "onsite":
            onsite_count += 1
        elif classification == "hybrid":
            hybrid_count += 1
        else:
            unknown_count += 1

    total = len(jobs)
    return {
        "total": total,
        "remote": remote_count,
        "onsite": onsite_count,
        "hybrid": hybrid_count,
        "unknown": unknown_count,
        "remote_pct": round(remote_count / total * 100, 1) if total else 0,
        "onsite_pct": round(onsite_count / total * 100, 1) if total else 0,
        "hybrid_pct": round(hybrid_count / total * 100, 1) if total else 0,
    }


def analyze_visa():
    """Analyze visa sponsorship availability."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    jobs = conn.execute(
        "SELECT title, description, company, location FROM jobs WHERE is_active = 1"
    ).fetchall()

    conn.close()

    visa_jobs = []
    companies_with_visa = Counter()

    for job in jobs:
        if detect_visa(job["title"], job["description"]):
            visa_jobs.append({
                "title": job["title"],
                "company": job["company"],
                "location": job["location"],
            })
            if job["company"]:
                companies_with_visa[job["company"]] += 1

    return {
        "total_visa_jobs": len(jobs),
        "visa_sponsorship_available": len(visa_jobs),
        "visa_pct": round(len(visa_jobs) / len(jobs) * 100, 1) if jobs else 0,
        "top_companies_sponsoring": [
            {"company": c, "count": n}
            for c, n in companies_with_visa.most_common(20)
        ],
    }


def run_all_analytics():
    """Run all analytics and return combined results."""
    log("Running all analytics...")
    return {
        "salary": analyze_salary(),
        "trends": analyze_market_trends(),
        "velocity": analyze_hiring_velocity(),
        "remote": analyze_remote(),
        "visa": analyze_visa(),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--salary", action="store_true")
    parser.add_argument("--trends", action="store_true")
    parser.add_argument("--hiring-velocity", action="store_true")
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--visa", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if args.all or (not any([args.salary, args.trends, args.hiring_velocity, args.remote, args.visa])):
        results = run_all_analytics()
        for category, data in results.items():
            print(f"\n{'='*60}")
            print(f"  {category.upper()}")
            print(f"{'='*60}")
            for k, v in data.items():
                if isinstance(v, list):
                    print(f"  {k}:")
                    for item in v[:5]:
                        print(f"    - {item}")
                else:
                    print(f"  {k}: {v}")
    else:
        if args.salary:
            print(json.dumps(analyze_salary(), indent=2))
        if args.trends:
            print(json.dumps(analyze_market_trends(), indent=2))
        if args.hiring_velocity:
            print(json.dumps(analyze_hiring_velocity(), indent=2))
        if args.remote:
            print(json.dumps(analyze_remote(), indent=2))
        if args.visa:
            print(json.dumps(analyze_visa(), indent=2))


if __name__ == "__main__":
    main()
