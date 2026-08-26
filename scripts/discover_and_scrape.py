#!/usr/bin/env python3
"""Auto-discover + scrape: probe thousands of company slugs on ATS platforms,
scrape every valid one in parallel. Target: 1M fresh jobs in 7 days.

Discovery strategy:
  1. Probe common slug patterns on Greenhouse/Lever/Ashby
  2. Use known tech company names as seeds
  3. Scrape every valid board in parallel (20 threads)
  4. Checkpoint after each batch
"""
from __future__ import annotations

import json
import re
import sqlite3
import string
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from itertools import product

import httpx

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP_FILE = ROOT / ".freebuff" / "discover_checkpoint.json"
LOG_FILE = ROOT / ".freebuff" / "discover.log"

DB_LOCK = Lock()

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_checkpoint() -> dict:
    if CP_FILE.exists():
        try:
            return json.loads(CP_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"discovered": [], "scraped": [], "stats": {"new": 0, "errors": 0, "valid_boards": 0}}


def save_checkpoint(cp: dict):
    CP_FILE.parent.mkdir(parents=True, exist_ok=True)
    CP_FILE.write_text(json.dumps(cp, indent=2), encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# MASSIVE company name seeds (thousands of real company names)
# ════════════════════════════════════════════════════════════════

# Real company names that likely have ATS boards
COMPANY_NAMES = [
    # FAANG+MAANG
    "google", "apple", "amazon", "meta", "netflix", "microsoft",
    "spotify", "twitter", "snap", "uber", "lyft", "airbnb",
    # Tech giants
    "salesforce", "adobe", "oracle", "ibm", "intel", "cisco",
    "vmware", "sap", "service-now", "servicenow", "workday",
    # Cloud/Infra
    "cloudflare", "fastly", "vercel", "netlify", "digitalocean",
    "hashicorp", "pulumi", "docker", "redhat", "suse",
    # Databases
    "mongodb", "elastic", "redis", "cockroachlabs", "planetscale",
    "neon", "supabase", "databricks", "snowflake", "confluent",
    # Dev tools
    "gitlab", "github", "bitbucket", "atlassian", "jira",
    "linear", "height", "notion", "figma", "canva",
    "slack", "discord", "zoom", "miro", "whimsical",
    # Monitoring
    "datadog", "newrelic", "pagerduty", "sentry", "grafana",
    "amplitude", "mixpanel", "segment", "heap", "fullstory",
    "hotjar", "posthog", "plausible",
    # Security
    "crowdstrike", "paloaltonetworks", "zscaler", "sentinelone",
    "snyk", "veracode", "abnormalsecurity", "huntress",
    "cybereason", " RecordedFuture", "expel",
    # AI/ML
    "openai", "anthropic", "xai", "stabilityai", "inflectionai",
    "scaleai", "snorkelai", "togetherai", "assemblyai",
    "cohere", "mistral", "huggingface", "replicate",
    # Fintech
    "stripe", "square", "plaid", "brex", "ramp", "chime",
    "sofi", "affirm", "klarna", "revolut", "monzo", "n26",
    "wise", "mercury", "marqeta", "galileo", "nubank",
    "coinbase", "binance", "kraken", "robinhood", "eToro",
    # E-commerce
    "shopify", "ebay", "etsy", "wayfair", "bonobos",
    "allbirds", "warby-parker", "casper", "peloton",
    # Logistics
    "flexport", "project44", "convoy", "uber-freight",
    "shipbob", "shippo", "stord", "fulfillment",
    # Health
    "flatironhealth", "cloverhealth", "hims", "forward",
    "one-medical", "whoop", "oura", "strava",
    # Gaming
    "epicgames", "riotgames", "roblox", "unity",
    "supercell", "krafton", "taketwo", "rockstargames",
    "activision", "blizzard", "ea", "ubisoft",
    # Media
    "spotify", "pandora", "soundcloud", "twitch",
    "youtube", "vimeo", "duolingo", "coursera",
    # India
    "razorpay", "phonepe", "groww", "zerodha", "upstox",
    "cred", "slice", "meesho", "swiggy", "zomato",
    "ola", "rapido", "freshworks", "zoho", "hasura",
    "darwinbox", "citiustech", "sahaj", "postman",
    # Remote-first
    "buffer", "zapier", "helpscout", "basecamp", "automattic",
    "toptal", "upwork", "fiverr", "gitlab", "turing",
    # More SaaS
    "gusto", "zenefits", "bamboohr", "lattice", "15five",
    "cultureamp", "leapsome", "personio", "remote",
    "deel", "oyster", "papaya-global", "factorial",
    # More dev
    "snyk", "launchdarkly", "split", "optimizely",
    "circleci", "travis-ci", "jfrog", "sonatype",
    "kong", "tyk", "redis-labs", "influxdata",
    # More enterprise
    "servicenow", "salesforce", "workday", "adobe",
    "vmware", "cisco", "juniper", "arista",
    "paloaltonetworks", "fortinet", "check-point",
    # More India
    "tcs", "infosys", "wipro", "hcl", "tech-mahindra",
    "ltimindtree", "persistent", "mphasis", "hexaware",
    "bsnl", "airtel", "jio", "vodafone-idea",
    "tata-steel", "tata-motors", "reliance", "adani",
    "bajaj", "mahindra", "larsen", " Infosys",
    # Startup ecosystems
    "figma", "canva", "notion", "airtable",
    "loom", "cal.com", "retool", "retailzi",
    "vercel", "netlify", "cloudflare-workers",
    "supabase", "planetscale", "neon", "turborepo",
    "prisma", "hasura", "hasura-graphql-engine",
    # More varied
    "bytedance", "tiktok", "alibaba", "tencent",
    "baidu", "jd.com", "pinduoduo", "meituan",
    "sea-group", "grab", "gojek", "traveloka",
    "shopee", "lazada", "tokopedia", "bukalapak",
    "flipkart", "paytm", "mobikwik", "freecharge",
    "policybazaar", "policybazaar", "paisabazaar",
    "etimes", "times-internet", "ndtv", "hotstar",
    "jiocinema", "zee5", "sony-liv", "altbalaji",
    "pratilipi", "kuku-fm", "gaana", "wynk",
    "josh", "moj", "sharechat", "tring",
    "curefit", "cult-fit", "healthifyme", " Practo",
    "1mg", "pharmeasy", "netmeds", "medlife",
    "bigbasket", "grofers", "blinkit", "instamart",
    "dunzo", "porter", "blackbuck", "rivigo",
    "uber", "ola", "rapido", "blu-smart",
    "zoomcar", "revv", "drivezy", "vogo",
    "oyo", "make-my-trip", "goibibo", "cleartrip",
    "ixigo", "redbus", "abhibus", "trivago",
    "zomato", "swiggy", "foodpanda", "ubereats",
    "dot", "dotpe", "cashfree", "razorpay-x",
    "phonepe", "paytm", "google-pay", "amazon-pay",
    "cred", "slice", "one-card", "github-copilot",
    # More tech companies
    "atlassian", "lassian", "zendesk", "intercom",
    "drift", "hubspot", "marketo", "pardot",
    "salesloft", "outreach", "gong", "chorus",
    "highspot", "seismic", "brainshark", "showpad",
    "clari", "boostup", "groove", "agile",
    # More varied
    "accenture", "deloitte", "pwc", "ey", "kpmg",
    "mckinsey", "bain", "bcg", "roland-berger",
    "oliver-wyman", "booz-allen", "leidos",
    "northrop-grumman", "raytheon", "lockheed-martin",
    "general-dynamics", "bae-systems", "l3harris",
]

# Generate slug variations from company names
def generate_slugs(names: list[str]) -> list[str]:
    """Generate ATS slug variations from company names."""
    slugs = set()
    for name in names:
        base = name.lower().strip()
        # Common slug patterns
        slugs.add(base)
        slugs.add(base.replace(" ", ""))
        slugs.add(base.replace(" ", "-"))
        slugs.add(base.replace(" ", "_"))
        slugs.add(base.replace(".", ""))
        slugs.add(base.replace(".", "-"))
        # Without common suffixes
        for suffix in [".com", ".io", ".ai", ".co", ".inc", ".corp"]:
            if base.endswith(suffix):
                slugs.add(base[:-len(suffix)])
    return list(slugs)


# ════════════════════════════════════════════════════════════════
# ATS scrapers
# ════════════════════════════════════════════════════════════════
def scrape_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True)
        if r.status_code != 200 or not r.text.strip():
            return []
        data = r.json()
        jobs = data.get("jobs", [])
        if not jobs:
            return []
        return [{
            "title": j.get("title", ""),
            "company": data.get("name", slug),
            "location": (j.get("location", {}) or {}).get("name", "") if isinstance(j.get("location"), dict) else str(j.get("location", "")),
            "url": j.get("absolute_url", ""),
            "posted_at": j.get("updated_at") or j.get("created_at"),
            "jobkey": str(j.get("id", "")),
            "source": f"greenhouse:{slug}",
            "description": (j.get("content") or "")[:500],
            "tags": (j.get("departments") or [{}])[0].get("name", "") if j.get("departments") else "",
        } for j in jobs]
    except Exception:
        return []


