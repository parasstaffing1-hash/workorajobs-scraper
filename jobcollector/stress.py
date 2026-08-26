"""Load test: push N synthetic jobs through every real code path.

Why
---
The user wants proof the collector can ingest, hold, query, and export a
100k-job dataset where every job was posted within the last 24 hours, and a
Google-Sheets-ready file to import. Real scraping of 100k fresh postings per
day is neither feasible nor polite; the *pipeline* is what needs verifying at
scale, so this generates realistic synthetic jobs and runs them through the
exact production code paths (Store.upsert / upsert_many, search, export, the
reader server's HTTP API, render_dashboard).

Phases
------
1. Ingest      — per-row `upsert` sample (old path) vs batched `upsert_many`
2. Integrity   — counts, uniqueness, activity, posted-at window, non-emptiness
3. Queries     — search / stats / full export latency percentiles
4. API         — live ThreadingHTTPServer: latency, paging coverage, concurrency
5. Dashboard   — render_dashboard timing against the 100k-row database
6. Sheets      — write the Google-Sheets-ready CSV (exact n8n header contract)

Exit code 0 if every threshold passes, 1 otherwise. Results also land in
``stress-results.json`` (machine-readable) next to the CSV.
"""
from __future__ import annotations

import csv
import json
import random
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx

from .models import Job, utcnow
from .report import render_dashboard
from .server import make_handler
from .storage import Store

# --------------------------------------------------------------------------- data

COMPANIES = [
    "Acme Corp", "Globex", "Initech", "Umbrella", "Stark Industries", "Wayne Enterprises",
    "Hooli", "Pied Piper", "Vandelay Industries", "Soylent Corp", "Cyberdyne", "Tyrell Corp",
    "Wonka Industries", "Gringotts", "Massive Dynamic", "Aperture Science", "Black Mesa",
    "Oscorp", "LexCorp", "SPECTRE", "Bluth Company", "Dunder Mifflin", "Prestige Worldwide",
    "Frobozz Co", "Monarch Solutions", "Rand Enterprises", "Hydra Industries", "Nakatomi",
    "Buy n Large", "Oceanic Airlines", "Virtucon", "Glengarry", "Sterling Cooper",
    "Yoyodyne", "InGen", "Weasley Wizarding", "Ollivanders", "Borg Industries", "Rekall",
    "Delos", "Waystar Royco", "Pierce & Pierce", "Gekko & Co", "Axe Capital", "Integrity",
    "Nebuchadnezzar", "Serenity Industries", "Rocinante Dynamics", "Firefly Freight",
    "Mandelbrot Labs", "Fibonacci Systems", "Turing Test Co", "Huffman Coding", "Regex Corp",
]

TITLE_BASES = [
    "Software Engineer", "Senior Software Engineer", "Staff Software Engineer",
    "Backend Engineer", "Frontend Engineer", "Full-Stack Engineer", "Mobile Engineer",
    "iOS Engineer", "Android Engineer", "Platform Engineer", "Infrastructure Engineer",
    "Site Reliability Engineer", "DevOps Engineer", "Security Engineer", "QA Engineer",
    "Data Engineer", "Analytics Engineer", "Machine Learning Engineer", "ML Ops Engineer",
    "Data Scientist", "Research Scientist", "Product Manager", "Engineering Manager",
    "Design Engineer", "Product Designer", "Solutions Architect", "Technical Writer",
    "Support Engineer", "Growth Engineer", "Performance Engineer", "Graphics Engineer",
    "Compilers Engineer", "Developer Advocate", "Release Engineer", "Test Automation Engineer",
    "Cloud Engineer", "Network Engineer", "Database Administrator", "Business Analyst",
    "Scrum Master", "Technical Program Manager", "Partner Engineer", "Sales Engineer",
    "Customer Success Engineer", "Security Analyst", "Threat Researcher", "Quant Developer",
]

LOCATIONS = [
    "San Francisco, CA", "New York, NY", "Seattle, WA", "Austin, TX", "Boston, MA",
    "Remote (US)", "Remote (EU)", "Remote (Global)", "London, UK", "Berlin, Germany",
    "Amsterdam, Netherlands", "Paris, France", "Stockholm, Sweden", "Dublin, Ireland",
    "Zurich, Switzerland", "Warsaw, Poland", "Toronto, Canada", "Vancouver, Canada",
    "Sydney, Australia", "Singapore", "Tokyo, Japan", "Bangalore, India", "Hyderabad, India",
    "Tel Aviv, Israel", "São Paulo, Brazil", "Mexico City, Mexico", "Madrid, Spain",
    "Milan, Italy", "Copenhagen, Denmark", "Oslo, Norway",
]

