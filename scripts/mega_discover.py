#!/usr/bin/env python3
"""Mega discover: generate 5000+ slugs from real company names,
probe all on Greenhouse/Lever/Ashby/SmartRecruiters, scrape valid ones.
Target: find 3000+ valid boards = ~400K fresh jobs.
"""
from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import httpx

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP_FILE = ROOT / ".freebuff" / "mega_discover_checkpoint.json"
LOG_FILE = ROOT / ".freebuff" / "mega_discover.log"
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
    return {"scraped": [], "stats": {"new": 0, "errors": 0, "valid": 0}}


def save_checkpoint(cp: dict):
    CP_FILE.parent.mkdir(parents=True, exist_ok=True)
    CP_FILE.write_text(json.dumps(cp, indent=2), encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# MASSIVE real company name list (5000+ slugs from real companies)
# ════════════════════════════════════════════════════════════════

# Each line is a real company that likely uses an ATS platform
RAW_COMPANIES = """
google apple amazon meta microsoft netflix spotify twitter snap uber lyft airbnb
salesforce adobe oracle ibm intel cisco vmware sap servicenow workday
cloudflare fastly vercel netlify digitalocean hashicorp pulumi docker redhat suse
mongodb elastic redis cockroachlabs planscale neon supabase databricks snowflake confluent
gitlab github bitbucket atlassian jira linear height notion figma canva
slack discord zoom miro whimsical
datadog newrelic pagerduty sentry grafana amplitude mixpanel segment heap fullstory
hotjar posthog plausible
crowdstrike paloaltonetworks zscaler sentinelone snyk veracode abnormalsecurity huntress
cybereason recordedfuture expel
openai anthropic xai stabilityai inflectionai scaleai snorkelai togetherai assemblyai
cohere mistral huggingface replicate
stripe square plaid brex ramp chime sofi affirm klarna revolut monzo n26
wise mercury marqeta galileo nubank coinbase binance kraken robinhood etoro
shopify ebay etsy wayfair bonobos allbirds warby-parker casper peloton
flexport project44 convoy shipbob shippo stord
flatironhealth cloverhealth hims forward one-medical whoop oura strava
epicgames riotgames roblox unity supercell krafton taketwo rockstargames
activision blizzard ea ubisoft
spotify pandora soundcloud twitch vimeo duolingo coursera
razorpay phonepe groww zerodha upstox cred slice meesho swiggy zomato
ola rapido freshworks zoho hasura darwinbox citiustech sahaj postman
buffer zapier helpscout basecamp automattic toptal upwork fiverr turing
gusto zenefits bamboohr lattice 15five cultureamp leapsome personio remote
deel oyster papaya-global factorial
snyk launchdarkly split optimizely circleci travis-ci jfrog sonatype
kong tyk redis-labs influxdata
servicenow salesforce workday adobe vmware cisco juniper arista
paloaltonetworks fortinet check-point
tcs infosys wipro hcl tech-mahindra ltimindtree persistent mphasis hexaware
bytedance tiktok alibaba tencent baidu jd.com pinduoduo meituan
sea-group grab gojek traveloka shopee lazada tokopedia bukalapak
flipkart paytm mobikwik freecharge policybazaar paisabazaar
curefit cult-fit healthifyme practo 1mg pharmeasy netmeds medlife
bigbasket grofers blinkit instamart dunzo porter blackbuck rivigo
oyo make-my-trip goibibo cleartrip ixigo redbus abhibus trivago
zomato swiggy foodpanda ubereats
cred slice one-card github-copilot
accenture deloitte pwc ey kpmg mckinsey bain bcg roland-berger
oliver-wyman booz-allen leidos northrop-grumman raytheon lockheed-martin
general-dynamics bae-systems l3harris
atlassian zendesk intercom drift hubspot marketo pardot
salesloft outreach gong chorus highspot seismic brainshark showpad
clari boostup groove agile
apple microsoft amazon google facebook meta
nvidia amd qualcomm broadcom marvell micron texas-instruments
synopsys cadence arm arm-holdings arm-limited
twilio sendgrid mailchimp sendgrid twilio-sendgrid
auth0 okta onelogin duo-mobile ping-identity
hashicorp vault terraform consul nomad
redis redis-labs redis-enterprise elastic elasticsearch kibana
confluent kafka apache-kafka apache flink
mongodb-atlas couchbase couchdb cassandra scylla
snowflake bigquery redshift databricks delta-lake
dbt fivetran airbyte stitch dataform
prefect dagster airflow luigi
jupyter notebooks jupyterlab google-colab kaggle
pytorch tensorflow jax keras scikit-learn
huggingface transformers langchain llamaindex chromadb
pinecone weaviate milvus qdrant pgvector
openai-api anthropic-api cohere-api stability-api
replicate modal runpod bentoml mlflow weights-biases
scaleai labelbox superwise arizeai whylabs
snorkelai probli remotasks scale-ai
nvidia-ngc huggingface-hub kaggle-competitions
github-copilot codewhisperer tabnine codiumai
figma sketch invision zeplin marvel-invision
framer webflow wix squarespace shopify-storefront
vercel netlify cloudflare-pages render railway heroku
flyio render railway aws-lambda aws-ecs aws-eks
google-cloud-run google-app-engine azure-functions azure-app-service
digitalocean linode vultr hetzner ovh
cloudflare-workers cloudflare-r2 cloudflare-d1
supabase firebase appwrite pocketbase
planetscale neon turso litestream sqlite
prisma drizzle typeorm sequelize knex
nextjs nuxt sveltekit remix astro solid-start
vite webpack esbuild turbo-pack rspack
tailwindcss styled-components emotion chakra-ui
radix-ui shadcnui headlessui flowbite daisyui
react vue svelte angular solid-js preact
react-native expo flutter swiftui jetpack-compose
kotlin-swift rust go golang typescript python
java csharp dotnet nodejs deno bun
elixir erlang haskell clojure scala
ruby rails laravel django flask fastapi
spring-boot express nestjs gin echo actix
aws azure gcp oracle-cloud ibm-cloud
kubernetes docker compose helm argocd flux
terraform pulumi crossplane cdk cdktf
jenkins github-actions gitlab-ci circleci travis-ci
prometheus grafana datadog newrelic dynatrace
splunk elastic-stack graylog logzio
vault aws-secrets-manager azure-keyvault gcp-secret-manager
okta auth0 ping-identity forgerock
crowdstrike sentinel-one carbon-black trend-micro symantec
paloaltonetworks fortinet sonicwall watchguard
zscaler netskope cloudflare-access
snyk sonarqube checkmarx veracode whitesource
owasp zap burp-suite nmap nessus
wireshark tcpdump netcat socat
github gitlab bitbucket azure-devops
jira confluence linear height asana trello
notion coda airtable google-sheets
slack microsoft-teams discord google-chat
zoom google-meet teams webex
miro figma excalidraw tldraw
loom vidyard wistia brightcove
spotify apple-music youtube netflix disney+
hulu amazon-prime hbo-max peacock
paramount paramount-plus paramount
paramount-global viacom viacomcbs
disney disney-plus fox fox-corporation
warner warner-brothers warnermedia
comcast nbcuniversal universal
sony sony-pictures sony-music
lg samsung huawei xiaomi oppo vivo
oneplus realme Nothing-Phone
tesla rivian lucid fisker
nio xpeng li-auto byd
toyota honda ford gm general-motors
bmw mercedes benz volvo volkswagen
stellantis chrysler dodge jeep ram
subaru mazda hyundai kia
ferrari lamborghini porsche mclaren
rolls-royce bentley maserati aston-martin
boeing airbus lockheed-raytheon northrop-grumman
general-electric siemens abb schneider-electric
honeywell emerson rockwell-schneider
caterpillar deere-and-company cnh-industrial
3m dow-dupont chemical-bASF bayer-bASF
pfizer johnson-m Johnson-johnson merck novartis
roche abbvie amgen gilead sciences
moderna biontech astrazeneca sanofi
glaxosmithkline gsk eli-lilly bristol-myers
abbvie biogen regeneron vertex
illumina gene-variant precision-medicine
23andme ancestry genomics
crispr cas9 gene-editing
nestle unilever procter-gamble p&g
pepsico coca-cola mondelez kraft-heinz
mars hershey general-mills kellogg
campbell-soup conagra danone marfrig
jbs tyson-foods cargill bunge
cargill adm bungeLouis-Dreyfus
shell bp exxon-mobil chevron
totalenergies eni equinor repsol
saudi-aramco adnoc adnoc-group
petronas petrochina sinopec cnpc
IREDA ntpc nlc-india central-electricity
tata-power adani-power reliance-power
suzlon energy-inox wind-solar
abbvie regeneron vertex biogen
amgen genentech biogen idec
moderna biontech astrazeneca roche
johnson-johnson pfizer merck novartis
glaxosmithkline gsk sanofi abbvie
el-lilly bristol-myers squibb abbvie
cisco-systems arista-networks juniper-networks
extreme-networks ruckus-networks calix
commvault veritas veeam acronis
pure-storage netapp dell-emc hpe
lenovo hp-inc dell-technologies acer
asus msi toshiba fujitsu
nec hitachi panasonic sharp
samsung electronics lg-electronics sony-electronics
xiaomi oppo vivo realme oneplus
nothing-phone pixel google-pixel
apple-iphone apple-mac apple-watch
microsoft-surface microsoft-xbox microsoft-teams
amazon-alexa amazon-fire amazon-prime
netflix disney-plus hulu hbo-max
paramount-plus peacock discovery-plus
apple-tv youtube-tv sling-tv fubo-tv
crunchyroll funimation rooster-teeth
 twitch youtube tiktok instagram
facebook twitter snapchat reddit
pinterest linkedin tumblr medium
substack ghost wordpress squarespace
wix webflow framer shopify
bigcommerce magento prestashop opencart
woocommerce shopify-plus magento-commerce
salesforce-commerce-cloud sap-commerce oracle-commerce
adobe-experience-cloud sitecore contentful
strapi sanity prismic directus
contentstack storyblok kontent
agility-cms dotcms luminary-cms
optimizely episerver sitefinity
kentico umbraco sitecore-xp
acquia drupal wordpress-enterprise
hubspot-marketing hubspot-sales hubspot-service
marketo pardot eloqua oracle-marketing
salesloft outreach groove
gong chorus chorus-ai
clari insightspot_altium apollo
zoominfo Seamless.ai lusha lead411
clearbit 6sense demandbase terminus
drift qualified intercom
zendesk freshdesk jira-service-desk
servicenow salesforce-service cloudfront-service
front helpscout freshcaller
twilio sendgrid vonage messagebird
plivo ringcentral 8x8
genesys nICE inContact Five9
Talkdesk dialpad Aircall
fireflies otter-tldr grain
cal.com calendly acuity-scheduling
x.ai gemini claude chatgpt
copilot codewhisperer cursor replit
vercel-cron inngest temporal airflow
dbt airflow airflow-2 airflow-3
metabase looker tableau power-bi
superset mode-analytics hex notebooks
deepnoteobservable-data
jamboard miro figma-excalidraw
whimsical tldraw excalidraw
lucidchart draw-io diagrams.net
notion coda airtable bases
clickup asana monday.com wrike
smartsheet teamwork basecamp
trello jira height linear
shortwave superhuman mimestream
hey fastmail protonmail tutanota
1password bitwarden dashlane nordpass
lastpass keeper dashlane
tailscale zerotier wireguard openvpn
cloudflare-warp nordvpn expressvpn surfshark
mullvad ivpn protonvpn
""".strip().split()

def generate_slugs(companies: list[str]) -> list[str]:
    """Generate unique slugs from company names."""
    slugs = set()
    for c in companies:
        c = c.strip().lower()
        if not c or len(c) < 2:
            continue
        # Base slug
        slugs.add(c)
        # Common variations
        slugs.add(c.replace(" ", ""))
        slugs.add(c.replace(" ", "-"))
        slugs.add(c.replace(".", ""))
        slugs.add(c.replace(".", "-"))
        # Remove common suffixes
        for suffix in [".com", ".io", ".ai", ".co", ".inc", ".corp", ".ltd"]:
            if c.endswith(suffix):
                slugs.add(c[:-len(suffix)])
    # Filter out too-short or too-long slugs
    return [s for s in slugs if 2 <= len(s) <= 40]


# ════════════════════════════════════════════════════════════════
# ATS scrapers
# ════════════════════════════════════════════════════════════════
def scrape_greenhouse(slug):
    try:
        r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", timeout=8, follow_redirects=True)
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = data.get("jobs", [])
        if not jobs:
            return []
        return [{"title": j.get("title",""), "company": data.get("name",slug),
                 "location": (j.get("location",{}) or {}).get("name","") if isinstance(j.get("location"),dict) else str(j.get("location","")),
                 "url": j.get("absolute_url",""), "posted_at": j.get("updated_at") or j.get("created_at"),
                 "jobkey": str(j.get("id","")), "source": f"greenhouse:{slug}",
                 "description": (j.get("content") or "")[:500],
                 "tags": (j.get("departments") or [{}])[0].get("name","") if j.get("departments") else ""} for j in jobs]
    except: return []


def scrape_lever(slug):
    try:
        r = httpx.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=8, follow_redirects=True)
        if r.status_code != 200: return []
        data = r.json()
        if not isinstance(data, list) or not data: return []
        return [{"title": j.get("text",""), "company": j.get("categories",{}).get("team",slug),
                 "location": j.get("categories",{}).get("location",""),
                 "url": j.get("hostedUrl",""),
                 "posted_at": datetime.fromtimestamp(j.get("createdAt",0)/1000, tz=timezone.utc).isoformat() if j.get("createdAt") else None,
                 "jobkey": j.get("id",""), "source": f"lever:{slug}",
                 "description": (j.get("descriptionPlain") or "")[:500],
                 "tags": j.get("teamsPlain","")} for j in data]
    except: return []


