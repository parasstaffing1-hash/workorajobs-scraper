#!/usr/bin/env python3
"""MEGA HUNT — the ultimate job scraper.

Combines:
1. ATS slug probing (15000+ companies across 7 platforms) — 80 threads
2. JobSpy keyword×location searches with pagination — 10 threads
3. All output stored directly to SQLite with dedup.

Each ATS company gives 50-400 unique jobs with ZERO dedup between companies.
Each JobSpy search gives 200-500 unique jobs with ~95% dedup from other searches.

Target: 1M fresh jobs in 7 days.
"""
from __future__ import annotations

import json
import os
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
LOG = ROOT / ".freebuff" / "mega_hunt.log"
CP = ROOT / ".freebuff" / "mega_hunt_cp.json"
DB_LOCK = Lock()

_client = httpx.Client(
    timeout=4.0,
    follow_redirects=True,
    limits=httpx.Limits(max_connections=120, max_keepalive_connections=50),
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8", errors="replace") as f:
        f.write(line + "\n")


def load_cp():
    if CP.exists():
        try:
            return json.loads(CP.read_text("utf-8"))
        except Exception:
            pass
    return {"ats_done": [], "jobspy_done": [], "stats": {"new": 0, "boards": 0, "searches": 0}}


def save_cp(cp):
    CP.parent.mkdir(parents=True, exist_ok=True)
    CP.write_text(json.dumps({
        "ats_done": list(cp["ats_done"])[-20000:],
        "jobspy_done": list(cp["jobspy_done"]),
        "stats": cp["stats"],
    }), "utf-8")


# =====================================================================
# EXPANDED COMPANY SLUGS (15000+)
# =====================================================================

def build_ats_slugs() -> list[str]:
    """Build massive slug list from mega_probe.py + additional companies."""
    slugs = set()

    # Load from mega_probe.py
    probe_file = ROOT / "scripts" / "mega_probe.py"
    if probe_file.exists():
        raw = probe_file.read_text("utf-8")
        m = re.search(r'COMPANIES = """(.*?)"""', raw, re.DOTALL)
        if m:
            for line in m.group(1).split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                for w in line.replace(",", " ").split():
                    w = w.strip().lower()
                    if not w or len(w) < 3 or len(w) > 40:
                        continue
                    if not re.match(r'^[a-z0-9][a-z0-9._-]+$', w):
                        continue
                    slugs.add(w)
                    slugs.add(w.replace("-", ""))
                    slugs.add(w.replace("_", ""))
                    slugs.add(w.replace(".", ""))

    # Additional Fortune 1000 / Forbes Global 2000 / Indian / European / Asian
    extra = """
    # Fortune 500 condensed
    walmart amazon apple berkshire unitedhealth exxon chevron
    costco citigroup verizon comcast qualcomm ibm ford gm
    boeing lockheed northrop raytheon honeywell siemens ge
    pfizer merck abbvie johnson-johnson eli-lilly bristol-myers
    procter-gamble pepsi-co coca-cola walt-disney nike mcdonalds
    home-depot lowe's target costco starbucks chipotle
    jpmorgan-chase goldman-sachs morgan-stanley bank-of-america
    wells-fargo citigroup us-bancorp pncfinancial truist
    visa mastercard american-express fidelity vanguard
    berkshire-hathaway leidos cognizant accenture deloitte
    pwc ey kpmg mckinsey bain bcg oliver-wyman booz-allen

    # FAANG + MAMAA
    meta platforms alphabet waymo deepmind youtube
    netflix disney paramount warner-bros discovery
    nvidia amd intel broadcom arm
    salesforce adobe oracle sap service-now
    snowflake palantir datadog cloudflare mongodb
    elasticsearch redis dynamodb couchdb cassandra

    # Indian IT Services
    infosys wipro hcltech tech-mahindra ltimindtree
    persistent-systems mphasis hexaware mindtree mphasis
    biocon dr-reddys sun-pharma cipla lupin
    tata-steel tata-motors tata-chemicals tata-power
    bajaj-auto hero-motocorp maruti-suzuki eicher-motors
    asian-paints titan-company hindustan-unilever
    relianc industries adani-enterprises adani-ports
    bharti-airtel bsnl jio

    # Indian Startups
    flipkart phonepe razorpay groww zerodha upstox cred
    meesho swiggy zomato ola rapido dunzo porter
    blackbuck oyo makemytrip goibibo cleartrip ixigo redbus
    policybazaar paisabazaar jar spinny cars24 leapfinance
    unacademy byjus physicswalla upgrad simplilearn
    urbancompany khatabook mygate nobroker housing
    cashfree pine-labs lendingkart indifi mswipe payu
    freshworks zoho hasura postman

    # European Tech
    sap siemens bmw mercedes volkswagen adidas puma
    allianz munich-re deutsche-bank commerzbank
    shell bp totalenergies equinor
    asos deliveryhero hellofresh getyourguide
    revolut monzo n26 klarna wise mambu solarisbank
    trigo wolt spotify trustpilot
    personio lempire aircall contentful datawrapper pitch
    northvolt volvo-cars polestar ferrero nestle-unilever

    # UK Tech
    revolut monzo starling transferwise go-cardless tide
    checkout dotdigital elvie faculty paddle patchwork
    genome iwoca tidal deezer bloomtech

    # Asian Tech
    samsung lg-electronics sony panasonic nec fujitsu
    hitachi toshiba sharp toyota honda nissan mazda
    bytedance tiktok alibaba tencent baidu
    jd.com pinduoduo meituan didi sea-group grab gojek
    traveloka shopee lazada tokopedia bukalapak
    flipkart paytm mobikwik rappi dlocal kavak
    mercadolibre 99app creditas loft quint-andar

    # African Tech
    flutterwave andela m-kopa twiga sendy chippercash jumia
    konga paystack moove lux wasoko carbon fairmoney
    kuda-bank teamapt moniepoint 360data wakanow

    # LATAM Tech
    mercadolibre rappi 99app kavak konfio clip stori bitso
    creditas loft quint-andar nu-bank earo dlocal despegar

    # Japanese Tech
    sony panasonic fujitsu nec hitachi toshiba sharp
    softbank rakuten mercari line yahoo-japan gmo
    preferred-networks abeja pacmed

    # Korean Tech
    samsung lg naver kakao line toss bunjang
    coupang marketkurly baemin

    # Australian Tech
    atlassian canva culture-amp safetyculture linktree
    reportlab deputy employment-hero hermovoice
    blacklane culture-amp ductus

    # Canadian Tech
    shopify hootsuite kik wattpad halian
    desjardins sun-life manulife rbc td-bank
    bmo scotia-cibc nationalbank

    # Middle East Tech
    careem noon talabat stc etisalat du
    omantel ooredoo zain

    # More US Unicorns
    stripe square plaid brex ramp chime sofi affirm
    robinhood coinbase wise mercury marqeta checkout
    adyen rippling payoneer tipalti flexport project44
    convoy shipbob shippo stord freightwaves
    flatironhealth cloverhealth hims forwardecord
    whoop oura strava noom lyra headway spring-health
    epicgames riotgames roblox unity supercell krafton
    taketwo rockstargames zynga scopely king
    spotify twitch vimeo duolingo coursera masterclass
    buzzfeed voxmedia figma canva sketch miro whimsical
    framer webflow wix squarespace asana monday-clickup
    smartsheet teamwork basecamp trello jira linear height
    notion coda airtable twilio sendgrid vonage plivo
    genesys five9 dialpad aircall kustomer zendesk freshdesk
    intercom helpscout front salesloft outreach gong
    chorus highspot seismic showpad clari zoominfo lusha
    clearbit demandbase 6sense drift qualified loom vidyard
    brightcove wistia tesla rivian lucid fisker nio xpeng

    # More AI Companies
    openai anthropic xai scaleai togetherai assemblyai
    mistral cohere stabilityai inflectionai snorkelai
    huggingface replicate modal weights-biases wandb
    runway eleven-labs descript notion-ai jasper

    # More SaaS
    airtable baze clickup freshbooks quickbooks xero
    hubspot marketo pardot dribbble

    # Gaming
    epicgames riotgames roblox unity supercell krafton
    take-two rockstargames zynga scopely king mi-hoyo
    supercell ea-games activision blizzard ubisoft

    # Telecom
    verizon tmobile at&t sprint vodafone orange
    bt-group deutsche-telekom Telefonica america-movil

    # Retail / Ecommerce
    walmart amazon shopify alibaba jd.com pinduoduo
    meituan shopee lazada flipkart snapdeal
    wayfair etsy poshmark depop vestiaire

    # Automotive
    tesla rivian lucid motors waymo cruise argo-ai
    toyota honda ford gm bmw mercedes volkswagen
    stellantis byd nio xpeng li-auto polestar

    # Aerospace / Defense
    boeing airbus lockheed-martin northrop-grumman
    raytheon general-dynamics l3-harris leidos

    # Pharma / Biotech
    pfizer johnson-johnson merck novartis roche abbvie
    amgen gilead moderna biontech astrazeneca sanofi gsk

    # Energy
    shell bp totalenergies exxon-mobil chevron equinor

    # Additional Tech
    pure-storage netapp splunk dynatrace newrelic
    pagerduty grafana datadog elastic redis confluent
    hashicorp pulumi terraform docker redhat suse
    digitalocean heroku vercel netlify fastly

    # More ATS companies (common slugs on Greenhouse)
    antlr gradle jetbrains maven jenkins circleci
    gitlab github bitbucket atlassian zendesk

    # More Indian Companies
    ibibolo bigbasket blinkit instamart cult.fit
    healthifyme practo 1mg pharmeasy netmeds medlife
    mylab pathkind vianai yellow-ai gupshup
    lead-squared verloop chatbot-in wisely

    # More European
    personio Remote-OK TeamTailorPersonio
    n26 solaris-bank finleap trade-republic
    wertego solarisbank

    # More UK
    go-cardless starling monzo revolut tide
    checkout.com genome iwoca cobalt

    # More LATAM
    nubank dlocal kavak rappi mercadolibre
    creditas loft quint-andar

    # More African
    flutterwave andela m-kopa twiga paystack
    chippercash jumia konga carbon kuda
    """

    for line in extra.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for w in line.replace(",", " ").split():
            w = w.strip().lower()
            if not w or len(w) < 3 or len(w) > 40:
                continue
            if not re.match(r'^[a-z0-9][a-z0-9._-]+$', w):
                continue
            slugs.add(w)
            slugs.add(w.replace("-", ""))
            slugs.add(w.replace("_", ""))
            slugs.add(w.replace(".", ""))

    # Filter out common words
    bad = {
        "the", "and", "for", "inc", "com", "all", "new", "our", "app", "big", "top", "pro", "out", "one",
        "get", "add", "its", "can", "has", "had", "was", "are", "not", "also", "into", "with", "from",
        "this", "that", "than", "your", "you", "now", "nexus", "pilot", "meta", "delta", "alpha",
        "apex", "core", "edge", "flux", "hub", "ion", "jet", "kin", "link", "map", "net", "oak",
        "opt", "pin", "ray", "set", "tap", "ux", "vim", "zen", "zeta", "ace", "arc", "bio", "box",
        "cap", "day", "ego", "fly", "fox", "gem", "hex", "ide", "jam", "key", "lab", "max", "mix",
        "neo", "nut", "peg", "py", "raw", "sky", "sun", "van", "via", "wax", "win", "zoo",
    }
    return sorted([s for s in slugs if len(s) >= 3 and s not in bad])


# =====================================================================
# ATS SCRAPERS
# =====================================================================

def try_greenhouse(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        if r.status_code != 200:
            return None
        data = r.json()
        jobs = data.get("jobs", [])
        if not jobs:
            return None
        co = data.get("name", slug)
        return [{
            "title": j.get("title", ""),
            "company": co,
            "location": (j.get("location", {}) or {}).get("name", "") if isinstance(j.get("location"), dict) else str(j.get("location", "")),
            "url": j.get("absolute_url", ""),
            "posted_at": j.get("updated_at") or j.get("created_at"),
            "external_id": str(j.get("id", "")),
            "source": f"greenhouse:{slug}",
            "description": (j.get("content") or "")[:500],
            "tags": (j.get("departments") or [{}])[0].get("name", "") if j.get("departments") else "",
        } for j in jobs]
    except Exception:
        return None


def try_lever(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list) or not data:
            return None
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
        return None


def try_ashby(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if r.status_code != 200:
            return None
        data = r.json()
        board = data.get("jobBoard", {})
        openings = board.get("openings", [])
        if not openings:
            return None
        co = board.get("name", slug)
        return [{
            "title": j.get("title", ""),
            "company": co,
            "location": j.get("locationName", ""),
            "url": j.get("url", ""),
            "posted_at": j.get("publishedAt"),
            "external_id": j.get("id", ""),
            "source": f"ashby:{slug}",
            "description": "",
            "tags": j.get("departmentName", ""),
        } for j in openings]
    except Exception:
        return None


def try_smartrecruiters(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0")
        if r.status_code != 200:
            return None
        data = r.json()
        content = data.get("content", [])
        if not content:
            return None
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
        return None


def try_workable(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
        if r.status_code != 200:
            return None
        data = r.json()
        jobs = data.get("jobs", [])
        if not jobs:
            return None
        co = data.get("name", slug)
        return [{
            "title": j.get("title", ""),
            "company": co,
            "location": f"{j.get('city', '')}, {j.get('country', '')}".strip(", "),
            "url": j.get("url", ""),
            "posted_at": j.get("date"),
            "external_id": j.get("id", ""),
            "source": f"workable:{slug}",
            "description": "",
            "tags": j.get("department", ""),
        } for j in jobs]
    except Exception:
        return None


def try_teamtailor(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://{slug}.teamtailor.com/jobs.json")
        if r.status_code != 200:
            return None
        data = r.json()
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        if not jobs:
            return None
        return [{
            "title": j.get("title", ""),
            "company": (j.get("department") or {}).get("name", slug) if isinstance(j.get("department"), dict) else slug,
            "location": j.get("city", "") or j.get("location", ""),
            "url": j.get("url", ""),
            "posted_at": j.get("published_at"),
            "external_id": str(j.get("id", "")),
            "source": f"teamtailor:{slug}",
            "description": "",
            "tags": "",
        } for j in jobs]
    except Exception:
        return None


def try_breezy(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://{slug}.breezy.hr/json")
        if r.status_code != 200:
            return None
        data = r.json()
        positions = data.get("positions", [])
        if not positions:
            return None
        return [{
            "title": j.get("name", ""),
            "company": (data.get("company") or {}).get("name", slug),
            "location": j.get("location", ""),
            "url": j.get("url", ""),
            "posted_at": j.get("created_at"),
            "external_id": j.get("id", ""),
            "source": f"breezy:{slug}",
            "description": "",
            "tags": "",
        } for j in positions]
    except Exception:
        return None


def probe_ats(slug: str) -> list[dict] | None:
    for fn in (try_greenhouse, try_lever, try_ashby, try_smartrecruiters,
               try_workable, try_teamtailor, try_breezy):
        result = fn(slug)
        if result:
            return result
    return None


# =====================================================================
# STORE JOBS
# =====================================================================

def store_jobs(conn, jobs, tag="") -> int:
    new = 0
    now = datetime.now(timezone.utc).isoformat()
    with DB_LOCK:
        for j in jobs:
            if not j.get("title") or not j.get("url"):
                continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO jobs (dedupe_key,title,company,location,description,url,source,source_kind,external_id,posted_at,salary,tags,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (j["url"] or j.get("external_id", ""), j["title"], j.get("company", ""),
                     j.get("location", ""), j.get("description", ""), j["url"],
                     j["source"], "ats", j.get("external_id", ""),
                     j.get("posted_at"), j.get("salary", ""), tag, now, now))
                if cur.rowcount > 0:
                    new += 1
            except Exception:
                continue
        conn.commit()
    return new


# =====================================================================
# MAIN
# =====================================================================

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Mega Hunt: ATS probing + JobSpy combined")
    ap.add_argument("--phase", choices=["ats", "all"], default="ats",
                    help="ats = ATS probing only (fastest), all = ATS + JobSpy")
    ap.add_argument("--threads", type=int, default=80)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--reset", action="store_true", help="Reset checkpoint")
    args = ap.parse_args()

    if args.reset and CP.exists():
        CP.unlink()
        log("Checkpoint reset")

    slugs = build_ats_slugs()
    log(f"Total ATS slugs: {len(slugs)}")

    cp = load_cp() if args.resume else {"ats_done": [], "jobspy_done": [], "stats": {"new": 0, "boards": 0, "searches": 0}}
    ats_done = set(cp["ats_done"])
    remaining = [s for s in slugs if s not in ats_done]
    log(f"Already done: {len(ats_done)}, Remaining: {len(remaining)}")

    if not remaining and args.phase == "ats":
        log("All slugs already probed!")
        return

    conn = sqlite3.connect(DB, check_same_thread=False)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB: {total_before:,} | Gap 1M: {max(0, 1_000_000 - total_before):,}")

    new_total = cp["stats"]["new"]
    boards = cp["stats"]["boards"]
    start = time.time()

    # Phase 1: ATS Probing
    if args.phase in ("ats", "all") and remaining:
        log(f"\n{'='*60}")
        log(f"PHASE 1: ATS PROBING — {len(remaining)} slugs, {args.threads} threads")
        log(f"{'='*60}")

        BATCH = 500
        for bi in range(0, len(remaining), BATCH):
            batch = remaining[bi:bi+BATCH]
            with ThreadPoolExecutor(max_workers=args.threads) as ex:
                futures = {ex.submit(probe_ats, s): s for s in batch}
                for f in as_completed(futures):
                    slug = futures[f]
                    ats_done.add(slug)
                    try:
                        jobs = f.result()
                        if jobs:
                            boards += 1
                            src = jobs[0]["source"]
                            new = store_jobs(conn, jobs)
                            new_total += new
                            log(f"  +{new:4d} {slug:30s} -> {src:30s} ({len(jobs)} jobs)")
                    except Exception:
                        pass

            # Save checkpoint
            cp["ats_done"] = list(ats_done)
            cp["stats"]["new"] = new_total
            cp["stats"]["boards"] = boards
            save_cp(cp)

            elapsed = time.time() - start
            current = total_before + new_total
            rate = new_total / (elapsed / 60) if elapsed > 0 else 0
            done_pct = len(ats_done) * 100 / len(slugs)
            log(f"  [{len(ats_done)}/{len(slugs)}] {done_pct:.0f}% | DB: {current:,} (+{new_total:,}) | Boards: {boards} | {rate:.0f}/min | Gap: {max(0, 1_000_000 - current):,}")

            # Check if we've hit 1M
            if current >= 1_000_000:
                log(f"\n*** 1M JOBS REACHED! ***")
                break

    # Phase 2: JobSpy (optional)
    if args.phase == "all":
        log(f"\n{'='*60}")
        log(f"PHASE 2: JOBSPY KEYWORD SEARCHES")
        log(f"{'='*60}")

        try:
            from jobspy import scrape_jobs as jobspy_scrape
        except ImportError:
            log("jobspy not installed, skipping Phase 2")
            return

        keywords = [
            "software engineer", "backend engineer", "frontend developer",
            "full stack developer", "data engineer", "devops engineer",
            "machine learning engineer", "product manager", "data scientist",
            "cloud engineer", "android developer", "ios developer",
            "python developer", "java developer", "react developer",
            "AI engineer", "blockchain developer", "security engineer",
            "QA engineer", "site reliability engineer",
            "platform engineer", "infrastructure engineer",
            "mobile developer", "web developer", "software developer",
            "system administrator", "database administrator",
            "network engineer", "solutions architect",
            "technical lead", "engineering manager",
            "staff engineer", "principal engineer",
            "senior software engineer", "junior software engineer",
            "remote software engineer", "remote backend developer",
            "C++ engineer", "ruby developer", "PHP developer",
            "scala developer", "kotlin developer", "swift developer",
            "Vue.js developer", "Angular developer",
            "technical writer", "data analyst",
        ]

        locations = [
            "", "Bangalore", "Hyderabad", "Chennai", "Mumbai", "Pune",
            "Delhi", "Noida", "Gurgaon", "Kolkata", "Ahmedabad",
            "New York", "San Francisco", "Seattle", "Austin", "Boston",
            "Chicago", "Los Angeles", "Denver", "Atlanta", "Miami",
            "London", "Berlin", "Toronto", "Singapore", "Dubai",
            "Amsterdam", "Dublin", "Sydney", "Remote",
        ]

        for kw in keywords:
            for loc in locations:
                search_key = f"{kw}|{loc}"
                if search_key in cp["jobspy_done"]:
                    continue

                log(f"  JobSpy: {kw} | {loc or 'global'}")
                all_jobs = []
                seen_ids = set()
                batch_size = 50
                max_pages = 5

                for page in range(max_pages):
                    offset = page * batch_size
                    try:
                        results = jobspy_scrape(
                            site_name=["linkedin", "indeed"],
                            search_term=kw,
                            location=loc if loc else None,
                            results_wanted=batch_size,
                            offset=offset,
                            country_india=True if "india" in loc.lower() or loc in ("Bangalore", "Hyderabad", "Chennai", "Mumbai", "Pune", "Delhi", "Noida", "Gurgaon", "Kolkata", "Ahmedabad") else False,
                        )
                        if not results or not hasattr(results, 'jobs'):
                            break

                        new_count = 0
                        for job in results.jobs:
                            jid = getattr(job, 'id', '') or getattr(job, 'title', '') + str(getattr(job, 'company', ''))
                            if jid in seen_ids:
                                break
                            seen_ids.add(jid)
                            job_url = getattr(job, 'url', '') or getattr(job, 'job_url', '')
                            title = getattr(job, 'title', '')
                            company = getattr(job, 'company', '') if hasattr(job, 'company') else ''
                            location_val = getattr(job, 'location', '') if hasattr(job, 'location') else ''
                            posted = getattr(job, 'date_posted', '') if hasattr(job, 'date_posted') else None
                            desc = getattr(job, 'description', '') if hasattr(job, 'description') else ''
                            source_name = getattr(job, 'site', '') if hasattr(job, 'site') else 'jobspy'
                            all_jobs.append({
                                "title": title,
                                "company": str(company),
                                "location": str(location_val),
                                "url": job_url,
                                "posted_at": str(posted) if posted else None,
                                "external_id": jid,
                                "source": f"jobspy:{source_name}",
                                "description": str(desc)[:500] if desc else "",
                                "tags": "",
                            })
                            new_count += 1

                        if new_count == 0:
                            break
                        time.sleep(0.5)
                    except Exception as e:
                        log(f"    JobSpy error: {e}")
                        break

                if all_jobs:
                    new = store_jobs(conn, all_jobs)
                    new_total += new
                    cp["jobspy_done"].append(search_key)
                    cp["stats"]["new"] = new_total
                    save_cp(cp)
                    log(f"    +{new} new ({len(all_jobs)} scraped)")
                else:
                    cp["jobspy_done"].append(search_key)
                    save_cp(cp)

                # Check 1M
                current = total_before + new_total
                if current >= 1_000_000:
                    log(f"\n*** 1M JOBS REACHED! ***")
                    break

    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    elapsed = time.time() - start
    conn.close()
    _client.close()

    log("")
    log("=" * 60)
    log(f"MEGA HUNT COMPLETE")
    log(f"DB: {final:,} | New: +{new_total:,} | Boards: {boards}")
    log(f"Time: {elapsed/60:.1f} min | Rate: {new_total/(elapsed/60):.0f}/min")
    log(f"Gap 1M: {max(0, 1_000_000 - final):,}")
    log("=" * 60)


if __name__ == "__main__":
    main()
