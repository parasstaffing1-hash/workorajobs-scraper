#!/usr/bin/env python3
"""Dedup Engine — Fuzzy match duplicate jobs across platforms.

Detects when the same job is posted on LinkedIn, Indeed, Glassdoor, etc.
using fuzzy title/company matching and normalizes to one canonical record.

Usage:
    python -m scripts.dedup_engine --analyze
    python -m scripts.dedup_engine --dedup --threshold 85
"""
from __future__ import annotations
import json, os, re, sqlite3, time
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "dedup.log"


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def normalize_text(text):
    """Normalize text for comparison."""
    if not text:
        return ""
    text = text.lower().strip()
    # Remove common variations
    text = re.sub(r'\b(sr|senior|jr|junior|lead|principal|staff|sr\.|jr\.)\b', '', text)
    text = re.sub(r'\b(i|ii|iii|iv|v|i\.|ii\.|iii\.)\b', '', text)
    text = re.sub(r'\b(software|engineer|developer|eng|dev|engr)\b', '', text)
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def similarity(a, b):
    """Calculate similarity between two strings."""
    if not a or not b:
        return 0.0
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def are_duplicates(job1, job2, threshold=0.85):
    """Check if two jobs are likely the same position."""
    title_sim = similarity(job1.get("title", ""), job2.get("title", ""))
    company_sim = similarity(job1.get("company", ""), job2.get("company", ""))

    # Both title and company must be similar
    if title_sim >= threshold and company_sim >= 0.7:
        return True

    # Or very similar title with same location
    if title_sim >= 0.9:
        loc1 = normalize_text(job1.get("location", ""))
        loc2 = normalize_text(job2.get("location", ""))
        if loc1 and loc2 and (loc1 == loc2 or loc1 in loc2 or loc2 in loc1):
            return True

    return False


def find_duplicates(threshold=0.85):
    """Find all duplicate job groups in the database."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    jobs = conn.execute(
        "SELECT dedupe_key, title, company, location, url, source, first_seen_at "
        "FROM jobs WHERE is_active = 1 ORDER BY first_seen_at ASC"
    ).fetchall()

    conn.close()

    # Convert to dicts
    job_dicts = [dict(j) for j in jobs]

    # Group by approximate company name
    company_groups = defaultdict(list)
    for job in job_dicts:
        norm_company = normalize_text(job.get("company", ""))
        if norm_company:
            company_groups[norm_company].append(job)

    # Find duplicates within each company group
    duplicate_groups = []
    processed = set()

    for company_key, company_jobs in company_groups.items():
        if len(company_jobs) < 2:
            continue

        for i, job1 in enumerate(company_jobs):
            if job1["dedupe_key"] in processed:
                continue

            group = [job1]
            for j in range(i + 1, len(company_jobs)):
                job2 = company_jobs[j]
                if job2["dedupe_key"] in processed:
                    continue
                if are_duplicates(job1, job2, threshold):
                    group.append(job2)
                    processed.add(job2["dedupe_key"])

            if len(group) > 1:
                duplicate_groups.append(group)
                processed.add(job1["dedupe_key"])

    return duplicate_groups


def deduplicate(dry_run=True, threshold=0.85):
    """Mark duplicate jobs as inactive, keeping the earliest one."""
    log(f"Finding duplicates (threshold={threshold})...")
    groups = find_duplicates(threshold)

    total_duplicates = sum(len(g) - 1 for g in groups)
    log(f"Found {len(groups)} duplicate groups ({total_duplicates} duplicate jobs)")

    if not groups:
        return 0

    if dry_run:
        print(f"\n{'='*60}")
        print(f"DRY RUN — {len(groups)} duplicate groups found")
        print(f"{'='*60}")
        for i, group in enumerate(groups[:20], 1):
            print(f"\nGroup {i}:")
            for job in group:
                print(f"  - [{job['source']}] {job['title'][:50]} @ {job['company']}")
        return total_duplicates

    # Mark duplicates as inactive (keep the earliest)
    conn = get_db()
    deactivated = 0

    for group in groups:
        # Keep the first (earliest), deactivate the rest
        for job in group[1:]:
            try:
                conn.execute(
                    "UPDATE jobs SET is_active = 0 WHERE dedupe_key = ?",
                    (job["dedupe_key"],)
                )
                deactivated += 1
            except:
                pass

    conn.commit()
    conn.close()

    log(f"Deactivated {deactivated} duplicate jobs")
    return deactivated


def get_dedup_stats():
    """Get deduplication statistics."""
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_active = 1").fetchone()[0]
    inactive = conn.execute("SELECT COUNT(*) FROM jobs WHERE is_active = 0").fetchone()[0]

    # Count by source
    sources = {}
    for row in conn.execute(
        "SELECT source, COUNT(*) FROM jobs WHERE is_active = 1 GROUP BY source ORDER BY COUNT(*) DESC"
    ).fetchall():
        sources[row[0]] = row[1]

    # Count unique titles (normalized)
    unique_titles = set()
    for row in conn.execute("SELECT title FROM jobs WHERE is_active = 1").fetchall():
        unique_titles.add(normalize_text(row[0]))

    conn.close()

    return {
        "total_active": total,
        "total_inactive": inactive,
        "unique_normalized_titles": len(unique_titles),
        "sources": sources,
        "duplication_rate": round((1 - len(unique_titles) / total) * 100, 1) if total > 0 else 0,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--analyze", action="store_true", help="Analyze duplicates")
    parser.add_argument("--dedup", action="store_true", help="Actually deduplicate")
    parser.add_argument("--threshold", type=float, default=0.85, help="Similarity threshold")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--execute", action="store_true", help="Actually execute dedup")
    args = parser.parse_args()

    if args.analyze:
        stats = get_dedup_stats()
        print(f"\nDeduplication Analysis:")
        print(f"  Active jobs: {stats['total_active']:,}")
        print(f"  Inactive (deduped): {stats['total_inactive']:,}")
        print(f"  Unique normalized titles: {stats['unique_normalized_titles']:,}")
        print(f"  Duplication rate: {stats['duplication_rate']}%")
        print(f"\nTop sources:")
        for src, count in list(stats['sources'].items())[:10]:
            print(f"    {src}: {count:,}")

        groups = find_duplicates(args.threshold)
        print(f"\nDuplicate groups found: {len(groups)}")
        for i, group in enumerate(groups[:10], 1):
            print(f"\n  Group {i}:")
            for job in group:
                print(f"    - [{job['source']}] {job['title'][:50]} @ {job['company']}")

    elif args.dedup:
        deactivate = deduplicate(not args.execute, args.threshold)
        if not args.execute:
            print(f"\nDry run complete. Use --execute to actually deactivate {deactivate} duplicates.")
    else:
        stats = get_dedup_stats()
        print(f"Active: {stats['total_active']:,} | Unique: {stats['unique_normalized_titles']:,} | Dup rate: {stats['duplication_rate']}%")


if __name__ == "__main__":
    main()