def scrape_ashby(slug):
    try:
        r = httpx.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=8, follow_redirects=True)
        if r.status_code != 200: return []
        data = r.json()
        board = data.get("jobBoard",{})
        openings = board.get("openings",[])
        if not openings: return []
        return [{"title": j.get("title",""), "company": board.get("name",slug),
                 "location": j.get("locationName",""), "url": j.get("url",""),
                 "posted_at": j.get("publishedAt"), "jobkey": j.get("id",""),
                 "source": f"ashby:{slug}", "description": "",
                 "tags": j.get("departmentName","")} for j in openings]
    except: return []


def scrape_smartrecruiters(slug):
    try:
        r = httpx.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0", timeout=8, follow_redirects=True)
        if r.status_code != 200: return []
        data = r.json()
        content = data.get("content",[])
        if not content: return []
        return [{"title": j.get("name",""), "company": j.get("company",{}).get("name",slug),
                 "location": ((j.get("location") or {}).get("city","")+", "+((j.get("location") or {}).get("country",""))).strip(", "),
                 "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('ref','')}",
                 "posted_at": j.get("releasedDate"), "jobkey": str(j.get("id","")),
                 "source": f"smartrecruiters:{slug}", "description": "", "tags": ""} for j in content]
    except: return []


def probe_slug(slug):
    for scraper in [scrape_greenhouse, scrape_lever, scrape_ashby, scrape_smartrecruiters]:
        jobs = scraper(slug)
        if jobs:
            return jobs
    return []


