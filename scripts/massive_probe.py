#!/usr/bin/env python3
"""Massive ATS probe - generate 10,000+ real company slugs and probe all.
Uses httpx connection pooling for speed, checkpoints after every 100 slugs.
"""
from __future__ import annotations
import json, re, sqlite3, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
import httpx

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP_FILE = ROOT / ".freebuff" / "massive_probe_checkpoint.json"
LOG = ROOT / ".freebuff" / "massive_probe.log"
DB_LOCK = Lock()

_client = httpx.Client(timeout=3.0, follow_redirects=True,
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=40),
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8", errors="replace") as f:
        f.write(line + "\n")

def load_cp():
    if CP_FILE.exists():
        try: return json.loads(CP_FILE.read_text("utf-8"))
        except: pass
    return {"done": [], "stats": {"new": 0, "boards": 0}}

def save_cp(cp):
    CP_FILE.parent.mkdir(parents=True, exist_ok=True)
    CP_FILE.write_text(json.dumps({"done": list(cp["done"]), "stats": cp["stats"]}), "utf-8")

# =====================================================================
# MASSIVE COMPANY SLUG GENERATOR
# Sources: Fortune 500, Forbes 2000, YC, Indian IT, European, Asian, etc.
# =====================================================================

NAMES = """
# Fortune 500 condensed
walmart amazon apple berkshire unitedhealth exxon chevron
costco walmart unitedhome unitedpgrate pfizer citigroup
verizon comcast qualcomm ibm ford motorola motorola
chevron boeing caterpillar mcdonnells starbucks target
lowe home depot microsoft alphabet meta netflix oracle
ibm cisco intel amd nvidia broadcom salesforce adobe
vmware paloalto crowdstrike zscaler cloudflare datadog
newrelic splunk grafana dynatrace pagerduty sentry

# FAANG+big tech
google apple amazon meta facebook microsoft netflix twitter
snap oracle ibm salesforce adobe vmware cisco intel
amd nvidia broadcom qualcomm servicenow workday

# Cloud/infra
cloudflare fastly digitalocean hashicorp pulumi docker redhat
suse linode ovh heroku render railway flyio vercel netlify
panzura purestorage netapp dell hpe lenovo acer

# Database/data
mongodb elastic redis cockroachlabs planetscale neon supabase
databricks snowflake confluent influxdata singlestore clickhouse
couchbase cassandra scylla litestream turso dbt fivetran airbyte
starburst hasura dremio

# DevOps/monitoring
datadog newrelic pagerduty grafana prometheus dynatrace splunk
sentry opsgenie nocodb

# Security
crowdstrike zscaler sentinelone snyk veracode expel
abnormalsecurity huntress cybereason recordedfuture wiz 1password
bitwarden beyondtrust pingidentity

# AI/ML
openai anthropic xai scaleai togetherai assemblyai mistral cohere
stabilityai inflectionai snorkelai huggingface replicate modal
groq fireworks deepgram weaviate pinecone langchain llamaindex
midjourney runway cursor replit

# Fintech
stripe square plaid brex ramp chime sofi affirm klarna
revolut monzo n26 wise mercury marqeta checkout adyen rippling
payoneer tipalti bill.com paycom paylocity paychex

# E-commerce
shopify ebay etsy wayfair stockx goat poshmark
bonobos allbirds casper peloton wish

# Logistics
flexport project44 convoy shipbob shippo stord freightwaves
fourkites turvo

# Health
flatironhealth cloverhealth hims forward onemedical whoop oura
noom lyra headway springhealth zocdoc

# Gaming
epicgames riotgames roblox unity supercell krafton taketwo
rockstargames zynga scopely king mihoyo square-enix sega
bandai-namco capcom konami valve

# Media
spotify twitch vimeo duolingo coursera masterclass
buzzfeed voxmedia vice

# Design
figma canva sketch miro whimsical framer webflow wix
squarespace invision penpot dribbble

# PM tools
asana monday clickup smartsheet teamwork basecamp trello
jira linear height notion coda airtable

# Comms
twilio sendgrid vonage plivo ringcentral 8x8 genesys
fivedialpad aircall kustomer

# Support
zendesk freshdesk intercom helpscout front

# Sales
salesloft outreach gong chorus highspot seismic showpad
clari boostup grove apollo zoominfo lusha clearbit

# Work tools
slack discord microsoft-teams google-chat zoom loom

# Indian tech
tcs infosys wipro hcltech tech-mahindra ltimindtree
persistent-systems mphasis hexaware mindtree cognizant
capgemini accenture-india razorpay phonepe groww zerodha
upstox cred slice meesho swiggy zomato ola rapido
freshworks zoho hasura postman curefit healthifyme practo
1mg pharmeasy bigbasket blinkit instamart dunzo porter
blackbuck oyo makemytrip goibibo cleartrip ixigo redbus
policybazaar paisabazaar jar spinny cars24 leapfinance
ofbusiness unacademy byjus physicswalla upgrad simplilearn
whitehat Great-Learning excelr urbancompany khatabook nobroker
lendingkart cashfree pine-labs jarapp mygate

# HR tech
gusto zenefits bamboohr lattice 15five cultureamp leapsome
personio remote deel oyster papaya-global factorialsapling

# Gaming/entertainment
riotgames epicgames roblox unity supercell krafton

# European
sap siemens bmw mercedes volkswagen adidas puma allianz
munich-re deutsche-bank shell bp totalenergies equinor
asos deliveryhero hellofresh getyourguide trivago booking
revolut monzo n26 klarna wise mambu solarisbank trigo
wolt northvolt volvo-cars polestar ferrero nestle

# UK
revolut monzo starling go-cardless tide checkout dotdigital
paddle elvie faculty bumble

# Asian
samsung lg sony panasonic nec fujitsu hitachi toshiba
bytedance tiktok alibaba tencent baidu jd pinduoduo
meituan didi sea-group grab gojek traveloka shopee lazada
tokopedia bukalapak flipkart paytm mobikwik

# African
flutterwave andela m-kopa twiga sendy chippercash jumia
konga paystack moove wasoko dpcdash kuda carbon fairmoney

# LATAM
mercadolibre rappi 99app kavak konfio clip stori bitso
creditas loft quint-andar nubank dlocal despegar

# More real companies
notion linear postman gitlab github bitbucket atlassian
gitlab github bitbucket atlassian jira confluence
samsara purestorage elastic cloudflare
datadog grafana pagerduty newrelic splunk
okta onelogin duosecurity wiz 1password
anthropic deepmind waymo cruise aurora
instacart doordash grubhub lyft uber
airbnb redfin zillow opendoor
snapchat pinterest reddit discord
figma canva sketch miro
twilio stripe plaid square
ramp brex chime sofi
databricks snowflake palantir
hashicorp terraform pulumi docker
mongodb redis elastic confluent
gitlab github atlassian linear
notion coda airtable monday
asana basecamp clickup trello
slack zoom teams discord
salesforce hubspot zendesk
workday sap oracle adobe
vmware cisco paloalto crowdstrike
nvidia amd qualcomm broadcom
openai anthropic huggingface
cohere mistral stability
replicate modal fireworks
groq deepgram pinecone
weaviate chromadb langchain
llamaindex cursor replit
vercel netlify flyio render
cloudflare fastly digitalocean
supabase planetscale neon
prisma drizzle typeorm
nextjs nuxt sveltekit remix
vite webpack turbo rspack
tailwindcss radix shadcn
react vue svelte angular
swift kotlin rust golang
typescript python java ruby
php elixir erlang scala
""".strip().split("\n")

