#!/usr/bin/env python3
"""Ultra-fast ATS probe — 3s timeout, 80 threads, connection pooling.
Probes 5000+ slugs across 7 ATS platforms in minutes.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import httpx

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
CP_FILE = ROOT / ".freebuff" / "fast_probe_checkpoint.json"
LOG_FILE = ROOT / ".freebuff" / "fast_probe.log"
DB_LOCK = Lock()

# Reuse a single connection pool
_client = httpx.Client(
    timeout=3.0,
    follow_redirects=True,
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=40),
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
)


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as f:
        f.write(line + "\n")


def load_checkpoint() -> dict:
    if CP_FILE.exists():
        try:
            return json.loads(CP_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done": [], "stats": {"new": 0, "boards": 0, "errors": 0}}


def save_checkpoint(cp: dict):
    CP_FILE.parent.mkdir(parents=True, exist_ok=True)
    save = {"done": list(cp["done"]), "stats": cp["stats"]}
    CP_FILE.write_text(json.dumps(save), encoding="utf-8")


# =====================================================================
# MASSIVE SLUG LIST
# =====================================================================
RAW = open(ROOT / "scripts" / "mega_probe.py", encoding="utf-8").read()
# Extract COMPANIES string from mega_probe.py
m = re.search(r'COMPANIES = """(.*?)"""', RAW, re.DOTALL)
COMPANIES_TEXT = m.group(1) if m else ""


def build_slugs() -> list[str]:
    slugs = set()
    for line in COMPANIES_TEXT.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for w in line.replace(",", " ").split():
            w = w.strip().lower()
            if not w or len(w) < 3 or len(w) > 40:
                continue
            if not re.match(r'^[a-z0-9][a-z0-9._-]+$', w):
                continue
            slugs.add(w)
            slugs.add(w.replace("-", ""))
            slugs.add(w.replace("_", ""))
            slugs.add(w.replace(".", ""))
    bad = {"the","and","for","inc","com","all","new","our","app","big","top","pro","out","one",
           "get","add","its","can","has","had","was","are","not","also","into","with","from",
           "this","that","than","your","you","now","nexus","pilot","meta","delta","alpha",
           "apex","core","edge","flux","hub","ion","jet","kin","link","map","net","oak",
           "opt","pin","ray","set","tap","ux","vim","zen","zeta","ion","ace","arc","bio",
           "box","cap","day","ego","fly","fox","gem","hex","ide","jam","key","lab","max",
           "mix","neo","nut","peg","py","raw","sky","sun","van","via","wax","win","zoo"}
    return sorted([s for s in slugs if len(s) >= 3 and s not in bad])


# =====================================================================
# FAST SCRAPERS (3s timeout via shared client)
# =====================================================================

def try_greenhouse(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        if r.status_code != 200:
            return None
        data = r.json()
        jobs = data.get("jobs", [])
        if not jobs:
            return None
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
    except:
        return None


def try_lever(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list) or not data:
            return None
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
    except:
        return None


def try_ashby(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if r.status_code != 200:
            return None
        data = r.json()
        board = data.get("jobBoard", {})
        openings = board.get("openings", [])
        if not openings:
            return None
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
    except:
        return None


def try_smartrecruiters(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0")
        if r.status_code != 200:
            return None
        data = r.json()
        content = data.get("content", [])
        if not content:
            return None
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
    except:
        return None


def try_workable(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
        if r.status_code != 200:
            return None
        data = r.json()
        jobs = data.get("jobs", [])
        if not jobs:
            return None
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
    except:
        return None


def try_teamtailor(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://{slug}.teamtailor.com/jobs.json")
        if r.status_code != 200:
            return None
        data = r.json()
        jobs = data if isinstance(data, list) else data.get("jobs", [])
        if not jobs:
            return None
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
    except:
        return None


def try_breezy(slug: str) -> list[dict] | None:
    try:
        r = _client.get(f"https://{slug}.breezy.hr/json")
        if r.status_code != 200:
            return None
        data = r.json()
        positions = data.get("positions", [])
        if not positions:
            return None
        return [{
            "title": j.get("name", ""),
            "company": (data.get("company") or {}).get("name", slug),
            "location": j.get("location", ""),
            "url": j.get("url", ""),
            "posted_at": j.get("created_at"),
            "external_id": j.get("id", ""),
            "source": f"breezy:{slug}",
            "description": "",
            "tags": "",
        } for j in positions]
    except:
        return None


def probe(slug: str) -> list[dict] | None:
    for fn in (try_greenhouse, try_lever, try_ashby, try_smartrecruiters,
               try_workable, try_teamtailor, try_breezy):
        result = fn(slug)
        if result:
            return result
    return None


def store(conn, jobs, tag) -> int:
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
                     j.get("posted_at"), j.get("salary", ""), tag, now, now))
                if cur.rowcount > 0:
                    new += 1
            except:
                continue
        conn.commit()
    return new


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=80)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    slugs = build_slugs()
    log(f"Slugs: {len(slugs)}")

    cp = load_checkpoint() if args.resume else {"done": [], "stats": {"new": 0, "boards": 0, "errors": 0}}
    done_set = set(cp["done"])
    remaining = [s for s in slugs if s not in done_set]
    log(f"Done: {len(done_set)}, Remaining: {len(remaining)}")

    if not remaining:
        log("All probed!")
        return

    conn = sqlite3.connect(DB, check_same_thread=False)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB: {total_before:,} | Gap 1M: {max(0, 1_000_000 - total_before):,}")

    new_total = cp["stats"]["new"]
    boards = cp["stats"]["boards"]
    errs = cp["stats"]["errors"]
    start = time.time()

    BATCH = 500
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
                        src = jobs[0]["source"]
                        new = store(conn, jobs, f"fast,{slug}")
                        new_total += new
                        log(f"  +{new:4d} {slug:30s} -> {src:30s} ({len(jobs)} jobs)")
                    else:
                        pass  # 404 fast, no output
                except:
                    errs += 1

        # Save checkpoint every batch
        cp["done"] = list(done_set)
        cp["stats"] = {"new": new_total, "boards": boards, "errors": errs}
        save_checkpoint(cp)

        elapsed = time.time() - start
        current = total_before + new_total
        rate = new_total / (elapsed / 60) if elapsed > 0 else 0
        done_pct = len(done_set) * 100 / len(slugs)
        log(f"  [{len(done_set)}/{len(slugs)}] {done_pct:.0f}% | DB: {current:,} (+{new_total:,}) | Boards: {boards} | {rate:.0f}/min | Gap: {max(0, 1_000_000 - current):,}")

    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    elapsed = time.time() - start
    conn.close()
    _client.close()

    log("")
    log("=" * 60)
    log(f"PROBE COMPLETE: {len(done_set)} slugs, {boards} boards, +{new_total:,} new jobs")
    log(f"DB: {final:,} | Gap 1M: {max(0, 1_000_000 - final):,}")
    log(f"Time: {elapsed/60:.1f} min | Rate: {new_total/(elapsed/60):.0f}/min")
    log("=" * 60)


if __name__ == "__main__":
    main()
