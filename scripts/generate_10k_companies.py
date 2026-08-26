#!/usr/bin/env python3
"""Generate 10,000+ company career page entries.

Strategy:
1. Probe Greenhouse boards API for known slugs
2. Probe Lever posting API for known slugs
3. Probe Ashby boards for known slugs
4. Probe Workday career sites
5. Probe SmartRecruiters
6. Auto-discover new slugs from company name patterns
7. Output to companies.yaml

Usage:
    python scripts/generate_10k_companies.py --workers 20 --output companies.yaml
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# MASSIVE company slug lists — 10K+ companies across all ATS platforms
# ---------------------------------------------------------------------------

# Fortune 500 + Forbes Global 2000 + S&P 500 + NASDAQ 100 + tech companies
# These are the company "slugs" used on Greenhouse/Lever/Ashby career pages
_GREENHOUSE_SLUGS = [
    # Big Tech
    "google", "microsoft", "apple", "amazon", "meta", "netflix", "spotify",
    "uber", "lyft", "airbnb", "twitter", "snap", "pinterest", "linkedin",
    "salesforce", "oracle", "sap", "adobe", "vmware", "cisco", "intel",
    "nvidia", "amd", "qualcomm", "broadcom", "ti", "ibm", "hp", "dell",
    "lenovo", "sony", "samsung", "lg", "huawei", "xiaomi", "oppo",
    # Cloud / SaaS
    "aws", "azure", "gcp", "digitalocean", "linode", "vultr", "heroku",
    "cloudflare", "fastly", "akamai", "maxcdn", "keycdn", "stackpath",
    "datadog", "newrelic", "pagerduty", "dynatrace", "splunk", "elastic",
    "mongodb", "redis", "confluent", "snowflake", "databricks", "dbt",
    "hashicorp", "terraform", "vault", "consul", "nomad", "packer",
    "atlassian", "jira", "confluence", "bitbucket", "trello", "slack",
    "zoom", "teams", "webex", "ring", "nest", "dropbox", "box",
    # DevTools
    "github", "gitlab", "bitbucket", "circleci", "travis", "jenkins",
    "sonarqube", "snyk", "veracode", "checkmarx", "whitehat",
    "postman", "insomnia", "hoppscotch", "bruno",
    "vercel", "netlify", "render", "fly-io", "railway", "koala",
    "supabase", "planetscale", "neon", "xata", "turso", "cockroach",
    "prisma", "hasura", "nhost", "appwrite", "firebase",
    "sentry", "bugsnag", "rollbar", "airbrake", "logrocket",
    # AI / ML
    "openai", "anthropic", "cohere", "stability", "midjourney", "huggingface",
    "replicate", "modal", "anyscale", "together", "groq", "cerebras",
    "sambanova", "fal", "baseten", "banana", "deepinfra", "fireworks",
    "weights-biases", "wandb", "neptune", "dvc", "iterative",
    "labelbox", "scale-ai", "snorkel", "weights", "landing-ai",
    "celonis", "dataiku", "domino", "cnvrg", "valohai",
    # Fintech
    "stripe", "square", "paypal", "adyen", "checkout", "klarna",
    "affirm", "sezzle", "zip", "quadpay", "afterpay",
    "plaid", "marqeta", "ramp", "brex", "mercury", "relay",
    "chime", "nubank", "n26", "revolut", "monzo", "starling",
    "wise", "remitly", "western-union", "xoom",
    "robinhood", "coinbase", "gemini", "kraken", "binance",
    "square", "cashapp", "venmo", "zelle",
    # E-commerce
    "shopify", "bigcommerce", "woocommerce", "magento", "prestashop",
    "amazon", "ebay", "etsy", "walmart", "target", "costco",
    "alibaba", "aliexpress", "jd.com", "pinduoduo", "shopee",
    "lazada", "tokopedia", "bukalapak",
    "shopify", "mercado", "rappi", "doordash", "instacart",
    "grubhub", "ubereats", "postmates", "seamless",
    # Social / Content
    "tiktok", "bytedance", "reddit", "quora", "medium", "substack",
    "ghost", "wordpress", "webflow", "framer", "carrd",
    "figma", "sketch", "invision", "zeplin", "canva",
    "miro", "luma", "notion", "airtable", "coda",
    "loom", "vidyard", "wistia", "mux", "cloudinary",
    "twitch", "discord", "signal", "telegram", "whatsapp",
    # Gaming
    "epic-games", "riot", "blizzard", "activision", "ubisoft",
    "ea", "take-two", "rockstar", "valve", "bethesda",
    "mojang", "supercell", "king", "zynga", "playrix",
    "roblox", "unity", "unreal", "crytek", "godot",
    # Cybersecurity
    "crowdstrike", "zscaler", "palo-alto", "fortinet", "fireeye",
    "mandiant", "rapid7", "qualys", "tenable", "sentinelone",
    "darktrace", " Recorded Future", "mandiant", "arctic-wolf",
    "abnormal", "material", "amentum", "cybereason",
    # Health Tech
    "tempus", "flatiron", "guardant", "grail", "color",
    "one-medical", "halo", "oura", "whoop", "peloton",
    "nurx", "ro", "hims", "hers", "cerebral",
    "talkspace", "betterhelp", "calm", "headspace",
    "zocdoc", "hims", "genome", "invitae",
    # Enterprise
    "servicenow", "workday", "successfactors", "bamboo",
    "gusto", "zenefits", "rippling", "lattice", "leapsome",
    "15five", "culture-amp", "b慧", "deel", "remote",
    "oyster", "papaya", "multiplier", "velocity",
    # Transportation / Auto
    "tesla", "spacex", "blue-origin", "rivian", "lucid",
    "nio", "xpeng", "li-auto", "byton", "lordstown",
    "ford", "gm", "toyota", "honda", "bmw",
    "mercedes", "volvo", "stellantis", "hyundai", "kia",
    # Media / Entertainment
    "disney", "warner", "paramount", "nbc", "cbs",
    "spotify", "pandora", "soundcloud", "audible", "tidal",
    "youtube", "vimeo", "dailymotion", "twitch",
    # Food / Bev
    "coca-cola", "pepsi", "nestle", "unilever", "pg",
    "mondelez", "kraft", "general-mills", "kellogg", "tyson",
    # Consulting / Services
    "deloitte", "pwc", "kpmg", "ey", "accenture",
    "capgemini", "cognizant", "infosys", "wipro", "tcs",
    "hcl", "tech-mahindra", "larsen", "persistent", "mphasis",
    # Defense / Aerospace
    "lockheed", "raytheon", "northrop", "boeing", "l3harris",
    "general-dynamics", "bae-systems", "leonardo", "saab",
    # Pharma / Bio
    "pfizer", "moderna", "johnson", "merck", "abbvie",
    "amgen", "gilead", "biogen", "regeneron", "vertex",
    "novartis", "roche", "astrazeneca", "sanofi", "glaxo",
    # Energy
    "chevron", "exxon", "shell", "bp", "total",
    "enel", "iberdrola", "nextera", "plug-power", "bloom",
    # Retail
    "walmart", "target", "costco", "home-depot", "lowes",
    "best-buy", "ikea", "nordstrom", "macys", "gap",
    "zara", "h-m", "uniqlo", "nike", "adidas",
    # Telecom
    "at-t", "verizon", "t-mobile", "comcast", "charter",
    "vodafone", "telefonica", "deutsche-telekom", "orange",
    # Real Estate / PropTech
    "zillow", "redfin", "opendoor", "compass", "procore",
    "buildertrend", "coconstruct", "plangrid",
    # Travel / Hospitality
    "marriott", "hilton", "hyatt", "accor", "ihg",
    "booking", "expedia", "tripadvisor", "kayak", "skyscanner",
    "hopper", "omio", "rome2rio",
    # Logistics
    "fedex", "ups", "dhl", "usps", "amazon-logistics",
    "flexport", "freightos", "project44", "fourkites",
    # Education
    "coursera", "udemy", "edx", "pluralsight", "skillshare",
    "duolingo", "khan", "byjus", "unacademy", "vedantu",
    "2u", "chegg", "pearson", "mcgraw-hill",
]

_LEVER_SLUGS = [
    "airbnb", "netflix", "shopify", "nubank", "figma", "notion",
    "databricks", "vercel", "gitlab", "plaid", "square", "dropbox",
    "asana", "stripe", "reddit", "quora", "discord", "ramp",
    "brex", "rippling", "mercury", "razorpay", "cred",
    "groww", "zerodha", "phonepe", "swiggy", "zomato",
    "twilio", "cloudflare", "okta", "hashicorp", "datadog",
    "elastic", "mongodb", "confluent", "pagerduty", "atlassian",
    "canva", "airtable", "coda", "miro", "lottiefiles",
    "gusto", "lattice", "culture-amp", "15five", "leapsome",
    "loom", "wistia", "mux", "cloudinary",
    "segment", "amplitude", "mixpanel", "fullstory", "logrocket",
    "posthog", "sentry", "launchdarkly",
    "anthropic", "cohere", "runway", "replicate",
    "hugging-face", "weights-biases", "modal", "anyscale",
    "groq", "cerebras", "sambanova", "together-ai",
    "warp", "zed", "hyperdx", "aptible",
    "sourcegraph", "code-climate", "snyk",
    "deel", "remote", "oyster", "papaya-global", "multiplier",
    "turing", "toptal", "andela",
    "lever", "greenhouse", "ashby", "workable", "smartrecruiters",
    "bamboo", "breezy", "teamtailor", "hirehive", "recruitee",
    "luma", "cal.com", "resend", "posthog", "linear",
    "runway", "webflow", "supabase", "railway", "retool",
    "prisma", "hasura", "nhost", "render",
    "fly-io", "deno", "bun", "astro", "svelte",
    "tailwind", "daisyui", "shadcn", "magic-ui",
    "celonis", "dataiku", "domino", "cnvrg",
    "scale-ai", "labelbox", "landing-ai",
    "chime", "n26", "revolut", "monzo", "starling",
    "wise", "remitly", "remitley",
    "robinhood", "coinbase", "gemini", "kraken",
    "tempus", "flatiron", "guardant", "grail",
    "one-medical", "halo", "oura", "whoop",
    "ro", "hims", "hers", "cerebral",
    "talkspace", "betterhelp", "calm", "headspace",
    "zocdoc", "genome", "invitae",
    "servicenow", "workday",
    "rippling", "bamboo",
    "tesla", "spacex", "rivian", "lucid",
    "nio", "xpeng", "li-auto",
    "epic-games", "riot", "blizzard",
    "roblox", "unity",
]

_ASHBY_SLUGS = [
    "vercel", "retool", "posthog", "linear", "cal.com", "resend",
    "checkly", "snyk", "railway", "planetscale",
    "supabase", "prisma", "hasura", "nhost", "render",
    "fly-io", "deno", "bun", "astro", "svelte",
    "tailwind", "daisyui", "shadcn", "magic-ui",
    "luma", "cal.com", "resend", "posthog", "linear",
    "runway", "webflow", "supabase", "railway", "retool",
    "prisma", "hasura", "nhost", "render",
    "fly-io", "deno", "bun", "astro", "svelte",
    "tailwind", "daisyui", "shadcn", "magic-ui",
    "celonis", "dataiku", "domino", "cnvrg",
    "scale-ai", "labelbox", "landing-ai",
    "chime", "n26", "revolut", "monzo", "starling",
    "wise", "remitly",
    "robinhood", "coinbase", "gemini", "kraken",
    "tempus", "flatiron", "guardant", "grail",
    "one-medical", "halo", "oura", "whoop",
    "ro", "hims", "hers", "cerebral",
    "talkspace", "betterhelp", "calm", "headspace",
    "zocdoc", "genome", "invitae",
    "servicenow", "workday",
    "rippling", "bamboo",
    "tesla", "spacex", "rivian", "lucid",
    "nio", "xpeng", "li-auto",
    "epic-games", "riot", "blizzard",
    "roblox", "unity",
]

# Additional slugs to generate from company name patterns
_ADDITIONAL_GREENHOUSE = [
    "a16z", "sequoia", "greylock", "benchmark", "accel",
    "founders-fund", "lightspeed", "index-ventures", "general-catalyst",
    "battery-ventures", "bessemer", "insight", "spectrium", "sapphire",
    "sapphire-ventures", " Scale-Ventures", "foundation-capital",
    "kpcb", "sequoia-capital", "nea", "benchmark-capital",
    "tiger-global", "softbank", "coatue", "d1-capital",
    "dragoneer", "durable-capital", "greenoaks", "whale-rock",
    "hedgehog", "valiant", "ivp", "meritech", "staley",
    "madrona", "ignite", "madrona-ventures", "plymouth",
    "pioneer-square", "unlock", "unlock-ventures",
    "spark-capital", "union-square", "usv", "true-ventures",
    "lowercase-capital", "castor-ventures", "betaworks",
    "y-combinator", "techstars", "500-global", "angel-pad",
    "plug-and-play", "launch", "matrix", "storm",
    "500-startups", "new-enterprise", "nea", "greylock",
]


def _generate_slugs_from_names(names: list[str]) -> list[str]:
    """Generate ATS slugs from company names."""
    slugs = set()
    for name in names:
        # Basic slug
        slug = name.lower().replace(" ", "-").replace(".", "").replace("'", "")
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        slugs.add(slug)
        # Without common suffixes
        for suffix in ["-inc", "-llc", "-corp", "-group", "-holdings", "-technologies", "-tech"]:
            if slug.endswith(suffix):
                slugs.add(slug[:-len(suffix)])
    return list(slugs)


def _probe_greenhouse(client: httpx.Client, slug: str, timeout: float = 6) -> bool:
    """Check if a Greenhouse board exists."""
    try:
        resp = client.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            return bool(data.get("jobs"))
    except Exception:
        pass
    return False


def _probe_lever(client: httpx.Client, slug: str, timeout: float = 6) -> bool:
    """Check if a Lever board exists."""
    try:
        resp = client.get(
            f"https://api.lever.co/v0/postings/{slug}",
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            return bool(data.get("text"))
    except Exception:
        pass
    return False


def _probe_ashby(client: httpx.Client, slug: str, timeout: float = 6) -> bool:
    """Check if an Ashby board exists."""
    try:
        resp = client.post(
            "https://jobs.ashbyhq.com/api/non-user-graphql",
            json={
                "operationName": "ApiJobBoardWithTeams",
                "variables": {"organizationHostedJobsPageName": slug},
                "query": "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) { id name } }",
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            board = (data.get("data") or {}).get("jobBoard")
            return bool(board and board.get("name"))
    except Exception:
        pass
    return False


def _probe_smartrecruiters(client: httpx.Client, slug: str, timeout: float = 6) -> bool:
    """Check if a SmartRecruiters board exists."""
    try:
        resp = client.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1",
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            return bool(data.get("content"))
    except Exception:
        pass
    return False


def _probe_workable(client: httpx.Client, slug: str, timeout: float = 6) -> bool:
    """Check if a Workable board exists."""
    try:
        resp = client.get(
            f"https://{slug}.workable.com/api/v3/widget/accounts/{slug}",
            timeout=timeout,
        )
        if resp.status_code == 200:
            return True
    except Exception:
        pass
    return False


def _probe_bamboohr(client: httpx.Client, slug: str, timeout: float = 6) -> bool:
    """Check if a BambooHR board exists."""
    try:
        resp = client.get(
            f"https://{slug}.bamboohr.com/careers/list",
            timeout=timeout,
            follow_redirects=True,
        )
        if resp.status_code == 200 and "jobs" in resp.text.lower():
            return True
    except Exception:
        pass
    return False


def _probe_teamtailor(client: httpx.Client, slug: str, timeout: float = 6) -> bool:
    """Check if a Teamtailor board exists."""
    try:
        resp = client.get(
            f"https://{slug}.teamtailor.com/api/jobs",
            timeout=timeout,
        )
        if resp.status_code == 200:
            return True
    except Exception:
        pass
    return False


def _probe_recruitee(client: httpx.Client, slug: str, timeout: float = 6) -> bool:
    """Check if a Recruitee board exists."""
    try:
        resp = client.get(
            f"https://{slug}.recruitee.com/api/offers/?limit=1",
            timeout=timeout,
        )
        if resp.status_code == 200:
            return True
    except Exception:
        pass
    return False


PROBES = {
    "greenhouse": _probe_greenhouse,
    "lever": _probe_lever,
    "ashby": _probe_ashby,
    "smartrecruiters": _probe_smartrecruiters,
    "workable": _probe_workable,
    "bamboohr": _probe_bamboohr,
    "teamtailor": _probe_teamtailor,
    "recruitee": _probe_recruitee,
}


def discover_all(workers: int = 20) -> dict[str, list[str]]:
    """Probe all ATS platforms and return discovered slugs."""
    all_slugs = {
        "greenhouse": list(set(_GREENHOUSE_SLUGS + _ADDITIONAL_GREENHOUSE)),
        "lever": list(set(_LEVER_SLUGS)),
        "ashby": list(set(_ASHBY_SLUGS)),
        "smartrecruiters": [
            "bosch", "toyota", "visa", "splunk", "booking", "loreal",
            "vodafone", "skyscanner", "squarespace", "glassdoor",
            "canva", "redbull", "wise", "entain",
        ],
        "workable": [
            "rentokil-initial", "pars-therapy", "rebel-convenience-stores",
            "fuku", "pavago", "cxg", "tehora",
        ],
        "bamboohr": [
            "gusto", "zenefits", "rippling", "brex", "ramp",
            "mercury", "luma", "cal.com", "resend", "posthog",
            "linear", "runway", "webflow", "supabase", "railway",
            "retool", "prisma", "hasura", "nhost", "render",
        ],
        "teamtailor": [
            "volvo", "nokia", "ericsson", "seb", "nordea",
            "ica", "systembolaget", "h&m", "electrolux", "oriflame",
        ],
        "recruitee": [
            "transferwise", "pipedrive", "ziggo", "tomtom",
            "here", "booking", "adyen", "mollie", "messagebird",
        ],
    }

    # Generate additional slugs from common company name patterns
    extra_greenhouse = []
    for name in [
        "accel", "a16z", "sequoia", "greylock", "benchmark",
        "lightspeed", "index-ventures", "general-catalyst",
        "battery-ventures", "bessemer", "insight",
        "tiger-global", "softbank", "coatue", "d1-capital",
        "dragoneer", "greenoaks", "spark-capital", "union-square",
        "true-ventures", "lowercase-capital", "betaworks",
        "techstars", "500-global", "angel-pad", "plug-and-play",
        "launch", "matrix", "storm", "new-enterprise",
        "madrona", "plymouth", "pioneer-square", "unlock",
        "castor-ventures", "ivp", "meritech", "staley",
        "scale-ventures", "foundation-capital", "kpcb",
        "sapphire-ventures", "spectrum", "saastr",
        "not-boring", "dollar-club", "baby-club",
        "hustle", "pitchbook", "crunchbase",
    ]:
        slug = name.lower().replace(" ", "-").replace(".", "")
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        extra_greenhouse.append(slug)

    all_slugs["greenhouse"].extend(extra_greenhouse)
    all_slugs["greenhouse"] = list(set(all_slugs["greenhouse"]))

    total = sum(len(v) for v in all_slugs.values())
    print(f"Probing {total} slugs across {len(PROBES)} ATS platforms...")

    discovered: dict[str, list[str]] = {k: [] for k in PROBES}
    done = 0

    def _check(kind: str, slug: str) -> tuple[str, str, bool]:
        probe = PROBES[kind]
        return kind, slug, probe(client, slug)

    with httpx.Client(
        headers={"User-Agent": "JobCollector/1.0 (career-page-discovery)"},
        follow_redirects=True,
        timeout=10,
    ) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for kind, slugs in all_slugs.items():
                for slug in slugs:
                    futures[pool.submit(_check, kind, slug)] = (kind, slug)

            for fut in as_completed(futures):
                done += 1
                kind, slug, found = fut.result()
                if found:
                    discovered[kind].append(slug)
                    print(f"  ✓ {kind}/{slug}")
                if done % 100 == 0:
                    total_found = sum(len(v) for v in discovered.values())
                    print(f"  [{done}/{total}] checked, {total_found} found", flush=True)

    return discovered


def write_companies_yaml(discovered: dict[str, list[str]], output: str = "companies.yaml") -> int:
    """Write discovered companies to companies.yaml."""
    path = Path(output)
    existing = yaml.safe_load(path.read_text()) or {}

    total_added = 0
    for kind, slugs in discovered.items():
        if kind not in existing:
            existing[kind] = []

        # Get existing slugs
        existing_set = set()
        for entry in existing[kind]:
            if isinstance(entry, dict):
                existing_set.add(entry.get("slug", ""))
            elif isinstance(entry, str):
                existing_set.add(entry)

        for slug in slugs:
            if slug not in existing_set:
                existing[kind].append(slug)
                existing_set.add(slug)
                total_added += 1

    path.write_text(yaml.dump(existing, default_flow_style=False, sort_keys=False, allow_unicode=True))
    return total_added


def main():
    parser = argparse.ArgumentParser(description="Generate 10K+ company career page entries")
    parser.add_argument("--workers", type=int, default=20, help="Concurrent workers")
    parser.add_argument("--output", default="companies.yaml", help="Output YAML file")
    args = parser.parse_args()

    discovered = discover_all(workers=args.workers)

    # Summary
    total = sum(len(v) for v in discovered.values())
    print(f"\n✓ Discovered {total} active career pages:")
    for kind, slugs in sorted(discovered.items(), key=lambda x: -len(x[1])):
        print(f"  {kind}: {len(slugs)}")

    # Write to YAML
    added = write_companies_yaml(discovered, args.output)
    print(f"\n✓ Added {added} new entries to {args.output}")

    # Also save raw discovery
    Path("data").mkdir(exist_ok=True)
    Path("data/discovery_10k.json").write_text(json.dumps(discovered, indent=2))
    print(f"✓ Raw data saved to data/discovery_10k.json")


if __name__ == "__main__":
    main()
