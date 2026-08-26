#!/usr/bin/env python3
"""Discover thousands of valid ATS company boards by probing slugs against APIs.

Strategy:
1. Generate slugs from known company name lists (Fortune 500, Forbes, unicorns, etc.)
2. Probe each slug against Greenhouse, Lever, Ashby, SmartRecruiters APIs
3. Save valid boards to companies.yaml
"""
from __future__ import annotations
import httpx, json, sys, time, threading, yaml
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
YAML_PATH = ROOT / "companies.yaml"
CP_PATH = ROOT / "ats_discover_cp.json"

_lock = threading.Lock()
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ═══════════════════════════════════════════════════════════════
# COMPANY SLUGS — thousands of known companies
# ═══════════════════════════════════════════════════════════════

GREENHOUSE_SLUGS = [
    # FAANG + Big Tech
    "google", "meta", "facebook", "apple", "microsoft", "amazon", "netflix",
    "tesla", "spacex", "blueorigin", "nvidia", "intel", "amd", "qualcomm",
    # Unicorns & High-Growth
    "stripe", "databricks", "figma", "canva", "notion", "airtable",
    "ramp", "brex", "mercury", "plaid", "rippling", "scaleai",
    "anthropic", "openai", "cohere", "huggingface", "mistral",
    "vercel", "netlify", "cloudflare", "fastly", "akamai",
    "gitlab", "github", "bitbucket", "atlassian", "jira", "confluence",
    "snowflake", "confluent", "elastic", "mongodb", "redis", "datadog",
    "newrelic", "sentry", "pagerduty", "twilio", "sendgrid",
    "shopify", "bigcommerce", "squarespace", "wix", "webflow",
    "slack", "zoom", "teams", "discord", "figma", "miro",
    "uber", "lyft", "doordash", "instacart", "postmates", "grubhub",
    "airbnb", "booking", "expedia", "tripadvisor", "vrbo",
    "palantir", "crowdstrike", "paloaltonetworks", "fortinet", "zscaler",
    "servicenow", "workday", "salesforce", "hubspot", "zoho",
    "bytedance", "tiktok", "snap", "pinterest", "reddit", "twitter",
    "linkedin", "instagram", "whatsapp",
    "robinhood", "coinbase", "kraken", "binance", "ftx",
    "affirm", "klarna", "afterpay", "zip", "sezzle",
    "databricks", "c3ai", "palantir", "splunk", "sumologic",
    "mongodb", "couchbase", "cassandra", "neo4j",
    "hashicorp", "pulumi", "docker", "suse", "redhat",
    "nvidia", "amd", "arm", "riscv", "broadcom",
    # Consulting & Enterprise
    "mckinsey", "bain", "bcg", "deloitte", "accenture", "capgemini",
    "ibm", "cognizant", "infosys", "wipro", "hcl", "techmahindra",
    "cisco", "juniper", "arista", "paloaltonetworks",
    # Gaming
    "epicgames", "riotgames", "activision", "blizzard", "ea",
    "ubisoft", "rockstar", "valve", "supercell", "krafton",
    "unity", "unreal", "roblox",
    # AI / ML
    "scaleai", "labelbox", "snorkel", "weightsbiases",
    "Weights & Biases", "anyscale", "modal", "replicate",
    "togetherai", "fireworksai", "deepinfra",
    "runway", "stabilityai", "midjourney",
    "characterai", "inflection", "adept",
    # Fintech
    "square", "block", "paypal", "venmo", "cashapp",
    "wise", "revolut", "n26", "monzo", "chime",
    "sofi", "marqeta", "galileo", "memberful",
    "celo", "circle", "chainalysis",
    # Healthtech
    "veracyte", "tempus", "graviti", "owkin",
    "haven", "cloverhealth", "hims", "one medical", "teladoc",
    "oxford nanopore", "pacbio", "10x genomics",
    # Edtech
    "duolingo", "coursera", "udemy", "byjus", "unacademy",
    "pluralsight", "codecademy", "datacamp",
    # E-commerce
    "shopify", "woocomerce", "magento", "commercetools",
    "checkout.com", "adyen", "braintree",
    # Logistics
    "flexport", "project44", "fourkites", "linx",
    # Space & Defense
    "spacex", "blueorigin", "relativityspace", "rocketlab",
    "palantir", "anduril", "shieldai", "skydio",
    # Media & Entertainment
    "spotify", "pandora", "soundcloud", "deezer",
    "hulu", "disney+", "hbomax", "peacock", "paramount",
    "youtube", "twitch", "vimeo",
    # Robotics & Autonomous
    "waymo", "cruise", "zoox", "nuro", "argorobotics",
    "bostondynamics", "fetchrobotics", "universalrobots",
    "luminar", "velodyne", "lidar", "aeon",
    # Climate & Energy
    "tesla", "rivian", "lucid", "nio", "xpeng", "byd",
    "sunrun", "sunpower", "enphase", "generac",
    "formenergy", "quantumscape", "sesai",
    # Biotech
    "genentech", "amgen", "gilead", "regeneron", "moderna",
    "biontech", "crispr", "edithcowan",
    # Real Estate
    "opendoor", "redfin", "zillow", "compass", "houzz",
    "procore", "plangrid",
    # Security
    "crowdstrike", "sentinelone", "carbonblack",
    "auth0", "okta", "onelogin", "forgerock",
    "1password", "lastpass", "dashlane",
    # Developer Tools
    "gitlab", "bitbucket", "circleci", "travisci",
    "vercel", "netlify", "heroku", "render",
    "postman", "insomnia", "hoppscotch",
    "grafana", "prometheus", "chronograf",
    "segment", "mparticle", "amplitude",
    "mixpanel", "heap", "fullstory",
    # Indian Tech
    "flipkart", "swiggy", "zomato", "paytm", "phonepe",
    "razorpay", "cred", "groww", "zerodha", "upstox",
    "ola", "meesho", "byju", "unacademy", "delhivery",
    "freshworks", "zoho", "tcs", "infosys", "wipro",
    "hcltech", "techmahindra", "mindtree", "ltimindtree",
    "mphasis", "persistent", "cyient", "sonata",
    "postman", "zenefits", "ninjacart", "meesho",
    "pritamdebnath", "apna", "brightchamps",
    # More well-known companies
    "akamai", "fastly", "stackpath", "limelight",
    "zendesk", "intercom", "drift", "hubspot",
    "atlassian", "monday", "asana", "clickup",
    "trello", "jira", "confluence", "notion",
    "figma", "sketch", "invision", "zeplin",
    "abstract", "abstracta",
    "launchdarkly", "split", "optimizely",
    "segment", "amplitude", "mixpanel",
    "algolia", "meilisearch", "typesense",
    "prisma", "hasura", "supabase",
    "planetscale", "tidb", "cockroachdb",
    "neon", "xata", "turso",
    "upstash", "keydb", "dragonfly",
    "temporal", "cadence", "conductor",
    "nats", "pulsar", "redpanda",
    "vector", "fluentd", "logstash",
    "jaeger", "zipkin", "opentelemetry",
    "harbor", "quay", "ecr",
    "consul", "vault", "nomad",
    "envoy", "istio", "linkerd",
    "argocd", "flux", "tekton",
    "backstage", "port", "cortex",
    "snyk", "sonar", "checkmarx",
    "veracode", "whitesource", "mend",
    "vercel", "netlify", "cloudflare",
    "flyio", "railway", "render",
    "deno", "bun", "cloudflare workers",
    "mongodb", "couchbase", "fauna",
    "neon", "supabase", "planetscale",
    "timescale", "questdb", "influxdb",
    "clickhouse", "starburst", "trino",
    "dbt", "airbyte", "fivetran",
    "metabase", "superset", "redash",
    "jupyter", "hex", "mode",
    "observable", "dagster", "prefect",
    "meltano", "snowplow", "segment",
    "amplitude", "mixpanel", "heap",
]