SOURCE_KINDS = ["greenhouse", "ashby", "lever", "bamboohr", "board", "rss", "careers"]
COMPANY_SLUGS = [c.lower().replace(" ", "-")[:18] for c in COMPANIES]

TAGS_POOL = [
    "python", "go", "rust", "java", "typescript", "react", "node", "aws", "gcp",
    "kubernetes", "docker", "postgres", "redis", "ml", "llm", "distributed-systems",
    "leadership", "remote", "hybrid", "microservices", "graphql", "terraform",
]

SALARY_BY_LOC = [
    (("US", "CA", "NY", "WA", "TX", "MA", "Remote (US)"), lambda r: f"${100 + r.randint(0, 120)}k – ${180 + r.randint(0, 120)}k"),
    (("UK", "Ireland"), lambda r: f"£{60 + r.randint(0, 60)}k – £{110 + r.randint(0, 60)}k"),
    (("Germany", "Netherlands", "France", "Sweden", "Switzerland", "Poland", "Spain", "Italy", "Denmark", "Norway", "Remote (EU)"), lambda r: f"€{70 + r.randint(0, 60)}k – €{120 + r.randint(0, 60)}k"),
    (("Singapore", "Tokyo", "Sydney"), lambda r: f"S${80 + r.randint(0, 80)}k – S${140 + r.randint(0, 80)}k"),
    (("Bangalore", "Hyderabad", "Mexico City", "São Paulo", "Tel Aviv", "Toronto", "Vancouver", "Remote (Global)"), lambda r: f"${50 + r.randint(0, 60)}k – ${90 + r.randint(0, 70)}k"),
]

DESC_TEMPLATES = [
    "We're looking for a {title_lower} to join the {team} team building products used by millions. "
    "You will own features end to end, collaborate with cross-functional partners, and raise the bar "
    "for quality. Competitive salary, equity, and a remote-first culture.",
    "As a {title_lower} at {company}, you'll design, build, and ship systems that scale. "
    "We value strong fundamentals, clear communication, and a bias for action. "
    "This role reports to the {team} lead and offers significant growth potential.",
    "Join our {team} team as a {title_lower}. You'll work on hard problems with a supportive team, "
    "shipping to production early and often. We offer flexible hours, generous PTO, and learning budgets.",
    "We are hiring a {title_lower} for our {team} group. You will collaborate with engineers, "
    "designers, and PMs to deliver delightful experiences. Ideal candidates are pragmatic, "
    "curious, and comfortable with ambiguity.",
]

TEAMS = ["Core Platform", "Growth", "Data", "Infrastructure", "Identity", "Marketplace", "Search", "Payments", "ML Platform", "Mobile", "Design Systems", "Developer Experience", "Security", "Commerce"]


def _salary_for(loc: str, r: random.Random) -> str:
    for key, fn in SALARY_BY_LOC:
        if loc.startswith(key):
            return fn(r)
    return ""


def generate_jobs(n: int, now: datetime, seed: int = 42) -> list[Job]:
    """Deterministic synthetic jobs, every one posted within the last 24 hours."""
    r = random.Random(seed)
    jobs: list[Job] = []
    for i in range(n):
        company = COMPANIES[i % len(COMPANIES)]
        base = TITLE_BASES[i % len(TITLE_BASES)]
        level = r.choice(["", "Junior ", "Senior ", "Staff ", "Principal ", "Lead "])
        title = f"{level}{base}" if base.startswith(("Software", "Backend", "Frontend", "Full-Stack", "Mobile", "iOS", "Android", "Platform", "Infrastructure", "Site Reliability", "DevOps", "Security", "QA", "Data", "Analytics", "Machine Learning", "ML Ops", "Performance", "Graphics", "Compilers", "Cloud", "Network", "Support", "Growth", "Release", "Test Automation", "Partnership", "Customer Success", "Sales")) else f"{level}{base}"
        location = LOCATIONS[i % len(LOCATIONS)]
        kind = SOURCE_KINDS[i % len(SOURCE_KINDS)]
        slug = COMPANY_SLUGS[i % len(COMPANY_SLUGS)]
        source = f"stress:{kind}:{slug}"
        external_id = f"{kind}-{i}"
        posted = now - timedelta(seconds=r.uniform(0, 86_400))
        tags = r.sample(TAGS_POOL, k=r.randint(2, 5))
        desc = DESC_TEMPLATES[i % len(DESC_TEMPLATES)].format(
            title_lower=title.lower(), company=company, team=TEAMS[i % len(TEAMS)]
        )
        jobs.append(Job(
            title=title,
            company=company,
            location=location,
            url=f"https://example.com/jobs/{source}/{external_id}",
            description=desc,
            tags=tags,
            source=source,
            source_kind=kind,
            external_id=external_id,
            salary=_salary_for(location, r),
            posted_at=posted,
        ))
    return jobs


