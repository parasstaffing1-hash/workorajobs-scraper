#!/usr/bin/env python3
"""Discover new ATS company slugs by scraping job board directories.

Strategy:
1. Scrape Greenhouse's embed pages for company listings
2. Scrape Lever's job board directory
3. Use Google to find companies on these platforms
4. Probe all discovered slugs
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
LOG = ROOT / ".freebuff" / "discover.log"
CP = ROOT / ".freebuff" / "discover_cp.json"
DB_LOCK = Lock()

_client = httpx.Client(
    timeout=4.0,
    follow_redirects=True,
    limits=httpx.Limits(max_connections=120, max_keepalive_connections=50),
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
    return {"probed": [], "discovered": [], "stats": {"new": 0, "boards": 0}}


def save_cp(cp):
    CP.parent.mkdir(parents=True, exist_ok=True)
    CP.write_text(json.dumps({
        "probed": list(cp["probed"])[-20000:],
        "discovered": list(cp["discovered"])[-20000:],
        "stats": cp["stats"],
    }), "utf-8")


# =====================================================================
# DISCOVER SLUGS FROM GREENHOUSE EMBED PAGES
# =====================================================================

def discover_greenhouse_slugs() -> set[str]:
    """Scrape Greenhouse's job board embed pages to find company slugs."""
    slugs = set()
    
    # Method 1: Scrape from boards.greenhouse.io
    log("Discovering from boards.greenhouse.io...")
    try:
        r = _client.get("https://boards.greenhouse.io")
        if r.status_code == 200:
            # Extract company slugs from links
            for m in re.finditer(r'boards\.greenhouse\.io/([a-z0-9][a-z0-9_-]+)', r.text):
                slugs.add(m.group(1).lower())
    except Exception:
        pass
    
    # Method 2: Scrape from job board aggregator sites
    aggregator_urls = [
        "https://www.himalayas.app/jobs/companies",
        "https://remoteok.com/remote-companies",
        "https://wellfound.com/companies",
        "https://www.crunchbase.com/hub/greenhouse-companies",
    ]
    
    for url in aggregator_urls:
        try:
            r = _client.get(url, timeout=5.0)
            if r.status_code == 200:
                # Extract any greenhouse/lever/ashby links
                for m in re.finditer(r'(?:greenhouse|lever|ashby|smartrecruiters|workable)\.com/([a-z0-9][a-z0-9_-]+)', r.text):
                    slugs.add(m.group(1).lower())
        except Exception:
            pass
    
    log(f"Discovered {len(slugs)} slugs from directories")
    return slugs


# =====================================================================
# GENERATE ADDITIONAL SLUGS FROM PATTERNS
# =====================================================================

def generate_pattern_slugs() -> set[str]:
    """Generate company slugs from common naming patterns."""
    slugs = set()
    
    # Common company name patterns
    prefixes = [
        'my', 'get', 'go', 'try', 'use', 'run', 'the', 'one', 'top', 'best',
        'fast', 'smart', 'blue', 'red', 'green', 'black', 'white', 'silver',
        'golden', 'alpha', 'beta', 'gamma', 'delta', 'omega', 'apex', 'prime',
        'ultra', 'mega', 'super', 'hyper', 'turbo', 'flash', 'swift', 'rapid',
        'quick', 'easy', 'simple', 'clear', 'pure', 'bright', 'dark', 'deep',
        'high', 'low', 'big', 'small', 'tiny', 'micro', 'macro', 'nano',
    ]
    
    roots = [
        'tech', 'works', 'labs', 'io', 'app', 'hub', 'spot', 'base', 'stack',
        'cloud', 'data', 'pay', 'health', 'care', 'flow', 'sync', 'shift',
        'rise', 'bolt', 'wave', 'pulse', 'spark', 'link', 'path', 'core',
        'edge', 'gate', 'zone', 'forge', 'craft', 'mind', 'byte', 'code',
        'dev', 'ops', 'net', 'web', 'bit', 'key', 'map', 'set', 'fly',
        'fox', 'owl', 'bee', 'ant', 'elk', 'ram', 'yak', 'ape', 'cat',
        'dog', 'cow', 'pig', 'hen', 'bug', 'bat', 'rat', 'gem', 'nut',
        'oak', 'elm', 'ash', 'fir', 'pine', 'cedar', 'maple', 'birch',
        'coral', 'ruby', 'jade', 'opal', 'onyx', 'sage', 'reed', 'fern',
        'moss', 'vine', 'root', 'leaf', 'seed', 'bloom', 'bud', 'petal',
    ]
    
    for p in prefixes:
        for r in roots:
            slugs.add(f"{p}{r}")
            slugs.add(f"{p}-{r}")
    
    # Tech company suffixes
    suffixes = ['ai', 'io', 'ly', 'fy', 'ify', 'ize', 'hub', 'now', 'up', 'go',
                'app', 'dev', 'ops', 'net', 'com', 'co', 'inc', 'lab', 'labs',
                'tech', 'works', 'systems', 'solutions', 'platforms', 'group', 'corp']
    
    for r in roots[:50]:
        for s in suffixes:
            slugs.add(f"{r}{s}")
            slugs.add(f"{r}-{s}")
    
    # Two-word combinations
    words1 = ['smart', 'fast', 'quick', 'easy', 'simple', 'clear', 'pure', 'bright',
              'blue', 'green', 'black', 'white', 'silver', 'golden', 'alpha', 'beta',
              'prime', 'ultra', 'mega', 'super', 'hyper', 'turbo', 'flash', 'swift',
              'rapid', 'quick', 'deep', 'high', 'low', 'big', 'new', 'old', 'hot',
              'cool', 'warm', 'cold', 'dry', 'wet', 'soft', 'hard', 'light', 'dark',
              'thick', 'thin', 'long', 'short', 'wide', 'narrow', 'tall', 'short']
    
    words2 = ['tech', 'labs', 'works', 'io', 'ai', 'app', 'hub', 'spot', 'base',
              'stack', 'cloud', 'data', 'pay', 'health', 'care', 'flow', 'sync',
              'shift', 'rise', 'bolt', 'wave', 'pulse', 'spark', 'link', 'path',
              'core', 'edge', 'gate', 'zone', 'forge', 'craft', 'mind', 'byte',
              'code', 'dev', 'ops', 'net', 'web', 'bit', 'key', 'map', 'set',
              'fly', 'fox', 'owl', 'bee', 'ant', 'gem', 'nut', 'oak', 'elm']
    
    for w1 in words1:
        for w2 in words2:
            slugs.add(f"{w1}{w2}")
            slugs.add(f"{w1}-{w2}")
    
    log(f"Generated {len(slugs)} pattern slugs")
    return slugs


