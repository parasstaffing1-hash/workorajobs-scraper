#!/usr/bin/env python3
"""MEGA SLUG GENERATOR — 50,000+ company slug variations.

Strategy: Generate every possible company name pattern, probe all on 7 ATS platforms.
Each valid board gives 50-400 unique jobs with ZERO dedup.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import string
import time
import itertools
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import httpx

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / ".freebuff" / "mega_slug.log"
CP = ROOT / ".freebuff" / "mega_slug_cp.json"
DB_LOCK = Lock()

_client = httpx.Client(
    timeout=3.0,
    follow_redirects=True,
    limits=httpx.Limits(max_connections=150, max_keepalive_connections=60),
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
    return {"done": [], "stats": {"new": 0, "boards": 0, "errors": 0}}


def save_cp(cp):
    CP.parent.mkdir(parents=True, exist_ok=True)
    done_list = list(cp["done"])
    if len(done_list) > 50000:
        done_list = done_list[-50000:]
    CP.write_text(json.dumps({"done": done_list, "stats": cp["stats"]}), "utf-8")


# =====================================================================
# MASSIVE SLUG GENERATOR
# =====================================================================

def generate_slugs() -> list[str]:
    """Generate 50,000+ unique company slug variations."""
    slugs = set()

    # --- Load all existing slugs from mega_probe.py ---
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
                    if 3 <= len(w) <= 40 and re.match(r'^[a-z0-9][a-z0-9._-]+$', w):
                        slugs.add(w)
                        slugs.add(w.replace("-", ""))
                        slugs.add(w.replace("_", ""))
                        slugs.add(w.replace(".", ""))

    # --- Load from discover_new_companies.py mega_probe too ---
    extra_files = [
        ROOT / "scripts" / "mega_probe.py",
        ROOT / "scripts" / "fast_probe.py",
    ]

    # --- COMMON REAL COMPANY NAME WORDS ---
    nouns = [
        "alpha", "beta", "gamma", "delta", "omega", "sigma", "theta", "zeta",
        "apex", "prime", "ultra", "mega", "super", "hyper", "turbo", "flash",
        "swift", "rapid", "quick", "fast", "instant", "snap", "dash", "bolt",
        "spark", "blaze", "flame", "fire", "heat", "warm", "cool", "chill",
        "frost", "ice", "snow", "rain", "storm", "cloud", "sky", "star", "sun",
        "moon", "mars", "venus", "neptune", "jupiter", "saturn", "pluto",
        "earth", "terra", "luna", "solar", "lunar", "astro", "cosmo", "nova",
        "zen", "zenith", "summit", "peak", "ridge", "crest", "wave", "tide",
        "river", "lake", "ocean", "sea", "bay", "cove", "reef", "shore",
        "pine", "oak", "elm", "ash", "cedar", "birch", "maple", "willow",
        "fern", "moss", "vine", "root", "leaf", "seed", "bloom", "bud",
        "rose", "lily", "lotus", "orchid", "iris", "daisy", "tulip",
        "ruby", "jade", "opal", "onyx", "topaz", "sapphire", "emerald",
        "crystal", "pearl", "amber", "coral", "gold", "silver", "bronze",
        "iron", "steel", "titan", "chrome", "nickel", "copper", "zinc",
        "eagle", "hawk", "falcon", "raven", "crow", "owl", "parrot",
        "wolf", "fox", "bear", "deer", "lion", "tiger", "lynx", "panther",
        "whale", "dolphin", "shark", "orca", "seal", "otter", "beaver",
        "bee", "ant", "wasp", "moth", "butterfly", "dragonfly",
        "ape", "cat", "dog", "cow", "pig", "hen", "ram", "yak", "gnu",
        "bit", "byte", "code", "data", "node", "link", "sync", "flow",
        "core", "edge", "hub", "lab", "net", "web", "app", "dev", "ops",
        "key", "map", "set", "run", "fly", "gem", "nut", "cap", "zip",
        "fox", "owl", "bat", "bug", "ray", "pin", "tap", "box", "hex",
        "ion", "jet", "kin", "mix", "van", "via", "zen", "ace", "bio",
    ]

    suffixes = [
        "tech", "works", "labs", "io", "ai", "app", "hub", "spot", "base",
        "stack", "cloud", "data", "pay", "health", "care", "flow", "sync",
        "shift", "rise", "bolt", "wave", "pulse", "spark", "link", "path",
        "core", "edge", "gate", "zone", "forge", "craft", "mind", "byte",
        "code", "dev", "ops", "net", "web", "bit", "key", "map", "set",
        "fly", "fox", "owl", "bee", "ant", "gem", "nut", "oak", "elm",
        "ai", "io", "ly", "fy", "ify", "ize", "hub", "now", "up", "go",
        "app", "dev", "ops", "net", "com", "co", "inc", "lab", "labs",
        "systems", "solutions", "platforms", "group", "corp", "digital",
        "ventures", "partners", "capital", "studios", "collective",
    ]

    # Two-word combos: noun + suffix
    for n in nouns:
        for s in suffixes:
            slugs.add(f"{n}{s}")
            slugs.add(f"{n}-{s}")

    # --- REAL COMPANY NAME PATTERNS ---
    # These are actual patterns that real companies use
    real_patterns = [
        # {adj}{noun} pattern
        ("smart", nouns[:30]),
        ("fast", nouns[:30]),
        ("bright", nouns[:30]),
        ("blue", nouns[:30]),
        ("green", nouns[:30]),
        ("dark", nouns[:30]),
        ("deep", nouns[:30]),
        ("high", nouns[:30]),
        ("pure", nouns[:30]),
        ("clear", nouns[:30]),
        ("open", nouns[:30]),
        ("new", nouns[:30]),
        ("one", nouns[:30]),
        ("first", nouns[:30]),
        ("top", nouns[:30]),
        ("best", nouns[:30]),
        ("big", nouns[:30]),
        ("mini", nouns[:30]),
        ("micro", nouns[:30]),
        ("macro", nouns[:30]),
        ("nano", nouns[:30]),
        ("ultra", nouns[:30]),
        ("meta", nouns[:30]),
        ("next", nouns[:30]),
        ("future", nouns[:30]),
        ("modern", nouns[:30]),
        ("prime", nouns[:30]),
        ("peak", nouns[:30]),
        ("core", nouns[:30]),
        ("edge", nouns[:30]),
        ("hub", nouns[:30]),
        ("sky", nouns[:30]),
        ("sun", nouns[:30]),
        ("moon", nouns[:30]),
        ("star", nouns[:30]),
        ("red", nouns[:30]),
        ("black", nouns[:30]),
        ("white", nouns[:30]),
        ("silver", nouns[:30]),
        ("golden", nouns[:30]),
    ]

    for adj, word_list in real_patterns:
        for w in word_list:
            slugs.add(f"{adj}{w}")
            slugs.add(f"{adj}-{w}")

    # --- COMMON TECH COMPANY SUFFIXES WITH REAL PREFIXES ---
    real_companies = [
        # AI/ML
        "openai", "anthropic", "cohere", "stability", "inflection", "character",
        "runway", "jasper", "copy", "writesonic", "grammarly", "notion",
        "obsidian", "figma", "canva", "sketch", "miro", "framer",
        "midjourney", "deepmind", "huggingface", "replicate", "modal",
        "weights", "wandb", "neptune", "determined", "anyscale",
        "together", "assemblyai", "mistral", "aleph", "voyage",
        "scale", "snorkel", "darktrace", "palantir", "c3ai",
        "dataiku", "domino", "dominodatalab", "databricks",
        "snowflake", "hashicorp", "pulumi", "terragrunt",

        # SaaS
        "salesforce", "hubspot", "marketo", "pardot", "intercom",
        "zendesk", "freshdesk", "helpscout", "front", "crisp",
        "drift", "qualified", "ollo", "sixsense", "demandbase",
        "terminus", "gong", "chorus", "chorusai", "clari",
        "boostup", "apollo", "lusha", "clearbit", "zoominfo",

        # Dev tools
        "gitlab", "github", "bitbucket", "jira", "confluence",
        "linear", "height", "shortcut", "clubhouse", "plane",
        "taiga", "notion", "coda", "airtable", "smartsheet",
        "asana", "monday", "clickup", "basecamp", "trello",

        # Cloud / Infra
        "aws", "gcp", "azure", "digitalocean", "linode", "vultr",
        "heroku", "vercel", "netlify", "cloudflare", "fastly",
        "akamai", "imperva", "incapsula", "sucuri",

        # Security
        "crowdstrike", "sentinelone", "cylance", "carbonblack",
        "snyk", "veracode", "checkmarx", "whitesource", "mend",
        "wiz", "lacework", "prisma", "paloalto", "fortinet",
        "zscaler", "okta", "onelogin", "auth0", "ping",

        # Data
        "mongodb", "elastic", "redis", "couchbase", "cassandra",
        "dynamodb", "cosmosdb", "neo4j", "arangodb", "fauna",
        "supabase", "planetscale", "tiDB", "cockroach", "yugabyte",
        "confluent", "pulsar", "kafka", "redpanda", "materialize",

        # Fintech
        "stripe", "square", "plaid", "brex", "ramp", "chime",
        "sofi", "affirm", "klarna", "wise", "revolut", "monzo",
        "n26", "starling", "tide", "checkout", "adyen", "worldpay",
        "marqeta", "lithic", "bond", "synapse", "column",

        # Ecommerce
        "shopify", "bigcommerce", "woocommerce", "magento",
        "saleor", "medusa", "commercejs", "snipcart",

        # Productivity
        "slack", "discord", "teams", "zoom", "google", "microsoft",
        "apple", "meta", "amazon", "netflix", "spotify",

        # Health
        "hims", "hers", "noom", "calm", "headspace", "teladoc",
        "amwell", "lyra", "spring", "cerebral", "cerebral",

        # Food
        "doordash", "ubereats", "grubhub", "instacart", "postmates",
        "gopuff", "getir", "jokr", "jumia", "swiggy", "zomato",

        # Mobility
        "uber", "lyft", "bolt", "lime", "bird", "tier", "waymo",
        "cruise", "argodrive", "aurora", "nuro", "pathname",

        # Real estate
        "zillow", "redfin", "opendoor", "compass", "houzz",
        "procore", "buildertrend", "cozy", "avail",

        # HR
        "bamboo", "gusto", "rippling", "deel", "remote",
        "oyster", "papaya", "factorial", "personio",
        "leapsome", "culture", "15five", "lattice",

        # More real names
        "palantir", "twilio", "sendgrid", "vonage", "plivo",
        "ringcentral", "8x8", "genesys", "five9", "dialpad",
        "aircall", "kustomer", "talkdesk", "twilio",

        # Indian companies
        "razorpay", "phonepe", "groww", "zerodha", "upstox",
        "cred", "slice", "meesho", "swiggy", "zomato",
        "ola", "rapido", "dunzo", "porter", "blackbuck",
        "oyo", "makemytrip", "goibibo", "cleartrip",
        "freshworks", "zoho", "hasura", "postman",
        "curefit", "healthifyme", "practo", "1mg",
        "bigbasket", "blinkit", "instamart",
        "unacademy", "byjus", "physicswalla", "upgrad",
        "urbancompany", "khatabook", "mygate", "nobroker",
        "cashfree", "pine-labs", "lendingkart", "indifi",

        # European
        "sap", "siemens", "bmw", "mercedes", "volkswagen",
        "adidas", "puma", "allianz", "munichre", "shell",
        "asos", "deliveryhero", "hellofresh", "getyourguide",
        "personio", "lempire", "aircall", "contentful",
        "northvolt", "volvo", "polestar", "spotify",

        # More global
        "grab", "gojek", "rappi", "didi", "tencent",
        "alibaba", "bytedance", "jd", "meituan", "sea",
        "samsung", "lg", "sony", "panasonic", "nec",
        "fujitsu", "hitachi", "toshiba", "softbank",
    ]

    for name in real_companies:
        slugs.add(name.lower())
        slugs.add(name.lower().replace("-", ""))
        slugs.add(name.lower().replace("_", ""))

    # --- ALPHABETICAL PERMUTATIONS (short) ---
    # 3-letter combos on Greenhouse often work
    for c1 in string.ascii_lowercase:
        for c2 in string.ascii_lowercase:
            for c3 in string.ascii_lowercase:
                slugs.add(f"{c1}{c2}{c3}")

    # 4-letter combos (common for startups)
    for c1 in "abcdefghiklmnorstuwz":
        for c2 in "abcdefghiklmnorstuwz":
            for c3 in "acehiklmnorstuwz":
                for c4 in "acehiklmnorstuwz":
                    slugs.add(f"{c1}{c2}{c3}{c4}")

    # --- NUMERIC PATTERNS ---
    for i in range(1, 10000):
        slugs.add(str(i))

    # Filter
    bad = {"the", "and", "for", "inc", "com", "all", "new", "our", "app",
           "get", "add", "its", "can", "has", "had", "was", "are", "not",
           "also", "into", "with", "from", "this", "that", "than", "your",
           "you", "now", "but", "may", "any", "old", "our", "her", "him",
           "his", "its", "our", "out", "own", "too", "two", "way", "who",
           "why", "yes", "yet", "how", "let", "put", "say", "she", "use"}

    result = sorted([s for s in slugs if 3 <= len(s) <= 40 and s not in bad])
    return result


# =====================================================================
# FAST ATS PROBING
# =====================================================================

def probe(slug: str) -> list[dict] | None:
    """Try slug on all ATS platforms, return jobs from first match."""
    # Greenhouse
    try:
        r = _client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            jobs = data.get("jobs", [])
            if jobs:
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
        pass

    # Lever
    try:
        r = _client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
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
        pass

    # Ashby
    try:
        r = _client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            board = data.get("jobBoard", {})
            openings = board.get("openings", [])
            if openings:
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
        pass

    # SmartRecruiters
    try:
        r = _client.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            content = data.get("content", [])
            if content:
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
        pass

    # Workable
    try:
        r = _client.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            jobs = data.get("jobs", [])
            if jobs:
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
        pass

    # TeamTailor
    try:
        r = _client.get(f"https://{slug}.teamtailor.com/jobs.json", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            jobs = data if isinstance(data, list) else data.get("jobs", [])
            if jobs:
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
        pass

    return None


def store(conn, jobs) -> int:
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
                     j.get("posted_at"), j.get("salary", ""), "", now, now))
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=100)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    if args.reset and CP.exists():
        CP.unlink()

    log("Generating slugs...")
    slugs = generate_slugs()
    log(f"Total slugs: {len(slugs)}")

    cp = load_cp() if args.resume else {"done": [], "stats": {"new": 0, "boards": 0, "errors": 0}}
    done_set = set(cp["done"])
    remaining = [s for s in slugs if s not in done_set]
    log(f"Done: {len(done_set)}, Remaining: {len(remaining)}")

    if not remaining:
        log("All slugs probed!")
        return

    conn = sqlite3.connect(DB, check_same_thread=False)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB: {total_before:,} | Gap 1M: {max(0, 1_000_000 - total_before):,}")

    new_total = cp["stats"]["new"]
    boards = cp["stats"]["boards"]
    errs = cp["stats"]["errors"]
    start = time.time()

    BATCH = 1000
    for bi in range(0, len(remaining), BATCH):
        batch = remaining[bi:bi+BATCH]
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(probe, s): s for s in batch}
            for f in as_completed(futures):
                slug = futures[f]
                done_set.add(slug)
                try:
                    jobs = f.result()
                    if jobs:
                        boards += 1
                        new = store(conn, jobs)
                        new_total += new
                        if new > 0:
                            log(f"  +{new:4d} {slug:30s} -> {jobs[0]['source']:30s} ({len(jobs)} jobs)")
                except Exception:
                    errs += 1

        cp["done"] = list(done_set)
        cp["stats"] = {"new": new_total, "boards": boards, "errors": errs}
        save_cp(cp)

        elapsed = time.time() - start
        current = total_before + new_total
        rate = new_total / (elapsed / 60) if elapsed > 0 else 0
        done_pct = len(done_set) * 100 / len(slugs)
        batch_num = bi // BATCH + 1
        total_batches = (len(remaining) + BATCH - 1) // BATCH
        log(f"  [{batch_num}/{total_batches}] {len(done_set)}/{len(slugs)} ({done_pct:.0f}%) | DB: {current:,} (+{new_total:,}) | Boards: {boards} | {rate:.0f}/min | Gap: {max(0, 1_000_000 - current):,}")

        if current >= 1_000_000:
            log(f"\n*** 1M JOBS REACHED! ***")
            break

    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    elapsed = time.time() - start
    conn.close()
    _client.close()

    log("")
    log("=" * 60)
    log(f"MEGA SLUG PROBE COMPLETE")
    log(f"Slugs: {len(done_set)} | Boards: {boards} | +{new_total:,} new | Errors: {errs}")
    log(f"DB: {final:,} | Gap 1M: {max(0, 1_000_000 - final):,}")
    log(f"Time: {elapsed/60:.1f} min | Rate: {new_total/(elapsed/60):.0f}/min")
    log("=" * 60)


if __name__ == "__main__":
    main()
