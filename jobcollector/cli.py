"""Command-line interface for the job collector."""
from __future__ import annotations

import html
import os
import shutil
import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import click
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

from .analyze import REPORTS, run_analysis
from .config import load_config
from .dedupe import find_duplicate_clusters
from .feeds import discover_feeds, fetch_feed_items
from .http import make_client
from .models import SourceConfig
from .notify import run_notify
from .pipeline import SOURCE_GROUPS, collect as run_collection
from .report import render_dashboard
from .scrape import ScraperConfig, run_scraper
from .sources.playwright_scrape import PlaywrightJobConfig, run_playwright_scraper
from .scheduler import install_windows_task, parse_time, remove_windows_task, resolve_tasks, run_loop, run_tasks
from .server import serve as serve_reader
from .storage import Store
from .stress import run_stress

console = Console()
DEFAULT_CONFIG = "companies.yaml"
DEFAULT_DB = "jobs.db"
DEFAULT_FEEDS = "feeds.yaml"
DEFAULT_SCRAPERS = "scrapers.yaml"
DEFAULT_NOTIFY = "notify.yaml"
EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "companies.example.yaml"
EXAMPLE_SCRAPERS = Path(__file__).resolve().parent.parent / "scrapers.example.yaml"
EXAMPLE_FEEDS = Path(__file__).resolve().parent.parent / "feeds.example.yaml"