def scrape_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True)
        if r.status_code != 200 or not r.text.strip():
            return []
        data = r.json()
        if not isinstance(data, list) or not data:
            return []
        return [{
            "title": j.get("text", ""),
            "company": j.get("categories", {}).get("team", slug),
            "location": j.get("categories", {}).get("location", ""),
            "url": j.get("hostedUrl", ""),
            "posted_at": datetime.fromtimestamp(j.get("createdAt", 0) / 1000, tz=timezone.utc).isoformat() if j.get("createdAt") else None,
            "jobkey": j.get("id", ""),
            "source": f"lever:{slug}",
            "description": (j.get("descriptionPlain") or "")[:500],
            "tags": j.get("teamsPlain", ""),
        } for j in data]
    except Exception:
        return []


def scrape_ashby(slug):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True)
        if r.status_code != 200 or not r.text.strip():
            return []
        data = r.json()
        board = data.get("jobBoard", {})
        openings = board.get("openings", [])
        if not openings:
            return []
        return [{
            "title": j.get("title", ""),
            "company": board.get("name", slug),
            "location": j.get("locationName", ""),
            "url": j.get("url", ""),
            "posted_at": j.get("publishedAt"),
            "jobkey": j.get("id", ""),
            "source": f"ashby:{slug}",
            "description": "",
            "tags": j.get("departmentName", ""),
        } for j in openings]
    except Exception:
        return []


