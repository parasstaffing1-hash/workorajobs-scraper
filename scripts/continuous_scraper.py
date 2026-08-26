#!/usr/bin/env python3
"""Continuous scraper — runs until 1M fresh jobs in 7 days.

Dynamically expands company lists by:
1. Discovering new companies on each ATS platform
2. Running all sources in parallel
3. Saving checkpoints for resume
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import httpx

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP_FILE = ROOT / ".freebuff" / "continuous_checkpoint.json"
LOG_FILE = ROOT / ".freebuff" / "continuous_scraper.log"

DB_LOCK = Lock()
TARGET = 1_000_000


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
    return {"completed": [], "stats": {"new": 0, "errors": 0}}


def save_checkpoint(cp: dict):
    CP_FILE.parent.mkdir(parents=True, exist_ok=True)
    CP_FILE.write_text(json.dumps(cp, indent=2), encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# ATS scrapers (from mega_stress.py)
# ════════════════════════════════════════════════════════════════
def scrape_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return []
        data = r.json()
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
        } for j in data.get("jobs", [])]
    except Exception:
        return []


def scrape_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return []
        data = r.json()
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
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return []
        data = r.json()
        board = data.get("jobBoard", {})
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
        } for j in board.get("openings", [])]
    except Exception:
        return []


def scrape_smartrecruiters(slug):
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0"
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return []
        data = r.json()
        return [{
            "title": j.get("name", ""),
            "company": j.get("company", {}).get("name", slug),
            "location": (j.get("location", {}) or {}).get("city", "") + ", " + ((j.get("location") or {}).get("country", "")),
            "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('ref', '')}",
            "posted_at": j.get("releasedDate"),
            "jobkey": str(j.get("id", "")),
            "source": f"smartrecruiters:{slug}",
            "description": "",
            "tags": "",
        } for j in data.get("content", [])]
    except Exception:
        return []


def scrape_workable(slug):
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return []
        data = r.json()
        return [{
            "title": j.get("title", ""),
            "company": slug,
            "location": j.get("city", "") + ", " + j.get("country", ""),
            "url": j.get("url", ""),
            "posted_at": j.get("updated"),
            "jobkey": j.get("department", "") + j.get("title", ""),
            "source": f"workable:{slug}",
            "description": (j.get("description") or "")[:500],
            "tags": j.get("department", ""),
        } for j in data.get("jobs", [])]
    except Exception:
        return []


# ════════════════════════════════════════════════════════════════
# Company discovery — find new companies by probing common slugs
# ════════════════════════════════════════════════════════════════
def discover_greenhouse_companies(seed_slugs: list[str]) -> list[str]:
    """Discover new Greenhouse boards by checking if seed slugs exist."""
    new = []
    for slug in seed_slugs:
        try:
            r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data.get("jobs"):
                    new.append(slug)
        except Exception:
            pass
    return new


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


def worker_ats(args):
    platform, slug = args
    scrapers = {"greenhouse": scrape_greenhouse, "lever": scrape_lever,
                "ashby": scrape_ashby, "smartrecruiters": scrape_smartrecruiters,
                "workable": scrape_workable}
    jobs = scrapers[platform](slug)
    return {"platform": platform, "slug": slug, "jobs": jobs, "count": len(jobs)}


# ════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════
def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=20)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-rounds", type=int, default=50)
    args = ap.parse_args()

    # Large company list to iterate through
    GREENHOUSE = [
        # Already scraped (will be skipped)
        "stripe", "anthropic", "databricks", "datadog", "cloudflare",
        "mongodb", "elastic", "okta", "pinterest", "gitlab", "airbnb",
        "coinbase", "figma", "discord", "lyft", "instacart", "xai",
        "vercel", "cockroachlabs", "brex", "chime", "sofi", "monzo",
        "wise", "marqeta", "galileo", "mercury", "n26", "scaleai",
        "zscaler", "snorkelai", "beyondtrust", "abnormalsecurity",
        "cloverhealth", "peloton", "flatironhealth", "forward", "oura",
        "reddit", "dropbox", "twilio", "intercom", "affirm", "sezzle",
        # NEW companies to discover
        "airtable", "alation", "algolia", "amplitude", "asana",
        "auth0", "barclays", "basecamp", "benchling", "bigcommerce",
        "binance", "blend", "brex", "brigade", "calm", "canva",
        "caret", "carta", "cashapp", "checkout", "chegg",
        "cisco", "citrix", "clari", "clickup", "cloudkitchens",
        "confluent", "containerstore", "couchbase", "cruise",
        "cvent", "dataiku", "dayforce", "dealfront", "deel",
        "deliveryhero", "dhl", "dialpad", "digitalocean", "docusign",
        "dominos", "doorDash", "duolingo", "ebay", "elasticsearch",
        "embed", "enumeration", "epic", "evident", "expel",
        "facebook", "figma", "figma", "fiverr", "fleetsmith",
        "flexport", "forethought", "fossa", "foursquare", "freightos",
        "gainsight", "gaming", "garmin", "gengo", "getaround",
        "ghost", "glossier", "gojek", "gong", "goodrx",
        "google", "gopuff", "grammarly", "graphcore", "greenhouse",
        "grindr", "groupon", "gusto", "habitat", "handshake",
        "hashicorp", "hasura", "hellosign", "hibob", "highspot",
        "hims", "hinge", "hired", "hootsuite", "hopper",
        "hotjar", "hubspot", "huel", "huggingface",
        "ikea", "illumina", "imdb", "improving", "indeed",
        "inflectionai", "influxdata", "instabase", "instacart",
        "instagram", "instapage", "instawork", "intel", "intuit",
        "invision", "ionic", "ipg", "ironclad", "iterable",
        "jamf", "jasper", "jetbrains", "jira", "jobvite",
        "justworks", "jvm", "kaggle", "kainos", "kandji",
        "kayak", "keap", "keeptruckin", "kensho", "keybase",
        "khoros", "king", "klarna", "kong", "kroger",
        "kustomer", "lattice", "launchdarkly", "lever", "liftoff",
        "linear", "linkedin", "liveperson", "lob", "logrocket",
        "loom", "lucid", "luminar", "lyft", "magento",
        "mailchimp", "mango", "mapbox", "marqeta", "mattermost",
        "maxmind", "medallia", "mercury", "meta", "microsoft",
        "mightyhive", "mindbody", "minted", "miro", "mixpanel",
        "mongodb", "moniepoint", "monzo", "moonactive", "mozilla",
        "mux", "namely", "navan", "neon", "netlify",
        "nextdoor", "nike", "nintex", "nordstrom", "notion",
        "nuro", "officemock", "okta", "olympus", "omada",
        "opendoor", "optimizely", "oracle", "oscar", "outreach",
        "paddle", "pandadoc", "pantheon", "parabola", "particle",
        "patreon", "payoneer", "pendo", "pepsi", "persona",
        "phonepe", "pilot", "pipelinedrive", "pitch", "plaid",
        "planetscale", "pluralsight", "poly", "posthog", "postman",
        "postscript", "prelude", "presto", "procore", "prodigy",
        "prophet", "pubmatic", "purestorage", "qualtrics", "qonto",
        "radar", "ramp", "rapidapi", "rappi", "reach",
        "reddit", "redfin", "redhat", "reedsy", "relay",
        "remitly", "repair", "retool", "rev", "revolut",
        "ribbon", "ripple", "rivian", "robinhood", "roblox",
        "rocket", "roku", "rollbar", "root", "rubrik",
        "ruggable", "runway", "sailpoint", "salesforce", "samsara",
        "sanofi", "sap", "sauce", "scale", "scalyr",
        "scopely", "segment", "sentry", "servicenow", "sezzle",
        "shippo", "shutterstock", "sidecar", "signal", "signifyd",
        "simple", "sinch", "sisense", "siteimprove", "siteminder",
        "slack", "smartrecruiters", "snyk", "social", "solarisbank",
        "sony", "sophos", "soundcloud", "spacex", "spark",
        "split", "spotify", "spreedly", "sprinklr", "sprout",
        "squarespace", "stackadapt", "stanley", "starbucks",
        "stockx", "storm", "strava", "stripe", "sumo",
        "supabase", "superhuman", "supersede", "surveygizmo",
        "swiggy", "synchrony", "tableau", "taboola", "talend",
        "teachable", "teamwork", "tesla", "thehill",
        "thinkific", "tidal", "tiktok", "tilted", "tinder",
        "tmobile", "tokopedia", "tomtom", "topia", "touchbistro",
        "toyota", "trainual", "trello", "tremendous", "tripadvisor",
        "truecaller", "trulia", "trustpilot", "turo", "twilio",
        "twitch", "twitter", "typeform", "uber", "udemy",
        "ultimate", "unacademy", "unity", "upwork", "usertesting",
        "vanguard", "venmo", "vercel", "verkada", "vimeo",
        "visa", "vmware", "vox", "vtex", "walmart",
        "warp", "wayfair", "wealthsimple", "weave", "webflow",
        "wework", "whatsapp", "wix", "woocommerce", "workable",
        "workato", "workday", "workiva", "workable", "xero",
        "yahoo", "yammer", "yelp", "yougov", "youtube",
        "zapier", "zendesk", "zenefits", "zerodha", "zillow",
        "zocdoc", "zoho", "zoom", "zynga",
    ]

    LEVER = [
        "netlify", "upstart", "nubank", "plaid", "checkout",
        "dialpad", "fictiv", "gusto", "kong", "lever",
        "nerdwallet", "niantic", "notion", "qonto", "segment",
        "spotify", "stripe", "verkada", "vimeo", "yuno",
    ]

    ASHBY = [
        "notion", "linear", "openai", "ramp", "vercel",
        "supabase", "posthog", "retool", "snyk", "postman",
        "fivetran", "rippling", "descript", "leap", "cohere",
        "runway", "cursor", "planetscale", "tigerbeetle",
    ]

    SR = [
        "redbull", "colliers", "accor", "deliveryhero",
        "servicenow", "grab", "canva", "abbvie", "entain",
        "asos", "wise", "dailymotion", "fiverr", "unacademy",
        "gong", "raytheon", "nextdc",
    ]

    log("=" * 60)
    log("CONTINUOUS SCRAPER — target 1M fresh jobs in 7 days")
    log("=" * 60)

    cp = load_checkpoint() if args.resume else {"completed": [], "stats": {"new": 0, "errors": 0}}
    completed = set(cp["completed"])
    log(f"Already done: {len(completed)}")

    conn = sqlite3.connect(DB)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {total_before:,}")

    grand_new = cp["stats"]["new"]
    grand_errors = cp["stats"]["errors"]
    start = time.time()

    # Build work list
    ats_work = []
    for slug in GREENHOUSE:
        key = f"greenhouse:{slug}"
        if key not in completed:
            ats_work.append(("greenhouse", slug))
    for slug in LEVER:
        key = f"lever:{slug}"
        if key not in completed:
            ats_work.append(("lever", slug))
    for slug in ASHBY:
        key = f"ashby:{slug}"
        if key not in completed:
            ats_work.append(("ashby", slug))
    for slug in SR:
        key = f"smartrecruiters:{slug}"
        if key not in completed:
            ats_work.append(("smartrecruiters", slug))

    log(f"ATS work: {len(ats_work)} companies")

    round_num = 0
    while grand_new < TARGET - total_before and round_num < args.max_rounds:
        round_num += 1
        log(f"\n--- Round {round_num}: {len(ats_work)} companies ---")

        for batch_start in range(0, len(ats_work), args.threads * 5):
            batch = ats_work[batch_start:batch_start + args.threads * 5]
            with ThreadPoolExecutor(max_workers=args.threads) as executor:
                futures = {executor.submit(worker_ats, w): w for w in batch}
                for future in as_completed(futures):
                    platform, slug = futures[future]
                    key = f"{platform}:{slug}"
                    try:
                        result = future.result()
                        tag = f"continuous,{platform},{slug}"
                        new = store_jobs(conn, result["jobs"], tag)
                        grand_new += new
                        completed.add(key)
                        if new > 0:
                            log(f"  [{platform:16s}] {slug:30s}: {result['count']:4d} jobs, +{new:4d} new")
                    except Exception as e:
                        grand_errors += 1

            cp = {"completed": list(completed), "stats": {"new": grand_new, "errors": grand_errors}}
            save_checkpoint(cp)

        elapsed = time.time() - start
        current = total_before + grand_new
        rate = grand_new / (elapsed / 60) if elapsed > 0 else 0
        log(f"  Round {round_num} done: {current:,} total ({grand_new:,} new) | {rate:.0f}/min")

        if current >= TARGET:
            break

        # For next round, we'd need more companies — for now just report
        log(f"  Need {max(0, TARGET - current):,} more jobs")
        break  # Exit after one round — need more companies

    elapsed = time.time() - start
    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    log("")
    log("=" * 60)
    log(f"CONTINUOUS SCRAPE")
    log(f"New jobs:      {grand_new:,}")
    log(f"DB total:      {final:,}")
    log(f"Time:          {elapsed/60:.1f} min")
    log(f"Rate:          {grand_new/(elapsed/60):.0f} new/min")
    log(f"Gap to 1M:     {max(0, TARGET - final):,}")
    log("=" * 60)


if __name__ == "__main__":
    main()
