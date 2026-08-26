#!/usr/bin/env python3
"""Mega stress test — collect 1M jobs in 7 days.

Strategy:
  1. Parallel ATS APIs — 2000+ companies across Greenhouse/Lever/Ashby/SmartRecruiters/Workable
     Each company gives 100-400 unique jobs, ZERO dedup between companies
  2. JobSpy parallel — LinkedIn+Indeed with diverse keywords
  3. Surf browser — apna, Shine, LinkedIn, Indeed India

To reach 1M fresh in 7 days we need ~143K/day.
ATS is the real lever: 2000 companies x 200 avg = 400K unique jobs.
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
CP_FILE = ROOT / ".freebuff" / "mega_checkpoint.json"
LOG_FILE = ROOT / ".freebuff" / "mega_stress.log"

DB_LOCK = Lock()

# ════════════════════════════════════════════════════════════════
# MASSIVE company lists per ATS platform
# ════════════════════════════════════════════════════════════════

# Greenhouse companies (1000+ — most popular ATS)
GREENHOUSE_COMPANIES = [
    # FAANG+MAANG
    "stripe", "spotify", "airbnb", "twitter", "square", "lyft", "uber",
    "coinbase", "instacart", "doordash", "pinterest", "snap", "reddit",
    "discord", "figma", "notion", "canva", "gitlab", "github",
    # Big Tech
    "anthropic", "openai", "xai", "databricks", "datadog", "snowflake",
    "cloudflare", "fastly", "vercel", "netlify", "elastic", "mongodb",
    "redis", "cockroachlabs", "planetscale", "neon", "supabase",
    # Enterprise SaaS
    "okta", "salesforce", "workday", "servicenow", "palantir",
    "zoom", "dropbox", "box", "atlassian", "twilio", "sendgrid",
    "hubspot", "marketo", "pardot", "drift", "intercom",
    # Fintech
    "brex", "ramp", "chime", "sofi", "affirm", "klarna", "nubank",
    "revolut", "monzo", "n26", "wise", "mercury", "relay",
    "marqeta", "marqeta", "galileo", "plaid", "stripe",
    # Cybersecurity
    "crowdstrike", "paloaltonetworks", "zscaler", "beyondtrust",
    " RecordedFuture", "abnormalsecurity", "huntress", "sentinelone",
    "snyk", "veracode", "cybereason",
    # AI/ML
    "scaleai", "snorkelai", "togetherai", "stabilityai", "inflectionai",
    "assemblyai", "heptabase", "jasper", "copy.ai", "runway",
    # Health/Biotech
    "flatironhealth", "cloverhealth", "hims", "forward", "one medical",
    "strava", "oura", "whoop", "peloton",
    # Gaming
    "epicgames", "riotgames", "roblox", "unity", "activision",
    "supercell", "krafton", "taketwo", "rockstargames",
    # E-commerce/Logistics
    "shopify", "etsy", "ebay", "wayfair", "bonobos", "allbirds",
    "flexport", "project44", "convoy", "rex",
    # Travel/Hospitality
    "booking", "tripadvisor", "hilton", "marriott", "airbnb",
    "kayak", "skyscanner", "omio", "getyourguide",
    # Content/Media
    "netflix", "disney", "warner", "spotify", "audible",
    "medium", "substack", "ghost", "buzzfeed", "vice",
    # Dev tools
    "gitlab", "bitbucket", "jira", "linear", "height",
    "postman", "swagger", "sentry", "newrelic", "pagerduty",
    "launchdarkly", "split", "optimizely", "amplitude", "mixpanel",
    "segment", "heap", "fullstory", "hotjar",
    # Remote-first
    "buffer", "zapier", "helpscout", "basecamp", "37signals",
    "automattic", "gitlab", "toptal", "upal", "baserow",
    # India-based
    "razorpay", "phonepe", "groww", "zerodha", "upstox",
    "cred", "slice", "lendingkart", "policybazaar",
    "meesho", "swiggy", "zomato", "ola", "rapido",
    "freshworks", "zoho", "postman", "hasura", "signzy",
    "htmlcss javascript", "darwinbox", "citiustech", "sahaj",
    # More SaaS
    "gusto", "zenefits", "bamboohr", "leapsome", "lattice",
    "15five", "cultureamp", "peakon", "officevibe", "quantum",
    "lattice", "breeze", "namecheap", "hostgator",
    # More dev/infra
    "hashicorp", "pulumi", "rancher", "suse", "redhat",
    "docker", "kong", "ngrok", "cloudflare", "fastly",
    "varnish", "akamai", "limelight",
    # Misc popular
    "betterhelp", "betterup", "talkspace", "cerebral",
    "crossover", "andela", "toptal", "upwork", "fiverr",
    "handshake", "linkedin", "glassdoor", "indeed",
    # Additional batches (common Greenhouse boards)
    "abbage", "acorns", "act-on", "adaptive", "adjacent",
    "adroll", "adwerx", "aerohive", "affirm", "agilebits",
    "alation", "algolia", "allego", "allume", "alongside",
    "alto", "amobee", "amplitude", "anchore", "angular",
    "anixter", "anvyl", "aqua", "arcturus", "arms",
    "askaround", "atlas", "august", "avalara", "avant",
    "aviso", "axial", "aylien",
    # Even more (reach 1000+)
    "b12", "babylon", "bain", "balihoo", "bandwidth",
    "bark", "batch", "beeswax", "bettercloud", "betterup",
    "bigcommerce", "bigpanda", "bigswitch", "bill.com", "bing",
    "bitly", "blackhawk", "blackline", "blissfully", "bloomin",
    "bolloré", "boomtown", "braze", "brightbytes", "brightpearl",
    "broadly", "broadridge", "browserstack", "brushfire", "buoyant",
    "calm", "campminder", "candy", "captivateiq", "cargomatic",
    "casetext", "castlight", "celtra", "centerfield", "central1",
    "chegg", "chime", "choozle", "cindsight", "cision",
    "citrusbyte", "clari", "classpass", "clickup", "clobby",
    "clover", "cmx", "cobalt", "code42", "codility",
    "coherent", "coinninja", "collabora", "collective",
    "collective health", "color", "comet", "commvault", "compliance",
    "compound", "confluent", "connexion", "convercent", "copilot",
    "coreos", "couchbase", "coursera", "coveo", "cradlepoint",
    "creatively", "creditkarma", "crossover", "crowdriff",
    "cruise", "culturalist", "cureatr", "cvent", "cycle",
    "d2iq", "dailypay", "databricks", "datafox", "dataiku",
    "dataloader", "dear", "deepgram", "defmethod", "deliveroo",
    "delta", "demandbase", "deploybot", "derby", "dexcom",
    "dialpad", "digicert", "digitalocean", "digikey", "discourse",
    "disqus", "dixa", "docker", "docusign", "dominodatalab",
    "dremio", "drift", "driven", "dropbox", "drud",
    "dubsmash", "duolingo", "dwolla",
    "ebay", "echosign", "edmodo", "egghead", "elastic",
    "elasticsearch", "element", "eloqua", "ember", "emerald",
    "enlitic", "enova", "ensighten", "epic", "epocrates",
    "equinix", "erlang", "erply", "esri", "estee lauder",
    "ethos", "evolv", "exact", "excella", "exposed",
    "extole", "ezcater",
    # F-G
    "f5", "fabfitfun", "factorial", "falcon.io", "fanatics",
    "fastly", "feathr", "feedzai", "fetchrewards", "fiftythree",
    "figshare", "finda", "findify", "fireeye", "first round",
    "first90days", "fitbit", "fivetran", "flying", "followupthen",
    "food52", "fool", "foot locker", "forbes", "forcepoint",
    "forethought", "formidable", "forth", "fossa", "foursquare",
    "fractal", "freshbooks", "freshworks", "front", "fullcontact",
    "fusionops",
    # G-H
    "gainsight", "gale", "gamedaily", "ganic", "garmin",
    "gearshare", "genuity", "geotrust", "gerrit", "getguru",
    "getaround", "getharvest", "getstream", "getyourguide",
    "gigaspaces", "gitbook", "gitkraken", "gitter", "glassdoor",
    "glossier", "gojek", "gong", "goodrx", "google",
    "gopuff", "gore", "gradle", "grammarly", "graphcore",
    "greenhouse", "greensky", "grofers", "groupon", "grubhub",
    "gusto", "guy",
    # H-I
    "habitat", "habitat", "habitat", "habitat", "habitat",
    "handshake", "harmonic", "hashicorp", "hasura", "have",
    "headout", "healthmarkets", "heapanalytics", "heetch",
    "helpshift", "hermes", "hex", "hey", "heyjobs",
    "hibob", "highspot", "hims", "hinge", "hippo",
    "hired", "hivestack", "hodinkee", "hootsuite", "hopper",
    "hotjar", "hotwire", "hsbc", "hubspot", "huel",
    "huggingface", "humaninterest", "humm", "huntsman",
    "hyland", "hypergrowth", "hyperos", "hyperqa", "hyro",
    "ibm", "icims", "ideal", "ideoclick", "ifme",
    "igloo", "ikea", "illumina", "imagine", "imdb",
    "impraise", "inboxbygmail", "indeed", "indiegogo",
    "infogroup", "influitive", "influxdata", "infusionsoft",
    "inherent", "insightly", "instabase", "instacart",
    "instapage", "instawork", "integral", "intel", "intelliseek",
    "intercom", "interos", "intuit", "invision", "inward",
    "ion", "ipsy", "ironclad", "ironnet", "iterable",
    "ivanti", "izettle",
    # J-K
    "jabra", "jaggaer", "jamf", "jamstack", "jane",
    "jasper", "javant", "jeel", "jellysmack", "jet",
    "jetbrains", "jetlore", "jiff", "jito", "jitt",
    "jobvite", "join", "jostle", "jpmorgan", "jugnoo",
    "jumpcloud", "jupiter", "juro", "just Eat",
    "justworks", "kaggle", "kainos", "kaltura", "kandji",
    "kanopy", "kantox", "kapa ai", "kareo", "kayak",
    "keen", "kept", "keplr", "kendra", "keybase",
    "keyence", "keyhole", "khoros", "kickbox", "kicks",
    "kiddom", "kineviz", "king", "kira", "kix",
    "klarna", "klesia", "kong", "konstantin", "kroger",
    "kustomer", "kyriba",
    # L
    "l4", "lattice", "launchdarkly", "layer0", "leapsome",
    "leaseweb", "lever", "liftoff", "light", "lightbeam",
    "lightstep", "limelight", "linear", "linktree", "lint",
    "liquidplanner", "listrak", "livedrive", "living social",
    "lob", "localytics", "logmein", "loom", "loop",
    "loveholidays", "loyalty", "lucid", "lucidchart", "lullabot",
    "luminar", "luminary", "luminosity", "luminus",
    "lyft", "lynx",
    # M
    "machine zone", "made", "magento", "magnolia", "mailchimp",
    "mailgun", "mailjet", "mainframe", "major league gaming",
    "mango", "mapbox", "marfeel", "margin", "marigold",
    "marqeta", "mashery", "massdrop", "mattering", "mattermost",
    "maxmind", "mcgraw hill", "medallia", "mediaocean",
    "meetup", "meltwater", "mention", "mercedes", "mercury",
    "merkle", "meta", "metacortex", "metamarkets", "method",
    "metricly", "mews", "microsoft", "mightyhive", "mindbody",
    "minted", "miro", "mixpanel", "mobile", "modist",
    "mohom", "momentfeed", "monetate", "monetization",
    "moniepoint", "monzo", "moonactive", "moonclipse",
    "morningbrew", "morpheus", "mosaic", "mParticle",
    "mulberry", "mule", "multicoin", "munvo", "mutiny",
    "mux", "myhr", "myjob",
    # N
    "n26", "namely", "nanigans", "natterbox", "navan",
    "nectar", "needle", "nelnet", "neocunder", "neogames",
    "neon", "nerves", "netlify", "network", "neulinger",
    "newrelic", "newsela", "nextdoor", "niantic", "nike",
    "nimble", "nintex", "nium", "norden", "nordstrom",
    "north", "northbeam", "notion", "nova", "novel",
    "novoda", "nowports", "numis", "nuro", "nutonomy",
    # O
    "oath", "odessa", "offerup", "office", "ogury",
    "okcupid", "okta", "ola", "oleve", "olympus",
    "omada", "omni", "omny", "onelogin", "onespot",
    "open", "opendoor", "openphone", "opensky", "optimizely",
    "oracle", "organic", "orgvue", "oscar", "outreach",
    "outbrain", "outschool", "oxford", "oxide",
    # P
    "pachama", "paddle", "panda", "pandadoc", "pantheon",
    "parabola", "parcel", "partake", "pathable", "patientpop",
    "patriot", "pax", "payoneer", "payu", "peanut",
    "pears", "peek", "peloton", "pendo", "penske",
    "peopleshr", "pepsi", "perch", "perf", "persona",
    "phantom", "phonepe", "pilot", "pinterest", "pipelinedrive",
    "pitch", "pizza hut", "plaid", "plan', 'planful",
    "plangrid", "planning", "plantlogo", "platform", "platter",
    "pluralsight", "point", "pointgrab", "polly", "pop",
    "popsugar", "poshmark", "postscript", "postscript", "potluck",
    "power", "pragmatic", "prebid", "prelight", "presto",
    "prettybird", "priceline", "pricetag", "primal", "primer",
    "procore", "prodigy", "profitero", "prognos", "prologue",
    "prometheus", "propel", "prophet", "prose", "protocol",
    "protoxa", "proven", "providence", "providence", "pubmatic",
    "pulse", "pump", "punchcard", "puppet", "purestorage",
    "purple", "purple", "pushpay", "putnam", "pyze",
    # Q-R
    "qonto", "quad", "qualaroo", "qualtrics", "quantexa",
    "quantum", "quartile", "quill", "quinto", "quizlet",
    "quotient", "r2c", "rabbitmq", "radar", "radiant",
    "rainforest", "rally", "ramp", "rappi", "rarible",
    "rational", "raygun", "reach", "rebate", "reddit",
    "redfin", "redhat", "redox", "redspot", "reedsy",
    "refereum", "refresh", "reggora", "relate", "relay",
    "remitly", "remix", "renmoney", "repair", "repl",
    "reportgarden", "repro", "rerun", "respect", "retail",
    "retool", "retrain", "retro", "rev", "revere",
    "revolut", "reward", "ribbon", "ripple", "riser",
    "ritual", "rivian", "rmg", "roadpass", "robinhood",
    "robo", "robocorp", "rocket", "rocketmatter", "roku",
    "rollbar", "root", "roots", "rosetta", "round",
    "routable", "rubrik", "ruggable", "rundown", "rush",
    # S
    "saama", "safegraph", "sailpoint", "sailthru", "saks",
    "salt", "salesforce", "salto", "sample", "sanofi",
    "sap", "sapphire", "sauce", "sauce labs", "scale",
    "scalpy", "scalyr", "scanner", "schwab", "scopely",
    "screen", "scrubbed", "sdfc", "seamless", "seattle",
    "sec", "secureauth", "segal", "segment", "selligent",
    "semgrep", "sendoso", "sensata", "sentinel", "sentry",
    "sequoia", "servicenow", "sesame", "session", "sezzle",
    "shippo", "shutterstock", "sidecar", "siemens", "sig",
    "signal", "signify", "signifyd", "silk", "silver",
    "simple", "simplilearn", "sinch", "singlestore", "sisense",
    "siteimprove", "siteminder", "skechers", "skillshare",
    "sky", "skyscanner", "slack", "slalom", "snyk",
    "social", "socrata", "soft", "softlayer", "solarisbank",
    "sollers", "sonatype", "songtrust", "sonic", "sophos",
    "sourcetree", "span", "spark", "sparkcentral", "spectrocloud",
    "splunk", "spoke", "spreedly", "sprinklr", "spring",
    "sprout", "spryker", "sqsp", "squid", "stamplay",
    "standard", "stanford", "stanzaliving", "stark", "stat",
    "statuspage", "stealth", "steelbrick", "stellar", "stitch",
    "sto", "stockx", "stoplight", "storm", "strava",
    "stripe", "strong", "strongarm", "stubhub", "stumbleupon",
    "stylight", "submittable", "sugar", "sumo", "sunrun",
    "super", "superhuman", "supermetrics", "supersede",
    "survey", "surveygizmo", "surveyMonkey", "sustainserv",
    "swiggy", "switch", "synchrony", "syndio",
    # T
    "tableau", "taboola", "talend", "talla", "talking",
    "tamara", "tangent", "target", "taro", "tax",
    "taxact", "taxslayer", "teachable", "teamwork", "tebra",
    "ted", "telenav", "telus", "tempest", "tenable",
    "tender", "tenor", "teradata", "terrameetch",
    "tesla", "tesladaq", "testlio", "textio", "thales",
    "the", "theaseanbanker", "theinfatuation", "think",
    "threatmodeler", "thrive", "tiaa", "tidal", "tiendeo",
    "tilted", "time", "tinuiti", "tivo", "tmobile",
    "tokbox", "tokopedia", "tomtom", "toolchain", "topia",
    "topshop", "torchy", "total", "touchbistro", "toyota",
    "trader", "trainual", "transfix", "treasure", "tree",
    "trello", "tremendous", "tri", "trifacta", "tripadvisor",
    "trov", "truecaller", "truist", "trust", "truth",
    "tsheets", "turbine", "turn", "turnitin", "tusk",
    "twilio", "twitch", "twitter", "tyk", "typeform",
    # U-V
    "uber", "ugroop", "uitencent", "ultimate", "umbraco",
    "unbabel", "uncover", "underarmour", "unified", "unit",
    "unity", "univar", "unomaly", "until", "updater",
    "upfront", "uplight", "upwork", "urban", "urbanground",
    "usabilla", "usertesting", "vanguard", "varo", "vault",
    "veem", "veeqo", "venmo", "veracode", "veritone",
    "verkada", "veronica", "versal", "vimeo", "vine",
    "vinted", "visa", "vital", "vizio", "vmware",
    "vodafone", "volta", "vouch", "voxmedia", "vper",
    "vsco", "vtex",
    # W-X
    "w3i", "wah", "walmart", "wander", "warp", "waze",
    "wealthsimple", "weave", "webflow", "weebly", "wework",
    "wharton", "whipsaw", "whole", "willy", "windfall",
    "wip", "wish", "wistia", "wix", "wonder",
    "wooha", "woocommerce", "wordstream", "wordpress", "workable",
    "workato", "workiva", "worklight", "workable", "workday",
    "workflow", "working", "workplace", "workstream", "worm",
    "wotif", "wpp", "xactly", "xero", "xfinity",
    "xola", "xome", "xoxoday", "xseed",
    # Y-Z
    "yale", "yammer", "yapstone", "yello", "yellow",
    "yesware", "ymedia", "yodlee", "youearnedit", "yougov",
    "young", "youtube", "yuno", "yuvafire", "zapier",
    "zendesk", "zenefits", "zenoss", "zeplin", "zerodha",
    "zigbang", "zillow", "zing", "ziprecruiter", "zocdoc",
    "zoho", "zola", "zomato", "zoom", "zops",
    "zulip", "zynga",
]

# Lever companies
LEVER_COMPANIES = [
    "netlify", "xd", "postmates", "upstart", "nubank",
    "plaid", "buff", "fossa", "circle", "checkout",
    "cloudkitchens", "dialpad", "fictiv", "flexe",
    "gusto", "kong", "lever", "matrix", "nerdwallet",
    "niantic", "notion", "pandion", "plaid", "postscript",
    "qonto", "rain", "responsify", "robots", "segment",
    "sky", "smarthire", "spenmo", "spotify", "stripe",
    "switch", "talent", "texas", "trio", "ultimate",
    "urban", "verkada", "vimeo", "vin", "weather",
    "whimsy", "woven", "xola", "yuno", "zen",
    "zola",
]

# Ashby companies
ASHBY_COMPANIES = [
    "notion", "linear", "openai", "anthropic", "ramp",
    "vercel", "supabase", "posthog", "retool", "snyk",
    "postman", "fivetran", "rippling", "ziggy", "descript",
    "haven", "fireflies", "leap", "cohere", "runway",
    "pika", "character", "together", "perplexity", "mistral",
    "adept", "stability", "midjourney", "cursor", "vercel",
    "planetscale", "tigerbeetle", "drizzle", "turborepo",
]

# SmartRecruiters companies
SMARTRECRUITERS_COMPANIES = [
    "redbull", "colliers", "accor", "dominos", "rolandberger",
    "deliveryhero", "servicenow", "grab", "canva", "abbvie",
    "entain", "asos", "wise", "dailymotion", "instructure",
    "siteminder", "fiverr", "unacademy", "zenefits",
    "gousto", "gong", "raytheon", "nextdc", "smartrecruiters",
]

# Workable companies  
WORKABLE_COMPANIES = [
    "fuku", "rentokil-initial", "pars-therapy",
    "rebel-convenience-stores", "cxg", "tehora", "huzzle",
    "pavago", "fiverr", "trinet", "talent",
    "lensa", "flexjobs", "builtin", "wework",
]

# ════════════════════════════════════════════════════════════════
# Logging & checkpoint
# ════════════════════════════════════════════════════════════════
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
    return {"completed": [], "stats": {"scraped": 0, "new": 0, "errors": 0}}


def save_checkpoint(cp: dict):
    CP_FILE.parent.mkdir(parents=True, exist_ok=True)
    CP_FILE.write_text(json.dumps(cp, indent=2), encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# ATS API scrapers (fast, structured JSON, no browser)
# ════════════════════════════════════════════════════════════════

def scrape_greenhouse(slug: str) -> list[dict]:
    """Fetch all jobs from a Greenhouse board."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = []
        for j in data.get("jobs", []):
            loc = j.get("location", {})
            loc_name = loc.get("name", "") if isinstance(loc, dict) else str(loc)
            dept = j.get("departments", [])
            dept_name = dept[0].get("name", "") if dept else ""
            posted = j.get("updated_at") or j.get("created_at")
            jobs.append({
                "title": j.get("title", ""),
                "company": data.get("name", slug),
                "location": loc_name,
                "url": j.get("absolute_url", f"https://boards.greenhouse.io/{slug}/jobs/{j.get('id', '')}"),
                "posted_at": posted,
                "jobkey": str(j.get("id", "")),
                "source": f"greenhouse:{slug}",
                "description": (j.get("content") or "")[:500],
                "tags": dept_name,
            })
        return jobs
    except Exception:
        return []


