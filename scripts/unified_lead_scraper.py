#!/usr/bin/env python3
"""Unified Lead Scraper — Runs all 6 scraping backends together.

Backends:
  1. Crawl4AI   — Smart async crawling with auto-retry
  2. Playwright  — Anti-detection browser scraping
  3. curl_cffi   — TLS fingerprint bypass
  4. JobSpy      — LinkedIn, Indeed, Glassdoor, Google
  5. Ever Jobs   — 160+ sources via ATS APIs
  6. DataForge   — Company enrichment

Usage:
    python -m scripts.unified_lead_scraper
    python -m scripts.unified_lead_scraper --backends curl,jobspy,everjobs --rounds 3
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, sys, time, random, importlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "unified_scraper.log"
CHECKPOINT = ROOT / "unified_scraper_cp.json"

# Default config
DEFAULT_KEYWORDS = [
    "software engineer", "software developer", "backend developer", "frontend developer",
    "full stack developer", "react developer", "python developer", "java developer",
    "devops engineer", "data engineer", "data scientist", "machine learning engineer",
    "mobile developer", "android developer", "ios developer", "flutter developer",
    "cloud engineer", "platform engineer", "SRE", "QA engineer",
    "test automation", "embedded engineer", "AI engineer", "ML engineer",
    "product engineer", "security engineer", "web developer", "typescript developer",
    "angular developer", "vue developer", "golang developer", "rust developer",
    "kubernetes engineer", "aws engineer", "azure engineer", "terraform engineer",
    "ruby developer", "php developer", "kotlin developer", "swift developer",
    "c++ developer", "c# developer", "node.js developer", "spark developer",
    "ETL developer", "database administrator", "network engineer", "firmware engineer",
    "blockchain developer", "game developer",
]

DEFAULT_LOCATIONS = [
    "", "remote", "United States", "New York", "San Francisco", "Austin",
    "Seattle", "Boston", "Chicago", "Los Angeles", "Denver", "London",
    "Berlin", "Toronto", "Sydney", "Singapore", "Dublin", "Bangalore",
    "Mumbai", "Delhi", "Hyderabad",
]


# ── Backend registry ──────────────────────────────────────────
BACKENDS = {
    "crawl4ai": {
        "module": "scripts.crawl4ai_scraper",
        "func": "run_crawl4ai",
        "cost": "fast",
        "label": "Crawl4AI (Smart Crawler)",
    },
    "playwright": {
        "module": "scripts.playwright_stealth_scraper",
        "func": "run_stealth",
        "cost": "slow",
        "label": "Playwright Stealth (Anti-Bot)",
    },
    "curl": {
        "module": "scripts.curl_scraper",
        "func": "run_curl_scraper",
        "cost": "fast",
        "label": "curl_cffi (TLS Bypass)",
    },
    "jobspy": {
        "module": "scripts.jobspy_scraper",
        "func": "run_jobspy",
        "cost": "fast",
        "label": "JobSpy (LinkedIn/Indeed/Google)",
    },
    "everjobs": {
        "module": "scripts.everjobs_scraper",
        "func": "run_everjobs",
        "cost": "fast",
        "label": "Ever Jobs (160+ ATS Sources)",
    },
    "enrich": {
        "module": "scripts.dataforge_enricher",
        "func": "enrich_jobs_from_db",
        "cost": "fast",
        "label": "DataForge (Company Enrichment)",
    },
}


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_db_count():
    try:
        conn = get_db()
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.close()
        return count
    except:
        return 0


def save_checkpoint(data):
    try:
        with open(CHECKPOINT, "w") as f:
            json.dump(data, f)
    except:
        pass


def load_checkpoint():
    try:
        with open(CHECKPOINT, "r") as f:
            return json.load(f)
    except:
        return {}


def run_backend(backend_name, keywords, locations):
    """Run a single backend and return (new_count, elapsed_seconds)."""
    start = time.time()
    try:
        cfg = BACKENDS[backend_name]
        mod = importlib.import_module(cfg["module"])
        func = getattr(mod, cfg["func"])

        if backend_name == "enrich":
            enriched, updated = func(keyword="", limit=500)
            return updated, time.time() - start
        elif backend_name == "jobspy":
            scraped, new = func(
                keywords=keywords[:15],
                locations=locations[:10],
                max_per_search=50,
            )
            return new, time.time() - start
        elif backend_name == "everjobs":
            new = func(keyword=keywords[0] if keywords else "software engineer")
            return new, time.time() - start
        elif backend_name == "curl":
            scraped, new = func(
                keywords=keywords,
                locations=locations,
                max_items=500,
                workers=20,
            )
            return new, time.time() - start
        elif backend_name == "crawl4ai":
            scraped, new = func(
                keywords=keywords,
                locations=locations,
                max_items=300,
            )
            return new, time.time() - start
        elif backend_name == "playwright":
            scraped, new = func(
                keywords=keywords[:10],
                locations=locations[:5],
                max_items=200,
                workers=2,
            )
            return new, time.time() - start
        else:
            return 0, time.time() - start
    except Exception as e:
        log(f"  [ERROR] {backend_name}: {e}")
        return 0, time.time() - start


def run_round(backends, keywords, locations, round_num):
    """Run one round of scraping across all backends."""
    log(f"{'='*60}")
    log(f"ROUND {round_num} — {len(backends)} backends, {len(keywords)} keywords, {len(locations)} locations")
    log(f"{'='*60}")

    start_db = get_db_count()
    results = {}
    total_new = 0

    for backend in backends:
        cfg = BACKENDS[backend]
        log(f"\n--- [{cfg['label']}] starting ---")
        try:
            new_count, elapsed = run_backend(backend, keywords, locations)
            results[backend] = {"new": new_count, "time": elapsed}
            total_new += new_count
            current = get_db_count()
            log(f"--- [{cfg['label']}] done: +{new_count} new ({elapsed:.0f}s) | DB: {current:,} ---")
        except Exception as e:
            log(f"--- [{cfg['label']}] FAILED: {e} ---")
            results[backend] = {"new": 0, "time": 0, "error": str(e)}

    end_db = get_db_count()
    elapsed_total = sum(r.get("time", 0) for r in results.values())
    rate = total_new / (elapsed_total / 60) if elapsed_total > 0 else 0

    log(f"\n{'='*60}")
    log(f"ROUND {round_num} SUMMARY:")
    log(f"  New jobs: {total_new}")
    log(f"  DB: {start_db:,} -> {end_db:,} (+{end_db - start_db:,})")
    log(f"  Time: {elapsed_total:.0f}s | Rate: {rate:.0f} new/min")
    log(f"  Gap to 1M: {max(0, 1000000 - end_db):,}")
    for name, r in results.items():
        status = f"+{r['new']}" if 'error' not in r else f"ERROR: {r.get('error', 'unknown')}"
        log(f"  {name}: {status} ({r.get('time', 0):.0f}s)")
    log(f"{'='*60}\n")

    return total_new, end_db


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Unified Lead Scraper — All 6 Backends")
    parser.add_argument("--backends", default="crawl4ai,curl,jobspy,everjobs,enrich",
                        help="Comma-separated backends to run")
    parser.add_argument("--keywords", default=None, help="Comma-separated keywords (or use defaults)")
    parser.add_argument("--locations", default=None, help="Comma-separated locations (or use defaults)")
    parser.add_argument("--rounds", type=int, default=3, help="Number of rounds to run")
    parser.add_argument("--target", type=int, default=1000000, help="Target total jobs")
    parser.add_argument("--list-backends", action="store_true", help="List available backends")
    args = parser.parse_args()

    if args.list_backends:
        print("Available backends:")
        for name, cfg in BACKENDS.items():
            print(f"  {name:12s} — {cfg['label']}")
        return

    backends = [b.strip() for b in args.backends.split(",") if b.strip() in BACKENDS]
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else DEFAULT_KEYWORDS
    locations = [l.strip() for l in args.locations.split(",") if l.strip()] if args.locations else DEFAULT_LOCATIONS

    # Load checkpoint
    cp = load_checkpoint()
    start_round = cp.get("round", 0) + 1

    log(f"{'='*60}")
    log(f"UNIFIED LEAD SCRAPER — Starting Round {start_round}")
    log(f"Backends: {', '.join(backends)}")
    log(f"Keywords: {len(keywords)} | Locations: {len(locations)}")
    log(f"Current DB: {get_db_count():,} | Target: {args.target:,}")
    log(f"{'='*60}\n")

    for round_num in range(start_round, start_round + args.rounds):
        new_count, total = run_round(backends, keywords, locations, round_num)

        # Save checkpoint
        save_checkpoint({
            "round": round_num,
            "total": total,
            "last_run": datetime.now(timezone.utc).isoformat(),
        })

        # Check if we've hit the target
        if total >= args.target:
            log(f"\n🎯 TARGET REACHED! {total:,} jobs in database!")
            break

        # Brief pause between rounds
        if round_num < start_round + args.rounds - 1:
            log(f"\nPausing 5 seconds before next round...")
            time.sleep(5)

    # Final summary
    final = get_db_count()
    log(f"\n{'='*60}")
    log(f"FINAL STATUS: {final:,} total jobs in database")
    log(f"Target: {args.target:,} | {'ACHIEVED ✅' if final >= args.target else f'STILL NEED {args.target - final:,} MORE'}")
    log(f"{'='*60}")


if __name__ == "__main__":
    main()