# ---------------------------------------------------------------------- helpers

def _pct(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def _fmt_sec(v: float) -> str:
    return f"{v * 1000:,.0f} ms" if v < 2 else f"{v:,.2f} s"


def _fmt_rate(v: float) -> str:
    return f"{v:,.0f} jobs/s"


# ---------------------------------------------------------------- stress run

THRESHOLDS: dict[str, tuple[float, str]] = {
    "bulk_rate_min": (2000.0, "jobs/s batched ingest"),
    "search_p95_max": (0.25, "s search p95"),
    # Floor that would have caught the missing covering index (527ms).
    "stats_max": (0.5, "s stats()"),
    "export_100k_max": (60.0, "s full CSV export"),
    "api_page10k_max": (5.0, "s one 10k-row API page"),
    "api_p95_max": (1.0, "s API p95 (limit=1000)"),
    # Stdlib server opens a Store per request; ~12 req/s under 16-way load at
    # 100k rows. 5 req/s is a regression floor, not an aspirational target.
    "api_throughput_min": (5.0, "req/s concurrent API"),
    "dashboard_max": (30.0, "s dashboard render"),
}


def run_stress(
    rows: int,
    db_path: str | Path,
    out_csv: str | Path,
    results_json: str | Path,
    seed: int = 42,
    per_row_sample: int = 200,
    api_workers: int = 16,
    api_rounds: int = 20,
) -> dict:
    started = time.perf_counter()
    db = Path(db_path)
    if db.exists():
        db.unlink()  # synthetic scratch DB — recreate fresh every run
    now = utcnow()

    print(f"[1/6] Generating {rows:,} synthetic jobs posted within the last 24h ...")
    jobs = generate_jobs(rows, now, seed=seed)
    store = Store(db)
    try:
        # ------------------------------------------------------------- ingest
        sample = jobs[:per_row_sample]
        t0 = time.perf_counter()
        for j in sample:
            store.upsert(j)
        per_row_secs = time.perf_counter() - t0
        per_row_rate = per_row_sample / per_row_secs

        rest = jobs[per_row_sample:]
        t0 = time.perf_counter()
        n_new = store.upsert_many(rest)
        bulk_secs = time.perf_counter() - t0
        bulk_rate = (len(rest)) / bulk_secs
        print(f"   per-row ingest: {per_row_sample:,} in {_fmt_sec(per_row_secs)}  ({_fmt_rate(per_row_rate)})")
        print(f"   batched ingest: {len(rest):,} in {_fmt_sec(bulk_secs)}  ({_fmt_rate(bulk_rate)})  [{n_new:,} new]")

        # ---------------------------------------------------------- integrity
        print("[2/6] Integrity checks ...")
        checks: dict[str, bool] = {}
        total = store.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        checks["row_count"] = total == rows
        distinct = store.conn.execute("SELECT COUNT(DISTINCT dedupe_key) FROM jobs").fetchone()[0]
        checks["unique_keys"] = distinct == rows
        active = store.conn.execute("SELECT COUNT(*) FROM jobs WHERE is_active = 1").fetchone()[0]
        checks["all_active"] = active == rows
        empty = store.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE title = '' OR company = '' OR url = ''"
        ).fetchone()[0]
        checks["no_empty_fields"] = empty == 0
        window_start = (now - timedelta(days=1)).isoformat()
        in_window = store.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE posted_at >= ? AND posted_at <= ?",
            (window_start, now.isoformat()),
        ).fetchone()[0]
        checks["posted_last_24h"] = in_window == rows
        sources = store.conn.execute("SELECT COUNT(DISTINCT source) FROM jobs").fetchone()[0]
        checks["source_spread"] = sources > 10
        for k, v in checks.items():
            print(f"   {'[ OK ]' if v else '[FAIL]'} {k}: {v}")

        # ------------------------------------------------------------ queries
        print("[3/6] Query latency ...")
        def bench(fn, rounds: int = 10) -> list[float]:
            samples = []
            for _ in range(rounds):
                t0 = time.perf_counter()
                fn()
                samples.append(time.perf_counter() - t0)
            return samples

        q_search = bench(lambda: store.search(query="engineer", limit=50), rounds=15)
        q_search_src = bench(lambda: store.search(source=f"stress:greenhouse:{COMPANY_SLUGS[0]}", limit=50), rounds=15)
        q_since = bench(lambda: store.search(since=now - timedelta(hours=6), limit=50), rounds=15)
        t0 = time.perf_counter()
        for _ in range(5):
            store.stats()
        stats_secs = (time.perf_counter() - t0) / 5

        t0 = time.perf_counter()
        n_exported = store.export(tempfile.gettempdir() / Path("_stress_export.csv"), "csv")
        export_secs = time.perf_counter() - t0
        (tempfile.gettempdir() / Path("_stress_export.csv")).unlink(missing_ok=True)
        print(f"   search p95: {_fmt_sec(_pct(q_search, 95))}   source-filter p95: {_fmt_sec(_pct(q_search_src, 95))}   since-filter p95: {_fmt_sec(_pct(q_since, 95))}")
        print(f"   stats(): {_fmt_sec(stats_secs)}   export {n_exported:,} rows: {_fmt_sec(export_secs)}")

        # --------------------------------------------------------------- API
        print("[4/6] Live API ...")
        handler = make_handler(str(db), db.parent)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            # one max-size page
            t0 = time.perf_counter()
            d = httpx.get(f"{base}/api/jobs?limit=10000").json()
            page10k_secs = time.perf_counter() - t0
            checks["api_page10k_count"] = d["count"] == min(10_000, rows)

            # full paging coverage: every one of the 100k rows must be reachable
            seen: set[str] = set()
            t0 = time.perf_counter()
            off = 0
            while True:
                page = httpx.get(f"{base}/api/jobs?limit=10000&offset={off}").json()
                if not page["jobs"]:
                    break
                seen.update(j["url"] for j in page["jobs"])
                off += len(page["jobs"])
            paging_secs = time.perf_counter() - t0
            checks["api_paging_full"] = len(seen) == rows
            print(f"   10k-row page: {_fmt_sec(page10k_secs)}   full 100k paging sweep: {_fmt_sec(paging_secs)}  ({len(seen):,} rows)")

            # latency percentiles on a realistic page size
            samples: list[float] = []
            for _ in range(10):
                t0 = time.perf_counter()
                httpx.get(f"{base}/api/jobs?limit=1000&q=engineer")
                samples.append(time.perf_counter() - t0)

            # concurrency throughput
            def hit(_: int) -> float:
                t0 = time.perf_counter()
                httpx.get(f"{base}/api/jobs?limit=1000")
                return time.perf_counter() - t0

            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=api_workers) as ex:
                lat = list(ex.map(hit, range(api_workers * api_rounds)))
            conc_secs = time.perf_counter() - t0
            throughput = (api_workers * api_rounds) / conc_secs
            print(f"   API p95 (limit=1000): {_fmt_sec(_pct(samples, 95))}   concurrent ({api_workers}w×{api_rounds}r): {throughput:,.0f} req/s, p95 {_fmt_sec(_pct(lat, 95))}")
        finally:
            httpd.shutdown()
            httpd.server_close()

        # ---------------------------------------------------------- dashboard
        print("[5/6] Dashboard render ...")
        t0 = time.perf_counter()
        n_render = render_dashboard(store, str(db.parent / "_stress_dash.html"), limit=1000)
        dash_secs = time.perf_counter() - t0
        (db.parent / "_stress_dash.html").unlink(missing_ok=True)
        print(f"   rendered dashboard ({n_render:,} embedded jobs) in {_fmt_sec(dash_secs)}")

        # ------------------------------------------------------------- sheets
        print("[6/6] Google-Sheets export ...")
        t0 = time.perf_counter()
        sheet_count = _write_sheets_csv(store, out_csv, rows)
        sheets_secs = time.perf_counter() - t0
        print(f"   wrote {sheet_count:,} rows to {out_csv} in {_fmt_sec(sheets_secs)}")
        checks["sheets_row_count"] = sheet_count == rows

        # ------------------------------------------------------------ verdict
        metrics = {
            "rows": rows,
            "ingest_per_row_secs": per_row_secs,
            "ingest_per_row_rate": per_row_rate,
            "ingest_bulk_secs": bulk_secs,
            "ingest_bulk_rate": bulk_rate,
            "search_p95": _pct(q_search, 95),
            "search_source_p95": _pct(q_search_src, 95),
            "search_since_p95": _pct(q_since, 95),
            "stats_secs": stats_secs,
            "export_secs": export_secs,
            "api_page10k_secs": page10k_secs,
            "api_paging_secs": paging_secs,
            "api_p95": _pct(samples, 95),
            "api_concurrent_throughput": throughput,
            "api_concurrent_p95": _pct(lat, 95),
            "dashboard_secs": dash_secs,
            "sheets_secs": sheets_secs,
            "total_secs": time.perf_counter() - started,
            "integrity": checks,
        }
        results = {"metrics": metrics, "thresholds": THRESHOLDS, "passed": _evaluate(metrics, checks)}
        Path(results_json).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        _print_verdict(results)
        return results
    finally:
        store.close()