def scrape_lever(slug: str) -> list[dict]:
    """Fetch all jobs from a Lever board."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = []
        for j in data:
            teams = j.get("teamsPlain") or ""
            posted = j.get("createdAt")
            if posted:
                from datetime import datetime as dt
                try:
                    posted = dt.fromtimestamp(posted / 1000, tz=timezone.utc).isoformat()
                except Exception:
                    posted = None
            jobs.append({
                "title": j.get("text", ""),
                "company": j.get("categories", {}).get("team", slug),
                "location": j.get("categories", {}).get("location", ""),
                "url": j.get("hostedUrl", ""),
                "posted_at": posted,
                "jobkey": j.get("id", ""),
                "source": f"lever:{slug}",
                "description": (j.get("descriptionPlain") or "")[:500],
                "tags": teams,
            })
        return jobs
    except Exception:
        return []


def scrape_ashby(slug: str) -> list[dict]:
    """Fetch all jobs from an Ashby board."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return []
        data = r.json()
        board = data.get("jobBoard", {})
        jobs = []
        for j in board.get("openings", []):
            loc = j.get("locationName", "")
            posted = j.get("publishedAt")
            jobs.append({
                "title": j.get("title", ""),
                "company": board.get("name", slug),
                "location": loc,
                "url": j.get("url", ""),
                "posted_at": posted,
                "jobkey": j.get("id", ""),
                "source": f"ashby:{slug}",
                "description": "",
                "tags": j.get("departmentName", ""),
            })
        return jobs
    except Exception:
        return []


