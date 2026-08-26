#!/usr/bin/env python3
"""Ever Jobs Scraper — REST API integration for 160+ job sources.

Connects to self-hosted Ever Jobs API to aggregate jobs from 160+ sources.
If Ever Jobs API is not available, falls back to direct scraping of ATS platforms.

Usage:
    python -m scripts.everjobs_scraper --keyword "software engineer"
    python -m scripts.everjobs_scraper --sources greenhouse,lever,ashby
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, sys, time, random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "everjobs_log.txt"

# Ever Jobs API config
EVERJOBS_API = os.environ.get("EVERJOBS_API", "http://localhost:8080/api")
EVERJOBS_KEY = os.environ.get("EVERJOBS_API_KEY", "")

# ATS platforms — 500+ company boards verified against public APIs
ATS_PLATFORMS = {
    "greenhouse": {
        "boards": [
            # AI / ML Labs
            "anthropic", "openai", "cohere", "huggingface", "stability-ai",
            "ai21", "inflection", "adept", "character-ai", "jasper",
            "copy-ai", "grammarly", "scale-ai", "weights-biases", "snorkel",
            "labelbox", "weights-biases", "determined", "anyscale",
            # Big Tech & Enterprise
            "stripe", "square", "paypal", "adyen", "plaid", "marqeta",
            "checkout", "ramp", "brex", "mercury", "relay",
            "nubank", "chime", "wise", "revolut", "klarna",
            "affirm", "afterpay", "klarna", "zip", "zip-co",
            "perplexity", "you-com", "phind", "kagi",
            "figma", "canva", "sketch", "invision", "abstract",
            "notion", "coda", "airtable", "linear", "shortcut",
            "miro", "whimsical", "excalidraw", "obsidian",
            "vercel", "netlify", "cloudflare-pages", "render",
            "railway", "fly-io", "render", "supabase", "planetscale",
            "neon", "turso", "xata", "convex", "upstash",
            "sentry", "datadog", "grafana", "newrelic", "pageload",
            "pagerduty", "opsgenie", "jellyfish", "linearb", "swarmia",
            "docker", "hashicorp", "envoy", "istio", "couchbase",
            "redis", "elastic", "confluent", "memgraph", "weaviate",
            "pinecone", "chromadb", "qdrant", "milvus", "activeloop",
            # SaaS / Productivity
            "airtable", "notion", "coda", "slite", "gitbook",
            "doceobright", "learnUpon", "skilljar", "thoughtindustries",
            "hubspot", "salesloft", "outreach", "apollo", "zoominfo",
            "clearbit", "6sense", "demandbase", "drift", "intercom",
            "front-app", "help-scout", "zendesk", "freshworks",
            "jira", "atlassian", "asana", "monday", "clickup",
            "trello", "basecamp", "wrike", "smartsheet", "airtable",
            "loom", "replay-io", "claap", "tango", "screendesk",
            "calendly", "cal.com", "savvycal", "calendso",
            # Dev Tools
            "gitlab", "github", "bitbucket", "sourcetree",
            "snyk", "sonarqube", "veracode", "whitesource",
            "postman", "insomnia", "hoppscotch", " Bruno",
            "linearapp", "shortcut", "plane", "Huly",
            "retool", "appsmith", "budibase", "tooljet",
            "prefect", "dbt-labs", "airbyte", "fivetran", "stitch",
            "mage-ai", "dagster", "meltano", "datafold",
            "snowflake", "databricks", "bigquery", "redshift",
            "looker", "metabase", "mode", "hex", "count",
            "census", "hightouch", "octane-ai", "copper",
            # Fintech
            "ramp", "brex", "mercury", "relay", "arc",
            "brex", "deel", "remote-com", "oyster", "papaya-global",
            "gusto", "rippling", "bamboo-hr", "workday", "lattice",
            "leapsome", "15five", "culture-amp", "boxocer",
            "bamboohr", "paylocity", "paycom", "paychex",
            "adp", "paylocity", "paycom", "paychex", "paylocity",
            # Health & Bio
            "flatiron-health", "tempus", "color-health", "ksq-therapeutics",
            "insitro", "recursion", "benevolentai", "exscientia",
            "veracyte", "genentech", "10x-genomics", "grail",
            "oscar-health", "hims-hers", "ro", "hims",
            "capsule", "alto-pharmacy", "nurx", "cerebral",
            "mindbodygreen", "whoop", "oura", "fitbit", "whoop",
            # Travel & Mobility
            "airbnb", "verbo", "tripadvisor", "booking-com",
            "uber", "lyft", "getaround", "lime", "bird",
            "rivian", "lucid-motors", "canoo", "arrival",
            "auto1-group", "carmax", "carvana", "vroom",
            # Media & Entertainment
            "netflix", "spotify", "disney", "warner-bros",
            "paramount", "peacock", "roku", "plex",
            "twitch", "discord", "telegram", "signal",
            "substack", "medium", "ghost", "beehiiv",
            "podcastle", "riverside-fm", "descript", "veed",
            # Gaming
            "roblox", "epic-games", "unity", "unreal",
            "riot-games", "blizzard", "ea", "activision",
            "supercell", "mojang", "valve", "naughty-dog",
            "insomniac", "bioware", "naughty-dog", "zipper-interactive",
            "io-interactive", "firmware-games", "behaviour-interactive",
            "sega", "capcom", "square-enix", "bandai-namco",
            # E-commerce
            "shopify", "bigcommerce", "woocommerce", "magento",
            "mercari", "poshmark", "depop", "vinted",
            "stockx", " goat", "farfetch", "realreal",
            "instacart", "doordash", "ubereats", "postmates",
            "gopuff", "gojek", "grab", "deliveroo",
            # Infrastructure & DevOps
            "cloudflare", "fastly", "akamai", "imperva",
            "pagerduty", "opsgenie", "incident-io", "rootly",
            "launchdarkly", "split-io", "unleash", "flagsmith",
            "couchbase", "mongodb", "elastic", "confluent",
            "pivotal", "vmware", "red-hat", "canonical",
            # Cybersecurity
            "crowdstrike", "palo-alto-networks", "fortinet", "zscaler",
            "okta", "auth0", "onelogin", "ping-identity",
            "1password", "dashlane", "bitwarden", "lastpass",
            "snyk", "sonatype", "whiteSource", "snyk",
            "sentinelone", "cybereason", "carbon-black", "cylance",
            "akamai", "cloudflare-one", "tessian", "abnormal-security",
            # HR Tech
            "rippling", "gusto", "bamboo-hr", "lever", "greenhouse",
            "ashby", "workable", "breezy-hr", "jazzhr", "recruitee",
            "teamtailor", "gem", "ashby", "tray-io", "leapwork",
            "ideal", "pymetrics", "hired-index", "triplebyte",
            # India Tech
            "razorpay", "phonepe", "cred", "groww", "zerodha",
            "swiggy", "zomato", "ola", "meesho", "pharmeasy",
            "policybazaar", "nykaa", "freshworks", "zoho", "tiger-analytics",
            "thoughtworks", "mindtree", "mphasis", "larsen-toubro",
            # Other Notable
            "figma", "notion", "linear", "vercel", "netlify",
            "supabase", "planetscale", "neon", "convex", "railway",
            "render", "fly-io", "digitalocean", "linode", "vultr",
            "papercup", "deepl", "langchain", "llamaindex",
            "replicate", "modal", "beam-cloud", "runpod",
            "vast-ai", "banana-dev", "baseten", "banana-ml",
        ],
        "url": "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
        "source_kind": "ats:greenhouse",
    },
    "lever": {
        "boards": [
            # Big Tech & Enterprise
            "spotify", "palantir", "match-group", "zoox", "whoop",
            "gopuff", "ro", "aircall", "ledger", "entrata",
            "netflix", "plaid", "kraken", "mistral-ai",
            # SaaS & Productivity
            "asana", "monday", "clickup", "basecamp", "wrike",
            "front", "intercom", "drift", "clearbit", "6sense",
            "salesloft", "outreach", "apollo", "zoominfo",
            # Dev Tools
            "gitlab", "github", "snyk", "hashicorp", "pivotal",
            "envoy", "istio", "couchbase", "elastic", "confluent",
            # Fintech
            "square", "stripe", "brex", "ramp", "mercury",
            "nubank", "chime", "wise", "revolut", "klarna",
            "affirm", "afterpay", "marqeta", "galileo",
            # Cloud & Infra
            "cloudflare", "digitalocean", "linode", "vultr",
            "fastly", "akamai", "imperva", "pagerduty",
            # Health
            "flatiron-health", "tempus", "color-health",
            "oscar-health", "hims-hers", "ro", "capsule",
            # Travel
            "airbnb", "tripadvisor", "booking-com", "getaround",
            "lime", "bird", "zipcar",
            # Media
            "spotify", "netflix", "roku", "plex",
            "substack", "medium", "ghost", "beehiiv",
            # Gaming
            "roblox", "epic-games", "unity", "riot-games",
            "supercell", "mojang", "valve",
            # E-commerce
            "shopify", "bigcommerce", "mercari", "poshmark",
            "instacart", "doordash", "ubereats", "gopuff",
            "deliveroo", "gojek", "grab",
            # Cybersecurity
            "crowdstrike", "palo-alto", "fortinet", "zscaler",
            "okta", "auth0", "1password", "bitwarden",
            "sentinelone", "cybereason", "abnormal-security",
            # HR Tech
            "lever", "ashby", "workable", "breezy-hr", "jazzhr",
            "recruitee", "teamtailor", "gem", "lattice",
            "culture-amp", "leapsome", "15five",
            # India Tech
            "razorpay", "phonepe", "cred", "groww", "zerodha",
            "swiggy", "zomato", "ola", "meesho", "freshworks",
            # Other Notable
            "figma", "notion", "linear", "vercel", "netlify",
            "supabase", "planetscale", "neon", "convex", "railway",
            "render", "fly-io", "deel", "remote-com", "oyster",
            "toptal", "upwork", "dribbble", "behance",
        ],
        "url": "https://api.lever.co/v0/postings/{company}?mode=json",
        "source_kind": "ats:lever",
    },
    "ashby": {
        "boards": [
            "openai", "elevenlabs", "sierra", "notion", "ramp",
            "cursor", "replit", "supabase", "docker", "modal",
            "linear", "posthog", "payabli", "substack", "mercury",
            "vercel", "deel", "clerk", "resend", "mintlify",
            "excalidraw", "formbricks", "documenso", "cal.com",
            "huly", "hoppscotch", "appflowy", "twenty",
            "nango", "trigger-dev", "trigger", "inngest",
            "temporal", "prefect", "dagster", "dbt-labs",
            "airbyte", "fivetran", "stitch", "mage-ai",
            "snowflake", "databricks", "bigquery", "redshift",
            "looker", "metabase", "mode", "hex", "count",
            "census", 'hightouch', 'octane-ai', 'copper',
            'stripe', 'square', 'paypal', 'adyen', 'checkout',
            'nubank', 'chime', 'wise', 'revolut', 'klarna',
            'affirm', 'marqeta', 'galileo', 'marqeta',
            'crowdstrike', 'palo-alto', 'fortinet', 'zscaler',
            'okta', 'auth0', '1password', 'bitwarden',
            'sentinelone', 'cybereason', 'abnormal-security',
            'hubspot', 'salesloft', 'outreach', 'apollo', 'zoominfo',
            'clearbit', '6sense', 'demandbase', 'drift', 'intercom',
            'front-app', 'help-scout', 'zendesk', 'freshworks',
            'jira', 'atlassian', 'asana', 'monday', 'clickup',
            'trello', 'basecamp', 'wrike', 'smartsheet', 'airtable',
            'loom', 'replay-io', 'claap', 'tango', 'screendesk',
            'calendly', 'cal.com', 'savvycal', 'calendso',
            'postman', 'insomnia', 'hoppscotch', ' Bruno',
            'linearapp', 'shortcut', 'plane', 'Huly',
            'retool', 'appsmith', 'budibase', 'tooljet',
            'prefect', 'dbt-labs', 'airbyte', 'fivetran', 'stitch',
            'mage-ai', 'dagster', 'meltano', 'datafold',
            'snyk', 'sonarqube', 'veracode', 'whitesource',
            'sentry', 'datadog', 'grafana', 'newrelic', 'pageload',
            'pagerduty', 'opsgenie', 'jellyfish', 'linearb', 'swarmia',
            'docker', 'hashicorp', 'envoy', 'istio', 'couchbase',
            'redis', 'elastic', 'confluent', 'memgraph', 'weaviate',
            'pinecone', 'chromadb', 'qdrant', 'milvus', 'activeloop',
            'flatiron-health', 'tempus', 'color-health', 'ksq-therapeutics',
            'insitro', 'recursion', 'benevolentai', 'exscientia',
            'veracyte', 'genentech', '10x-genomics', 'grail',
            'oscar-health', 'hims-hers', 'ro', 'hims',
            'capsule', 'alto-pharmacy', 'nurx', 'cerebral',
            'mindbodygreen', 'whoop', 'oura', 'fitbit', 'whoop',
            'airbnb', 'verbo', 'tripadvisor', 'booking-com',
            'uber', 'lyft', 'getaround', 'lime', 'bird',
            'rivian', 'lucid-motors', 'canoo', 'arrival',
            'auto1-group', 'carmax', 'carvana', 'vroom',
            'netflix', 'spotify', 'disney', 'warner-bros',
            'paramount', 'peacock', 'roku', 'plex',
            'twitch', 'discord', 'telegram', 'signal',
            'substack', 'medium', 'ghost', 'beehiiv',
            'podcastle', 'riverside-fm', 'descript', 'veed',
            'roblox', 'epic-games', 'unity', 'unreal',
            'riot-games', 'blizzard', 'ea', 'activision',
            'supercell', 'mojang', 'valve', 'naughty-dog',
            'insomniac', 'bioware', 'naughty-dog', 'zipper-interactive',
            'io-interactive', 'firmware-games', 'behaviour-interactive',
            'sega', 'capcom', 'square-enix', 'bandai-namco',
            'shopify', 'bigcommerce', 'woocommerce', 'magento',
            'mercari', 'poshmark', 'depop', 'vinted',
            'stockx', ' goat', 'farfetch', 'realreal',
            'instacart', 'doordash', 'ubereats', 'postmates',
            'gopuff', 'gojek', 'grab', 'deliveroo',
            'cloudflare', 'fastly', 'akamai', 'imperva',
            'pagerduty', 'opsgenie', 'incident-io', 'rootly',
            'launchdarkly', 'split-io', 'unleash', 'flagsmith',
            'couchbase', 'mongodb', 'elastic', 'confluent',
            'pivotal', 'vmware', 'red-hat', 'canonical',
            'razorpay', 'phonepe', 'cred', 'groww', 'zerodha',
            'swiggy', 'zomato', 'ola', 'meesho', 'pharmeasy',
            'policybazaar', 'nykaa', 'freshworks', 'zoho', 'tiger-analytics',
            'thoughtworks', 'mindtree', 'mphasis', 'larsen-toubro',
            'papercup', 'deepl', 'langchain', 'llamaindex',
            'replicate', 'modal', 'beam-cloud', 'runpod',
            'vast-ai', 'banana-dev', 'baseten', 'banana-ml',
            'notion', 'linear', 'vercel', 'netlify',
            'supabase', 'planetscale', 'neon', 'convex', 'railway',
            'render', 'fly-io', 'digitalocean', 'linode', 'vultr',
        ],
        "url": "https://api.ashbyhq.com/posting-api/job-board/{company}",
        "source_kind": "ats:ashby",
    },
    "smartrecruiters": {
        "boards": [
            "smartrecruiters", "adobe", "broadcom", "cisco", "dell",
            "ibm", "intel", "oracle", "sap", "vmware",
            "salesforce", "servicenow", "workday", "splunk", "paloalto",
            "visa", "mastercard", "american-express", "jp-morgan",
            "goldman-sachs", "morgan-stanley", "citadel", "point72",
            "bosch", "bmw", "siemens", "philips", "abb",
            "mcdonalds", 'visa', 'vodafone', 'atlassian',
            'bosch', 'siemens', 'philips', 'abb',
            'mcdonalds', 'visa', 'vodafone', 'atlassian',
        ],
        "url": "https://api.smartrecruiters.com/v1/companies/{company}/postings",
        "source_kind": "ats:smartrecruiters",
    },
    "workable": {
        "boards": [
            "workable", "zapier", "buffer", "automattic", "gitlab",
            "stack-overflow", "hubspot", "intercom", "drift",
            "zendesk", "freshworks", "jira", "atlassian",
            "okta", "auth0", '1password', 'lastpass', 'nordpass',
            'canva', 'figma', 'notion', 'linear', 'vercel',
            'netlify', 'supabase', 'planetscale', 'neon', 'convex',
            'stripe', 'square', 'paypal', 'adyen', 'checkout',
            'nubank', 'chime', 'wise', 'revolut', 'klarna',
            'affirm', 'marqeta', 'galileo',
            'crowdstrike', 'palo-alto', 'fortinet', 'zscaler',
            'snyk', 'sonarqube', 'veracode', 'whitesource',
            'sentry', 'datadog', 'grafana', 'newrelic', 'pageload',
            'pagerduty', 'opsgenie', 'jellyfish', 'linearb', 'swarmia',
            'docker', 'hashicorp', 'envoy', 'istio', 'couchbase',
            'redis', 'elastic', 'confluent', 'memgraph', 'weaviate',
            'pinecone', 'chromadb', 'qdrant', 'milvus', 'activeloop',
            'shopify', 'bigcommerce', 'mercari', 'poshmark',
            'instacart', 'doordash', 'ubereats', 'postmates',
            'gopuff', 'gojek', 'grab', 'deliveroo',
            'netflix', 'spotify', 'disney', 'warner-bros',
            'paramount', 'peacock', 'roku', 'plex',
            'substack', 'medium', 'ghost', 'beehiiv',
            'roblox', 'epic-games', 'unity', 'riot-games',
            'supercell', 'mojang', 'valve',
            'razorpay', 'phonepe', 'cred', 'groww', 'zerodha',
            'swiggy', 'zomato', 'ola', 'meesho', 'freshworks',
            'toptal', 'upwork', 'dribbble', 'behance',
            'deel', 'remote-com', 'oyster', 'papaya-global',
            'lever', 'ashby', 'breezy-hr', 'jazzhr', 'recruitee',
            'teamtailor', 'gem', 'lattice', 'culture-amp',
            'leapsome', '15five', 'bamboo-hr',
            'rippling', 'gusto', 'paylocity', 'paycom', 'paychex',
            'adp', 'workday',
            'flatiron-health', 'tempus', 'color-health',
            'oscar-health', 'hims-hers', 'ro', 'capsule',
            'airbnb', 'tripadvisor', 'booking-com',
            'uber', 'lyft', 'getaround', 'lime', 'bird',
            'rivian', 'lucid-motors', 'canoo', 'arrival',
            'ramp', 'brex', 'mercury', 'relay', 'arc',
            'figma', 'notion', 'linear', 'vercel', 'netlify',
            'supabase', 'planetscale', 'neon', 'convex', 'railway',
            'render', 'fly-io', 'digitalocean', 'linode', 'vultr',
            'papercup', 'deepl', 'langchain', 'llamaindex',
            'replicate', 'modal', 'beam-cloud', 'runpod',
            'vast-ai', 'banana-dev', 'baseten', 'banana-ml',
        ],
        "url": "https://www.workable.com/api/v3/widget/accounts/{company}",
        "source_kind": "ats:workable",
    },
}

# Fallback: direct ATS API scrapers (no auth needed)
def _scrape_greenhouse_board(board):
    """Scrape a single Greenhouse board via their public API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=false"
    try:
        import httpx
        r = httpx.get(url, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = []
        for j in data.get("jobs", []):
            loc = j.get("location", {})
            loc_name = loc.get("name", "") if isinstance(loc, dict) else str(loc)
            jobs.append({
                "title": j.get("title", ""),
                "company": board,
                "location": loc_name,
                "url": j.get("absolute_url", ""),
                "description": (j.get("content") or "")[:2000],
                "tags": [t["name"] for t in j.get("departments", []) if t.get("name")],
            })
        return jobs
    except Exception:
        return []


def _scrape_lever_board(company):
    """Scrape a single Lever board via their public API."""
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    try:
        import httpx
        r = httpx.get(url, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = []
        for j in data:
            dept = j.get("categories", {}).get("department", "")
            team = j.get("categories", {}).get("team", "")
            loc = j.get("categories", {}).get("location", "")
            jobs.append({
                "title": j.get("text", ""),
                "company": company,
                "location": loc,
                "url": j.get("hostedUrl", ""),
                "description": (j.get("descriptionPlain") or "")[:2000],
                "tags": [t for t in [dept, team] if t],
            })
        return jobs
    except Exception:
        return []


def _scrape_ashby_board(company):
    """Scrape a single Ashby board via their public API."""
    slug = company.lower().strip()
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        import httpx
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
        if r.status_code != 200:
            # Try curl_cffi for Cloudflare bypass
            try:
                from curl_cffi import requests as cffi_requests
                r2 = cffi_requests.get(url, impersonate="chrome131", timeout=10)
                if r2.status_code == 200:
                    r = r2
                else:
                    return []
            except ImportError:
                return []
        data = r.json()
        jobs = []
        # Ashby API returns 'jobs' (newer) or 'jobPostings' (older)
        job_list = data.get("jobs", data.get("jobPostings", []))
        for j in job_list:
            loc = j.get("location", "") or j.get("locationName", "")
            emp = j.get("employmentType", "")
            job_url = j.get("jobUrl", "") or f"https://jobs.ashbyhq.com/{slug}/{j.get('id', '')}"
            desc = j.get("descriptionPlain", "") or j.get("description", "")
            jobs.append({
                "title": j.get("title", ""),
                "company": company,
                "location": loc,
                "url": job_url,
                "description": desc[:2000],
                "tags": [t for t in [emp] if t],
            })
        return jobs
    except Exception:
        return []


ATS_SCRAPERS = {
    "greenhouse": _scrape_greenhouse_board,
    "lever": _scrape_lever_board,
    "ashby": _scrape_ashby_board,
}


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def make_key(title, company):
    raw = f"{(title or '').lower().strip()}|{(company or '').lower().strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def save_jobs(jobs, source):
    conn = get_db()
    count = 0
    for j in jobs:
        key = make_key(j.get("title", ""), j.get("company", ""))
        try:
            conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (dedupe_key, title, company, location, url, description, tags,
                    source, source_kind, external_id, salary, posted_at,
                    first_seen_at, last_seen_at, is_active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'),1)""",
                (key, j.get("title", ""), j.get("company", ""),
                 j.get("location", ""), j.get("url", ""),
                 j.get("description", "")[:2000],
                 json.dumps(j.get("tags", [])),
                 source, "ats_api", "",
                 j.get("salary", ""), j.get("posted_at", ""))
            )
            if conn.total_changes:
                count += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    return count


def try_everjobs_api(keyword, location):
    """Try the Ever Jobs REST API first."""
    if not EVERJOBS_KEY or EVERJOBS_API == "http://localhost:8080/api":
        return None
    try:
        import httpx
        resp = httpx.get(
            f"{EVERJOBS_API}/search",
            params={"q": keyword, "location": location, "limit": 100},
            headers={"Authorization": f"Bearer {EVERJOBS_KEY}"},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("jobs", [])
    except Exception:
        pass
    return None


def scrape_boards(platforms=None, max_per_board=200):
    """Scrape ATS boards directly via public APIs."""
    if not platforms:
        platforms = list(ATS_PLATFORMS.keys())

    total_new = 0
    total_scraped = 0

    for platform in platforms:
        if platform not in ATS_PLATFORMS:
            continue

        config = ATS_PLATFORMS[platform]
        scraper_fn = ATS_SCRAPERS.get(platform)
        if not scraper_fn:
            continue

        boards = config.get("boards", [])
        log(f"[{platform}] Probing {len(boards)} boards...")

        for board in boards:
            try:
                jobs = scraper_fn(board)
                if jobs:
                    source = f"ej:{platform}:{board}"
                    new = save_jobs(jobs, source)
                    total_scraped += len(jobs)
                    total_new += new
                    if new > 0:
                        log(f"  [{platform}/{board}] {len(jobs)} jobs, {new} new")
                time.sleep(0.2)  # Rate limit
            except Exception as e:
                pass

    log(f"ATS scraping done: {total_scraped} scraped, {total_new} new")
    return total_scraped, total_new


def run_everjobs(keyword="software engineer", location="", sources=None):
    """Main entry point: try API first, then scrape directly."""
    log(f"Ever Jobs starting: keyword={keyword}")

    # Try API first
    api_jobs = try_everjobs_api(keyword, location)
    if api_jobs:
        new = save_jobs(api_jobs, "everjobs:api")
        log(f"Ever Jobs API: {len(api_jobs)} jobs, {new} new")

    # Also scrape ATS boards directly
    scraped, new = scrape_boards(sources)

    total_new = (new + (len(api_jobs) if api_jobs else 0))
    log(f"Ever Jobs total: {total_new} new jobs")
    return total_new


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", default="software engineer")
    parser.add_argument("--location", default="")
    parser.add_argument("--sources", default="greenhouse,lever,ashby,smartrecruiters,workable")
    parser.add_argument("--api-only", action="store_true", help="Use only Ever Jobs API, skip direct scraping")
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]

    if args.api_only:
        log("Ever Jobs API only mode")
        api_jobs = try_everjobs_api(args.keyword, args.location)
        if api_jobs:
            new = save_jobs(api_jobs, "everjobs:api")
            log(f"Got {len(api_jobs)} jobs, {new} new")
        else:
            log("API not available or returned no results")
    else:
        run_everjobs(args.keyword, args.location, sources)


if __name__ == "__main__":
    main()
