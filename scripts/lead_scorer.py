#!/usr/bin/env python3
"""Lead Scorer — AI-powered scoring for job leads.

Scoring factors:
1. Recency (0-25): Jobs posted today = 25, 7 days ago = 0
2. Company Size (0-25): 100-1000 employees = 25, 1-10 = 10
3. Tech Match (0-25): Match user skills against job tags/description
4. Salary Score (0-25): Higher salary = higher score
5. Source Quality (bonus): ATS direct = +5, Job board = +2

Usage:
    python -m scripts.lead_scorer --skills "python,javascript,react" --limit 100
    python -m scripts.lead_scorer --update-all
"""
from __future__ import annotations
import json, os, re, sqlite3, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "lead_scorer.log"

# Default skills for matching
DEFAULT_SKILLS = [
    "python", "javascript", "typescript", "react", "node", "angular", "vue",
    "java", "go", "rust", "c++", "c#", "ruby", "php", "swift", "kotlin",
    "django", "flask", "fastapi", "spring", "express", "nextjs", "nuxt",
    "postgresql", "mysql", "mongodb", "redis", "elasticsearch",
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform",
    "devops", "ci/cd", "jenkins", "github actions",
    "machine learning", "deep learning", "tensorflow", "pytorch",
    "data science", "sql", "spark", "hadoop", "kafka",
    "graphql", "rest api", "microservices", "linux",
]

# Company size scoring
SIZE_SCORES = {
    "1-10": 10, "11-50": 15, "51-200": 20, "201-500": 25,
    "501-1000": 22, "1001-5000": 20, "5001-10000": 18, "10000+": 15,
    "500+": 22, "1000+": 20, "2000+": 20, "5000+": 18, "10000+": 15,
}

# Source quality bonus
SOURCE_BONUS = {
    "ats": 5, "ats:greenhouse": 5, "ats:lever": 5, "ats:ashby": 5,
    "ats:smartrecruiters": 4, "ats:workable": 4, "ats_api": 5,
    "jobspy": 3, "crawl4ai": 3, "curl_cffi": 3, "stealth": 2,
}


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_skills_from_text(text):
    """Extract skills mentioned in text."""
    if not text:
        return set()
    text_lower = text.lower()
    found = set()
    for skill in DEFAULT_SKILLS:
        if skill.lower() in text_lower:
            found.add(skill.lower())
    return found


def calculate_recency_score(posted_at):
    """Score based on how recently the job was posted. 0-25 points."""
    if not posted_at:
        return 5  # Unknown = middle score

    try:
        if isinstance(posted_at, str):
            # Handle various date formats
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%f"]:
                try:
                    posted = datetime.strptime(posted_at[:19], fmt).replace(tzinfo=timezone.utc)
                    break
                except:
                    continue
            else:
                return 5
        else:
            posted = posted_at

        days_ago = (datetime.now(timezone.utc) - posted).days

        if days_ago <= 0:
            return 25  # Posted today
        elif days_ago <= 1:
            return 23  # Yesterday
        elif days_ago <= 3:
            return 20  # Last 3 days
        elif days_ago <= 7:
            return 15  # Last week
        elif days_ago <= 14:
            return 10  # Last 2 weeks
        elif days_ago <= 30:
            return 5   # Last month
        else:
            return 1   # Older
    except:
        return 5


def calculate_company_score(company_data):
    """Score based on company size. 0-25 points."""
    if not company_data:
        return 10  # Unknown = middle score

    size = company_data.get("size", "")
    if not size:
        return 10

    # Try to match size string
    for size_range, score in SIZE_SCORES.items():
        if size_range.lower() in str(size).lower():
            return score

    # Try to parse numeric size
    try:
        num = int(re.sub(r'[^0-9]', '', str(size)))
        if num < 10:
            return 10
        elif num < 50:
            return 15
        elif num < 200:
            return 20
        elif num < 1000:
            return 25
        elif num < 5000:
            return 20
        else:
            return 15
    except:
        return 10


def calculate_tech_match_score(job_skills, user_skills):
    """Score based on how well job matches user's skills. 0-25 points."""
    if not job_skills or not user_skills:
        return 10  # Unknown

    user_set = set(s.lower().strip() for s in user_skills)
    job_set = set(s.lower().strip() for s in job_skills)

    if not user_set or not job_set:
        return 10

    matches = user_set.intersection(job_set)
    match_ratio = len(matches) / len(user_set) if user_set else 0

    # Score based on how many user skills are mentioned
    if match_ratio >= 0.8:
        return 25
    elif match_ratio >= 0.6:
        return 22
    elif match_ratio >= 0.4:
        return 18
    elif match_ratio >= 0.2:
            return 14
    elif match_ratio > 0:
        return 10
    else:
        return 5


