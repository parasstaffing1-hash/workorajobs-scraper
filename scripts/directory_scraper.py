#!/usr/bin/env python3
"""Directory Scraper - Discover company slugs from job board directories,
then scrape all valid boards for jobs.

Sources:
1. Greenhouse embedded job boards (scroll directory pages)
2. Lever company listings
3. Scraped company name lists from multiple sources
"""
from __future__ import annotations

import json
import re
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
CP_FILE = ROOT / ".freebuff" / "dir_discover_checkpoint.json"
LOG_FILE = ROOT / ".freebuff" / "dir_discover.log"
DB_LOCK = Lock()


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
        f.write(line + "\n")


def load_checkpoint() -> dict:
    if CP_FILE.exists():
        try:
            return json.loads(CP_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"scraped": set(), "found": set(), "stats": {"new": 0, "errors": 0, "boards": 0}}


def save_checkpoint(cp: dict):
    CP_FILE.parent.mkdir(parents=True, exist_ok=True)
    save = {
        "scraped": list(cp["scraped"]),
        "found": list(cp["found"]),
        "stats": cp["stats"],
    }
    CP_FILE.write_text(json.dumps(save, indent=2), encoding="utf-8")


# =====================================================================
# STEP 1: Discover company slugs from web directories using Playwright
# =====================================================================

