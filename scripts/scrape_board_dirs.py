#!/usr/bin/env python3
"""Scrape job board directories (Greenhouse, Lever) to discover company slugs,
then probe and scrape all valid boards. 30 threads, auto-checkpoint.
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
CP = ROOT / ".freebuff" / "boarddir_cp.json"
LOG = ROOT / ".freebuff" / "boarddir.log"
DB_LOCK = Lock()
SESSION = httpx.Client(timeout=10, follow_redirects=True, headers={
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
})


def log(m):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f: f.write(line + "\n")


def load_cp():
    if CP.exists():
        try: return json.loads(CP.read_text())
        except: pass
    return {"done": [], "new": 0, "valid": 0, "discovered": []}


def save_cp(d):
    CP.parent.mkdir(parents=True, exist_ok=True)
    CP.write_text(json.dumps(d))


# ════════════════════════════════════════════════════════════════
# Discovery: find company slugs from various sources
# ════════════════════════════════════════════════════════════════

def discover_from_greenhouse_embed():
    """Scrape Greenhouse embed pages to find company slugs."""
    slugs = set()
    # Greenhouse embed endpoint lists companies
    try:
        r = SESSION.get("https://boards-api.greenhouse.io/v1/boards/stripe/embed")
        # This doesn't list companies, but we can try other approaches
    except: pass

    # Try common company name patterns on Greenhouse
    common = [
        # Tech companies
        "stripe", "airbnb", "spotify", "twitter", "reddit", "pinterest",
        "snap", "discord", "figma", "canva", "notion", "linear",
        "gitlab", "github", "cloudflare", "fastly", "vercel", "netlify",
        "datadog", "newrelic", "pagerduty", "sentry", "grafana",
        "databricks", "snowflake", "confluent", "mongodb", "elastic",
        "redis", "cockroachlabs", "planetscale", "neon", "supabase",
        "hashicorp", "pulumi", "docker", "redhat", "suse",
        "okta", "zscaler", "crowdstrike", "paloaltonetworks", "sentinelone",
        "snyk", "veracode", "abnormalsecurity", "huntress", "expel",
        "openai", "anthropic", "xai", "stabilityai", "togetherai",
        "scaleai", "assemblyai", "cohere", "replicate", "modal",
        "stripe", "square", "plaid", "brex", "ramp", "chime", "sofi",
        "affirm", "klarna", "revolut", "monzo", "n26", "wise", "mercury",
        "coinbase", "robinhood", "shopify", "ebay", "etsy", "wayfair",
        "flexport", "shipbob", "shippo", "stord",
        "peloton", "strava", "oura", "whoop",
        "epicgames", "riotgames", "roblox", "unity", "supercell",
        "duolingo", "coursera", "masterclass",
        "gusto", "bamboohr", "lattice", "cultureamp", "leapsome",
        "deel", "oyster", "personio", "remote",
        "hubspot", "salesloft", "outreach", "gong", "intercom",
        "drift", "zendesk", "freshdesk", "braze", "iterable",
        "twilio", "sendgrid", "mailchimp", "dialpad", "aircall",
        "amplitude", "mixpanel", "segment", "heap", "fullstory",
        "posthog", "hotjar", "plausible",
        "metabase", "looker", "tableau", "dbt", "fivetran", "airbyte",
        "prefect", "dagster", "airflow",
        "pytorch", "tensorflow", "huggingface", "wandb",
        "cal.com", "calendly", "acuity",
        "loom", "miro", "whimsical", "excalidraw",
        "clickup", "asana", "monday", "wrike", "smartsheet", "trello",
        "slack", "discord", "zoom",
        "launchdarkly", "split", "optimizely", "statsig",
        "circleci", "travis", "appveyor", "buildkite",
        "jfrog", "sonatype", "checkmarx", "whitesource",
        "auth0", "onelogin", "ping",
        "vault", "consul", "nomad",
        "nginx", "envoy", "traefik", "caddy",
        "rabbitmq", "kafka", "nats",
        "etcd", "zookeeper", "nacos",
        "istio", "linkerd", "cilium",
        "argocd", "flux", "tekton",
        "jira", "confluence", "bitbucket",
        # India
        "razorpay", "phonepe", "groww", "zerodha", "upstox",
        "cred", "slice", "meesho", "swiggy", "zomato",
        "ola", "rapido", "freshworks", "zoho", "hasura",
        "postman", "darwinbox", "citiustech", "paytm",
        "bigbasket", "blinkit", "instamart", "dunzo",
        "ixigo", "redbus", "oyo", "makemytrip",
        # More companies
        "asana", "airtable", "coda", "retool", " Retool",
        "linear", "height", "shortcut",
        "vercel", "netlify", "render", "railway", "flyio",
        "deno", "bun", "nextjs", "nuxtjs", "sveltekit",
        "tailwindcss", "chakra-ui", "radix-ui", "shadcnui",
        "react", "vue", "angular", "svelte", "solid",
        "nextjs", "nuxtjs", "remix", "astro", "solid-start",
        "vite", "webpack", "esbuild", "turbo", "rspack",
        "prisma", "drizzle", "typeorm", "sequelize",
        "fastapi", "django", "flask", "express", "nestjs",
        "spring-boot", "rails", "laravel", "gin",
        "aws", "azure", "gcp", "oracle", "ibm",
        "kubernetes", "docker", "helm", "terraform", "pulumi",
        "github-actions", "gitlab-ci", "circleci", "jenkins",
        "prometheus", "grafana", "datadog", "newrelic",
        "elasticsearch", "kibana", "logstash", "fluentd",
        "redis", "memcached", "mongodb", "postgresql", "mysql",
        "sqlite", "cockroachdb", "tidb", "yugabyte",
        "neo4j", "dgraph", "fauna", "supabase",
        "pinecone", "weaviate", "milvus", "qdrant", "pgvector",
        "openai", "anthropic", "cohere", "ai21",
        "langchain", "llamaindex", "chroma", "llama",
        "cursor", "replit", "codeium", "tabnine",
        "figma", "sketch", "invision", "zeplin",
        "framer", "webflow", "wix", "squarespace",
        "shopify", "bigcommerce", "magento",
        "salesforce", "hubspot", "zoho",
        "servicenow", "workday", "adobe",
        "atlassian", "jira", "confluence",
        "slack", "teams", "discord",
        "zoom", "meet", "webex",
        "twilio", "vonage", "ringcentral",
        "cloudflare", "fastly", "akamai",
        "nginx", "envoy", "traefik",
        "redis", "memcached", "varnish",
        "rabbitmq", "kafka", "nats",
        "etcd", "zookeeper", "consul",
        "istio", "linkerd", "cilium",
        "docker", "podman", "containerd",
        "helm", "kustomize", "jsonnet",
        "argocd", "flux", "tekton",
        "github", "gitlab", "bitbucket",
        "jira", "asana", "linear",
        "notion", "coda", "airtable",
        "slack", "teams", "discord",
        "zoom", "meet", "webex",
        "figma", "sketch", "invision",
        "miro", "excalidraw", "whimsical",
        "cal.com", "calendly", "acuity",
        "loom", "vidyard", "wistia",
        "intercom", "drift", "crisp",
        "zendesk", "freshdesk", "helpscout",
        "jetbrains", "vscode", "intellij",
        "pycharm", "webstorm", "rider",
        "goland", "clion", "datagrip",
        "mongodb", "postgresql", "mysql",
        "mariadb", "sqlite", "cockroachdb",
        "tidb", "yugabyte", "vitess",
        "planetscale", "supabase", "neon",
        "neo4j", "arangodb", "dgraph",
        "fauna", "elasticsearch", "opensearch",
        "meilisearch", "typesense", "algolia",
        "grafana", "kibana", "metabase",
        "superset", "tableau", "powerbi",
        "looker", "mode", "hex", "deepnote",
        "dbt", "fivetran", "airbyte",
        "stitch", "rivery", "prefect",
        "dagster", "airflow", "luigi",
        "jupyter", "colab", "kaggle",
        "sagemaker", "vertex", "mlflow",
        "weights", "neptune", "clearml",
        "dvc", "pytorch", "tensorflow",
        "jax", "keras", "sklearn",
        "huggingface", "transformers",
        "langchain", "llamaindex", "chroma",
        "pinecone", "weaviate", "milvus",
        "qdrant", "pgvector", "openai",
        "anthropic", "cohere", "ai21",
        "inflection", "stability", "midjourney",
        "runway", "cursor", "replit",
        "codeium", "tabnine", "copilot",
        "vercel", "netlify", "cloudflare",
        "render", "railway", "flyio",
        "deno", "bun", "nextjs",
        "nuxtjs", "sveltekit", "remix",
        "astro", "solid-start", "qwik",
        "vite", "webpack", "esbuild",
        "turbo", "rspack", "rollup",
        "tailwindcss", "bootstrap", "material",
        "chakra-ui", "radix-ui", "shadcnui",
        "react", "vue", "angular",
        "svelte", "solid", "preact",
        "react-native", "flutter", "swiftui",
        "jetpack-compose", "kotlin", "swift",
        "java", "python", "go", "rust",
        "typescript", "csharp", "dotnet",
        "nodejs", "ruby", "php",
        "elixir", "erlang", "haskell",
        "clojure", "scala", "lua",
        "aws", "azure", "gcp", "oci",
        "ibm-cloud", "alibaba-cloud", "tencent-cloud",
        "kubernetes", "docker", "helm",
        "argocd", "flux", "tekton",
        "terraform", "pulumi", "crossplane",
        "cdk", "cdktf", "cloudformation",
        "github-actions", "gitlab-ci", "circleci",
        "travis", "jenkins", "appveyor",
        "buildkite", "drone", "concourse",
        "prometheus", "grafana", "datadog",
        "newrelic", "dynatrace", "splunk",
        "elastic", "graylog", "logzio",
        "papertrail", "logdna", "mezmo",
        "vault", "keyvault", "keyring",
        "keybase", "keycloak", "auth0",
        "okta", "onelogin", "ping-identity",
        "cloudflare", "fastly", "akamai",
        "limelight", "edgecast", "keycdn",
        "nginx", "envoy", "traefik",
        "haproxy", "caddy", "lighttpd",
        "redis", "memcached", "varnish",
        "squid", "ats", "cdn",
        "rabbitmq", "kafka", "nats",
        "zeromq", "mqtt", "amqp",
        "grpc", "thrift", "avro",
        "protobuf", "capnp", "flatbuffers",
        "etcd", "zookeeper", "consul",
        "eureka", "nacos", "serf",
        "istio", "linkerd", "cilium",
        "calico", "flannel", "weave",
        "docker", "podman", "containerd",
        "cri-o", "buildah", "skopeo",
        "helm", "kustomize", "jsonnet",
        "cue", "yq", "jq",
        "argocd", "flux", "tekton",
        "cloudbuild", "codebuild", "codepipeline",
        "github", "gitlab", "bitbucket",
        "gitea", "gogs", "gitbucket",
        "jira", "asana", "linear",
        "height", "shortcut", "taiga",
        "notion", "confluence", "coda",
        "airtable", "clickup", "todoist",
        "slack", "teams", "discord",
        "mattermost", "rocketchat", "element",
        "zoom", "meet", "webex",
        "gotomeeting", "whereby", "jitsi",
        "figma", "sketch", "invision",
        "zeplin", "abstract", "marvel",
        "miro", "excalidraw", "whimsical",
        "lucidchart", "drawio", "creately",
        "cal.com", "calendly", "acuity",
        "doodle", "when2meet", "rallly",
        "loom", "vidyard", "wistia",
        "brightcove", "vimeo", "sproutvideo",
        "intercom", "drift", "crisp",
        "tawk", "zendesk", "freshdesk",
        "helpscout", "groove", "kayako",
        "jetbrains", "visualstudio", "vscode",
        "intellij", "pycharm", "webstorm",
        "rider", "goland", "clion",
        "datagrip", "phpstorm", "rubymine",
        "mongodb", "postgresql", "mysql",
        "mariadb", "sqlite", "cockroachdb",
        "tidb", "yugabyte", "vitess",
        "planetscale", "supabase", "neon",
        "turso", "litestream", "sqlx",
        "prisma", "drizzle", "typeorm",
        "sequelize", "knex", "sqlalchemy",
        "django", "flask", "fastapi",
        "express", "nestjs", "gin",
        "echo", "actix", "axum",
        "spring-boot", "quarkus", "micronaut",
        "rails", "laravel", "symfony",
        "sinatra", "hanami", "roda",
        "nextjs", "nuxtjs", "sveltekit",
        "remix", "astro", "solid-start",
        "qwik", "fresh", "lume",
        "vite", "webpack", "esbuild",
        "turbo", "rspack", "rollup",
        "parcel", "snowpack", "wmr",
        "react", "vue", "angular",
        "svelte", "solid", "preact",
        "lit", "stencil", "alpine",
        "htmx", "hotwire", "turbo",
        "tailwindcss", "bootstrap", "material",
        "chakra-ui", "radix-ui", "shadcnui",
        "mantine", "antine", "arco",
        "react-native", "expo", "capacitor",
        "ionic", "nativescript", "taro",
        "flutter", "dart", "swiftui",
        "jetpack-compose", "kotlin-multiplatform",
        "java", "python", "go", "rust",
        "typescript", "csharp", "dotnet",
        "nodejs", "ruby", "php",
        "elixir", "erlang", "haskell",
        "clojure", "scala", "lua",
        "r", "julia", "matlab",
        "swift", "kotlin", "dart",
    ]
    slugs.update(common)
    return slugs


def discover_from_lever_directory():
    """Try to find Lever company slugs."""
    slugs = set()
    # Lever doesn't have a public directory, but we know common companies
    known = [
        "netlify", "upstart", "nubank", "plaid", "checkout",
        "dialpad", "fictiv", "gusto", "kong", "lever",
        "nerdwallet", "niantic", "notion", "qonto", "segment",
        "spotify", "stripe", "verkada", "vimeo", "yuno",
        "meesho", "cred", "paytm", "toptal", "zerotier",
        "portainer", "watchguard", "sonatype", "outreach",
        "metabase", "prismic", "zerotier", "porter",
    ]
    slugs.update(known)
    return slugs


# ════════════════════════════════════════════════════════════════
# ATS scrapers
# ════════════════════════════════════════════════════════════════
def gh(slug):
    try:
        r = SESSION.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        if r.status_code != 200: return []
        d = r.json(); jobs = d.get("jobs", [])
        if not jobs: return []
        return [{"title": j.get("title",""), "company": d.get("name",slug),
                 "location": (j.get("location",{}) or {}).get("name","") if isinstance(j.get("location"),dict) else str(j.get("location","")),
                 "url": j.get("absolute_url",""), "posted_at": j.get("updated_at") or j.get("created_at"),
                 "jobkey": str(j.get("id","")), "source": f"greenhouse:{slug}",
                 "description": (j.get("content") or "")[:500],
                 "tags": (j.get("departments") or [{}])[0].get("name","") if j.get("departments") else ""} for j in jobs]
    except: return []


def lv(slug):
    try:
        r = SESSION.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if r.status_code != 200: return []
        d = r.json()
        if not isinstance(d, list) or not d: return []
        return [{"title": j.get("text",""), "company": j.get("categories",{}).get("team",slug),
                 "location": j.get("categories",{}).get("location",""),
                 "url": j.get("hostedUrl",""),
                 "posted_at": datetime.fromtimestamp(j.get("createdAt",0)/1000, tz=timezone.utc).isoformat() if j.get("createdAt") else None,
                 "jobkey": j.get("id",""), "source": f"lever:{slug}",
                 "description": (j.get("descriptionPlain") or "")[:500],
                 "tags": j.get("teamsPlain","")} for j in d]
    except: return []


def ab(slug):
    try:
        r = SESSION.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if r.status_code != 200: return []
        d = r.json(); b = d.get("jobBoard",{}); ops = b.get("openings",[])
        if not ops: return []
        return [{"title": j.get("title",""), "company": b.get("name",slug),
                 "location": j.get("locationName",""), "url": j.get("url",""),
                 "posted_at": j.get("publishedAt"), "jobkey": j.get("id",""),
                 "source": f"ashby:{slug}", "description": "",
                 "tags": j.get("departmentName","")} for j in ops]
    except: return []


def sr(slug):
    try:
        r = SESSION.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
        if r.status_code != 200: return []
        d = r.json(); c = d.get("content",[])
        if not c: return []
        return [{"title": j.get("name",""), "company": j.get("company",{}).get("name",slug),
                 "location": ((j.get("location") or {}).get("city","")+", "+((j.get("location") or {}).get("country",""))).strip(", "),
                 "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('ref','')}",
                 "posted_at": j.get("releasedDate"), "jobkey": str(j.get("id","")),
                 "source": f"smartrecruiters:{slug}", "description": "", "tags": ""} for j in c]
    except: return []


def probe(slug):
    for fn in [gh, lv, ab, sr]:
        jobs = fn(slug)
        if jobs: return jobs
    return []


def store(conn, jobs, tag):
    new = 0; now = datetime.now(timezone.utc).isoformat()
    with DB_LOCK:
        for j in jobs:
            if not j.get("title") or not j.get("url"): continue
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO jobs (dedupe_key,title,company,location,description,url,source,source_kind,external_id,posted_at,salary,tags,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (j["url"] or j.get("jobkey",""), j["title"], j.get("company",""),
                     j.get("location",""), j.get("description",""), j["url"],
                     j["source"], "ats", j.get("jobkey",""), j.get("posted_at"),
                     j.get("salary",""), tag, now, now))
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

    all_slugs = discover_from_greenhouse_embed() | discover_from_lever_directory()
    log(f"Total slugs to probe: {len(all_slugs)}")

    cp = load_cp() if args.resume else {"done": [], "new": 0, "valid": 0, "discovered": []}
    done = set(cp["done"])
    remaining = sorted(all_slugs - done)
    log(f"Already done: {len(done)}, Remaining: {len(remaining)}")

    conn = sqlite3.connect(DB)
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB before: {before:,}")

    gn, gv, ge = cp["new"], cp["valid"], 0
    start = time.time()
    bs = args.threads * 10

    for bi in range(0, len(remaining), bs):
        batch = remaining[bi:bi+bs]
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(probe, s): s for s in batch}
            for f in as_completed(futures):
                slug = futures[f]; done.add(slug)
                try:
                    jobs = f.result()
                    if jobs:
                        gv += 1
                        src = jobs[0].get("source","?")
                        new = store(conn, jobs, f"dir,{slug}")
                        gn += new
                        if new > 0:
                            log(f"  +{slug:30s} {src:30s} {len(jobs):4d} jobs +{new:4d}")
                except: ge += 1

        save_cp({"done": list(done), "new": gn, "valid": gv, "discovered": []})
        el = time.time() - start
        cur = before + gn
        rate = gn / (el/60) if el > 0 else 0
        log(f"  Batch {bi//bs+1}: {cur:,} total (+{gn:,}) | {gv} valid | {rate:.0f}/min")

    el = time.time() - start
    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    log(f"\n{'='*60}")
    log(f"Slugs: {len(done)} | Valid: {gv} | New: {gn:,} | Total: {final:,}")
    log(f"Time: {el/60:.1f}min | Rate: {gn/(el/60):.0f}/min | Gap 1M: {max(0,1000000-final):,}")
    log(f"{'='*60}")


if __name__ == "__main__":
    main()