def scrape_smartrecruiters(slug: str) -> list[dict]:
    """Fetch jobs from SmartRecruiters."""
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0"
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = []
        for j in data.get("content", []):
            loc = j.get("location", {})
            loc_name = loc.get("city", "") + ", " + loc.get("country", "") if loc else ""
            ref = j.get("ref", "")
            posted = j.get("releasedDate")
            jobs.append({
                "title": j.get("name", ""),
                "company": j.get("company", {}).get("name", slug),
                "location": loc_name.strip(", "),
                "url": f"https://jobs.smartrecruiters.com/{slug}/{ref}" if ref else "",
                "posted_at": posted,
                "jobkey": str(j.get("id", "")),
                "source": f"smartrecruiters:{slug}",
                "description": (j.get("jobAd", {}).get("sections", {}).get("jobDescription", {}).get("text", "") or "")[:500],
                "tags": "",
            })
        return jobs
    except Exception:
        return []


def scrape_workable(slug: str) -> list[dict]:
    """Fetch jobs from Workable."""
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = []
        for j in data.get("jobs", []):
            posted = j.get("updated")
            jobs.append({
                "title": j.get("title", ""),
                "company": slug,
                "location": j.get("city", "") + ", " + j.get("country", ""),
                "url": j.get("url", ""),
                "posted_at": posted,
                "jobkey": j.get("department", "") + j.get("title", ""),
                "source": f"workable:{slug}",
                "description": (j.get("description") or "")[:500],
                "tags": j.get("department", ""),
            })
        return jobs
    except Exception:
        return []


