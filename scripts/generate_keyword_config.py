"""Generate `keywords.yaml` — a keyword-sweep config for the job collector.

Turns the software-engineering taxonomy into concrete source entries:

* ``boards:``   remotive entries (one per high-signal keyword — its server-side
                `?search=` genuinely narrows), plus one match-any entry per
                client-side board (arbeitnow/remoteok/jobicy fetch the feed
                once and match any of the taxonomy terms locally).
* ``adzuna:``   ``country|keyword`` per country using Adzuna's `what` search
                param. Adzuna's free tier is ~1,000 calls/month, so the default
                sweep stays tight (~24 calls/day for us+gb); raise ADZUNA_MONTHLY
                budget or reduce countries to taste.

Usage:
    python scripts/generate_keyword_config.py            # writes keywords.yaml
    jobcollect collect --config keywords.yaml --sources board,ats

The keyword lists below are the normalized, deduplicated heart of the full
software-engineering taxonomy (core titles, specializations, seniority, and the
technology stack). Edit the lists and re-run to regenerate.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "keywords.yaml"

# ---------------------------------------------------------------------------
# The taxonomy, normalized to searchable terms.
# ---------------------------------------------------------------------------

# Core engineering titles (all seniorities captured via the general terms).
CORE = [
    "software engineer",
    "software developer",
    "software programmer",
    "software development engineer",
    "application engineer",
    "application developer",
    "product engineer",
    "software architect",
    "software engineering manager",
    "engineering manager",
    "technical lead",
    "tech lead",
    "principal engineer",
    "staff engineer",
]

# Entry-level / graduate.
ENTRY = [
    "junior engineer",
    "junior developer",
    "associate engineer",
    "graduate engineer",
    "entry level engineer",
    "entry level developer",
    "fresher",
    "software engineer trainee",
    "intern",
    "apprentice",
]

# Specializations.
SPECIALIZATIONS = {
    "backend": [
        "backend engineer",
        "back-end engineer",
        "backend developer",
        "server-side engineer",
        "api engineer",
        "api developer",
        "distributed systems",
        "microservices",
        "middleware engineer",
    ],
    "frontend": [
        "frontend engineer",
        "front-end engineer",
        "frontend developer",
        "ui engineer",
        "ui developer",
        "web ui engineer",
        "client-side engineer",
    ],
    "fullstack": [
        "full stack engineer",
        "full-stack engineer",
        "full stack developer",
        "fullstack",
        "full cycle engineer",
    ],
    "mobile": [
        "mobile engineer",
        "mobile developer",
        "android engineer",
        "ios engineer",
        "ios developer",
        "react native",
        "flutter",
    ],
    "cloud": [
        "cloud engineer",
        "cloud developer",
        "cloud platform",
        "cloud infrastructure",
        "cloud-native",
    ],
    "devops-sre": [
        "devops engineer",
        "devops developer",
        "site reliability engineer",
        "sre",
        "reliability engineer",
        "production engineer",
        "platform engineer",
        "infrastructure engineer",
        "build engineer",
        "release engineer",
        "developer productivity",
        "developer experience",
        "ci/cd",
        "automation engineer",
        "systems engineer",
    ],
    "systems": [
        "systems programmer",
        "operating systems engineer",
        "os engineer",
        "kernel engineer",
        "kernel developer",
        "low-level software",
        "computer systems engineer",
    ],
    "embedded": [
        "embedded engineer",
        "embedded developer",
        "firmware engineer",
        "firmware developer",
        "device driver",
        "rtos engineer",
        "embedded linux",
    ],
    "ai-ml": [
        "ai engineer",
        "machine learning engineer",
        "ml engineer",
        "deep learning",
        "ml platform",
        "ml infrastructure",
        "inference engineer",
        "generative ai",
        "genai",
        "llm",
        "ai application",
        "mlops",
        "ai/ml",
    ],
    "data": [
        "data engineer",
        "data platform engineer",
        "data infrastructure",
        "big data engineer",
        "database engineer",
        "database developer",
        "etl engineer",
        "data pipeline",
        "data integration",
    ],
    "security": [
        "security engineer",
        "application security",
        "appsec",
        "product security",
        "cloud security",
        "cybersecurity",
        "cyber security",
        "identity engineer",
        "iam engineer",
        "cryptography engineer",
        "zero trust",
    ],
    "blockchain": [
        "blockchain engineer",
        "blockchain developer",
        "smart contract",
        "web3",
        "protocol engineer",
    ],
    "game": [
        "game engineer",
        "game developer",
        "gameplay programmer",
        "game engine",
        "graphics engineer",
        "rendering engineer",
        "unity developer",
        "unreal engine",
    ],
    "graphics": [
        "computer graphics",
        "graphics software",
        "shader engineer",
        "gpu software",
        "3d graphics",
        "visualization engineer",
    ],
    "video-media": [
        "video engineer",
        "streaming engineer",
        "media engineer",
        "video streaming",
        "audio engineer",
        "multimedia engineer",
    ],
    "robotics": [
        "robotics engineer",
        "robotics developer",
        "autonomous systems",
        "autonomy engineer",
        "motion planning",
        "controls engineer",
        "perception engineer",
    ],
    "vision": [
        "computer vision",
        "vision engineer",
        "image processing",
        "visual computing",
        "machine vision",
    ],
    "arvr": [
        "augmented reality",
        "virtual reality",
        "xr engineer",
        "spatial computing",
        "mixed reality",
    ],
    "qa-sdet": [
        "software test engineer",
        "test automation",
        "qa automation",
        "quality engineer",
        "sdet",
        "software development engineer in test",
        "software engineer in test",
        "test infrastructure",
    ],
    "devtools": [
        "developer tools",
        "developer tooling",
        "build systems",
        "build tools",
        "compiler engineer",
        "compiler developer",
        "language tools",
        "ide engineer",
        "runtime engineer",
    ],
    "database-storage": [
        "storage engineer",
        "storage systems",
        "file systems",
        "filesystem engineer",
        "search engineer",
        "query engine",
        "database kernel",
    ],
    "networking": [
        "network software",
        "network engineer",
        "network systems",
        "protocol engineer",
        "networking engineer",
    ],
    "fintech": [
        "fintech",
        "financial software",
        "payments engineer",
        "banking software",
        "trading systems",
        "quantitative software",
        "quant developer",
        "ledger engineer",
    ],
    "enterprise": [
        "enterprise software",
        "enterprise application",
        "business application",
        "erp developer",
        "crm developer",
        "saas engineer",
        "saas developer",
    ],
    "api-integration": [
        "api engineer",
        "rest api",
        "graphql",
        "integration engineer",
        "integration developer",
        "api platform",
    ],
    "architecture": [
        "software architect",
        "systems architect",
        "application architect",
        "solution architect",
        "technical architect",
        "platform architect",
        "enterprise architect",
    ],
}

# Technology stack — the highest-signal terms for keyword matching.
TECH = [
    # languages
    "python",
    "java",
    "javascript",
    "typescript",
    "golang",
    "go developer",
    "rust",
    "c++",
    "c#",
    ".net",
    "php",
    "ruby",
    "kotlin",
    "swift",
    "scala",
    "dart",
    "objective-c",
    "elixir",
    "erlang",
    "haskell",
    "matlab",
    "groovy",
    "assembly",
    # frontend frameworks
    "react",
    "react.js",
    "next.js",
    "angular",
    "vue",
    "vue.js",
    "svelte",
    "nuxt",
    "redux",
    "webpack",
    "vite",
    "tailwind",
    "webassembly",
    # backend frameworks
    "node.js",
    "nodejs",
    "express",
    "nestjs",
    "django",
    "flask",
    "fastapi",
    "spring boot",
    "spring",
    "asp.net",
    "laravel",
    "rails",
    "ruby on rails",
    # databases
    "postgresql",
    "postgres",
    "mysql",
    "mongodb",
    "redis",
    "cassandra",
    "dynamodb",
    "elasticsearch",
    "clickhouse",
    "snowflake",
    "bigquery",
    "sql server",
    "sqlite",
    # cloud
    "aws",
    "azure",
    "google cloud",
    "gcp",
    "kubernetes",
    "docker",
    "terraform",
    "serverless",
    "lambda",
    # ai/ml stack
    "pytorch",
    "tensorflow",
    "keras",
    "transformers",
    "hugging face",
    "langchain",
    "llamaindex",
    "cuda",
    "onnx",
    "openai",
    "rag",
    "agents",
    # data / streaming
    "spark",
    "kafka",
    "airflow",
    "databricks",
    "hadoop",
    "flink",
    # devops
    "jenkins",
    "github actions",
    "gitlab ci",
    "argocd",
    "helm",
    "prometheus",
    "grafana",
    "istio",
    "ansible",
    "pulumi",
]

# ---------------------------------------------------------------------------
# Sweep policy (edit to taste, then regenerate).
# ---------------------------------------------------------------------------

# Boards with a real server-side search param get ONE entry per keyword —
# each call is a genuinely narrow search. Boards without one (arbeitnow,
# remoteok, jobicy) fetch the full feed regardless, so they get a single
# match-any entry covering the whole taxonomy instead of N identical fetches.
SERVER_SIDE_BOARDS = ["remotive"]
CLIENT_SIDE_BOARDS = ["arbeitnow", "remoteok", "jobicy"]

# High-signal keywords used for the per-keyword server-side sweep and for
# Adzuna. Derived from the taxonomy: core titles + top specializations + the
# most common tech terms. The full taxonomy still drives the match-any entry.
HIGH_SIGNAL = [
    # core titles
    "software engineer",
    "software developer",
    "software architect",
    "engineering manager",
    "technical lead",
    # specializations
    "backend engineer",
    "frontend engineer",
    "full stack developer",
    "mobile developer",
    "cloud engineer",
    "devops engineer",
    "site reliability engineer",
    "data engineer",
    "machine learning engineer",
    "security engineer",
    "embedded engineer",
    "qa automation engineer",
    # languages
    "python",
    "java",
    "javascript",
    "typescript",
    "golang",
    "rust",
    "c++",
    "c#",
    # frameworks / platforms
    "react",
    "node.js",
    "aws",
    "azure",
    "kubernetes",
    "docker",
    "terraform",
    "pytorch",
    "tensorflow",
    "postgresql",
    "mongodb",
    "kafka",
    "spark",
]

# Adzuna countries to sweep. Free tier ~1,000 calls/month, so the adzuna
# sweep deliberately uses a smaller keyword set: with ~7 calls per country per
# run, us+gb daily stays under ~450 calls/month. Bump ADZUNA_KEYWORDS or the
# country list only if you raise the quota or run weekly.
ADZUNA_COUNTRIES = ["us", "gb"]

# Subset of HIGH_SIGNAL used for Adzuna (keeps the monthly call budget sane).
ADZUNA_KEYWORDS = [
    "software engineer",
    "software developer",
    "backend engineer",
    "frontend engineer",
    "full stack developer",
    "devops engineer",
    "data engineer",
]

# USAJobs keyword sweep (free key; no hard monthly quota documented, so this
# can be broader than Adzuna's). Each entry is one API call per run.
USAJOBS_KEYWORDS = [
    "software engineer",
    "data engineer",
    "computer science",
    "cybersecurity",
    "IT specialist",
    "artificial intelligence",
]


def _all_terms() -> list[str]:
    """Every taxonomy term, deduplicated, for the client-side match-any entry."""
    seen: set[str] = set()
    out: list[str] = []
    for group in [CORE, ENTRY, *SPECIALIZATIONS.values(), TECH]:
        for term in group:
            t = term.strip().lower()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def build() -> dict:
    cfg: dict = {"boards": [], "adzuna": [], "usajobs": []}
    # Server-side boards: one narrow entry per high-signal keyword.
    for board in SERVER_SIDE_BOARDS:
        for kw in HIGH_SIGNAL:
            cfg["boards"].append(f"{board}|{kw}")
    # Client-side boards: one match-any entry covering the full taxonomy.
    for board in CLIENT_SIDE_BOARDS:
        cfg["boards"].append(f"{board}|{','.join(_all_terms())}")
    # Adzuna: country|keyword — a tight set to respect the free call quota.
    for country in ADZUNA_COUNTRIES:
        for kw in ADZUNA_KEYWORDS:
            cfg["adzuna"].append(f"{country}|{kw}")
    # USAJobs: keyword-only (single federal board), free key, no tight quota.
    cfg["usajobs"] = list(USAJOBS_KEYWORDS)
    return cfg


def render(cfg: dict) -> str:
    lines = [
        "# Keyword sweep config — generated by scripts/generate_keyword_config.py.",
        "# Run:  jobcollect collect --config keywords.yaml --sources board,ats",
        "#",
        "# remotive entries use its server-side ?search= (one call per keyword).",
        "# arbeitnow/remoteok/jobicy entries are match-any: one feed fetch that",
        "# keeps jobs matching ANY term of the software-engineering taxonomy.",
        "# adzuna entries need a free key (api_keys: adzuna_app_id/adzuna_api_key)",
        "# and each country|keyword is one API call — keep the country list tight",
        "# to stay inside the free monthly quota.",
        "# Regenerate with: python scripts/generate_keyword_config.py",
        "",
        "boards:",
    ]
    for entry in cfg["boards"]:
        lines.append(f"  - {entry}")
    lines.append("")
    lines.append("adzuna:")
    for entry in cfg["adzuna"]:
        lines.append(f"  - {entry}")
    lines.append("")
    if cfg.get("usajobs"):
        lines.append("usajobs:")
        for entry in cfg["usajobs"]:
            lines.append(f"  - {entry}")
        lines.append("")
    # Carry over api_keys from companies.yaml so keyed sources work standalone.
    import yaml

    companies = ROOT / "companies.yaml"
    if companies.exists():
        raw = yaml.safe_load(companies.read_text(encoding="utf-8")) or {}
        keys = (raw or {}).get("api_keys") or {}
        keys = {k: v for k, v in keys.items() if v}
        if keys:
            lines.append("api_keys:")
            for k, v in keys.items():
                lines.append(f'  {k}: "{v}"')
            lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    cfg = build()
    OUT.write_text(render(cfg), encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"  boards: {len(cfg['boards'])} entries")
    print(f"  adzuna: {len(cfg['adzuna'])} entries")
    print(f"  taxonomy terms in match-any entry: {len(_all_terms())}")