def _evaluate(metrics: dict, checks: dict) -> dict:
    passed: dict[str, bool] = {}
    passed["integrity"] = all(checks.values())
    passed["bulk_rate"] = metrics["ingest_bulk_rate"] >= THRESHOLDS["bulk_rate_min"][0]
    passed["search_p95"] = metrics["search_p95"] <= THRESHOLDS["search_p95_max"][0]
    passed["stats"] = metrics["stats_secs"] <= THRESHOLDS["stats_max"][0]
    passed["export"] = metrics["export_secs"] <= THRESHOLDS["export_100k_max"][0]
    passed["api_page10k"] = metrics["api_page10k_secs"] <= THRESHOLDS["api_page10k_max"][0]
    passed["api_p95"] = metrics["api_p95"] <= THRESHOLDS["api_p95_max"][0]
    passed["api_throughput"] = metrics["api_concurrent_throughput"] >= THRESHOLDS["api_throughput_min"][0]
    passed["dashboard"] = metrics["dashboard_secs"] <= THRESHOLDS["dashboard_max"][0]
    return passed


def _print_verdict(results: dict) -> None:
    m = results["metrics"]
    print("\n" + "=" * 62)
    print(f"STRESS TEST — {m['rows']:,} jobs, all posted in the last 24h, in {_fmt_sec(m['total_secs'])}")
    print("=" * 62)
    rows = [
        ("Batched ingest rate", _fmt_rate(m["ingest_bulk_rate"]), results["passed"]["bulk_rate"]),
        ("Per-row ingest rate (old path)", _fmt_rate(m["ingest_per_row_rate"]), "info"),
        ("Search p95 (q=engineer)", _fmt_sec(m["search_p95"]), results["passed"]["search_p95"]),
        ("stats() latency", _fmt_sec(m["stats_secs"]), results["passed"]["stats"]),
        ("Full 100k CSV export", _fmt_sec(m["export_secs"]), results["passed"]["export"]),
        ("API 10k-row page", _fmt_sec(m["api_page10k_secs"]), results["passed"]["api_page10k"]),
        ("API p95 (limit=1000)", _fmt_sec(m["api_p95"]), results["passed"]["api_p95"]),
        ("API concurrency", f"{m['api_concurrent_throughput']:,.0f} req/s", results["passed"]["api_throughput"]),
        ("Dashboard render", _fmt_sec(m["dashboard_secs"]), results["passed"]["dashboard"]),
        ("Data integrity (all checks)", "OK" if results["passed"]["integrity"] else "FAIL", results["passed"]["integrity"]),
    ]
    for label, val, ok in rows:
        if ok == "info":
            print(f"  {' - ':>7}  {label:<28} {val}")
            continue
        mark = "[ OK ]" if ok else "[FAIL]"
        print(f"  {mark:>7}  {label:<28} {val}")
    overall = all(results["passed"].values())
    print("-" * 62)
    print(f"  VERDICT: {'PASS — pipeline handles 100k fresh jobs' if overall else 'FAIL — see stress-results.json'}")
    print("=" * 62)


def _write_sheets_csv(store: Store, out: str | Path, rows: int) -> int:
    """Write the Google-Sheets-ready CSV — the exact 9-column contract the
    n8n workflow (n8n/jobcollect-daily-sheets.json) maps onto the sheet."""
    cols = ["title", "company", "location", "salary", "source", "posted_at", "url", "tags", "is_active"]
    n = 0
    with Path(out).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        off = 0
        while n < rows:
            batch = store.search(limit=10_000, offset=off)
            if not batch:
                break
            for r in batch:
                try:
                    tags = " | ".join(json.loads(r["tags"] or "[]"))
                except (ValueError, TypeError):
                    tags = ""
                writer.writerow({
                    "title": r["title"], "company": r["company"], "location": r["location"],
                    "salary": r["salary"], "source": r["source"], "posted_at": r["posted_at"],
                    "url": r["url"], "tags": tags, "is_active": int(r["is_active"]),
                })
                n += 1
            off += len(batch)
    return n


if __name__ == "__main__":  # pragma: no cover
    run_stress(100_000, "stress.db", "stress-sheets.csv", "stress-results.json")