# ════════════════════════════════════════════════════════════════
# JobSpy scraper (reuse from parallel_scraper)
# ════════════════════════════════════════════════════════════════
def scrape_jobspy_one(query: str, location: str, max_pages: int = 5) -> list[dict]:
    from jobspy import scrape_jobs, Country
    loc = f"{location}, India" if location else ""
    all_jobs = []
    seen = set()
    offset = 0
    empty = 0
    for _ in range(max_pages):
        try:
            result = scrape_jobs(
                site_name=["linkedin", "indeed"],
                search_term=query, location=loc,
                results_wanted=50,
                country=Country.INDIA if location else None,
                offset=offset,
            )
        except Exception:
            break
        if result is None or len(result) == 0:
            empty += 1
            if empty >= 2:
                break
            offset += 50
            time.sleep(0.5)
            continue
        empty = 0
        new_count = 0
        for _, row in result.iterrows():
            url = row.get("job_url", "")
            if url in seen:
                continue
            seen.add(url)
            new_count += 1
            posted = None
            dp = row.get("date_posted")
            if dp is not None:
                try:
                    posted = dp.isoformat() if hasattr(dp, "isoformat") else str(dp)
                except Exception:
                    pass
            all_jobs.append({
                "title": str(row.get("title", "")),
                "company": str(row.get("company", "")),
                "location": str(row.get("location", "")),
                "url": url,
                "posted_at": posted,
                "jobkey": str(row.get("id", "")),
                "source": f"jobspy:{row.get('site', 'unknown')}",
                "salary": "",
                "description": str(row.get("description", ""))[:500] if row.get("description") else "",
                "tags": "",
            })
        if new_count == 0:
            break
        offset += 50
        time.sleep(0.2)
    return all_jobs


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
                    (
                        j["url"] or j.get("jobkey", ""),
                        j["title"],
                        j.get("company", ""),
                        j.get("location", ""),
                        j.get("description", ""),
                        j["url"],
                        j["source"],
                        "ats" if "greenhouse" in j.get("source", "") or "lever" in j.get("source", "") or "ashby" in j.get("source", "") else "browser",
                        j.get("jobkey", ""),
                        j.get("posted_at"),
                        j.get("salary", ""),
                        tag,
                        now,
                        now,
                    ),
                )
                if cur.rowcount > 0:
                    new += 1
            except Exception:
                continue
        conn.commit()
    return new