# =====================================================================
# ATS PROBING
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
    except Exception:
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
    except Exception:
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
    except Exception:
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
    except Exception:
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
    except Exception:
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
    except Exception:
        return None


def probe(slug: str) -> list[dict] | None:
    for fn in (try_greenhouse, try_lever, try_ashby, try_smartrecruiters,
               try_workable, try_teamtailor):
        result = fn(slug)
        if result:
            return result
    return None


# =====================================================================
# STORE JOBS
# =====================================================================

def store_jobs(conn, jobs) -> int:
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
    ap.add_argument("--threads", type=int, default=80)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    if args.reset and CP.exists():
        CP.unlink()
        log("Checkpoint reset")

    # Build slug list
    all_slugs = set()
    
    # Load existing from mega_probe.py
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
                    if len(w) >= 3 and len(w) <= 40 and re.match(r'^[a-z0-9][a-z0-9._-]+$', w):
                        all_slugs.add(w)
    
    # Add directory-discovered slugs
    dir_slugs = discover_greenhouse_slugs()
    all_slugs.update(dir_slugs)
    
    # Add pattern-generated slugs
    pattern_slugs = generate_pattern_slugs()
    all_slugs.update(pattern_slugs)
    
    # Filter
    bad = {"the","and","for","inc","com","all","new","our","app","big","top","pro","out","one",
           "get","add","its","can","has","had","was","are","not","also","into","with","from",
           "this","that","than","your","you","now"}
    all_slugs = sorted([s for s in all_slugs if len(s) >= 3 and s not in bad])
    
    log(f"Total slugs to probe: {len(all_slugs)}")
    
    # Load checkpoint
    cp = load_cp() if args.resume else {"probed": [], "discovered": [], "stats": {"new": 0, "boards": 0}}
    probed_set = set(cp["probed"])
    remaining = [s for s in all_slugs if s not in probed_set]
    log(f"Already probed: {len(probed_set)}, Remaining: {len(remaining)}")
    
    if not remaining:
        log("All slugs already probed!")
        return
    
    conn = sqlite3.connect(DB, check_same_thread=False)
    total_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    log(f"DB: {total_before:,} | Gap 1M: {max(0, 1_000_000 - total_before):,}")
    
    new_total = cp["stats"]["new"]
    boards = cp["stats"]["boards"]
    start = time.time()
    
    # Probe in batches
    BATCH = 500
    for bi in range(0, len(remaining), BATCH):
        batch = remaining[bi:bi+BATCH]
        with ThreadPoolExecutor(max_workers=args.threads) as ex:
            futures = {ex.submit(probe, s): s for s in batch}
            for f in as_completed(futures):
                slug = futures[f]
                probed_set.add(slug)
                try:
                    jobs = f.result()
                    if jobs:
                        boards += 1
                        src = jobs[0]["source"]
                        new = store_jobs(conn, jobs)
                        new_total += new
                        log(f"  +{new:4d} {slug:30s} -> {src:30s} ({len(jobs)} jobs)")
                except Exception:
                    pass
        
        # Save checkpoint
        cp["probed"] = list(probed_set)
        cp["stats"]["new"] = new_total
        cp["stats"]["boards"] = boards
        save_cp(cp)
        
        elapsed = time.time() - start
        current = total_before + new_total
        rate = new_total / (elapsed / 60) if elapsed > 0 else 0
        done_pct = len(probed_set) * 100 / len(all_slugs)
        log(f"  [{len(probed_set)}/{len(all_slugs)}] {done_pct:.0f}% | DB: {current:,} (+{new_total:,}) | Boards: {boards} | {rate:.0f}/min | Gap: {max(0, 1_000_000 - current):,}")
        
        if current >= 1_000_000:
            log(f"\n*** 1M JOBS REACHED! ***")
            break
    
    final = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    elapsed = time.time() - start
    conn.close()
    _client.close()
    
    log("")
    log("=" * 60)
    log(f"DISCOVERY COMPLETE: {len(probed_set)} slugs, {boards} boards, +{new_total:,} new jobs")
    log(f"DB: {final:,} | Gap 1M: {max(0, 1_000_000 - final):,}")
    log(f"Time: {elapsed/60:.1f} min | Rate: {new_total/(elapsed/60):.0f}/min")
    log("=" * 60)


if __name__ == "__main__":
    main()