def scrape_smartrecruiters(slug):
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0"
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True)
        if r.status_code != 200 or not r.text.strip():
            return []
        data = r.json()
        content = data.get("content", [])
        if not content:
            return []
        return [{
            "title": j.get("name", ""),
            "company": j.get("company", {}).get("name", slug),
            "location": ((j.get("location") or {}).get("city", "") + ", " + ((j.get("location") or {}).get("country", ""))).strip(", "),
            "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('ref', '')}",
            "posted_at": j.get("releasedDate"),
            "jobkey": str(j.get("id", "")),
            "source": f"smartrecruiters:{slug}",
            "description": "",
            "tags": "",
        } for j in content]
    except Exception:
        return []


# ════════════════════════════════════════════════════════════════
# Discovery: probe a slug on all platforms
# ════════════════════════════════════════════════════════════════
def probe_slug(slug: str) -> list[dict]:
    """Probe a slug on all ATS platforms. Return jobs if found."""
    for scraper in [scrape_greenhouse, scrape_lever, scrape_ashby, scrape_smartrecruiters]:
        jobs = scraper(slug)
        if jobs:
            return jobs
    return []


# ════════════════════════════════════════════════════════════════
# DB storage (thread-safe)
# ════════════════════════════════════════════════════════════════
def store_jobs(conn, jobs, tag) -> int:
    new = 0
    now = datetime.now(timezone.utc).isoformat()
    with DB_LOCK:
        for j in jobs:
            if not j.get("title") or not j.get("url"):
                continue
            try:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO jobs
                       (dedupe_key, title, company, location, description, url, source,
                        source_kind, external_id, posted_at, salary, tags,
                        first_seen_at, last_seen_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (j["url"] or j.get("jobkey", ""), j["title"], j.get("company", ""),
                     j.get("location", ""), j.get("description", ""), j["url"],
                     j["source"], "ats", j.get("jobkey", ""), j.get("posted_at"),
                     j.get("salary", ""), tag, now, now),
                )
                if cur.rowcount > 0:
                    new += 1
            except Exception:
                continue
        conn.commit()
    return new


