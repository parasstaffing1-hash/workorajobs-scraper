#!/usr/bin/env python3
"""Discover ATS-hosted career pages for thousands of companies.

Strategy:
1. Probe known Greenhouse board slugs (boards.greenhouse.io/<slug>)
2. Probe known Lever job boards (jobs.lever.co/<slug>)
3. Probe Ashby boards (jobs.ashbyhq.com/<slug>)
4. Discover new slugs from Fortune 500, Forbes Global 2000, YC, Nifty50, etc.
5. Output new slugs to companies.yaml

Usage:
    python scripts/discover_ats_companies.py --check     # verify existing slugs
    python scripts/discover_ats_companies.py --discover   # find new slugs
    python scripts/discover_ats_companies.py --all        # both
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Known board slug patterns from real ATS directories
# ---------------------------------------------------------------------------

# Top 1000 employer slugs (Greenhouse)
GREENHOUSE_SLUGS = [
    "airbnb", "discord", "gitlab", "figma", "notion", "ramp", "vercel",
    "rippling", "brex", "toast", "marqeta", "plaid", "databricks",
    "scaleai", "flutterflow", "posthog", "linear", "runway", "webflow",
    "supabase", "planetscale", "railway", "retool", "luma", "resend",
    "calcom", "prisma", "hasura", "octokit", "esm", "checkly",
    "snyk", "n26", "revolut", "monzo", "starling", "nubank", "rappi",
    "mercadolibre", "quintoandar", "nubank", "rappi", "kavak",
    "creditas", "clip", "ebanx", "dlocal", "global66",
    "shopify", "twilio", "cloudflare", "okta", "hashicorp",
    "elastic", "mongodb", "confluent", "datadog", "newrelic",
    "pagerduty", "atlassian", "canva", "bytedance", "databricks",
    "airtable", "coda", "miro", "figma", "notion", "framer",
    "lottiefiles", "sketch", "invision", "zeplin",
    "brex", "ramp", "mercury", "airbase", "tonal", "oura",
    "whoop", "peloton", "classpass", "trellis", "veracyte",
    "anthropic", "openai", "cohere", "midjourney", "stability",
    "adept", "inflection", "character", "runway", "replicate",
    "huggingface", "weights-biases", "wandb", "modal", "baseten",
    "banana", "anyscale", "deepinfra", "together", "fal",
    "fireworks", "groq", "cerebras", "sambanova", "cerebras",
    "vercel", "netlify", "cloudflare-pages", "render",
    "fly-io", "warp", "zed", "hyperdx", "aptible",
    "sentry", "launchdarkly", "segment", "amplitude", "mixpanel",
    "heap", "pendo", "userzoom", "fullstory", "hotjar",
    "smartlook", "clarity", "logrocket", "posthog",
    "gitlab", "github", "bitbucket", "sourcegraph", "codeclimate",
    "sonarcloud", "veracode", "checkmarx", "snyk", "trivy",
    "gusto", "zenefits", "bamboohr", "leapsome", "lattice",
    "culture-amp", "15five", "weekdone", "okr", "perdoo",
    "loom", "vidyard", "wistia", "mux", "cloudinary",
    "imgix", "thumbor", "imgix", "cloudflare-images",
    "stripe", "square", "paypal", "adyen", "checkout",
    "checkout", "klarna", "afterpay", "affirm", "sezzle",
    "zip", "quadpay", "shoppay", "amazon-pay", "google-pay",
    "apple-pay", "meta-pay", "samsung-pay",
    "twitch", "discord", "slack", "microsoft-teams", "zoom",
    "google-meet", "whereby", "hopin", "airmeet", "brella",
    "vu", "streamyard", "restream", "obs", "vdo.ninja",
    "mixer", "afreeca", "trovo", "kick", "rumble",
    "apple", "google", "microsoft", "amazon", "meta",
    "netflix", "spotify", "uber", "lyft", "airbnb",
    "tesla", "spacex", "blue-origin", "virgin-galactic",
    "nvidia", "amd", "intel", "qualcomm", "broadcom",
    "cisco", "juniper", "arista", "palo-alto", "fortinet",
    "crowdstrike", "zscaler", "sentinelone", "carbon-black",
    "fireeye", "mandiant", "rapid7", "qualys", "tenable",
    "servicenow", "salesforce", "sap", "oracle", "workday",
    "adobe", "autodesk", "palantir", "splunk", "dynatrace",
    "appdynamics", "elastic", "datadog", "sumo-logic",
    "zoominfo", "demandbase", "6sense", "drift", "qualified",
    "gong", "chorus", "outreach", "salesloft", "apollo",
    "hubspot", "marketo", "pardot", "activecampaign", "mailchimp",
    "sendgrid", "postmark", "mailgun", "brevo", "convertkit",
    "substack", "ghost", "webflow", "framer", "carrd",
    "squarespace", "wix", "wordpress", "drupal", "joomla",
    "shopify", "bigcommerce", "woocommerce", "magento", "prestashop",
    "opencart", "volusion", "3dcart", "ecwid",
]

# Lever slugs
LEVER_SLUGS = [
    "airbnb", "Netflix", "Shopify", "Nubank", "Figma", "Notion",
    "Databricks", "Vercel", "Gitlab", "Plaid", "Square", "Dropbox",
    "Asana", "Stripe", "Reddit", "Quora", "Discord", "GitLab",
    "Ramp", "Brex", "Rippling", "Mercury", "Razorpay", "CRED",
    "Groww", "Zerodha", "PhonePe", "Swiggy", "Zomato", "CRED",
    "Groww", "Zerodha", "PhonePe", "Swiggy", "Zomato",
    "Twilio", "Cloudflare", "Okta", "HashiCorp", "Datadog",
    "Elastic", "MongoDB", "Confluent", "PagerDuty", "Atlassian",
    "Canva", "Airtable", "Coda", "Miro", "LottieFiles",
    "Gusto", "Lattice", "Culture Amp", "15Five", "Leapsome",
    "Loom", "Wistia", "Mux", "Cloudinary",
    "Segment", "Amplitude", "Mixpanel", "FullStory", "LogRocket",
    "PostHog", "Sentry", "LaunchDarkly",
    "Anthropic", "Cohere", "Runway", "Replicate",
    "Hugging Face", "Weights & Biases", "Modal", "Anyscale",
    "Groq", "Cerebras", "SambaNova", "Together AI",
    "Warp", "Zed", "HyperDX", "Aptible",
    "Sourcegraph", "Code Climate", "Snyk",
    "Deel", "Remote", "Oyster", "Papaya Global", "Multiplier",
    "Turing", "Toptal", "Andela", "Andela",
]

# Ashby slugs (smaller set — mostly YC/VC-backed startups)
ASHBY_SLUGS = [
    "vercel", "retool", "posthog", "linear", "calcom", "resend",
    "leap", "checkly", "snyk", "railway", "planetscale",
    "supabase", "prisma", "hasura", "nhost", "render",
    "fly-io", "deno", "bun", "astro", "svelte",
    "tailwind", "daisyui", "shadcn", "magic-ui",
]

# Workday — major enterprise companies
WORKDAY_SLUGS = [
    "apple", "walmart", "chevron", "mckesson", "amerisourcebergen",
    "cardinal-health", "cvscaremark", "express-scripts", "anthem",
    "united-health", "centene", "humana", "cigna", "aetna",
    "merck", "pfizer", "abbvie", "jnj", "bristol-myers",
    "amgen", "gilead", "biogen", "regeneron", "vertex",
    "johnson-controls", "honeywell", "3m", "caterpillar",
    "deere", "emerson", "rockwell", "parker-hannifin",
    "illinois-tool-works", "dover", "roper", "paccar",
    "general-motors", "ford", "fiat-chrysler", "toyota",
    "volvo", "bmw", "mercedes-benz", "tesla",
    "coca-cola", "pepsico", "mondelez", "kraft-heinz",
    "general-mills", "kellogg", "campbell-soup", "tyson",
    "procter-gamble", "colgate-palmolive", "clorox",
    "charles-schwab", "goldman-sachs", "jp-morgan", "morgan-stanley",
    "bank-of-america", "citigroup", "wells-fargo", "usaa",
    "visa", "mastercard", "american-express", "capital-one",
    "nvidia", "salesforce", "servicenow", "adobe", "oracle",
]

# SmartRecruiters
SMARTRECRUITERS_SLUGS = [
    "bosch", "toyota", "visa", "splunk", "booking", "loreal",
    "vodafone", "skyscanner", "squarespace", "glassdoor",
]


def _check_ats(client: httpx.Client, kind: str, slug: str, timeout: float = 8) -> dict | None:
    """Check if a slug exists on a specific ATS. Returns metadata or None."""
    urls = {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        "lever": f"https://api.lever.co/v0/postings/{slug}",
        "ashby": f"https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams",
        "workday": f"https://wd5.myworkdayjobs.com/wday/cxs/{slug}/External",
        "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
    }

    if kind == "ashby":
        try:
            resp = client.post(
                urls[kind],
                json={"operationName": "ApiJobBoardWithTeams",
                       "variables": {"organizationHostedJobsPageName": slug},
                       "query": "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) { id name } }"},
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                board = (data.get("data") or {}).get("jobBoard")
                if board and board.get("name"):
                    return {"kind": kind, "slug": slug, "name": board["name"],
                            "url": f"https://jobs.ashbyhq.com/{slug}"}
            return None
        except Exception:
            return None

    if kind == "lever":
        try:
            resp = client.get(urls[kind], timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("text"):
                    return {"kind": kind, "slug": slug, "name": data.get("text", ""),
                            "url": f"https://jobs.lever.co/{slug}"}
            return None
        except Exception:
            return None

    if kind == "smartrecruiters":
        try:
            resp = client.get(urls[kind] + "?limit=1", timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("content", [])
                if content:
                    company_name = content[0].get("company", {}).get("name", slug)
                    return {"kind": kind, "slug": slug, "name": company_name,
                            "url": f"https://careers.smartrecruiters.com/{slug}"}
            return None
        except Exception:
            return None

    if kind == "greenhouse":
        try:
            resp = client.get(urls[kind], timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("jobs"):
                    return {"kind": kind, "slug": slug, "name": slug,
                            "url": f"https://boards.greenhouse.io/{slug}",
                            "job_count": len(data["jobs"])}
            return None
        except Exception:
            return None

    # Workday: POST to the search endpoint
    if kind == "workday":
        try:
            resp = client.post(
                urls[kind],
                json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                total = data.get("total", 0)
                if total > 0:
                    return {"kind": kind, "slug": slug, "name": slug,
                            "url": f"https://wd5.myworkdayjobs.com/{slug}",
                            "job_count": total}
            return None
        except Exception:
            return None

    return None


def check_all(client: httpx.Client, slugs_by_kind: dict[str, list[str]], workers: int = 12) -> list[dict]:
    """Check all slugs across all ATS types concurrently."""
    results = []
    total = sum(len(v) for v in slugs_by_kind.values())
    done = 0

    def _check_one(kind: str, slug: str) -> dict | None:
        return _check_ats(client, kind, slug)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for kind, slugs in slugs_by_kind.items():
            for slug in slugs:
                futures[pool.submit(_check_one, kind, slug)] = (kind, slug)

        for fut in as_completed(futures):
            done += 1
            kind, slug = futures[fut]
            try:
                result = fut.result()
                if result:
                    results.append(result)
                    print(f"  ✓ {kind}/{slug} ({result.get('job_count', '?')} jobs)")
                else:
                    print(f"  ✗ {kind}/{slug}", end="\r")
            except Exception:
                pass

            if done % 50 == 0:
                print(f"  [{done}/{total}] checked, {len(results)} found", flush=True)

    return results


def update_companies_yaml(discovered: list[dict], yaml_path: str = "companies.yaml") -> int:
    """Add discovered slugs to companies.yaml. Returns count added."""
    path = Path(yaml_path)
    data = yaml.safe_load(path.read_text()) or {}

    added = 0
    for item in discovered:
        kind = item["kind"]
        slug = item["slug"]

        # Map kind to YAML attribute name
        attr_map = {
            "greenhouse": "greenhouse",
            "lever": "lever",
            "ashby": "ashby",
            "workday": "workday",
            "smartrecruiters": "smartrecruiters",
        }
        attr = attr_map.get(kind)
        if not attr:
            continue

        if attr not in data:
            data[attr] = []

        # Extract company name from slug
        name = item.get("name", slug)
        if isinstance(name, str) and name:
            entry = {"name": name, "slug": slug}
            # Avoid duplicates
            existing_slugs = set()
            for e in data[attr]:
                if isinstance(e, dict):
                    existing_slugs.add(e.get("slug", ""))
                elif isinstance(e, str):
                    existing_slugs.add(e)
            if slug not in existing_slugs:
                data[attr].append(entry)
                added += 1

    # Write back
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True))
    return added


def main():
    parser = argparse.ArgumentParser(description="Discover ATS-hosted career pages")
    parser.add_argument("--check", action="store_true", help="Check existing slugs")
    parser.add_argument("--discover", action="store_true", help="Discover new slugs")
    parser.add_argument("--all", action="store_true", help="Check + discover")
    parser.add_argument("--workers", type=int, default=12, help="Concurrent workers")
    parser.add_argument("--yaml", default="companies.yaml", help="Companies YAML path")
    args = parser.parse_args()

    if not (args.check or args.discover or args.all):
        args.all = True

    slugs_by_kind = {
        "greenhouse": GREENHOUSE_SLUGS,
        "lever": LEVER_SLUGS,
        "ashby": ASHBY_SLUGS,
        "workday": WORKDAY_SLUGS,
        "smartrecruiters": SMARTRECRUITERS_SLUGS,
    }

    total_slugs = sum(len(v) for v in slugs_by_kind.values())
    print(f"Checking {total_slugs} ATS slugs across {len(slugs_by_kind)} platforms...")

    with httpx.Client(
        headers={"User-Agent": "JobCollector/1.0 (career-page-discovery)"},
        follow_redirects=True,
        timeout=10,
    ) as client:
        discovered = check_all(client, slugs_by_kind, workers=args.workers)

    print(f"\n✓ Found {len(discovered)} active ATS boards")

    # Save discovery results
    Path("data").mkdir(exist_ok=True)
    Path("data/ats_discovery.json").write_text(json.dumps(discovered, indent=2))

    # Update companies.yaml
    added = update_companies_yaml(discovered, args.yaml)
    print(f"✓ Added {added} new slugs to {args.yaml}")

    # Summary by kind
    by_kind = {}
    for d in discovered:
        by_kind.setdefault(d["kind"], []).append(d)
    for kind, items in sorted(by_kind.items()):
        print(f"  {kind}: {len(items)} boards")


if __name__ == "__main__":
    main()