def _time_ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - dt
    days = age.days
    if days > 30:
        return f"{days // 30}mo"
    if days > 0:
        return f"{days}d"
    hours = max(0, int(age.total_seconds() // 3600))
    return f"{hours}h" if hours else "now"


@click.group()
@click.version_option(package_name="jobcollector")
def cli() -> None:
    """Daily job collector: company career pages, RSS feeds, and job boards."""


@cli.command()
@click.option("--config", default=DEFAULT_CONFIG, help="Path to the YAML config file.")
@click.option("--db", default=DEFAULT_DB, help="Path to the SQLite database.")
@click.option(
    "--sources",
    default="board,ats,rss,careers",
    show_default=True,
    help="Comma-separated source groups to run.",
)
@click.option("--limit", default=200, type=int, help="Max items per source.")
@click.option("--concurrency", default=8, type=int, help="Threads for career-page crawling.")
@click.option("--js", is_flag=True, help="Render SPA career pages with Playwright (slow).")
def collect(
    config: str,
    db: str,
    sources: str,
    limit: int,
    concurrency: int,
    js: bool,
) -> None:
    """Run one collection pass over all configured sources."""
    cfg = load_config(config)
    groups = tuple(s.strip() for s in sources.split(",") if s.strip())
    unknown = [g for g in groups if g not in SOURCE_GROUPS]
    if unknown:
        raise click.ClickException(f"Unknown source group(s): {', '.join(unknown)}. "
                                   f"Valid: {', '.join(SOURCE_GROUPS)}")
    store = Store(db)
    try:
        with console.status("[bold green]Collecting jobs...[/]", spinner="dots"):
            report = run_collection(
                cfg, store,
                sources=groups,
                limit_per_source=limit,
                concurrency=concurrency,
                use_js=js,
            )
    finally:
        store.close()

    _render_report(report, groups)


def _render_report(report, groups) -> None:
    table = Table(title="Collection report")
    table.add_column("Source group", style="cyan")
    table.add_column("Status")
    for group in groups:
        status = "ran" if group in report.groups_run else "skipped/failed"
        table.add_row(SOURCE_GROUPS[group], status)
    console.print(table)

    console.print(f"[bold]Jobs seen:[/] {report.jobs_seen}  "
                  f"[bold green]new:[/] {report.jobs_new}  "
                  f"[bold yellow]expired:[/] {report.jobs_expired}")

    if report.errors:
        console.print("\n[bold yellow]Errors (non-fatal):[/]")
        for err in report.errors[:25]:
            console.print(f"  [red]•[/] {err}")
        if len(report.errors) > 25:
            console.print(f"  ... and {len(report.errors) - 25} more")


@cli.command()
@click.argument("query", required=False, default="")
@click.option("--db", default=DEFAULT_DB)
@click.option("--location", default="")
@click.option("--source", default="")
@click.option("--all", "active_only", is_flag=True, help="Include expired jobs.")
@click.option("--limit", default=30, type=int)
@click.option("--days", type=int, help="Only jobs posted within the last N days.")
def search(query: str, db: str, location: str, source: str, active_only: bool, limit: int, days: int | None) -> None:
    """Search collected jobs (SQL LIKE across title/company/description/tags)."""
    store = Store(db)
    try:
        since = None
        if days is not None:
            since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = store.search(
            query=query, location=location, source=source,
            active_only=not active_only, limit=limit, since=since,
        )
    finally:
        store.close()
    if not rows:
        console.print("[yellow]No jobs found.[/]")
        return
    table = Table(title=f"{len(rows)} job(s)")
    table.add_column("Title", style="bold", no_wrap=False)
    table.add_column("Company")
    table.add_column("Location")
    table.add_column("Source", style="dim")
    table.add_column("Posted", style="dim")
    for r in rows:
        table.add_row(r["title"], r["company"], r["location"], r["source"], _time_ago(r["posted_at"]))
    console.print(table)
    console.print("\nUse `jobcollect export` to write results to CSV/JSONL.")


@cli.command()
@click.option("--db", default=DEFAULT_DB)
@click.option("--query", default="")
@click.option("--format", "fmt", type=click.Choice(["csv", "jsonl"]), default="csv")
@click.option("--out", default="jobs.csv", help="Output file path.")
@click.option("--all", "active_only", is_flag=True, help="Include expired jobs.")
def export(db: str, query: str, fmt: str, out: str, active_only: bool) -> None:
    """Export matching jobs to CSV or JSONL."""
    store = Store(db)
    try:
        n = store.export(out, fmt, query=query, active_only=not active_only)
    finally:
        store.close()
    console.print(f"[green]Exported {n} job(s) to {out}[/]")


@cli.command()
@click.option("--db", default=DEFAULT_DB)
@click.option("--out", default="dashboard.html", help="Output HTML file path.")
@click.option("--limit", default=1000, type=int, help="Max jobs embedded in the page.")
def report(db: str, out: str, limit: int) -> None:
    """Write a self-contained HTML dashboard of collected jobs."""
    store = Store(db)
    try:
        n = render_dashboard(store, out, limit=limit)
    finally:
        store.close()
    console.print(f"[green]Wrote {out} with {n} job(s).[/]")


@cli.command()
@click.option("--db", default=DEFAULT_DB)
@click.option("--host", default="127.0.0.1", help="Bind address (use 0.0.0.0 to share on LAN).")
@click.option("--port", default=8600, type=int)
@click.option("--out", default="dashboard.html", help="Dashboard file to serve.")
def serve(db: str, host: str, port: int, out: str) -> None:
    """Run the local RSS reader: dashboard + JSON API for read/star state.

    Serves the Notion-style dashboard with a live backend, so the Reader page
    can mark items read, star them, and load full article text (persisted in
    the SQLite database).
    """
    store = Store(db)
    try:
        render_dashboard(store, out)
    finally:
        store.close()
    url = f"http://{host}:{port}/dashboard.html"
    console.print(f"[bold green]Reader running at {url}[/]  (Ctrl-C to stop)")
    serve_reader(db, host=host, port=port, root=Path(out).resolve().parent)


# ------------------------------------------------------------ items engine

@cli.group()
def feed() -> None:
    """Manage general-purpose RSS feeds (items engine, not jobs)."""


def _load_feeds(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return raw.get("feeds", []) or []


def _save_feeds(path: str, feeds: list[dict]) -> None:
    header = "# General-purpose RSS/Atom feeds for the items engine.\n" \
             "# Manage with: jobcollect feed add/list/fetch\n\n"
    Path(path).write_text(header + yaml.safe_dump({"feeds": feeds}, sort_keys=False, allow_unicode=True), encoding="utf-8")


@feed.command("add")
@click.argument("name")
@click.argument("url")
@click.option("--category", default="", help="Category tag, e.g. news, blog, changelog.")
@click.option("--tags", default="", help="Comma-separated tags.")
@click.option("--config", default=DEFAULT_FEEDS)
def feed_add(name: str, url: str, category: str, tags: str, config: str) -> None:
    """Add a feed (any RSS/Atom URL) to the feeds config."""
    feeds = _load_feeds(config)
    if any(f.get("name") == name for f in feeds):
        raise click.ClickException(f"Feed {name!r} already exists in {config}.")
    feeds.append({
        "name": name,
        "url": url,
        "category": category,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
    })
    _save_feeds(config, feeds)
    console.print(f"[green]Added feed {name!r} -> {url}[/] ({config})")


@feed.command("list")
@click.option("--config", default=DEFAULT_FEEDS)
def feed_list(config: str) -> None:
    """List configured feeds."""
    feeds = _load_feeds(config)
    if not feeds:
        console.print(f"[yellow]No feeds configured. Add one: jobcollect feed add NAME URL[/]")
        return
    table = Table(title=f"Feeds ({config})")
    table.add_column("Name", style="bold")
    table.add_column("URL")
    table.add_column("Category")
    for f in feeds:
        table.add_row(f.get("name", ""), f.get("url", ""), f.get("category", ""))
    console.print(table)


@feed.command("fetch")
@click.option("--name", default=None, help="Fetch only this feed.")
@click.option("--limit", default=200, type=int, help="Max items per feed.")
@click.option("--db", default=DEFAULT_DB)
@click.option("--config", default=DEFAULT_FEEDS)
def feed_fetch(name: str | None, limit: int, db: str, config: str) -> None:
    """Ingest configured feeds into the items table."""
    feeds = _load_feeds(config)
    if not feeds:
        raise click.ClickException(f"No feeds in {config}. Add one: jobcollect feed add NAME URL")
    if name:
        feeds = [f for f in feeds if f.get("name") == name]
        if not feeds:
            raise click.ClickException(f"Feed {name!r} not found in {config}.")
    store = Store(db)
    try:
        with make_client() as client:
            total_seen = total_new = 0
            errors: list[str] = []
            for f in feeds:
                try:
                    items = fetch_feed_items(client, f, limit=limit)
                except Exception as exc:
                    errors.append(f"{f.get('name')}: {exc}")
                    store.record_feed_fetch(
                        f"rss:{f.get('name')}", f.get("url", ""), f.get("name", ""),
                        f.get("category", ""), 0, error=str(exc),
                    )
                    continue
                seen = new = 0
                for item in items:
                    if store.upsert_item(item):
                        new += 1
                    seen += 1
                total_seen += seen
                total_new += new
                store.record_feed_fetch(
                    f"rss:{f.get('name')}", f.get("url", ""), f.get("name", ""),
                    f.get("category", ""), seen,
                )
                console.print(f"  [cyan]{f.get('name')}[/]: {seen} seen, [green]{new} new[/]")
    finally:
        store.close()
    console.print(f"[bold]Items: {total_seen} seen, {total_new} new[/]")
    for err in errors:
        console.print(f"  [red]•[/] {err}")


@feed.command("discover")
@click.argument("url")
@click.option("--add", is_flag=True, help="Add the first discovered feed to the config.")
@click.option("--config", default=DEFAULT_FEEDS)
def feed_discover(url: str, add: bool, config: str) -> None:
    """Find the RSS/Atom feed(s) of any website."""
    with make_client() as client:
        feeds = discover_feeds(client, url)
    if not feeds:
        raise click.ClickException(f"No feeds found at {url}.")
    table = Table(title=f"Feeds found for {url}")
    table.add_column("Title", style="bold")
    table.add_column("URL")
    table.add_column("Type", style="dim")
    for f in feeds:
        table.add_row(f["title"], f["url"], f["type"])
    console.print(table)
    if add:
        f = feeds[0]
        name = f["title"]
        feed_add(name, f["url"], "", "", config)


@feed.command("unread")
@click.option("--db", default=DEFAULT_DB)
@click.option("--source", default="", help="Filter by source, e.g. rss:Hacker News")
@click.option("--limit", default=50, type=int)
def feed_unread(db: str, source: str, limit: int) -> None:
    """List unread items (the reader's inbox)."""
    store = Store(db)
    try:
        rows = store.search_items(source=source, unread_only=True, limit=limit)
        n = store.unread_total()
    finally:
        store.close()
    if not rows:
        console.print(f"[green]Inbox zero![/] ({n} unread total)")
        return
    table = Table(title=f"{len(rows)} unread item(s) · {n} total")
    table.add_column("Title", style="bold")
    table.add_column("Feed", style="dim")
    table.add_column("Published", style="dim")
    for r in rows:
        table.add_row(r["title"], r["source"], _time_ago(r["published_at"]))
    console.print(table)


@feed.command("import")
@click.argument("opml")
@click.option("--config", default=DEFAULT_FEEDS)
def feed_import(opml: str, config: str) -> None:
    """Import subscriptions from an OPML file (Feedly/Inoreader/NewsBlur export)."""
    if not Path(opml).exists():
        raise click.ClickException(f"{opml} not found.")
    soup = BeautifulSoup(Path(opml).read_text(encoding="utf-8"), "lxml")
    feeds = _load_feeds(config)
    existing = {f.get("name") for f in feeds}
    added = 0
    for outline in soup.find_all("outline"):
        url = outline.get("xmlurl") or outline.get("xmlUrl")
        if not url:
            continue
        title = outline.get("title") or outline.get("text") or urlparse(url).netloc
        category = (outline.find_parent("outline") or {}).get("text", "") or ""
        if title in existing:
            continue
        feeds.append({"name": title, "url": url, "category": category, "tags": []})
        existing.add(title)
        added += 1
    _save_feeds(config, feeds)
    console.print(f"[green]Imported {added} feed(s) into {config} (total {len(feeds)}).[/]")
    console.print("Next: jobcollect feed fetch")


@feed.command("export")
@click.option("--config", default=DEFAULT_FEEDS)
@click.option("--out", default="feeds.opml")
def feed_export(config: str, out: str) -> None:
    """Export subscriptions to OPML (portable to any reader)."""
    feeds = _load_feeds(config)
    if not feeds:
        raise click.ClickException(f"No feeds in {config}.")
    esc = html.escape
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<opml version="2.0">', "  <head><title>JobCollector feeds</title></head>", "  <body>"]
    for f in feeds:
        name, url = esc(f.get("name", "")), esc(f.get("url", ""))
        cat = esc(f.get("category", ""))
        if cat:
            lines.append(f'    <outline text="{cat}"><outline type="rss" text="{name}" title="{name}" xmlUrl="{url}"/></outline>')
        else:
            lines.append(f'    <outline type="rss" text="{name}" title="{name}" xmlUrl="{url}"/>')
    lines.append("  </body>")
    lines.append("</opml>")
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]Exported {len(feeds)} feed(s) to {out}.[/]")


@cli.command()
@click.option("--config", default=DEFAULT_SCRAPERS)
@click.option("--name", default=None, help="Run only this scraper.")
@click.option("--limit", default=None, type=int, help="Cap items per scraper.")
@click.option("--db", default=DEFAULT_DB)
def scrape(config: str, name: str | None, limit: int | None, db: str) -> None:
    """Run configured scrapers from scrapers.yaml into the items table."""
    p = Path(config)
    if not p.exists():
        raise click.ClickException(
            f"{config} not found. Copy scrapers.example.yaml to scrapers.yaml or write your own."
        )
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    scrapers = [ScraperConfig.model_validate(s) for s in raw.get("scrapers", [])]
    if name:
        scrapers = [s for s in scrapers if s.name == name]
        if not scrapers:
            raise click.ClickException(f"Scraper {name!r} not found in {config}.")
    store = Store(db)
    try:
        with make_client() as client:
            for cfg in scrapers:
                with console.status(f"[cyan]Scraping {cfg.name}...[/]", spinner="dots"):
                    seen, new, errors = run_scraper(cfg, store, client=client, limit=limit)
                console.print(f"  [cyan]{cfg.name}[/]: {seen} seen, [green]{new} new[/]")
                for err in errors[:10]:
                    console.print(f"    [red]•[/] {err}")
    finally:
        store.close()


@cli.command()
@click.option("--category", default="")
@click.option("--source", default="", help="Filter by source substring, e.g. rss:")
@click.option("--query", default="")
@click.option("--limit", default=30, type=int)
@click.option("--db", default=DEFAULT_DB)
def items(category: str, source: str, query: str, limit: int, db: str) -> None:
    """Browse the items table (RSS + scraped data from the general engine)."""
    store = Store(db)
    try:
        rows = store.search_items(query=query, category=category, source=source, limit=limit)
    finally:
        store.close()
    if not rows:
        console.print("[yellow]No items found. Try: jobcollect feed fetch / jobcollect scrape[/]")
        return
    table = Table(title=f"{len(rows)} item(s)")
    table.add_column("Title", style="bold")
    table.add_column("Category", style="dim")
    table.add_column("Source", style="dim")
    table.add_column("Published", style="dim")
    for r in rows:
        table.add_row(r["title"], r["category"], r["source"], _time_ago(r["published_at"]))
    console.print(table)


@cli.command()
@click.option("--db", default=DEFAULT_DB)
@click.option("--config", default=DEFAULT_NOTIFY)
@click.option("--limit", default=10, type=int, help="Top entries per digest section.")
@click.option("--dry-run", is_flag=True, help="Print the digest without sending or updating state.")
def notify(db: str, config: str, limit: int, dry_run: bool) -> None:
    """Send a digest of new jobs + engine items via apprise."""
    urls: list[str] = []
    p = Path(config)
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        urls = list(raw.get("urls") or [])
    env_urls = os.environ.get("JOBCOLLECT_NOTIFY_URLS", "")
    if env_urls:
        urls += [u.strip() for u in env_urls.split(",") if u.strip()]
    store = Store(db)
    try:
        message, errors = run_notify(store, urls, top=limit, dry_run=dry_run)
    finally:
        store.close()
    if dry_run:
        console.print("[bold yellow]Dry run — digest not sent, state not updated:[/]\n")
    console.print(message)
    for err in errors:
        console.print(f"\n[red]•[/] {err}")


@cli.command()
@click.option("--db", default=DEFAULT_DB)
@click.option("--report", type=click.Choice(sorted(REPORTS)), default="jobs_by_source")
@click.option("--sql", default=None, help="Custom SQL over j.jobs / j.items (DuckDB).")
@click.option("--limit", default=25, type=int, help="Row cap for custom SQL.")
def analyze(db: str, report: str, sql: str | None, limit: int) -> None:
    """Run SQL analytics over jobs + items via DuckDB."""
    name, cols, rows = run_analysis(db, sql=sql, report=report, limit=limit)
    if not rows:
        console.print("[yellow]No results.[/]")
        return
    table = Table(title=name)
    for c in cols:
        table.add_column(c)
    for r in rows[:200]:
        table.add_row(*[str(v) if v is not None else "" for v in r])
    console.print(table)
    if len(rows) > 200:
        console.print(f"[dim]... {len(rows) - 200} more rows (use --sql with LIMIT)[/]")


@cli.command()
@click.option("--db", default=DEFAULT_DB)
@click.option("--threshold", default=88, type=int, help="Similarity score 0-100.")
@click.option("--limit", default=25, type=int, help="Max clusters to show.")
def dedupe(db: str, threshold: int, limit: int) -> None:
    """Find likely duplicates of the same posting across sources."""
    store = Store(db)
    try:
        clusters = find_duplicate_clusters(store, threshold=threshold, limit=limit)
    finally:
        store.close()
    if not clusters:
        console.print(f"[green]No cross-source duplicates found above {threshold} similarity.[/]")
        return
    console.print(f"[bold]{len(clusters)} cluster(s) of likely duplicates (threshold {threshold}):[/]")
    for idx, cluster in enumerate(clusters, 1):
        console.print(f"\n[bold cyan]{idx}. {cluster[0]['title']}[/]")
        for r in cluster:
            console.print(f"    • {r['company']} [{r['source']}] {r['url']}")


@cli.command()
@click.option("--config", default=DEFAULT_CONFIG)
def sources(config: str) -> None:
    """List the sources configured in the YAML file."""
    cfg: SourceConfig = load_config(config)
    console.print(f"[bold]Config:[/] {config}")
    console.print(f"[cyan]Boards:[/] {', '.join(cfg.boards) or '(none)'}")
    for label, slugs in (
        ("Greenhouse", cfg.greenhouse),
        ("Ashby", cfg.ashby),
        ("BambooHR", cfg.bamboohr),
        ("Lever", cfg.lever),
        ("Workday", cfg.workday),
    ):
        console.print(f"[cyan]{label}:[/] {', '.join(slugs) or '(none)'}")
    console.print(f"[cyan]RSS feeds:[/] {', '.join(f.get('name', f['url']) for f in cfg.rss_feeds) or '(none)'}")
    console.print(f"[cyan]Companies (crawled):[/] {', '.join(cfg.company_names) or '(none)'}")


@cli.command()
@click.option("--rows", default=100_000, type=int, help="Number of synthetic jobs (default 100k).")
@click.option("--db", default="stress.db", help="Scratch DB (recreated fresh each run).")
@click.option("--out", default="stress-sheets.csv", help="Google-Sheets-ready CSV output.")
@click.option("--results", default="stress-results.json", help="Machine-readable results output.")
@click.option("--seed", default=42, type=int, help="RNG seed for reproducible jobs.")
@click.option("--per-row-sample", default=200, type=int, help="Jobs ingested via the slow per-row path to measure it (rate is stable after ~100).")
@click.option("--api-workers", default=16, type=int, help="Concurrency for the API throughput test.")
@click.option("--api-rounds", default=20, type=int, help="Requests per worker for the API throughput test.")
def stress(rows: int, db: str, out: str, results: str, seed: int, per_row_sample: int, api_workers: int, api_rounds: int) -> None:
    """Load test: N synthetic jobs (posted in the last 24h) through every path.

    Ingests into a scratch SQLite DB, verifies data integrity, measures query /
    API / dashboard latency, and writes a Google-Sheets-ready CSV. Verdict
    PASS/FAIL with thresholds in stress-results.json. Synthetic data only —
    this never touches the real jobs.db.
    """
    results = run_stress(
        rows=rows, db_path=db, out_csv=out, results_json=results,
        seed=seed, per_row_sample=per_row_sample,
        api_workers=api_workers, api_rounds=api_rounds,
    )
    if not all(results["passed"].values()):
        raise click.ClickException("Stress test FAILED — see stress-results.json")


@cli.command("pw-scrape")
@click.option("--config", default=DEFAULT_SCRAPERS)
@click.option("--name", default=None, help="Run only this Playwright scraper.")
@click.option("--limit", default=None, type=int, help="Cap items per scraper.")
@click.option("--db", default=DEFAULT_DB)
def pw_scrape(config: str, name: str | None, limit: int | None, db: str) -> None:
    """Run Playwright-based job scrapers (JS-rendered career pages).

    Loads scrapers from the ``playwright_scrapers:`` section of scrapers.yaml.
    Requires ``pip install playwright && playwright install chromium``.
    """
    from .jsrender import close_browser

    p = Path(config)
    if not p.exists():
        raise click.ClickException(f"{config} not found.")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    pw_configs = [PlaywrightJobConfig.model_validate(s) for s in raw.get("playwright_scrapers", [])]
    if name:
        pw_configs = [s for s in pw_configs if s.name == name]
        if not pw_configs:
            raise click.ClickException(f"Playwright scraper {name!r} not found in {config}.")
    if not pw_configs:
        raise click.ClickException("No playwright_scrapers defined in config.")
    store = Store(db)
    try:
        total_seen = total_new = 0
        all_errors: list[str] = []
        for cfg in pw_configs:
            with console.status(f"[cyan]Playwright scraping {cfg.name}...[/]", spinner="dots"):
                seen, new, errors = run_playwright_scraper(cfg, store, limit=limit)
            total_seen += seen
            total_new += new
            all_errors.extend(errors)
            console.print(f"  [cyan]{cfg.name}[/]: {seen} seen, [green]{new} new[/]")
            for err in errors[:5]:
                console.print(f"    [red]•[/] {err}")
        console.print(f"\n[bold]Playwright total:[/] {total_seen} seen, [green]{total_new} new[/]")
        if all_errors:
            console.print(f"[yellow]{len(all_errors)} errors (showing first 10):[/]")
            for err in all_errors[:10]:
                console.print(f"  [red]•[/] {err}")
    finally:
        store.close()
        close_browser()


@cli.command("browser-scrape")
@click.option("--boards", default=None, help="Comma-separated board names to scrape (default: all).")
@click.option("--keywords", default=None, help="Comma-separated search keywords.")
@click.option("--limit", default=200, type=int, help="Max items per board.")
@click.option("--db", default=DEFAULT_DB)
def browser_scrape(boards: str | None, keywords: str | None, limit: int, db: str) -> None:
    """Run browser-based job scrapers (Playwright with anti-detection).

    Scrapes Indeed, LinkedIn, Glassdoor, Google Jobs, ZipRecruiter,
    Dice, Naukri, and SimplyHired using a real browser.

    Examples:
        jobcollect browser-scrape
        jobcollect browser-scrape --boards indeed,linkedin,glassdoor
        jobcollect browser-scrape --keywords "python developer,react engineer"
        jobcollect browser-scrape --limit 100
    """
    from .browser.runner import run_all_scrapers

    board_list = [b.strip() for b in boards.split(",")] if boards else None
    kw_list = [k.strip() for k in keywords.split(",")] if keywords else None

    console.print(f"[bold cyan]Browser scrape[/] — boards: {board_list or 'all'}, keywords: {kw_list or 'default'}, limit: {limit}")
    with console.status("[cyan]Launching browser...[/]", spinner="dots"):
        result = run_all_scrapers(
            keywords=kw_list,
            boards=board_list,
            max_items=limit,
            db_path=db,
        )

    console.print(f"\n[bold]Browser scrape complete:[/]")
    console.print(f"  Total seen: [cyan]{result['total_seen']}[/]")
    console.print(f"  Total new:  [green]{result['total_new']}[/]")
    console.print(f"  Database:   {result['db_path']}")
    console.print()
    for board, (seen, new, errors) in result["stats"].items():
        err_str = f' [yellow]({len(errors)} errors)[/]' if errors else ''
        console.print(f"  {board}: {seen} seen, [green]{new} new[/]{err_str}")
        for err in (errors or [])[:3]:
            console.print(f"    [red]•[/] {err}")


@cli.command()
@click.option("--db", default=DEFAULT_DB)
def stats(db: str) -> None:
    """Show database totals per source (jobs + engine items)."""
    store = Store(db)
    try:
        s = store.stats()
        it = store.items_stats()
    finally:
        store.close()
    console.print(f"[bold]Total jobs:[/] {s['total']}  [bold]active:[/] {s['active']}")
    table = Table(title="Jobs per source")
    table.add_column("Source")
    table.add_column("Active")
    table.add_column("Total")
    for name, counts in s["by_source"].items():
        table.add_row(name, str(counts["active"]), str(counts["total"]))
    console.print(table)
    console.print(f"\n[bold]Engine items:[/] {it['total']}  (per category: {', '.join(f'{k}={v}' for k, v in list(it['by_category'].items())[:8])})")
    if it["by_source"]:
        table2 = Table(title="Items per source")
        table2.add_column("Source")
        table2.add_column("Count")
        for name, n in it["by_source"].items():
            table2.add_row(name, str(n))
        console.print(table2)


@cli.command()
@click.option("--time", "run_time", default="09:00", show_default=True, help="Daily run time as HH:MM (local time).")
@click.option("--tasks", default="collect,browser,surf,master,web,feed,scrape,report", show_default=True, help="Comma-separated tasks: collect, browser, surf, web, feed, scrape, report.")
@click.option("--config", default=DEFAULT_CONFIG, help="Path to the companies YAML.")
@click.option("--feeds", default=DEFAULT_FEEDS, help="Path to feeds.yaml.")
@click.option("--scrapers", default=DEFAULT_SCRAPERS, help="Path to scrapers.yaml.")
@click.option("--db", default=DEFAULT_DB, help="Path to the SQLite database.")
@click.option("--log", default="logs/schedule.log", show_default=True, help="Log file for task output.")
@click.option("--once", is_flag=True, help="Run the tasks immediately once, then exit.")
@click.option("--install-task", is_flag=True, help="Windows: register a daily Scheduled Task that calls --once.")
@click.option("--remove-task", is_flag=True, help="Windows: remove the Scheduled Task.")
def schedule(
    run_time: str, tasks: str, config: str, feeds: str, scrapers: str,
    db: str, log: str, once: bool, install_task: bool, remove_task: bool,
) -> None:
    """Run collection tasks on a daily clock (no n8n/cron needed).

    Loops forever, running the chosen tasks once per day at --time. Each task
    runs as its own subprocess; a failing task never stops the others.
    Use --once to run today's tasks immediately (also used by the Windows
    Scheduled Task), --install-task to register the Windows daily task.
    """
    if install_task and remove_task:
        raise click.ClickException("Pick one of --install-task or --remove-task.")
    if install_task:
        at = parse_time(run_time)
        # Absolute paths so the scheduled task works from any cwd.
        try:
            cmd = install_windows_task(
                at,
                config=str(Path(config).resolve()),
                feeds=str(Path(feeds).resolve()),
                scrapers=str(Path(scrapers).resolve()),
                db=str(Path(db).resolve()),
                log_path=str(Path(log).resolve()),
            )
        except RuntimeError as exc:
            raise click.ClickException(str(exc))
        console.print(f"[green]Registered 'JobCollector Daily' scheduled task:[/] {at:%H:%M}")
        console.print(f"[dim]Command:[/] {cmd}")
        console.print("[dim]Verify: schtasks /Query /TN 'JobCollector Daily'. Remove: jobcollect schedule --remove-task[/]")
        return
    if remove_task:
        try:
            remove_windows_task()
        except RuntimeError as exc:
            raise click.ClickException(str(exc))
        console.print("[green]Removed 'JobCollector Daily' scheduled task.[/]")
        return

    try:
        at = parse_time(run_time)
        names = resolve_tasks(tasks)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    # Resolve paths so subprocesses work regardless of the caller's cwd.
    config = str(Path(config).resolve())
    feeds = str(Path(feeds).resolve())
    scrapers = str(Path(scrapers).resolve())
    db = str(Path(db).resolve())
    log_path = str(Path(log).resolve())

    if once:
        console.print(f"[bold]Running once:[/] {', '.join(names)}")
        results = run_tasks(names, config=config, feeds=feeds, scrapers=scrapers, db=db, log_path=log_path)
        for task in names:
            ok, status = results[task]
            console.print(f"  [{'green' if ok else 'red'}]{status}[/]")
        failed = [t for t, (ok, _) in results.items() if not ok]
        if failed:
            raise click.ClickException(f"Tasks failed: {', '.join(failed)} — see {log_path}")
        return

    run_loop(names, at=at, config=config, feeds=feeds, scrapers=scrapers, db=db, log_path=log_path)


@cli.command("watchlist-import")
@click.argument("csv_file", default="data/top-1000-employers.csv")
@click.option("--kind", default="company", type=click.Choice(["company", "keyword"]))
@click.option("--column", default="company", help="CSV column holding the names/terms.")
@click.option("--db", default=DEFAULT_DB)
def watchlist_import(csv_file: str, kind: str, column: str, db: str) -> None:
    """Bulk-add watchlist entries from a CSV (default: the top-1000 employers list).

    Example: jobcollect watchlist-import data/top-1000-employers.csv
    """
    p = Path(csv_file)
    if not p.exists():
        raise click.ClickException(f"{csv_file} not found.")
    import csv as _csv

    with open(p, encoding="utf-8", newline="") as f:
        rows = list(_csv.DictReader(f))
    if not rows or column not in rows[0]:
        raise click.ClickException(f"Column {column!r} not found in {csv_file}.")
    values = [r.get(column, "").strip() for r in rows if (r.get(column) or "").strip()]
    store = Store(db)
    try:
        res = store.watchlist_bulk_add(kind, values)
    finally:
        store.close()
    console.print(
        f"[green]Imported {res['added']} {kind}s[/] into the watchlist "
        f"({res['existing']} already present, {len(values)} rows read from {p})."
    )


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite an existing companies.yaml.")
def init_config(force: bool) -> None:
    """Create companies.yaml from the bundled example."""
    dest = Path(DEFAULT_CONFIG)
    if dest.exists() and not force:
        raise click.ClickException(f"{dest} already exists (use --force to overwrite).")
    if not EXAMPLE_CONFIG.exists():
        raise click.ClickException(f"Example config missing: {EXAMPLE_CONFIG}")
    shutil.copy(EXAMPLE_CONFIG, dest)
    console.print(f"[green]Wrote {dest}. Edit it to add your target companies and feeds.[/]")


@cli.command("init-engine")
@click.option("--force", is_flag=True, help="Overwrite existing example files.")
def init_engine(force: bool) -> None:
    """Create feeds.yaml and scrapers.yaml from the bundled examples."""
    for src, dest in ((EXAMPLE_FEEDS, DEFAULT_FEEDS), (EXAMPLE_SCRAPERS, DEFAULT_SCRAPERS)):
        if Path(dest).exists() and not force:
            console.print(f"[yellow]Skipped {dest} (exists; use --force to overwrite).[/]")
            continue
        shutil.copy(src, dest)
        console.print(f"[green]Wrote {dest}[/]")
    console.print("\nNext: jobcollect feed fetch  |  jobcollect scrape  |  jobcollect pw-scrape")


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
