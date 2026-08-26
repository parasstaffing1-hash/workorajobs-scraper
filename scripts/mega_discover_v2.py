#!/usr/bin/env python3
"""Mega ATS Discovery v2 - Probe 5000+ real companies across 7 ATS platforms.

Strategy: Each valid ATS board gives 50-400 unique jobs with ZERO dedup.
Find 3000+ valid boards = 400K+ fresh jobs.

Platforms: Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Breezy, Teamtailor
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
CP_FILE = ROOT / ".freebuff" / "mega_v2_checkpoint.json"
LOG_FILE = ROOT / ".freebuff" / "mega_v2.log"
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
    return {"scraped": [], "stats": {"new": 0, "errors": 0, "valid": 0, "boards_found": 0}}


def save_checkpoint(cp: dict):
    CP_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Save as compact list for scraped slugs
    cp["scraped"] = list(cp["scraped"]) if isinstance(cp["scraped"], set) else cp["scraped"]
    CP_FILE.write_text(json.dumps(cp, indent=2), encoding="utf-8")


# =====================================================================
# MASSIVE REAL COMPANY LIST - 5000+ companies that actually hire
# These are REAL companies, not tools or frameworks
# =====================================================================

REAL_COMPANIES = """
# --- FAANG + Big Tech ---
google apple amazon meta microsoft netflix twitter snap salesforce adobe oracle ibm intel cisco
vmware sap servicenow workday paloaltonetworks

# --- Cloud & Infrastructure ---
cloudflare fastly digitalocean hashicorp pulumi docker redhat suse linode ovh
heroku render railway flyio vercel netlify

# --- Database & Data ---
mongodb elastic redis cockroachlabs planetscale neon supabase databricks snowflake
confluent influxdata singlestore clickhouse elastic elasticsearch

# --- DevOps & Monitoring ---
datadog newrelic pagerduty grafana prometheus dynatrace splunk sentry

# --- Security ---
crowdstrike zscaler sentinelone snyk veracode abnormalsecurity huntress cybereason
recordedfuture expel beyondtrust pingidentity onelogin duosecurity

# --- AI & ML ---
openai anthropic xai stabilityai inflectionai scaleai snorkelai togetherai assemblyai
cohere mistral huggingface replicate weightsandbiases

# --- Fintech ---
stripe square plaid brex ramp chime sofi affirm klarna revolut monzo n26
wise mercury marqeta nubank coinbase binance kraken robinhood etoro
payoneer checkout.com adyenrippling

# --- E-commerce & Marketplace ---
shopify ebay etsy wayfair bonobos allbirds casper peloton wish
etsy poshmark depop vinted

# --- Logistics & Supply Chain ---
flexport project44 convoy shipbob shippo stord shiphero freightwaves

# --- Health Tech ---
flatironhealth cloverhealth hims forward onemedical whoop oura strava
noom included health lyra headway spring health

# --- Gaming ---
epicgames riotgames roblox unity supercell krafton taketwo rockstargames
zynga scopley king mihoyo

# --- Entertainment & Media ---
spotify twitch vimeo duolingo coursera masterclass
buzzfeed voxmedia theverge theinformation

# --- Indian Tech ---
razorpay phonepe groww zerodha upstox cred slice meesho swiggy zomato
ola rapido freshworks zoho hasura postman phonepe
curefit healthifyme practo 1mg pharmeasy
bigbasket blinkit instamart dunzo porter blackbuck
oyo makemytrip goibibo cleartrip ixigo redbus
policybazaar paisabazaar jar jarapp jarvis spinny cars24
leap finance ofbusiness unacademy byjus PhysicsWalla upgrad simplilearn
whitehat junior Great Learning tcs infosys wipro hcl tech-mahindra
ltimindtree persistent mphasis hexaware mindtree mphasis

# --- HR & People Tech ---
gusto zenefits bamboohr lattice 15five cultureamp leapsome personio
remote deel oyster papaya-global factorialsapling springshire workable

# --- Sales & Marketing ---
salesloft outreach gong highspot seismic brainshark showpad
clari boostup groove apollo zoominfo lusha

# --- Communication ---
twilio sendgrid vonage messagebird plivo ringcentral 8x8
genesys fivedialpad aircall