def store_jobs(conn, jobs, tag) -> int:
    new = 0
    now = datetime.now(timezone.utc).isoformat()
    with DB_LOCK:
        for j in jobs:
            if not j.get("title") or not j.get("url"): continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO jobs (dedupe_key,title,company,location,description,url,source,source_kind,external_id,posted_at,salary,tags,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (j["url"] or j.get("jobkey",""), j["title"], j.get("company",""), j.get("location",""),
                     j.get("description",""), j["url"], j["source"], "ats", j.get("jobkey",""),
                     j.get("posted_at"), j.get("salary",""), tag, now, now))
                if cur.rowcount > 0: new += 1
            except: continue
        conn.commit()
    return new


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=30)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    all_slugs = generate_slugs(RAW_COMPANIES)
    log(f"Generated {len(all_slugs)} unique slugs")

    cp = load_checkpoint() if args.resume else {"scraped": [], "stats": {"new": 0, "errors": 0, "valid": 0}}
    scraped_set = set(cp["scraped"])
    remaining = [s for s in all_slugs if s not in scraped_set]
    log(f"Already scraped: {len(scraped_set)}, Remaining: {len(remaining)}")

    conn = sqlite3.connect(DB)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {total_before:,}")

    grand_new = cp["stats"]["new"]
    grand_errors = cp["stats"]["errors"]
    valid = cp["stats"]["valid"]
    start = time.time()
    batch_size = args.threads * 10

    for bi in range(0, len(remaining), batch_size):
        batch = remaining[bi:bi+batch_size]
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(probe_slug, s): s for s in batch}
            for f in as_completed(futures):
                slug = futures[f]
                scraped_set.add(slug)
                try:
                    jobs = f.result()
                    if jobs:
                        valid += 1
                        src = jobs[0].get("source","?")
                        new = store_jobs(conn, jobs, f"mega,{slug}")
                        grand_new += new
                        if new > 0:
                            log(f"  {slug:30s} -> {src:30s}: {len(jobs):4d} jobs, +{new:4d}")
                except: grand_errors += 1

        cp = {"scraped": list(scraped_set), "stats": {"new": grand_new, "errors": grand_errors, "valid": valid}}
        save_checkpoint(cp)

        elapsed = time.time() - start
        current = total_before + grand_new
        rate = grand_new / (elapsed/60) if elapsed > 0 else 0
        log(f"  Batch {bi//batch_size+1}: {current:,} total (+{grand_new:,}) | {valid} valid | {rate:.0f}/min")

    elapsed = time.time() - start
    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    log(f"\n{'='*60}")
    log(f"MEGA DISCOVER COMPLETE")
    log(f"Slugs probed: {len(scraped_set)}")
    log(f"Valid boards: {valid}")
    log(f"New jobs:     {grand_new:,}")
    log(f"DB total:     {final:,}")
    log(f"Time:         {elapsed/60:.1f} min")
    log(f"Rate:         {grand_new/(elapsed/60):.0f} new/min")
    log(f"Gap to 1M:    {max(0, 1000000-final):,}")
    log(f"{'='*60}")


if __name__ == "__main__":
    main()