# ════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=20)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--batch", type=int, default=200, help="slugs per batch")
    args = ap.parse_args()

    # Generate all slugs
    all_slugs = generate_slugs(COMPANY_NAMES)
    log(f"Generated {len(all_slugs)} slugs from {len(COMPANY_NAMES)} company names")

    cp = load_checkpoint() if args.resume else {"discovered": [], "scraped": [], "stats": {"new": 0, "errors": 0, "valid_boards": 0}}
    scraped_set = set(cp["scraped"])
    remaining = [s for s in all_slugs if s not in scraped_set]
    log(f"Already scraped: {len(scraped_set)}, Remaining: {len(remaining)}")

    conn = sqlite3.connect(DB)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {total_before:,}")

    grand_new = cp["stats"]["new"]
    grand_errors = cp["stats"]["errors"]
    valid_boards = cp["stats"]["valid_boards"]
    start = time.time()

    # Process in batches
    for batch_start in range(0, len(remaining), args.batch):
        batch = remaining[batch_start:batch_start + args.batch]
        log(f"\n--- Batch {batch_start//args.batch + 1}: {len(batch)} slugs, {args.threads} threads ---")

        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            futures = {executor.submit(probe_slug, slug): slug for slug in batch}

            for future in as_completed(futures):
                slug = futures[future]
                scraped_set.add(slug)
                try:
                    jobs = future.result()
                    if jobs:
                        valid_boards += 1
                        source = jobs[0].get("source", "unknown")
                        tag = f"discover,{slug}"
                        new = store_jobs(conn, jobs, tag)
                        grand_new += new
                        if new > 0:
                            log(f"  VALID: {slug:30s} -> {source:30s}: {len(jobs):4d} jobs, +{new:4d} new")
                    # else: slug not found on any platform (expected)
                except Exception as e:
                    grand_errors += 1

        # Checkpoint
        cp = {
            "discovered": list(scraped_set),
            "scraped": list(scraped_set),
            "stats": {"new": grand_new, "errors": grand_errors, "valid_boards": valid_boards},
        }
        save_checkpoint(cp)

        elapsed = time.time() - start
        current = total_before + grand_new
        rate = grand_new / (elapsed / 60) if elapsed > 0 else 0
        log(f"  Batch done: {current:,} total (+{grand_new:,} new) | {valid_boards} valid boards | {rate:.0f}/min | {elapsed/60:.1f} min")

    elapsed = time.time() - start
    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    log("")
    log("=" * 60)
    log(f"DISCOVERY + SCRAPE COMPLETE")
    log(f"Slugs probed:   {len(scraped_set)}")
    log(f"Valid boards:   {valid_boards}")
    log(f"New jobs:       {grand_new:,}")
    log(f"DB total:       {final:,}")
    log(f"Time:           {elapsed/60:.1f} min")
    log(f"Rate:           {grand_new/(elapsed/60):.0f} new/min")
    log(f"Gap to 1M:      {max(0, 1000000 - final):,}")
    log("=" * 60)


if __name__ == "__main__":
    main()