# --- Customer Support ---
zendesk freshdesk intercom helpscout front

# --- Design & Creative ---
figma canva sketch miro whimsical framer webflow wix squarespace

# --- Project Management ---
asana monday.com clickup smartsheet teamwork basecamp trello jira height linear

# --- Video & Streaming ---
zoom vimeo brightcove wistia vidyard loom

# --- Automotive ---
tesla rivian lucid fisker nio xpeng li-autobyd
toyota honda ford gm bmw mercedes benz volvwagen volkswagen
stellantis chrysler dodge jeep subaru mazda hyundai kia

# --- Aerospace & Defense ---
boeing airbus lockheed-martin northrop-grumman raytheon
general-electric siemens honeywell

# --- Telecom ---
att verizon tmobile comcast charter
viattv alltel centurylink lumen

# --- Consumer Goods ---
nestle unilever procter-gamble pepsico coca-cola mondelz kraft-heinz
mars hershey general-mills kellogg

# --- Energy ---
shell bp exxon-mobil chevron totalenergies equinor

# --- Pharma & Biotech ---
pfizer johnson-johnson merck novartis roche abbvie amgen gilead
moderna biontech astrazeneca sanofi gsk eli-lilly bristol-myers
biogen regeneron vertex illumina

# --- Consulting ---
accenture deloitte pwc ey kpmg mckinsey bain bcg
roland-berger oliver-wyman booz-allen

# --- Real Estate & Proptech ---
opendoor redfin zillow compass oyo

# --- Edtech ---
coursera udemy byjus unacademy PhysicsWalla simplilearn
skillshare pluralsight linkedin-learning 2u guild-education

# --- Food & Delivery ---
ubereats doordash grubhub instacart gofood

# --- Travel & Hospitality ---
booking expedia trivago airbnb hotelscom kayak

# --- Payments & Banking ---
paypal venmo cashapp zelle wise remitly

# --- Work Tools ---
notion coda airtable confluence miro
slack microsoft-teams discord google-chat
loom vidyard cal.com caldendly

# --- More Tech Companies ---
github gitlab bitbucket atlassian
jira linear height notion figma
affirm samsara purestorage elastic cloudkitchen
squarespace wix webflow framer
postman pingdom bugsnag
launchdarkly split optimizely
circleci travis-ci jfrog sonatype
kong tyk redis-labs
experian equifax transunion
nvidia amd qualcomm broadcom marvell micron
synopsys cadence arm

# --- Indian IT Services ---
tcs infosys wipro hcltech tech-mahindra ltimindtree
persistent-systems mphasis hexaware mphasis
cognizant capgemini accenture-india

# --- More Fintech ---
payu cashfree razorpay-phonepe
pine-labs posiqload payubiz

# --- More Indian ---
meesho cred slice grofers bigbasket
porter dunzo blackbuck rivigo
policybazaar paisabazaar jar jarapp
spinny cars24 OLX shiftai
leap-finance ofbusiness
unacademy byjus PhysicsWalla upgrad simplilearn
whitehat Great-Learning upgrad excelr

# --- African ---
flutterwave andela m-kopa twiga sendy chippercash
jumia konga paystack moove lux 
wasoko dpcdash

# --- Latin American ---
mercadolibre rappi 99app Nubank kavak Konfio
Clip stori Bitso Creditas Loft QuintoAndar

# --- European ---
n26 revolut monzo klarna trustpilot
wise deliveryhero callihelp
n26 solarisbank figma notion
wise pulse energy trigo

