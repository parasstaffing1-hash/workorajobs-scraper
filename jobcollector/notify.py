"""Daily digest notifications via apprise.

Builds a markdown summary of what changed since the last digest — new/expired
jobs and new engine items — and delivers it to any apprise-supported channel
(email, Slack, Telegram, Discord, ntfy, ...). Delivery state is tracked in the
DB so re-runs stay incremental.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .storage import Store

LAST_NOTIFY_KEY = "last_notify"


def build_digest(store: Store, since: str, top: int = 10) -> str:
    """Markdown digest of jobs and items changed since ``since`` (ISO)."""
    since_iso = since
    lines = [f"**Jobs & data digest** — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
    lines.append("")

    # ---------------------------------------------------------------- jobs
    new_jobs = store.search(active_only=False, limit=100000)
    fresh = [r for r in new_jobs if (r["first_seen_at"] or "") >= since_iso]
    expired = [r for r in new_jobs if not r["is_active"] and (r["last_seen_at"] or "") >= since_iso]
    active = sum(1 for r in new_jobs if r["is_active"])

    lines.append(f"**Jobs** — new: {len(fresh)} · expired: {len(expired)} · active: {active}")
    if fresh:
        lines.append(f"Top new jobs ({min(top, len(fresh))}):")
        for r in sorted(fresh, key=lambda x: x["first_seen_at"] or "", reverse=True)[:top]:
            lines.append(f"- [{r['title']}]({r['url']}) — {r['company']} ({r['source']})")
    if expired:
        lines.append(f"Expired ({min(5, len(expired))}):")
        for r in expired[:5]:
            lines.append(f"- ~~{r['title']}~~ — {r['company']} ({r['source']})")
    lines.append("")

    # --------------------------------------------------------------- items
    items = store.search_items(limit=100000)
    fresh_items = [r for r in items if (r["first_seen_at"] or "") >= since_iso]
    if fresh_items:
        by_cat: dict[str, int] = {}
        for r in fresh_items:
            by_cat[r["category"] or "(none)"] = by_cat.get(r["category"] or "(none)", 0) + 1
        cats = ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1]))
        lines.append(f"**Engine items** — new: {len(fresh_items)} ({cats})")
        lines.append(f"Top new items ({min(top, len(fresh_items))}):")
        for r in sorted(fresh_items, key=lambda x: x["first_seen_at"] or "", reverse=True)[:top]:
            lines.append(f"- [{r['title']}]({r['url']}) — {r['source']}")
        lines.append("")
    else:
        lines.append("**Engine items** — nothing new.")
        lines.append("")

    return "\n".join(lines).strip()


def send_digest(urls: list[str], message: str, dry_run: bool = False) -> list[str]:
    """Deliver the digest via apprise. Returns a list of errors (empty = ok)."""
    errors: list[str] = []
    if dry_run:
        return errors
    if not urls:
        errors.append("No notification URLs configured (notify.yaml `urls:` or JOBCOLLECT_NOTIFY_URLS).")
        return errors
    try:
        import apprise
    except ImportError as exc:  # pragma: no cover
        errors.append("apprise not installed. Run `pip install -e '.[engine]'`.")
        return errors
    apobj = apprise.Apprise()
    for url in urls:
        if not apobj.add(url):
            errors.append(f"Unrecognized apprise URL: {url}")
    if errors:
        return errors
    ok = apobj.notify(body=message, title="JobCollector digest", body_format=apprise.NotifyFormat.MARKDOWN)
    if not ok:
        errors.append("apprise reported a delivery failure.")
    return errors


def run_notify(store: Store, urls: list[str], top: int = 10, dry_run: bool = False) -> tuple[str, list[str]]:
    """Build + send the digest. Returns (message, errors). Updates state unless dry-run."""
    last = store.get_notify_state(LAST_NOTIFY_KEY)
    since = last or (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    message = build_digest(store, since, top=top)
    errors = send_digest(urls, message, dry_run=dry_run)
    if not dry_run and not errors:
        store.set_notify_state(LAST_NOTIFY_KEY, datetime.now(timezone.utc).isoformat())
    return message, errors
