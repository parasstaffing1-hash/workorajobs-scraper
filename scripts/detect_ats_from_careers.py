#!/usr/bin/env python3
"""Detect ATS platform from company career page URLs.

Probes known ATS URL patterns to find which platform hosts a company's jobs.

Usage:
    python scripts/detect_ats_from_careers.py --names "google,microsoft,apple"
    python scripts/detect_ats_from_careers.py --file company_names.txt
    python scripts/detect_ats_from_careers.py --generate  # auto-generate from lists
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _slugify(name: str) -> str:
    """Convert company name to ATS slug."""
    slug = name.lower().strip()
    slug = slug.replace(" ", "").replace(".", "").replace("'", "")
    slug = slug.replace("&", "and").replace(",", "").replace("(", "").replace(")", "")
    slug = re.sub(r"[^a-z0-9]", "", slug)
    return slug


def _probe_greenhouse(client: httpx.Client, slug: str, timeout: float = 5) -> dict | None:
    try:
        resp = client.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobs", [])
            if jobs:
                return {"kind": "greenhouse", "slug": slug, "count": len(jobs)}
    except Exception:
        pass
    return None


def _probe_lever(client: httpx.Client, slug: str, timeout: float = 5) -> dict | None:
    try:
        resp = client.get(
            f"https://api.lever.co/v0/postings/{slug}",
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("text"):
                return {"kind": "lever", "slug": slug}
    except Exception:
        pass
    return None


def _probe_ashby(client: httpx.Client, slug: str, timeout: float = 5) -> dict | None:
    try:
        resp = client.post(
            "https://jobs.ashbyhq.com/api/non-user-graphql",
            json={
                "operationName": "ApiJobBoardWithTeams",
                "variables": {"organizationHostedJobsPageName": slug},
                "query": "query ApiJobBoardWithTeams($o: String!) { jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $o) { id name } }",
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            board = (data.get("data") or {}).get("jobBoard")
            if board and board.get("name"):
                return {"kind": "ashby", "slug": slug, "name": board["name"]}
    except Exception:
        pass
    return None


def _probe_smartrecruiters(client: httpx.Client, slug: str, timeout: float = 5) -> dict | None:
    try:
        resp = client.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1",
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("content", [])
            if content:
                name = content[0].get("company", {}).get("name", slug)
                return {"kind": "smartrecruiters", "slug": slug, "name": name}
    except Exception:
        pass
    return None


def _probe_workable(client: httpx.Client, slug: str, timeout: float = 5) -> dict | None:
    try:
        resp = client.get(
            f"https://{slug}.workable.com/api/v3/widget/accounts/{slug}",
            timeout=timeout,
        )
        if resp.status_code == 200:
            return {"kind": "workable", "slug": slug}
    except Exception:
        pass
    return None


def _probe_bamboohr(client: httpx.Client, slug: str, timeout: float = 5) -> dict | None:
    try:
        resp = client.get(
            f"https://{slug}.bamboohr.com/careers/list",
            timeout=timeout,
            follow_redirects=True,
        )
        if resp.status_code == 200 and "jobs" in resp.text.lower():
            return {"kind": "bamboohr", "slug": slug}
    except Exception:
        pass
    return None


def detect_ats(client: httpx.Client, name: str) -> dict | None:
    """Try all ATS probes for a company name. Return first match."""
    slug = _slugify(name)
    probes = [
        _probe_greenhouse,
        _probe_lever,
        _probe_ashby,
        _probe_smartrecruiters,
        _probe_workable,
        _probe_bamboohr,
    ]
    for probe in probes:
        result = probe(client, slug)
        if result:
            result["company"] = name
            return result
    # Try alternative slugs
    alt_slugs = [
        name.lower().replace(" ", "-"),
        name.lower().replace(" ", ""),
        name.lower().replace(" ", "_"),
    ]
    for alt in alt_slugs:
        for probe in probes:
            result = probe(client, alt)
            if result:
                result["company"] = name
                result["slug"] = alt
                return result
    return None


# ---------------------------------------------------------------------------
# MASSIVE company name lists
# ---------------------------------------------------------------------------

COMPANY_NAMES = [
    # Fortune 500 (top 100)
    "walmart", "amazon", "apple", "unitedhealth", "exxon", "berkshire",
    "alphabet", " cvshealth", "costco", "cardinalhealth", "mckesson",
    "amerisourcebergen", "microsoft", "cigna", "marathon", "phabet",
    "dow", "chevron", "pepsi", "phillips", "abbvie", "pfizer",
    "johnson", "unitedairlines", "kraftheinz", "procter", "generalmotors",
    "centene", "ibm", "anthem", "cardinal", "boeing", "coca",
    "charter", "honeywell", "3m", "costco", "wellsfargo",
    "bloomberg", "goldmansachs", "jpmand", "dell", "target",
    "lowes", "walgreens", "caterpillar", "merck", "general electric",
    "altera", "abbvie", "visa", "mastercard", "blackrock",
    "schwab", "northrop", "raytheon", "lockheed", "generaldynamics",
    "bae", "hp", "intuitive", "bestbuy", "northwesternmutual",
    "prudential", "metlife", "aflac", "allstate", "progressive",
    "usaa", "northland", "synchrony", "ally", "Discover",
    "tjx", "rossstores", "gap", "lbrands", "nordstrom",
    "dollar", "familydollar", "seveneleven", "macys", "biglots",
    "starbucks", "mcdonalds", "yumbrands", "subway", "chipotle",
    "dominos", "wendys", "dunkin", "sonic", "papa",
    "nike", "adidas", "underarmour", "lululemon", "ri",
    "deere", "emerson", "rockwell", "parkerhannifin", "illinoistool",
    "dover", "roper", "paccar", "cummins", "eaton",
    "wasteconnections", "republicservices", "waste management",
    "tysonfoods", "sandersonfarms", "pilgrims", "hormel", "smithfield",
    "kellogg", "general mills", "campbell", "conagra", "hain",
    "mondelez", "kraft", "hershey", "mccormick", "jbs",
    # Tech Companies (1000+)
    "google", "microsoft", "apple", "amazon", "meta", "netflix", "spotify",
    "uber", "lyft", "airbnb", "twitter", "snap", "pinterest", "linkedin",
    "salesforce", "oracle", "sap", "adobe", "vmware", "cisco", "intel",
    "nvidia", "amd", "qualcomm", "broadcom", "ibm", "hp", "dell",
    "sony", "samsung", "lg", "huawei", "xiaomi",
    "cloudflare", "fastly", "akamai",
    "datadog", "newrelic", "pagerduty", "dynatrace", "splunk", "elastic",
    "mongodb", "redis", "confluent", "snowflake", "databricks",
    "hashicorp", "atlassian", "slack", "zoom", "dropbox", "box",
    "github", "gitlab", "circleci", "sonarqube", "snyk",
    "vercel", "netlify", "render", "fly-io", "railway",
    "supabase", "planetscale", "neon", "prisma", "hasura",
    "sentry", "logrocket", "posthog",
    "openai", "anthropic", "cohere", "stability", "midjourney", "huggingface",
    "replicate", "modal", "anyscale", "together", "groq", "cerebras",
    "stripe", "square", "paypal", "adyen", "klarna", "affirm",
    "plaid", "marqeta", "ramp", "brex", "mercury",
    "chime", "nubank", "n26", "revolut", "monzo",
    "wise", "robinhood", "coinbase", "gemini", "kraken",
    "shopify", "bigcommerce", "ebay", "etsy",
    "tiktok", "bytedance", "reddit", "quora", "medium", "substack",
    "figma", "sketch", "canva", "miro", "notion", "airtable", "coda",
    "loom", "wistia", "mux", "cloudinary",
    "twitch", "discord", "signal", "telegram",
    "epic-games", "riot", "blizzard", "activision", "ubisoft",
    "ea", "take-two", "rockstar", "valve",
    "crowdstrike", "zscaler", "palo-alto", "fortinet",
    "sentinelone", "darktrace", "cybereason",
    "tempus", "flatiron", "guardant", "grail", "color",
    "oura", "whoop", "peloton", "nurx", "ro", "hims",
    "zocdoc", "talkspace", "betterhelp", "calm",
    "servicenow", "workday", "bamboo", "gusto", "rippling",
    "lattice", "deel", "remote", "oyster",
    "tesla", "spacex", "rivian", "lucid", "nio", "xpeng",
    "disney", "warner", "paramount", "nbc", "spotify",
    "coca-cola", "pepsi", "nestle", "unilever",
    "deloitte", "pwc", "kpmg", "ey", "accenture",
    "capgemini", "cognizant", "infosys", "wipro", "tcs",
    "lockheed", "raytheon", "northrop", "boeing", "l3harris",
    "pfizer", "moderna", "merck", "abbvie", "amgen", "gilead",
    "novartis", "roche", "astrazeneca",
    "chevron", "exxon", "shell", "bp",
    "zillow", "redfin", "opendoor", "compass", "procore",
    "coursera", "udemy", "edx", "pluralsight", "duolingo",
    "marriott", "hilton", "hyatt", "booking", "expedia",
    "fedex", "ups", "dhl", "flexport",
    "at-t", "verizon", "t-mobile", "comcast",
    "nike", "adidas", "lululemon", "underarmour",
    "home-depot", "lowes", "costco", "target", "walmart",
    "best-buy", "nordstrom", "macys", "gap",
    # Indian IT Services
    "tcs", "infosys", "wipro", "hcl", "tech-mahindra",
    "larsen", "persistent", "mphasis", "hexaware", "sonata",
    "mindtree", "ltts", "route", "niit", "bsnl",
    # Indian Startups
    "razorpay", "phonepe", "swiggy", "zomato", "ola",
    "byju", "unacademy", "vedantu", "meesho", "Groww",
    "cred", "policybazaar", "dream11", "openai", "physicswallah",
    # European Tech
    "revolut", "n26", "monzo", "starling", "wise",
    "adidas", "bmw", "mercedes", "siemens", "sap",
    "arm", "spotify", "king", "skyscanner", "wise",
    "booking", "adyen", "mollie", " messagebird",
    # Chinese Tech
    "alibaba", "tencent", "baidu", "jd.com", "pinduoduo",
    "bytedance", "didi", "meituan", "netease", "xiaomi",
    # Japanese / Korean Tech
    "sony", "samsung", "lg", "nintendo", "nec",
    "fujitsu", "hitachi", "toshiba", "panasonic",
    # Australian / NZ
    "atlassian", "canva", "afterpay", "zip", "xero",
    # Canadian
    "shopify", "hut8", "nuvei", "lightspeed", "descartes",
    # Israeli
    "check", "wix", "monday", "basel", "gong",
    "salesloft", "outreach", "chili", "payoneer",
    # Latin American
    "mercadolibre", "rappi", "nubank", "kavak", "creditas",
    "clip", "ebanx", "dlocal", "global66",
    # Southeast Asian
    "grab", "gojek", "sea", "shopee", "lazada",
    "tokopedia", "bukalapak", "traveloka",
    # Middle Eastern
    "careem", "noon", "swvl", "fawry",
    # African
    "flutterwave", "paystack", "opay", "jumia", "andela",
    # Ukrainian / Eastern European
    "gitlab", "grammarly", "macpaw", "jetoctopus", "ajax",
    # Remote-First Companies
    "automattic", "gitlab", "buffer", "zapier", "toptal",
    "andela", "turing", "crossover", "upwork", "fiverr",
]


def main():
    parser = argparse.ArgumentParser(description="Detect ATS platforms for companies")
    parser.add_argument("--names", help="Comma-separated company names")
    parser.add_argument("--file", help="File with one company name per line")
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--output", default="companies.yaml")
    args = parser.parse_args()

    names = []
    if args.names:
        names = [n.strip() for n in args.names.split(",") if n.strip()]
    elif args.file:
        names = [line.strip() for line in Path(args.file).read_text().splitlines() if line.strip()]
    else:
        names = COMPANY_NAMES

    print(f"Detecting ATS for {len(names)} companies...")

    results: dict[str, list[dict]] = {}
    done = 0

    with httpx.Client(
        headers={"User-Agent": "JobCollector/1.0 (ats-detection)"},
        follow_redirects=True,
        timeout=10,
    ) as client:
        def _check(name: str) -> dict | None:
            return detect_ats(client, name)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_check, n): n for n in names}

            for fut in as_completed(futures):
                done += 1
                name = futures[fut]
                try:
                    result = fut.result()
                    if result:
                        kind = result["kind"]
                        results.setdefault(kind, []).append(result)
                        print(f"  [OK] {name} -> {kind}")
                except Exception:
                    pass

                if done % 100 == 0:
                    total = sum(len(v) for v in results.values())
                    print(f"  [{done}/{len(names)}] checked, {total} found", flush=True)

    # Summary
    total = sum(len(v) for v in results.values())
    print(f"\n[OK] Detected {total} ATS platforms:")
    for kind, items in sorted(results.items(), key=lambda x: -len(x[1])):
        print(f"  {kind}: {len(items)}")

    # Write to YAML
    path = Path(args.output)
    data = yaml.safe_load(path.read_text()) or {}

    added = 0
    for kind, items in results.items():
        if kind not in data:
            data[kind] = []
        existing = set()
        for entry in data[kind]:
            if isinstance(entry, dict):
                existing.add(entry.get("slug", ""))
            elif isinstance(entry, str):
                existing.add(entry)

        for item in items:
            slug = item.get("slug", "")
            if slug and slug not in existing:
                data[kind].append(slug)
                existing.add(slug)
                added += 1

    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True))
    print(f"\n[OK] Added {added} new entries to {args.output}")

    # Save raw results
    Path("data").mkdir(exist_ok=True)
    raw = {k: [{"company": i.get("company"), "slug": i.get("slug")} for i in v] for k, v in results.items()}
    Path("data/ats_detection.json").write_text(json.dumps(raw, indent=2))
    print(f"[OK] Raw data saved to data/ats_detection.json")


if __name__ == "__main__":
    import json
    main()