# ════════════════════════════════════════════════════════════════
# Worker functions (run in thread pool)
# ════════════════════════════════════════════════════════════════
def worker_ats(args):
    platform, slug = args
    scrapers = {
        "greenhouse": scrape_greenhouse,
        "lever": scrape_lever,
        "ashby": scrape_ashby,
        "smartrecruiters": scrape_smartrecruiters,
        "workable": scrape_workable,
    }
    jobs = scrapers[platform](slug)
    return {"platform": platform, "slug": slug, "jobs": jobs, "count": len(jobs)}


def worker_jobspy(args):
    query, loc = args
    jobs = scrape_jobspy_one(query, loc)
    return {"query": query, "location": loc, "jobs": jobs, "count": len(jobs)}


# ════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Mega stress test — 1M jobs in 7 days")
    ap.add_argument("--threads", type=int, default=20, help="parallel threads")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--ats-only", action="store_true", help="only ATS APIs (fastest)")
    ap.add_argument("--jobspy-only", action="store_true", help="only JobSpy")
    args = ap.parse_args()

    # Build ATS work list
    ats_work = []
    if not args.jobspy_only:
        for slug in GREENHOUSE_COMPANIES:
            ats_work.append(("greenhouse", slug))
        for slug in LEVER_COMPANIES:
            ats_work.append(("lever", slug))
        for slug in ASHBY_COMPANIES:
            ats_work.append(("ashby", slug))
        for slug in SMARTRECRUITERS_COMPANIES:
            ats_work.append(("smartrecruiters", slug))
        for slug in WORKABLE_COMPANIES:
            ats_work.append(("workable", slug))

    # Build JobSpy work list
    jobspy_work = []
    if not args.ats_only:
        kw_mini = ["Software Engineer", "Backend Engineer", "Frontend Engineer",
                   "Full Stack Developer", "Data Engineer", "DevOps Engineer",
                   "ML Engineer", "Python Developer", "Java Developer",
                   "React Developer", "SDE", "Cloud Engineer", "Security Engineer"]
        locs_india = ["Delhi", "Bengaluru", "Hyderabad", "Pune", "Mumbai",
                      "Chennai", "Kolkata", "Noida", "Gurgaon"]
        for kw in kw_mini:
            for loc in locs_india:
                jobspy_work.append((kw, loc))
        # Remote/global
        for kw in kw_mini[:5]:
            jobspy_work.append((kw, ""))

    log("=" * 60)
    log("MEGA STRESS TEST — 1M jobs target")
    log(f"ATS companies: {len(ats_work)} (Greenhouse:{len(GREENHOUSE_COMPANIES)} + "
        f"Lever:{len(LEVER_COMPANIES)} + Ashby:{len(ASHBY_COMPANIES)} + "
        f"SmartRecruiters:{len(SMARTRECRUITERS_COMPANIES)} + Workable:{len(WORKABLE_COMPANIES)})")
    log(f"JobSpy searches: {len(jobspy_work)}")
    log(f"Threads: {args.threads}")
    log("=" * 60)

    # Checkpoint
    cp = load_checkpoint() if args.resume else {"completed": [], "stats": {"scraped": 0, "new": 0, "errors": 0}}
    completed_set = set(cp["completed"])
    log(f"Resuming: {len(completed_set)} already done")

    conn = sqlite3.connect(args.db)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {total_before:,} jobs")

    grand_scraped = cp["stats"]["scraped"]
    grand_new = cp["stats"]["new"]
    grand_errors = cp["stats"]["errors"]
    start_time = time.time()

    # ── Phase 1: ATS APIs (all companies in parallel) ──
    if ats_work:
        remaining_ats = [(p, s) for p, s in ats_work if f"ats:{p}:{s}" not in completed_set]
        log(f"\n{'='*60}")
        log(f"PHASE 1: ATS APIs — {len(remaining_ats)} companies, {args.threads} threads")
        log(f"{'='*60}")

        batch_num = 0
        for batch_start in range(0, len(remaining_ats), args.threads * 5):
            batch = remaining_ats[batch_start:batch_start + args.threads * 5]
            with ThreadPoolExecutor(max_workers=args.threads) as executor:
                futures = {executor.submit(worker_ats, work): work for work in batch}
                for future in as_completed(futures):
                    platform, slug = futures[future]
                    key = f"ats:{platform}:{slug}"
                    try:
                        result = future.result()
                        grand_scraped += result["count"]
                        tag = f"mega,{platform},{slug}"
                        new = store_jobs(conn, result["jobs"], tag)
                        grand_new += new
                        completed_set.add(key)
                        batch_num += 1
                        if new > 0:
                            log(f"  [{platform:16s}] {slug:30s}: {result['count']:4d} jobs, +{new:4d} new")
                    except Exception as e:
                        grand_errors += 1
                        log(f"  [{platform}] {slug}: ERROR {e}")

            # Checkpoint every batch
            cp = {"completed": list(completed_set), "stats": {"scraped": grand_scraped, "new": grand_new, "errors": grand_errors}}
            save_checkpoint(cp)
            elapsed = time.time() - start_time
            rate = grand_new / (elapsed / 60) if elapsed > 0 else 0
            log(f"  --- ATS batch done: {grand_new:,} new total, {rate:.0f}/min, {elapsed/60:.1f} min ---")

    # ── Phase 2: JobSpy parallel ──
    if jobspy_work:
        remaining_js = [(q, l) for q, l in jobspy_work if f"jobspy:{q}:{l}" not in completed_set]
        log(f"\n{'='*60}")
        log(f"PHASE 2: JobSpy — {len(remaining_js)} searches, {args.threads} threads")
        log(f"{'='*60}")

        for batch_start in range(0, len(remaining_js), args.threads * 3):
            batch = remaining_js[batch_start:batch_start + args.threads * 3]
            with ThreadPoolExecutor(max_workers=args.threads) as executor:
                futures = {executor.submit(worker_jobspy, work): work for work in batch}
                for future in as_completed(futures):
                    query, loc = futures[future]
                    key = f"jobspy:{query}:{loc}"
                    try:
                        result = future.result()
                        grand_scraped += result["count"]
                        tag = f"mega,jobspy,{query.lower().replace(' ', '-')[:30]}"
                        new = store_jobs(conn, result["jobs"], tag)
                        grand_new += new
                        completed_set.add(key)
                        if new > 0:
                            log(f"  [JobSpy] {query:30s} | {loc or 'global':12s}: {result['count']:4d} scraped, +{new:4d} new")
                    except Exception as e:
                        grand_errors += 1
                        log(f"  [JobSpy] {query}|{loc}: ERROR {e}")

            cp = {"completed": list(completed_set), "stats": {"scraped": grand_scraped, "new": grand_new, "errors": grand_errors}}
            save_checkpoint(cp)
            elapsed = time.time() - start_time
            rate = grand_new / (elapsed / 60) if elapsed > 0 else 0
            log(f"  --- JobSpy batch done: {grand_new:,} new total, {rate:.0f}/min ---")

    # ── Final ──
    elapsed = time.time() - start_time
    final_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    log("")
    log("=" * 60)
    log("MEGA STRESS TEST COMPLETE")
    log(f"Total scraped: {grand_scraped:,}")
    log(f"New inserted:  {grand_new:,}")
    log(f"Errors:        {grand_errors}")
    log(f"Time:          {elapsed/60:.1f} minutes")
    log(f"Rate:          {grand_new/(elapsed/60):.0f} new jobs/min")
    log(f"DB total:      {final_total:,}")
    log(f"Gap to 1M:     {max(0, 1000000 - final_total):,}")
    log("=" * 60)


if __name__ == "__main__":
    main()
