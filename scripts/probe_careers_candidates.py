"""For companies whose guessed careers_url failed, probe common alternatives.

Tries, in order:  https://careers.<domain>, https://jobs.<domain>,
https://<domain>/jobs, /en/careers, /careers/jobs, /company/careers —
and reports the first one that answers 2xx.

Usage:
    python scripts/probe_careers_candidates.py            # uses data/careers-url-failures.json
    python scripts/probe_careers_candidates.py --json out.json
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
UA = "jobcollector/0.1 (job aggregator; https://github.com/example/jobcollector)"


def candidates_for(url: str) -> list[str]:
    host = urlparse(url).netloc.lower()
    labels = [p for p in host.split(".") if p]
    dom = ".".join(labels[-2:]) if len(labels) > 2 else host
    # drop a leading www. from the registrable guess
    dom = dom[4:] if dom.startswith("www.") else dom
    base = f"https://{dom}"
    cands = [
        f"https://careers.{dom}",
        f"https://jobs.{dom}",
        f"{base}/jobs",
        f"{base}/en/careers",
        f"{base}/careers/jobs",
        f"{base}/company/careers",
    ]
    seen: set[str] = set()
    return [c for c in cands if not (c in seen or seen.add(c))]


def probe(client: httpx.Client, url: str) -> tuple[str, int]:
    try:
        r = client.get(url)
        return str(r.url), r.status_code
    except httpx.HTTPError:
        return url, 0


def main() -> int:
    src = ROOT / "data" / "careers-url-failures.json"
    failures = json.loads(src.read_text(encoding="utf-8"))
    print(f"probing {len(failures)} failing companies")

    out = []
    with httpx.Client(timeout=12.0, follow_redirects=True, headers={"User-Agent": UA}) as client:
        for f in failures:
            cands = candidates_for(f["url"])
            results = {}
            with ThreadPoolExecutor(max_workers=8) as pool:
                futs = {pool.submit(probe, client, c): c for c in cands}
                for fut in as_completed(futs):
                    c = futs[fut]
                    final, status = fut.result()
                    results[c] = (status, final)
            hit = next(((c, st, fin) for c, (st, fin) in results.items() if 200 <= st < 400), None)
            rec = {"name": f["name"], "current": f["url"], "candidates": results}
            if hit:
                rec["working"] = {"candidate": hit[0], "status": hit[1], "final": hit[2]}
            out.append(rec)
            tag = f"-> {hit[0]} ({hit[1]})" if hit else "ALL FAILED"
            print(f"{f['name']:<45} {tag}", flush=True)

    dest = ROOT / "data" / "careers-candidate-probes.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    ok = sum(1 for r in out if "working" in r)
    print(f"\nwrote {dest}: {ok}/{len(out)} found a working candidate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
