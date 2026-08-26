#!/usr/bin/env python3
"""Resume Matcher — Match your skills against job descriptions, rank by fit %.

Usage:
    python -m scripts.resume_matcher --resume path/to/resume.txt
    python -m scripts.resume_matcher --skills "python,react,aws,docker"
    python -m scripts.resume_matcher --top 20
"""
from __future__ import annotations
import json, os, re, sqlite3, time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "resume_matcher.log"

# Comprehensive skill database with categories
SKILL_DB = {
    # Languages
    "python": ["python", "python3", "py"],
    "javascript": ["javascript", "js", "ecmascript", "es6", "es2015"],
    "typescript": ["typescript", "ts"],
    "java": ["java", "jvm"],
    "go": ["go", "golang"],
    "rust": ["rust", "rustlang"],
    "c++": ["c++", "cpp"],
    "c#": ["c#", "csharp", ".net", "dotnet"],
    "ruby": ["ruby", "rails", "ruby on rails"],
    "php": ["php", "laravel", "symfony"],
    "swift": ["swift", "swiftui", "ios"],
    "kotlin": ["kotlin", "android"],
    "scala": ["scala"],
    "r": [" r ", " r,", "r language", "r programming"],
    "sql": ["sql", "mysql", "postgresql", "sqlite", "t-sql", "plsql"],
    # Frontend
    "react": ["react", "reactjs", "react.js", "jsx"],
    "angular": ["angular", "angularjs"],
    "vue": ["vue", "vuejs", "vue.js", "vuex"],
    "svelte": ["svelte", "sveltekit"],
    "nextjs": ["next.js", "nextjs", "next js"],
    "html/css": ["html", "css", "sass", "less", "scss"],
    "tailwind": ["tailwind", "tailwindcss"],
    # Backend
    "node.js": ["node", "nodejs", "node.js", "express", "nest.js", "nestjs"],
    "django": ["django"],
    "flask": ["flask"],
    "fastapi": ["fastapi", "fast api"],
    "spring": ["spring", "spring boot", "springboot"],
    ".net": [".net", "asp.net", "dotnet"],
    "laravel": ["laravel"],
    "rails": ["rails", "ruby on rails"],
    # Databases
    "postgresql": ["postgresql", "postgres", "psql"],
    "mysql": ["mysql", "mariadb"],
    "mongodb": ["mongodb", "mongo"],
    "redis": ["redis"],
    "elasticsearch": ["elasticsearch", "elastic", "kibana"],
    "cassandra": ["cassandra"],
    "dynamodb": ["dynamodb", "dynamo"],
    "snowflake": ["snowflake"],
    "bigquery": ["bigquery", "big query"],
    "oracle": ["oracle"],
    # Cloud
    "aws": ["aws", "amazon web services", "ec2", "s3", "lambda", "ecs", "eks"],
    "azure": ["azure", "microsoft azure"],
    "gcp": ["gcp", "google cloud", "google cloud platform"],
    # DevOps
    "docker": ["docker", "containerization", "containers"],
    "kubernetes": ["kubernetes", "k8s"],
    "terraform": ["terraform", "iac", "infrastructure as code"],
    "jenkins": ["jenkins", "ci/cd", "ci/cd pipeline"],
    "github actions": ["github actions", "github ci"],
    "gitlab ci": ["gitlab", "gitlab ci"],
    # AI/ML
    "machine learning": ["machine learning", "ml", "supervised learning", "unsupervised learning"],
    "deep learning": ["deep learning", "neural network", "cnn", "rnn", "lstm"],
    "tensorflow": ["tensorflow", "tf"],
    "pytorch": ["pytorch", "torch"],
    "nlp": ["nlp", "natural language processing", "text processing"],
    "computer vision": ["computer vision", "cv", "image processing"],
    # Data
    "data engineering": ["data engineer", "data engineering", "etl", "data pipeline"],
    "data science": ["data science", "data scientist", "analytics"],
    "spark": ["spark", "apache spark", "pyspark"],
    "kafka": ["kafka", "apache kafka"],
    "hadoop": ["hadoop", "hdfs"],
    # Soft skills
    "leadership": ["leadership", "lead", "leading", "team lead"],
    "agile": ["agile", "scrum", "kanban", "sprint"],
    "communication": ["communication", "communicate"],
    "teamwork": ["team", "collaboration", "collaborative"],
    "problem solving": ["problem solving", "problem-solving", "analytical"],
}


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def extract_skills_from_text(text):
    """Extract skills from text using the skill database."""
    if not text:
        return set()
    text_lower = text.lower()
    found = set()
    for skill, keywords in SKILL_DB.items():
        for kw in keywords:
            if kw in text_lower:
                found.add(skill)
                break
    return found