def build_slugs():
    slugs = set()
    for line in NAMES:
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
            # Also try common ATS slug variations
            slugs.add(f"{w}-inc")
            slugs.add(f"{w}-io")
            slugs.add(f"{w}-hq")
    bad = {"the","and","for","inc","com","all","new","our","app","big","top","pro","out","one",
           "get","add","its","can","has","had","was","are","not","also","into","with","from",
           "this","that","than","your","you","now","nexus","pilot","meta","delta","alpha",
           "apex","core","edge","flux","hub","ion","jet","kin","link","map","net","oak",
           "opt","pin","ray","set","tap","ux","vim","zen","zeta","ace","arc","bio","box",
           "cap","day","ego","fly","fox","gem","hex","ide","jam","key","lab","max","mix",
           "neo","nut","peg","py","raw","sky","sun","van","via","wax","win","zoo","golang",
           "scala","kotlin","swift","rust","ruby","python","java","php","elixir","erlang",
           "typescript","svelte","angular","react","vue","nextjs","nuxt","remix","astro",
           "vite","webpack","turbo","rspack","prisma","drizzle","typeorm","tailwindcss",
           "shadcn","radix","headlessui","pytorch","tensorflow","jax","keras","langchain",
           "llamaindex","chromadb","pinecone","weaviate","cursor","replit","vercel","flyio",
           "render","fastly","digitalocean","supabase","planetscale","neon"}
    return sorted([s for s in slugs if len(s) >= 3 and s not in bad])