def calculate_salary_score(salary):
    """Score based on salary. 0-25 points."""
    if not salary:
        return 10  # Unknown

    try:
        salary_num = float(re.sub(r'[^0-9.]', '', str(salary)))
        if salary_num <= 0:
            return 10
        elif salary_num < 50000:
            return 8
        elif salary_num < 80000:
            return 12
        elif salary_num < 120000:
            return 18
        elif salary_num < 150000:
            return 22
        else:
            return 25
    except:
        return 10


def calculate_source_bonus(source_kind):
    """Bonus points for high-quality sources."""
    if not source_kind:
        return 0
    for key, bonus in SOURCE_BONUS.items():
        if key in str(source_kind).lower():
            return bonus
    return 1


def calculate_job_score(job, user_skills=None):
    """Calculate total score for a job (0-100)."""
    if not user_skills:
        user_skills = DEFAULT_SKILLS

    # Parse skills from job description/tags
    job_skills = set()
    if job.get("tags"):
        try:
            tags = json.loads(job["tags"]) if isinstance(job["tags"], str) else job["tags"]
            job_skills.update(t.lower() for t in tags if isinstance(t, str))
        except:
            pass
    if job.get("description"):
        job_skills.update(parse_skills_from_text(job["description"]))
    if job.get("title"):
        job_skills.update(parse_skills_from_text(job["title"]))

    # Calculate individual scores
    recency = calculate_recency_score(job.get("posted_at") or job.get("first_seen_at"))
    company = 10  # Default, enrich later
    tech = calculate_tech_match_score(job_skills, user_skills)
    salary = calculate_salary_score(job.get("salary"))
    source = calculate_source_bonus(job.get("source_kind"))

    total = min(recency + company + tech + salary + source, 100)

    return {
        "total": total,
        "recency": recency,
        "tech_match": tech,
        "salary": salary,
        "source_bonus": source,
        "matched_skills": list(job_skills.intersection(set(s.lower() for s in user_skills))),
    }


def score_jobs_in_db(user_skills=None, limit=1000):
    """Score all jobs in the database and store scores."""
    if not user_skills:
        user_skills = DEFAULT_SKILLS

    conn = get_db()
    conn.row_factory = sqlite3.Row

    jobs = conn.execute(
        "SELECT dedupe_key, title, company, location, url, description, tags, "
        "source, source_kind, salary, posted_at, first_seen_at "
        "FROM jobs WHERE is_active = 1 ORDER BY first_seen_at DESC LIMIT ?",
        (limit,)
    ).fetchall()

    scored = 0
    scores = {}

    for job in jobs:
        result = calculate_job_score(dict(job), user_skills)
        scores[job["dedupe_key"]] = result["total"]
        scored += 1

    # Update scores in database
    for key, score in scores.items():
        try:
            conn.execute(
                "UPDATE jobs SET tags = json_set(COALESCE(tags, '[]'), '$.score', ?) WHERE dedupe_key = ?",
                (score, key)
            )
        except:
            pass

    conn.commit()
    conn.close()

    # Sort by score
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_scores[:50], scored


def get_top_leads(limit=20):
    """Get the top-scored leads."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    leads = conn.execute(
        "SELECT * FROM jobs WHERE is_active = 1 "
        "ORDER BY first_seen_at DESC LIMIT 1000"
    ).fetchall()

    scored = []
    for lead in leads:
        result = calculate_job_score(dict(lead))
        scored.append({
            **dict(lead),
            "score": result["total"],
            "recency": result["recency"],
            "tech_match": result["tech_match"],
            "salary_score": result["salary"],
            "matched_skills": result["matched_skills"],
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills", default=",".join(DEFAULT_SKILLS))
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--update-all", action="store_true")
    args = parser.parse_args()

    user_skills = [s.strip() for s in args.skills.split(",") if s.strip()]

    if args.update_all:
        log("Scoring all jobs in database...")
        top, total = score_jobs_in_db(user_skills, args.limit)
        log(f"Scored {total} jobs")
        print("\nTop 20 Leads:")
        for i, (key, score) in enumerate(top[:20], 1):
            print(f"  {i}. Score: {score}/100 — {key[:60]}")
    else:
        leads = get_top_leads(args.top)
        print(f"\nTop {len(leads)} Leads:")
        for i, lead in enumerate(leads, 1):
            print(f"  {i}. [{lead['score']}/100] {lead['title'][:50]} @ {lead['company']}")
            if lead['matched_skills']:
                print(f"     Skills: {', '.join(lead['matched_skills'][:5])}")


if __name__ == "__main__":
    main()