# --- More Real Companies ---
abbvie accenture adidas agilent allegion
amadeus amgen amphenol ansys apollo apptus
aramark artesian autodesk avanade
banamex barclays barnes-noble bcg
blackrock bloomberg booz-allen
broadridge broadcom buffalo-wild-wings
builder capital-one carvana caterpillar
cerner cgi chartis cognizant comcast
conocophillips constellium cork-dry
cushman wakefield dataiku dentsu
devon energy digital-reality docu-sign
drata dropbox eastman ecolab
edward-jones einpresswire electric-arts
elsevier emerson emerson-electric equifax
ericsson esri expedia exxon fannie-mae
fdic fico fidelis firstround flipkart
flywire fomento fonds-de-dotation
foot-locker freshfields frontera
fti-consulting gather github
glassdoor globe lifter gmm-global
go-daddy gsk gtech guidepoint
hca healthways here hilton
hitachi holcim hpe hp
hsbc hulu huawei hyatt
ibm ice-forums ima immunomedics
indeed infor informatica ingram-micro
intel intercontinental ionis
iron-mountain itaas itg jabil
jll johnson-ctrls jpmorgan
justworks kaiser-permanente kayak keurig
kiehl's kimberly-clark kkr kleiner-perkins
lbrands latham-watkins laxmi-associates
learnamp leidos lenovo levi-strauss
lightstone lilly linc-global
littler lockheed-martin loreal lseg
lucent lumen luminar lviv
maersk manulife markit marklogic
marriott marsh mckesson medi-data
merck merial metlife mettl
microfocus microsoft mohawk-delta montecito
morgan-stanley motion-metrics motus
msi-group mulesoft nanostring
nasdaq nec netflix netgear
news-corp nftc nimble-storage
nintendo nitro-consulting nnn-reit
nokia nordson north-south-governance
northern-trust nortonlifelock nutanix
nvidia oerlikon olympus one-a-day
optum oracle otsuka oxfam
oxfordanalytica palantir panasonic pandora
paramount parker-hannifin parkour8
parsons peapod pekin-association
perkinelmer pfizer philips
phillips66 phoenix-geophysics pimco
plantronics pmi pop-cap praecis
precision-for-medicine procter-gamble
progress-software progress-software
progressive-ohio prudential ptc
publicis pwc qualcomm quaker
qualys quip quorum-labs
rackspace radius health rai
ralph-lauren raytheon red-hat
red-hat regeneron relay therapeutics
reliance resmed revature
revolve rick-associates riot-games rithm
robert-bosch robert-half robinhood
roblox rockwell roivant
roku rolled-alloys rollins
rosendin roth-capital routefusion
rrd rsm-consulting rtx
rubrik ruffer sanmina
sanofi sas sas-institute
schlumberger seagate select-medical
servicenow shell signet-jewelers
skyworks smartrecruiters societe-generale
softbank solugen sonicwall sorrento
soundcloud southern-glazers soyatec
splunk spotstamp stakeholder
stanley-black decker starbucks
starmind stellantis stryker
sumup sunlife sunrun
supermicro switch synopsys
takeda tata-tech teamviewer
techstars teladoc telenav
telstra tempus termly
tesla teva pharma tgi
thales thehartford thomson-reuters
thomsonreuters thomson-reuters
tibco time-warner tivo
tmobile topgolf toronto-dominion
toss tp-link tradestation
transunion travelport trendmicro
trend-micro tripadvisor trulia
trustpilot trycourier tui
turo tvec tyme-group
ubisoft uipath ultimate-software
ultimedia under-armour unicommerce
unit4 united-airlines united-health
unix-shop upstart upwork urgeweb
vail-resorts valero valiant
vanilla forums varonis vayant
verint verisk veritas verizon
via-varejo victoria-s secret videoamp
virgin-vault virta-health virtusa
visa visual Genome vivid-seats vmware
volvo volvo-trucks vote-america vouch
voya vyond walmart
walmart-labs warnermedia warp-9
washington-post wayfair we-are-mission
weare-heretics weavering web-flow
web-help webceo webct wework
western-digital western-union what-dreams
whipclip whitepages wipro wistia
wix wolf-insurance wolters-kluwer wolters-kluwer-health
wonderlic wood-mackenzie world-languages
workday workiva workspace
wpp xactly xero xiaomi
xl-group xperi yahoo yamaha
yelp yext yodlee you-tube
your-world zapier zebpay zego
zenefits zensar zerohash zerto
zillow zingeros ziprecruiter zoox
zscaler
""".strip().split("\n")

# Flatten to clean company slugs
def build_slugs():
    slugs = set()
    for line in REAL_COMPANIES:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Split on spaces/comma-separated words too
        words = line.replace(",", " ").split()
        for w in words:
            w = w.strip().lower()
            if not w or len(w) < 2 or len(w) > 45:
                continue
            slugs.add(w)
            slugs.add(w.replace(" ", ""))
            slugs.add(w.replace(" ", "-"))
            slugs.add(w.replace("-", ""))
            slugs.add(w.replace("_", ""))
            slugs.add(w.replace(".", ""))
            # Without common suffixes
            for sfx in [".com", ".io", ".ai", ".co", ".inc", ".corp"]:
                if w.endswith(sfx):
                    slugs.add(w[:-len(sfx)])
    # Filter
    bad = {"the", "and", "for", "inc", "com", "the", "all", "new", "our", "app", "big", "top",
           "pro", "out", "one", "get", "add", "its", "can", "has", "had", "was", "are", "not"}
    return [s for s in slugs if 2 <= len(s) <= 40 and s not in bad]


# =====================================================================
# ATS SCRAPERS
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
        results = []
        for j in jobs:
            loc = j.get("location")
            if isinstance(loc, dict):
                loc = loc.get("name", "")
            results.append({
                "title": j.get("title", ""),
                "company": data.get("name", slug),
                "location": str(loc or ""),
                "url": j.get("absolute_url", ""),
                "posted_at": j.get("updated_at") or j.get("created_at"),
                "external_id": str(j.get("id", "")),
                "source": f"greenhouse:{slug}",
                "description": (j.get("content") or "")[:500],
                "tags": (j.get("departments") or [{}])[0].get("name", "") if j.get("departments") else "",
            })
        return results
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
        results = []
        for j in data:
            cat = j.get("categories", {})
            ts = j.get("createdAt", 0)
            posted = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat() if ts else None
            results.append({
                "title": j.get("text", ""),
                "company": cat.get("team", slug),
                "location": cat.get("location", ""),
                "url": j.get("hostedUrl", ""),
                "posted_at": posted,
                "external_id": j.get("id", ""),
                "source": f"lever:{slug}",
                "description": (j.get("descriptionPlain") or "")[:500],
                "tags": j.get("teamsPlain", ""),
            })
        return results
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
        results = []
        for j in content:
            loc = j.get("location") or {}
            loc_str = f"{loc.get('city', '')}, {loc.get('country', '')}".strip(", ")
            results.append({
                "title": j.get("name", ""),
                "company": j.get("company", {}).get("name", slug),
                "location": loc_str,
                "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('ref', '')}",
                "posted_at": j.get("releasedDate"),
                "external_id": str(j.get("id", "")),
                "source": f"smartrecruiters:{slug}",
                "description": "",
                "tags": "",
            })
        return results
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
        results = []
        for j in jobs:
            results.append({
                "title": j.get("title", ""),
                "company": data.get("name", slug),
                "location": j.get("city", "") + ", " + j.get("country", ""),
                "url": j.get("url", ""),
                "posted_at": j.get("date"),
                "external_id": j.get("id", ""),
                "source": f"workable:{slug}",
                "description": "",
                "tags": j.get("department", ""),
            })
        return results
    except Exception:
        return []


def scrape_breezy(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://{slug}.breezy.hr/json?verbose=true",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200:
            return []
        data = r.json()
        positions = data.get("positions", [])
        if not positions:
            return []
        return [{
            "title": j.get("name", ""),
            "company": data.get("company", {}).get("name", slug),
            "location": j.get("location", ""),
            "url": j.get("url", ""),
            "posted_at": j.get("created_at"),
            "external_id": j.get("id", ""),
            "source": f"breezy:{slug}",
            "description": "",
            "tags": "",
        } for j in positions]
    except Exception:
        return []


def scrape_teamtailor(slug: str) -> list[dict]:
    try:
        r = httpx.get(
            f"https://{slug}.teamtailor.com/jobs.json",
            timeout=8, follow_redirects=True
        )
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        if not jobs:
            return []
        return [{
            "title": j.get("title", ""),
            "company": j.get("department", {}).get("name", slug) if isinstance(j.get("department"), dict) else slug,
            "location": j.get("city", "") + ", " + j.get("country", "") if j.get("city") else j.get("location", ""),
            "url": j.get("url", ""),
            "posted_at": j.get("published_at"),
            "external_id": str(j.get("id", "")),
            "source": f"teamtailor:{slug}",
            "description": "",
            "tags": "",
        } for j in jobs]
    except Exception:
        return []


SCRAPERS = [
    scrape_greenhouse,
    scrape_lever,
    scrape_ashby,
    scrape_smartrecruiters,
    scrape_workable,
    scrape_breezy,
    scrape_teamtailor,
]


def probe_slug(slug: str) -> list[dict]:
    """Try slug on all ATS platforms, return jobs from first match."""
    for scraper in SCRAPERS:
        jobs = scraper(slug)
        if jobs:
            return jobs
    return []


def store_jobs(conn: sqlite3.Connection, jobs: list[dict], tag: str) -> int:
    """Store jobs in DB, return count of new jobs."""
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
                    (
                        dedupe_key, j["title"], j.get("company", ""),
                        j.get("location", ""), j.get("description", ""),
                        j["url"], j["source"], "ats", j.get("external_id", ""),
                        j.get("posted_at"), j.get("salary", ""), tag, now, now,
                    ),
                )
                if cur.rowcount > 0:
                    new += 1
            except Exception:
                continue
        conn.commit()
    return new


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Mega ATS Discovery v2")
    ap.add_argument("--threads", type=int, default=40)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    all_slugs = list(build_slugs())
    log(f"Generated {len(all_slugs)} unique slugs to probe")

    cp = load_checkpoint() if args.resume else {
        "scraped": [], "stats": {"new": 0, "errors": 0, "valid": 0, "boards_found": 0}
    }
    scraped_set = set(cp["scraped"])
    remaining = [s for s in all_slugs if s not in scraped_set]
    log(f"Already scraped: {len(scraped_set)}, Remaining: {len(remaining)}")

    if not remaining:
        log("All slugs already probed! Use without --resume to start fresh.")
        return

    conn = sqlite3.connect(DB)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {total_before:,}")
    log(f"Gap to 1M: {max(0, 1_000_000 - total_before):,}")

    grand_new = cp["stats"]["new"]
    grand_errors = cp["stats"]["errors"]
    valid = cp["stats"]["valid"]
    boards_found = cp["stats"].get("boards_found", 0)
    start = time.time()

    BATCH = args.threads * 10

    for bi in range(0, len(remaining), BATCH):
        batch = remaining[bi : bi + BATCH]
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(probe_slug, s): s for s in batch}
            for f in as_completed(futures):
                slug = futures[f]
                scraped_set.add(slug)
                try:
                    jobs = f.result()
                    if jobs:
                        valid += 1
                        boards_found += 1
                        src = jobs[0].get("source", "?")
                        new = store_jobs(conn, jobs, f"mega_v2,{slug}")
                        grand_new += new
                        log(f"  {slug:30s} -> {src:30s}: {len(jobs):4d} jobs, +{new:4d}")
                except Exception:
                    grand_errors += 1

        # Save checkpoint every batch
        cp = {
            "scraped": list(scraped_set),
            "stats": {
                "new": grand_new,
                "errors": grand_errors,
                "valid": valid,
                "boards_found": boards_found,
            },
        }
        save_checkpoint(cp)

        elapsed = time.time() - start
        current = total_before + grand_new
        rate = grand_new / (elapsed / 60) if elapsed > 0 else 0
        progress_pct = (len(scraped_set) / len(all_slugs)) * 100
        log(
            f"  [{bi + len(batch)}/{len(all_slugs)}] "
            f"{progress_pct:.1f}% done | "
            f"DB: {current:,} (+{grand_new:,}) | "
            f"Boards: {valid} | "
            f"Rate: {rate:.0f}/min | "
            f"Gap 1M: {max(0, 1_000_000 - current):,}"
        )

    elapsed = time.time() - start
    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    log("")
    log("=" * 60)
    log("MEGA DISCOVER V2 COMPLETE")
    log(f"Slugs probed: {len(scraped_set)}")
    log(f"Valid boards: {valid}")
    log(f"New jobs:     {grand_new:,}")
    log(f"DB total:     {final:,}")
    log(f"Time:         {elapsed / 60:.1f} min")
    log(f"Rate:         {grand_new / (elapsed / 60):.0f} new/min")
    log(f"Gap to 1M:    {max(0, 1_000_000 - final):,}")
    log("=" * 60)


if __name__ == "__main__":
    main()