LEVER_SLUGS = [
    "netflix", "shopify", "quizlet", "databricks", "gitlab",
    "zapier", "postman", "calm", "robinhood", "notion",
    "lattice", "rippling", "fivetran", "vercel", "figma",
    "miro", "webflow", "gusto", "greenhouse", "lever",
    "upstart", "affirm", "mercury", "brex", "chime",
    "ruan", "square", "checkout", "klarna",
    "wish", "coupang", "rappi", "mercadolibre",
    "gojek", "grab", "sea", "shopee",
    "bytedance", "tiktok", "snap", "pinterest",
    "reddit", "discord", "twitch",
    "waymo", "cruise", "nuro", "zoox",
    "peloton", "whoop", "oura", "noom",
    "modern treasury", "airbase", "tipalti",
    "codeclimate", "circleci", "jfrog",
    "snyk", "checkmarx", "veracode",
    "launchdarkly", "split", "optimizely",
    "segment", "mparticle", "mParticle",
    "kong", "tyk", "postman",
    "crawl", "fiscalnote", "nuro",
    "gopuff", "instacart", "doordash",
    "citrusad", "spring", "quora",
    "reddit", "pinterest", "snap",
    "oculus", "magic leap", "varjo",
    "niantic", "supercell", "ea",
    "unity", "roblox", "epicgames",
    "plaid", "marqeta", "galileo",
    "oscar", "clover", "devoted",
    "bicycle", "pillar", "hims",
    "rome", "tessera", "teladoc",
]