def load_resume_skills(resume_path):
    """Load skills from a resume file."""
    try:
        with open(resume_path, 'r', encoding='utf-8') as f:
            text = f.read()
        skills = extract_skills_from_text(text)
        log(f"Extracted {len(skills)} skills from resume: {', '.join(sorted(skills))}")
        return skills
    except Exception as e:
        log(f"Error reading resume: {e}")
        return set()


def calculate_match_score(job_skills, user_skills):
    """Calculate match percentage between job requirements and user skills."""
    if not user_skills:
        return 0, []
    if not job_skills:
        return 50, []  # No requirements listed = neutral match

    matches = user_skills.intersection(job_skills)
    match_ratio = len(matches) / len(job_skills) if job_skills else 0
    coverage = len(matches) / len(user_skills) if user_skills else 0

    # Score: 60% match ratio + 40% coverage
    score = (match_ratio * 60 + coverage * 40)
    return round(min(score, 100)), list(matches)


def match_jobs(user_skills, keyword="", location="", limit=100):
    """Match user skills against all jobs and rank by fit."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    conditions = ["is_active = 1"]
    params = []

    if keyword:
        conditions.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like])

    if location:
        conditions.append("LOWER(location) LIKE ?")
        params.append(f"%{location.lower()}%")

    params.append(limit * 3)  # Get more to filter

    rows = conn.execute(
        f"SELECT dedupe_key, title, company, location, url, description, tags, "
        f"source, salary, posted_at FROM jobs "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY first_seen_at DESC LIMIT ?",
        params
    ).fetchall()

    conn.close()

    results = []
    for row in rows:
        job = dict(row)
        # Extract job skills
        job_skills = extract_skills_from_text(
            f"{job.get('title', '')} {job.get('description', '')}"
        )
        # Also from tags
        if job.get('tags'):
            try:
                tags = json.loads(job['tags']) if isinstance(job['tags'], str) else job['tags']
                for t in tags:
                    if isinstance(t, str):
                        job_skills.update(extract_skills_from_text(t))
            except:
                pass

        score, matches = calculate_match_score(job_skills, user_skills)

        if score > 0:
            results.append({
                **job,
                "match_score": score,
                "matched_skills": matches,
                "job_skills": list(job_skills),
            })

    # Sort by match score
    results.sort(key=lambda x: x["match_score"], reverse=True)
    return results[:limit]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", help="Path to resume file")
    parser.add_argument("--skills", help="Comma-separated skills")
    parser.add_argument("--keyword", default="")
    parser.add_argument("--location", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.resume:
        user_skills = load_resume_skills(args.resume)
    elif args.skills:
        user_skills = set(s.strip().lower() for s in args.skills.split(",") if s.strip())
    else:
        user_skills = set(SKILL_DB.keys())
        log("No skills specified, using all skills")

    if not user_skills:
        print("No skills found. Provide a resume or skills list.")
        return

    log(f"Matching {len(user_skills)} skills against jobs...")
    results = match_jobs(user_skills, args.keyword, args.location, args.limit)

    if args.json:
        print(json.dumps(results[:args.limit], indent=2))
    else:
        print(f"\nTop {len(results)} Matching Jobs:")
        print(f"{'='*80}")
        for i, job in enumerate(results, 1):
            print(f"\n{i}. [{job['match_score']}% match] {job['title']}")
            print(f"   Company: {job['company']} | Location: {job.get('location', 'N/A')}")
            print(f"   Matched: {', '.join(job['matched_skills'][:5])}")
            print(f"   URL: {job.get('url', 'N/A')}")


if __name__ == "__main__":
    main()