# =====================================================================
# ATS SCRAPERS
# =====================================================================
def try_greenhouse(slug):
    try:
        r = _client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        if r.status_code != 200: return None
        d = r.json(); jobs = d.get("jobs",[])
        if not jobs: return None
        co = d.get("name", slug)
        return [{"title":j.get("title",""),"company":co,
                 "location":(j.get("location",{}) or {}).get("name","") if isinstance(j.get("location"),dict) else str(j.get("location","")),
                 "url":j.get("absolute_url",""),"posted_at":j.get("updated_at") or j.get("created_at"),
                 "external_id":str(j.get("id","")),"source":f"greenhouse:{slug}",
                 "description":(j.get("content") or "")[:500],
                 "tags":(j.get("departments") or [{}])[0].get("name","") if j.get("departments") else ""} for j in jobs]
    except: return None

def try_lever(slug):
    try:
        r = _client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if r.status_code != 200: return None
        d = r.json()
        if not isinstance(d,list) or not d: return None
        return [{"title":j.get("text",""),"company":(j.get("categories",{}) or {}).get("team",slug),
                 "location":(j.get("categories",{}) or {}).get("location",""),
                 "url":j.get("hostedUrl",""),
                 "posted_at":datetime.fromtimestamp(j.get("createdAt",0)/1000,tz=timezone.utc).isoformat() if j.get("createdAt") else None,
                 "external_id":j.get("id",""),"source":f"lever:{slug}",
                 "description":(j.get("descriptionPlain") or "")[:500],"tags":j.get("teamsPlain","")} for j in d]
    except: return None

def try_ashby(slug):
    try:
        r = _client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if r.status_code != 200: return None
        d = r.json(); board = d.get("jobBoard",{})
        openings = board.get("openings",[])
        if not openings: return None
        co = board.get("name", slug)
        return [{"title":j.get("title",""),"company":co,"location":j.get("locationName",""),
                 "url":j.get("url",""),"posted_at":j.get("publishedAt"),
                 "external_id":j.get("id",""),"source":f"ashby:{slug}",
                 "description":"","tags":j.get("departmentName","")} for j in openings]
    except: return None

def try_smartrecruiters(slug):
    try:
        r = _client.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0")
        if r.status_code != 200: return None
        d = r.json(); content = d.get("content",[])
        if not content: return None
        return [{"title":j.get("name",""),"company":(j.get("company") or {}).get("name",slug),
                 "location":f"{(j.get('location') or {}).get('city','')}, {(j.get('location') or {}).get('country','')}".strip(", "),
                 "url":f"https://jobs.smartrecruiters.com/{slug}/{j.get('ref','')}",
                 "posted_at":j.get("releasedDate"),"external_id":str(j.get("id","")),
                 "source":f"smartrecruiters:{slug}","description":"","tags":""} for j in content]
    except: return None

def try_workable(slug):
    try:
        r = _client.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
        if r.status_code != 200: return None
        d = r.json(); jobs = d.get("jobs",[])
        if not jobs: return None
        co = d.get("name", slug)
        return [{"title":j.get("title",""),"company":co,
                 "location":f"{j.get('city','')}, {j.get('country','')}".strip(", "),
                 "url":j.get("url",""),"posted_at":j.get("date"),
                 "external_id":j.get("id",""),"source":f"workable:{slug}",
                 "description":"","tags":j.get("department","")} for j in jobs]
    except: return None