ASHBY_SLUGS = [
    "ramp", "vercel", "notion", "linear", "retool",
    "supabase", "planetscale", "resend", "cal.com",
    "tailwind", "tailwindcss", "lemonsqueezy",
    "gitbutler", "zed", "inngest", "trigger.dev",
    "highstorm", "upstash", "snipitz",
    "dub", "dubco", "calcom", "calendso",
    "hoppscoty", "documenso", "infisical",
    "formbricks", "twenty", "twentycrm",
    "chatwoot", "metabase", "n8n",
    "appwrite", "firebase", "pocketbase",
    "langua", "perplexityai", "mistral",
    "photoroom", "jasper", "copy.ai",
    "writesonic", "regal", "instantly",
    "instantlyai", "smartlead", "apollo",
    "clay", "ramp", "mercury",
]

SMARTRECRUITERS_SLUGS = [
    "google", "microsoft", "adobe", "booking",
    "indeed", "visa", "mastercard", "sap",
    "siemens", "bosch", "philips",
    "cognizant", "infosys", "wipro",
    "deloitte", "pwc", "ey", "kpmg",
    "hsbc", "barclays", "jpmorgan",
    "goldmansachs", "morganstanley",
    "unilever", "nestle", "pepsi", "cocacola",
    "ikea", "h&m", "zara",
    "nokia", "ericsson", "samsung",
    "sony", "panasonic", "toshiba",
    "fujitsu", "nec", "hitachi",
]

WORKDAY_SLUGS = [
    "apple", "google", "microsoft", "amazon",
    "walmart", "target", "costco", "home depot",
    "goldmansachs", "jpmorgan", "morganstanley",
    "dell", "hp", "cisco", "oracle",
    "pepsico", "cocacola", "procter", "unilever",
    "johnson", "pfizer", "merck", "abbvie",
    "ge", "siemens", "bosch", "abb",
    "ubs", "credit", "deutsche",
    "bp", "shell", "exxon", "chevron",
]

TEAMTAILOR_SLUGS = [
    "volvo", "hasselblad", "klarna", "spotify",
    "king", "mojang", "dice", "mtg",
    "ericsson", "telia", "telenor",
    "ica", "coop", "icaab",
]

def generate_all_slugs():
    """Generate company slugs with variations."""
    all_greenhouse = list(GREENHOUSE_SLUGS)
    # Add lowercase/no-space variations
    extra = []
    for s in GREENHOUSE_SLUGS:
        clean = s.lower().replace(" ", "").replace(".", "").replace("'", "").replace("&", "and")
        if clean != s:
            extra.append(clean)
    all_greenhouse.extend(extra)

    return {
        "greenhouse": list(set(all_greenhouse)),
        "lever": list(set(LEVER_SLUGS)),
        "ashby": list(set(ASHBY_SLUGS)),
        "smartrecruiters": list(set(SMARTRECRUITERS_SLUGS)),
        "workday": list(set(WORKDAY_SLUGS)),
        "teamtailor": list(set(TEAMTAILOR_SLUGS)),
    }

def probe_greenhouse(client, slug):
    """Probe if a Greenhouse board exists and has jobs."""
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        resp = client.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobs", [])
            return len(jobs) > 0, len(jobs)
        return False, 0
    except:
        return False, 0

def probe_lever(client, slug):
    """Probe if a Lever board exists and has jobs."""
    try:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        resp = client.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return isinstance(data, list) and len(data) > 0, len(data) if isinstance(data, list) else 0
        return False, 0
    except:
        return False, 0

def probe_ashby(client, slug):
    """Probe if an Ashby board exists."""
    try:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        resp = client.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobPostings", [])
            return len(jobs) > 0, len(jobs)
        return False, 0
    except:
        return False, 0