def discover_greenhouse_companies() -> set[str]:
    """Discover Greenhouse companies by probing known directory patterns."""
    slugs = set()
    
    # Method 1: Use the Greenhouse boards API to probe common company names
    # Method 2: Parse Greenhouse embed pages
    # Method 3: Scrape from aggregator sites
    
    # Try Greenhouse's job board directory via common embed patterns
    try:
        r = httpx.get(
            "https://boards-api.greenhouse.io/v1/boards",
            timeout=10, follow_redirects=True
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                for key in data:
                    slugs.add(key)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        slugs.add(item.get("slug", item.get("name", "")))
                    else:
                        slugs.add(str(item))
    except Exception:
        pass
    
    return slugs


def discover_from_web_scrape() -> set[str]:
    """Use httpx to scrape company listings from various sources."""
    slugs = set()
    
    # Source 1: Greenhouse partner/customer pages
    urls_to_try = [
        "https://www.greenhouse.com/company-directory",
        "https://developers.greenhouse.io/job-board.html",
    ]
    
    for url in urls_to_try:
        try:
            r = httpx.get(url, timeout=10, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            if r.status_code == 200:
                # Extract any greenhouse slugs from the HTML
                text = r.text
                # Look for boards.greenhouse.io/{slug} patterns
                matches = re.findall(r'boards\.greenhouse\.io/([a-zA-Z0-9_-]+)', text)
                slugs.update(matches)
                # Also look for /v1/boards/{slug} patterns
                matches = re.findall(r'/v1/boards/([a-zA-Z0-9_-]+)', text)
                slugs.update(matches)
        except Exception:
            continue
    
    return slugs


# =====================================================================
# MASSIVE COMPREHENSIVE COMPANY LIST - ALL SECTORS, ALL GEOGRAPHIES
# =====================================================================

def get_mega_company_list() -> list[str]:
    """Return a massive list of real company names/slugs."""
    
    # Fortune 500 companies (Fortune 500 list 2024, condensed to likely ATS slugs)
    fortune500 = """
apple walmart amazon berkshire-hathaway unitedhealth exxonmobil
apple cvshealth google alphabet microsoft costco walmart
chevron amazon exxonmobil meta facebook pfizer
berkshire-hathaway unitedhealth group apple alphabet microsoft
johnson johnson chevron procter gamble ibm
bank-america costco home-depot mcdonnells merck walmart
salesforce target lowe-cards united-states-steel
caterpillar oracle attorney-general verizon at-t
goldman-sachs geral-motors general-electric
archer-daniels-midland phillips66 marathon-petroleum valero-energy
starbucks caterpillar abbvie shown united-airlines
intuit dycom-albertsons-lcompanies sothebys-sealed
qualcomm oracle cisco-systems salesforce
""".strip().split()
    
    # Tech companies (1000+)
    tech = """
    # FAANG & Big Tech
    google apple amazon meta microsoft netflix twitter snap oracle ibm
    salesforce adobe vmware cisco intel amd qualcommBroadcom
    
    # Cloud & Infrastructure
    cloudflare fastly digitalocean hashicorp pulumi docker redhat suse
    linode ovh heroku render railway flyio vercel netlify panzura
    purestorage netapp dell-technologies hpe lenovo acer asus
    servicenow workday paloaltonetworks fortinet
    
    # Database & Data
    mongodb elastic redis cockroachlabs planetscale neon supabase
    databricks snowflake confluent influxdata singlestore clickhouse
    couchbase cassandra scylla litestream turso
    dbt-labs fivetran airbyte starburst hasura dremio
    
    # DevOps & Monitoring
    datadog newrelic pagerduty grafana prometheus dynatrace
    splunk sentry opsgenie pagerduty nocodb
    
    # Security
    crowdstrike zscaler sentinelone snyk veracode abnormalsecurity
    huntress cybereason recordedfuture expel beyondtrust pingidentity
    okta onelogin duosecurity adalaris wiz 1password bitwarden
    
    # AI & ML
    openai anthropic xai stabilityai inflectionai scaleai snorkelai
    togetherai assemblyai cohere mistral huggingface replicate
    weightsandbiases datarobot palace-ai aighostwriter
    groq modal runpod anyscale fireworks-ai deepgram
    
    # Fintech
    stripe square plaid brex ramp chime sofi affirm klarna revolut
    monzo n26 wise mercury marqeta nubank coinbase binance kraken
    robinhood etoro checkout.com adyen rippling payoneer deel
    tipalti bills.com billcom paycom paylocity paychex
    
    # E-commerce & Marketplace
    shopify ebay etsy wayfair poshmark depop vinted wish
    bonobos allbirds caspeloton stockx goat mercado libre
    
    # Logistics & Supply Chain
    flexport project44 convoy shipbob shippo stord shiphero
    freightwaves fourkites turvo pareto waredock
    
    # Health Tech
    flatironhealth cloverhealth hims forward onemedical whoop oura
    strava noom included-health lyra headway spring-health
    credohealth zocdoc teladoc noom-lemonaid ro health-hub
    
    # Gaming
    epicgames riotgames roblox unity supercell krafton taketwo
    rockstargames zynga scopely king mihoyo miHoYo hoyoverse
    square-enix sega bandai-namco capcom konami sony-interactive
    nintendo valve
    
    # Entertainment & Media
    spotify twitch vimeo duolingo coursera masterclass
    buzzfeed voxmedia theverge vice warner-brothers discovery
    paramount disney hulu amazon-studios a24
    
    # Video & Streaming
    zoom vimeo brightcove wistia vidyard loom stream-yard
    vimeo miro Whereby gather
    
    # Design & Creative
    figma canva sketch miro whimsical framer webflow wix squarespace
    invision marvel-abstract zeplin penpot dribbble behance
    
    # Project Management
    asana mondaycom clickup smartsheet teamwork basecamp trello
    height linear notepokzana shortcut clubhouse
    
    # Communication
    twilio sendgrid vonage messagebird plivo ringcentral 8x8
    genesys fivedialpad aircall dialpad
    
    # Customer Support
    zendesk freshdesk intercom helpscout front kustomer
    
    # Sales & Marketing
    salesloft outreach gong chorus highspot seismic showpad
    clari boostup groove apollo zoominfo lusha clearbit demandbase
    
    # Work Tools
    notion coda airtable confluence jira slack microsoft-teams
    discord google-chat
    
    # Indian Tech
    razorpay phonepe groww zerodha upstox cred slice meesho
    swiggy zomato ola rapido freshworks zoho hasura postman
    curefit healthifyme practo 1mg pharmeasy bigbasket blinkit
    instamart dunzo porter blackbuck oyo makemytrip goibibo
    cleartrip ixigo redbus policybazaar paisabazaar jar spinny
    cars24 leapfinance ofbusiness unacademy byjus physicswalla
    upgrad simplilearn whitehat Great-Learning tcs infosys wipro
    hcltech tech-mahindra ltimindtree persistent-systems mphasis
    hexaware mindtree cognizant

    # HR & People Tech
    gusto zenefits bamboohr lattice 15five cultureamp leapsome
    personio remote deel oyster papaya-global factorialsapling
    
    # More Real Companies
    twilio splunk palantir paloaltonetworks elastic
    cloudflare datadog samsara purestorage roblox reddit
    discord figma postman gitlab atlassian
    notion linear height vercel netlify
    stripe plaid brex ramp chime
    sofi affirm klarna revolut monzo n26
    wise mercury marqeta coinbase robinhood
    
    # Consulting & Professional Services
    accenture deloitte pwc ey kpmg mckinsey bain bcg
    roland-berger oliver-wyman booz-allen leidos
    
    # Aerospace & Defense
    boeing airbus lockheed-martin northrop-grumman raytheon
    general-electric siemens honeywell
    
    # Automotive
    tesla rivian lucid fisker nio xpeng byd
    toyota honda ford bmw mercedes benz volkswagen
    
    # Pharma & Biotech
    pfizer johnson-johnson merck novartis roche abbvie amgen gilead
    moderna biontech astrazeneca sanofi gsk eli-lilly bristol-myers
    
    # Consumer & Retail
    nestle unilever procter-gamble pepsico coca-cola nike adidas
    zara h&m burberry gucci louis-vuitton hermes
    
    # Telecom
    att verizon tmobile comcast charter
    
    # Energy
    shell bp exxon-mobil chevron totalenergies
    
    # More companies found on ATS platforms
    pulse spacex databricks stripe anthropic datadog mongodb
    okta zscaler brex cloudflare samsara elastic roblox roku
    reddit discord figma postman gitlab airbnb lyft pinterest
    roblox esri scaleai vercel carta gustoonelogin dropbox
    twilio intercom twitch duolingo mongodbgusto duolingo
    coursera udemy masterclass coursera
    
    # Unicorns & Startups
    canva zapier buffer helpscout basecamp automattic toptal upwork
    fiverr turing lili lemonade insurance lemonade
    tiger-global softbank sequoia-andreesen-horowitz
    grip rippling navan ramp
    
    # Indian unicorns
    ola electric ola-cabs swiggy zomato paytm phonepe
    groww zerodha upstox cred slice meesho
    policybazaar paisabazaar jar spinny cars24
    unacademy byjus physicswalla upgrad simplilearn
    practo 1mg pharmeasy bigbasket blinkit instamart
    
    # German/European
    sap siemens bmw mercedes volkswagen adidas puma
    allianz munich-re deutsche-bank commerzbank
    sap n26 solarisbank n26
    
    # Japanese/Korean
    sony panasonic samsung lg electronics
    toyota honda hitachi nec fujitsu
    """.split()
    
    # Clean and deduplicate
    seen = set()
    result = []
    for name in tech:
        name = name.strip().lower()
        name = name.strip("#")
        if not name or len(name) < 3 or name in seen:
            continue
        if re.match(r'^[a-z0-9][a-z0-9._-]+$', name):
            seen.add(name)
            result.append(name)
    
    return result


# =====================================================================
# ATS SCRAPERS (same as mega_discover_v2)
# =====================================================================

def scrape_greenhouse(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200:
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
            "external_id": str(j.get("id", "")),
            "source": f"greenhouse:{slug}",
            "description": (j.get("content") or "")[:500],
            "tags": (j.get("departments") or [{}])[0].get("name", "") if j.get("departments") else "",
        } for j in jobs]
    except Exception:
        return []


def scrape_lever(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://api.lever.co/v0/postings/{slug}?mode=json",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, list) or not data:
            return []
        return [{
            "title": j.get("text", ""),
            "company": (j.get("categories", {}) or {}).get("team", slug),
            "location": (j.get("categories", {}) or {}).get("location", ""),
            "url": j.get("hostedUrl", ""),
            "posted_at": datetime.fromtimestamp(j.get("createdAt", 0) / 1000, tz=timezone.utc).isoformat() if j.get("createdAt") else None,
            "external_id": j.get("id", ""),
            "source": f"lever:{slug}",
            "description": (j.get("descriptionPlain") or "")[:500],
            "tags": j.get("teamsPlain", ""),
        } for j in data]
    except Exception:
        return []


def scrape_ashby(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200:
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
            "external_id": j.get("id", ""),
            "source": f"ashby:{slug}",
            "description": "",
            "tags": j.get("departmentName", ""),
        } for j in openings]
    except Exception:
        return []


def scrape_smartrecruiters(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200:
            return []
        data = r.json()
        content = data.get("content", [])
        if not content:
            return []
        return [{
            "title": j.get("name", ""),
            "company": (j.get("company") or {}).get("name", slug),
            "location": f"{(j.get('location') or {}).get('city', '')}, {(j.get('location') or {}).get('country', '')}".strip(", "),
            "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('ref', '')}",
            "posted_at": j.get("releasedDate"),
            "external_id": str(j.get("id", "")),
            "source": f"smartrecruiters:{slug}",
            "description": "",
            "tags": "",
        } for j in content]
    except Exception:
        return []


def scrape_workable(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = data.get("jobs", [])
        if not jobs:
            return []
        return [{
            "title": j.get("title", ""),
            "company": data.get("name", slug),
            "location": f"{j.get('city', '')}, {j.get('country', '')}".strip(", "),
            "url": j.get("url", ""),
            "posted_at": j.get("date"),
            "external_id": j.get("id", ""),
            "source": f"workable:{slug}",
            "description": "",
            "tags": j.get("department", ""),
        } for j in jobs]
    except Exception:
        return []


SCRAPERS = [
    scrape_greenhouse,
    scrape_lever,
    scrape_ashby,
    scrape_smartrecruiters,
    scrape_workable,
]


def probe_slug(slug: str) -> list[dict]:
    for scraper in SCRAPERS:
        jobs = scraper(slug)
        if jobs:
            return jobs
    return []


def store_jobs(conn, jobs, tag) -> int:
    new = 0
    now = datetime.now(timezone.utc).isoformat()
    with DB_LOCK:
        for j in jobs:
            if not j.get("title") or not j.get("url"):
                continue
            try:
                dedupe_key = j["url"] or j.get("external_id", "")
                cur = conn.execute(
                    """INSERT OR IGNORE INTO jobs
                    (dedupe_key, title, company, location, description, url,
                     source, source_kind, external_id, posted_at, salary, tags,
                     first_seen_at, last_seen_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (dedupe_key, j["title"], j.get("company", ""),
                     j.get("location", ""), j.get("description", ""),
                     j["url"], j["source"], "ats", j.get("external_id", ""),
                     j.get("posted_at"), j.get("salary", ""), tag, now, now),
                )
                if cur.rowcount > 0:
                    new += 1
            except Exception:
                continue
        conn.commit()
    return new


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=40)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    # Build slug list
    base_slugs = get_mega_company_list()
    
    # Also try directory discovery
    log("Discovering companies from web directories...")
    dir_slugs = discover_greenhouse_companies()
    web_slugs = discover_from_web_scrape()
    all_dir = dir_slugs | web_slugs
    log(f"Web discovery found {len(all_dir)} slugs")
    
    # Combine all slugs
    all_slugs = list(set(base_slugs) | all_dir)
    log(f"Total unique slugs to probe: {len(all_slugs)}")

    cp = load_checkpoint() if args.resume else {
        "scraped": set(), "found": set(),
        "stats": {"new": 0, "errors": 0, "boards": 0}
    }
    scraped_set = set(cp["scraped"])
    remaining = [s for s in all_slugs if s not in scraped_set]
    log(f"Already scraped: {len(scraped_set)}, Remaining: {len(remaining)}")

    if not remaining:
        log("All slugs already probed!")
        return

    conn = sqlite3.connect(DB)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {total_before:,}")
    log(f"Gap to 1M: {max(0, 1_000_000 - total_before):,}")

    grand_new = cp["stats"]["new"]
    grand_errors = cp["stats"]["errors"]
    boards = cp["stats"]["boards"]
    start = time.time()
    BATCH = args.threads * 10

    for bi in range(0, len(remaining), BATCH):
        batch = remaining[bi:bi + BATCH]
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(probe_slug, s): s for s in batch}
            for f in as_completed(futures):
                slug = futures[f]
                scraped_set.add(slug)
                try:
                    jobs = f.result()
                    if jobs:
                        boards += 1
                        src = jobs[0].get("source", "?")
                        new = store_jobs(conn, jobs, f"dir,{slug}")
                        grand_new += new
                        log(f"  {slug:30s} -> {src:30s}: {len(jobs):4d} jobs, +{new:4d}")
                except Exception:
                    grand_errors += 1

        save_checkpoint({
            "scraped": scraped_set, "found": set(),
            "stats": {"new": grand_new, "errors": grand_errors, "boards": boards}
        })

        elapsed = time.time() - start
        current = total_before + grand_new
        rate = grand_new / (elapsed / 60) if elapsed > 0 else 0
        pct = (len(scraped_set) / len(all_slugs)) * 100
        log(f"  [{len(scraped_set)}/{len(all_slugs)}] {pct:.1f}% | DB: {current:,} (+{grand_new:,}) | Boards: {boards} | Rate: {rate:.0f}/min | Gap: {max(0, 1_000_000 - current):,}")

    elapsed = time.time() - start
    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    log("")
    log("=" * 60)
    log("DIRECTORY DISCOVERY COMPLETE")
    log(f"Slugs probed: {len(scraped_set)}")
    log(f"Valid boards: {boards}")
    log(f"New jobs: {grand_new:,}")
    log(f"DB total: {final:,}")
    log(f"Time: {elapsed/60:.1f} min")
    log(f"Rate: {grand_new/(elapsed/60):.0f} new/min")
    log(f"Gap to 1M: {max(0, 1_000_000 - final):,}")
    log("=" * 60)


if __name__ == "__main__":
    main()