def try_teamtailor(slug):
    try:
        r = _client.get(f"https://{slug}.teamtailor.com/jobs.json")
        if r.status_code != 200: return None
        d = r.json(); jobs = d if isinstance(d,list) else d.get("jobs",[])
        if not jobs: return None
        return [{"title":j.get("title",""),
                 "company":(j.get("department") or {}).get("name",slug) if isinstance(j.get("department"),dict) else slug,
                 "location":j.get("city","") or j.get("location",""),
                 "url":j.get("url",""),"posted_at":j.get("published_at"),
                 "external_id":str(j.get("id","")),"source":f"teamtailor:{slug}",
                 "description":"","tags":""} for j in jobs]
    except: return None

def try_breezy(slug):
    try:
        r = _client.get(f"https://{slug}.breezy.hr/json")
        if r.status_code != 200: return None
        d = r.json(); positions = d.get("positions",[])
        if not positions: return None
        return [{"title":j.get("name",""),"company":(d.get("company") or {}).get("name",slug),
                 "location":j.get("location",""),"url":j.get("url",""),
                 "posted_at":j.get("created_at"),"external_id":j.get("id",""),
                 "source":f"breezy:{slug}","description":"","tags":""} for j in positions]
    except: return None

def probe(slug):
    for fn in (try_greenhouse, try_lever, try_ashby, try_smartrecruiters,
               try_workable, try_teamtailor, try_breezy):
        r = fn(slug)
        if r: return r
    return None

def store(conn, jobs, tag):
    new = 0; now = datetime.now(timezone.utc).isoformat()
    with DB_LOCK:
        for j in jobs:
            if not j.get("title") or not j.get("url"): continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO jobs (dedupe_key,title,company,location,description,url,source,source_kind,external_id,posted_at,salary,tags,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (j["url"] or j.get("external_id",""), j["title"], j.get("company",""),
                     j.get("location",""), j.get("description",""), j["url"],
                     j["source"], "ats", j.get("external_id",""),
                     j.get("posted_at"), j.get("salary",""), tag, now, now))
                if cur.rowcount > 0: new += 1
            except: continue
        conn.commit()
    return new

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=80)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    slugs = build_slugs()
    log(f"Generated {len(slugs)} slugs")

    cp = load_cp() if args.resume else {"done": [], "stats": {"new": 0, "boards": 0}}
    done = set(cp["done"])
    remaining = [s for s in slugs if s not in done]
    log(f"Done: {len(done)}, Remaining: {len(remaining)}")

    if not remaining:
        log("All done!"); return

    conn = sqlite3.connect(DB, check_same_thread=False)
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB: {before:,} | Gap 1M: {max(0, 1000000-before):,}")

    new_t = cp["stats"]["new"]; boards = cp["stats"]["boards"]
    start = time.time()

    for bi in range(0, len(remaining), 500):
        batch = remaining[bi:bi+500]
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(probe, s): s for s in batch}
            for f in as_completed(futures):
                slug = futures[f]; done.add(slug)
                try:
                    jobs = f.result()
                    if jobs:
                        boards += 1
                        new = store(conn, jobs, f"mass,{slug}")
                        new_t += new
                        if new > 0:
                            log(f"  +{new:4d} {slug:25s} -> {jobs[0]['source']} ({len(jobs)})")
                except: pass

        cp["done"] = list(done)
        cp["stats"] = {"new": new_t, "boards": boards}
        save_cp(cp)

        el = time.time() - start
        cur = before + new_t
        log(f"  [{len(done)}/{len(slugs)}] DB:{cur:,} +{new_t:,} | {boards} boards | {el/60:.1f}min | Gap:{max(0,1000000-cur):,}")

    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    el = time.time() - start
    conn.close(); _client.close()

    log("="*60)
    log(f"DONE: {len(done)} slugs, {boards} boards, +{new_t:,} new")
    log(f"DB: {final:,} | Gap 1M: {max(0,1000000-final):,}")
    log(f"Time: {el/60:.1f}min | Rate: {new_t/(el/60):.0f}/min")
    log("="*60)

if __name__ == "__main__":
    main()