def probe_smartrecruiters(client, slug):
    """Probe SmartRecruiters."""
    try:
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1"
        resp = client.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            total = data.get("totalFound", 0)
            return total > 0, total
        return False, 0
    except:
        return False, 0

def main():
    log("=== ATS Board Discovery ===")
    all_slugs = generate_all_slugs()
    total_slugs = sum(len(v) for v in all_slugs.values())
    log(f"Total slugs to probe: {total_slugs:,}")

    # Load checkpoint
    found = {"greenhouse": [], "lever": [], "ashby": [], "smartrecruiters": [],
             "teamtailor": [], "workday": [], "bamboohr": []}
    done = set()
    if CP_PATH.exists():
        try:
            cp = json.loads(CP_PATH.read_text("utf-8"))
            found = cp.get("found", found)
            done = set(cp.get("done", []))
        except: pass

    def save_cp():
        CP_PATH.write_text(json.dumps({"found": found, "done": list(done)}, indent=1), "utf-8")

    client = httpx.Client(timeout=15, follow_redirects=True)
    probed = 0
    start = time.time()

    probes = {
        "greenhouse": probe_greenhouse,
        "lever": probe_lever,
        "ashby": probe_ashby,
        "smartrecruiters": probe_smartrecruiters,
    }

    for ats_type, slugs in all_slugs.items():
        if ats_type not in probes:
            continue
        probe_fn = probes[ats_type]
        log(f"Probing {ats_type}: {len(slugs)} slugs...")

        for slug in slugs:
            key = f"{ats_type}:{slug}"
            if key in done:
                continue
            done.add(key)

            ok, count = probe_fn(client, slug)
            probed += 1
            if ok:
                found[ats_type].append(slug)
                log(f"  FOUND: {ats_type}/{slug} ({count} jobs)")

            if probed % 50 == 0:
                save_cp()
                elapsed = (time.time() - start) / 60
                total_found = sum(len(v) for v in found.values())
                log(f"  Progress: {probed}/{total_slugs} probed, {total_found} found, {elapsed:.1f}min")

            time.sleep(0.1)  # Be polite

    client.close()
    save_cp()

    total_found = sum(len(v) for v in found.values())
    log(f"=== DISCOVERY COMPLETE ===")
    log(f"Probed: {probed:,} | Found: {total_found:,}")
    for ats_type, slugs in found.items():
        if slugs:
            log(f"  {ats_type}: {len(slugs)}")

    # Update companies.yaml
    log("Updating companies.yaml...")
    update_yaml(found)
    log("Done!")

def update_yaml(found):
    """Add discovered boards to companies.yaml."""
    if not YAML_PATH.exists():
        log("companies.yaml not found!")
        return

    content = YAML_PATH.read_text("utf-8")

    for ats_type, slugs in found.items():
        if not slugs:
            continue

        # Check if section exists
        section_header = f"{ats_type}:"
        if section_header in content:
            # Get existing slugs in section
            lines = content.split("\n")
            existing = set()
            in_section = False
            for line in lines:
                stripped = line.strip()
                if stripped == section_header:
                    in_section = True
                    continue
                if in_section and stripped.startswith("- "):
                    slug = stripped[2:].strip()
                    if slug and not slug.startswith(" "):
                        existing.add(slug)
                    else:
                        in_section = False
                elif in_section and stripped and not stripped.startswith(" ") and ":" in stripped:
                    in_section = False

            # Add new slugs
            new_slugs = [s for s in slugs if s not in existing]
            if new_slugs:
                # Find the section and append
                new_content = []
                in_section = False
                added = False
                for line in lines:
                    stripped = line.strip()
                    if stripped == section_header:
                        in_section = True
                        new_content.append(line)
                        continue
                    if in_section and not added:
                        # Check if we've left the section
                        if stripped and not stripped.startswith("- ") and ":" in stripped:
                            # Add before this new section
                            for s in sorted(new_slugs):
                                new_content.append(f"- {s}")
                            added = True
                            in_section = False
                    new_content.append(line)

                if not added and in_section:
                    for s in sorted(new_slugs):
                        new_content.append(f"- {s}")

                content = "\n".join(new_content)
                log(f"  Added {len(new_slugs)} new {ats_type} companies")
        else:
            # Create new section
            content += f"\n{section_header}\n"
            for s in sorted(slugs):
                content += f"- {s}\n"
            log(f"  Created {ats_type} section with {len(slugs)} companies")

    YAML_PATH.write_text(content, "utf-8")

if __name__ == "__main__":
    main()
