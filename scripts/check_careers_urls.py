"""Probe configured companies' careers_url values and report their health.

Usage:
    python scripts/check_careers_urls.py                 # check all companies in companies.yaml
    python scripts/check_careers_urls.py --limit 120     # check only the first N (rank order)
    python scripts/check_careers_urls.py --top-1000      # check companies-top1000.yaml instead

For each company:  OK (2xx), REDIRECT (final URL differs from the guess), or
FAIL (HTTP error / connection error / DNS failure). Prints a compact table plus
a machine-readable JSON of failures at the end for curation.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent

UA = "jobcollector/0.1 (job aggregator; https://github.com/example/jobcollector)"


def load_companies(use_top1000: bool):
    from jobcollector.config import load_config

    path = ROOT / ("companies-top1000.yaml" if use_top1000 else "companies.yaml")
    return load_config(path).companies


def check(client: httpx.Client, url: str) -> tuple[str, str]:
    """Return (status, final_url)."""
    try:
        resp = client.get(url)
    except httpx.HTTPError as exc:
        return "FAIL", f"{type(exc).__name__}: {exc}"
    final = str(resp.url)
    if resp.status_code >= 400:
        return f"FAIL({resp.status_code})", final
    if final.rstrip("/") != url.rstrip("/"):
        return "REDIRECT", final
    return "OK", final


def main() -> int:
    args = sys.argv[1:]
    use_top1000 = "--top-1000" in args
    limit = None
    for i, a in enumerate(args):
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])
        elif a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])

    companies = load_companies(use_top1000)
    if limit:
        companies = companies[:limit]

    results: list[tuple[str, str, str, str]] = [None] * len(companies)
    with httpx.Client(
        timeout=15.0, follow_redirects=True, headers={"User-Agent": UA}
    ) as client:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {
                pool.submit(check, client, c.careers_url): (i, c) for i, c in enumerate(companies)
            }
            for fut in as_completed(futs):
                i, c = futs[fut]
                status, final = fut.result()
                results[i] = (c.name, c.careers_url, status, final)
        for i, (name, url, status, final) in enumerate(results, 1):
            print(f"{i:>4} {status:<12} {url:<55} -> {final}", flush=True)

    fails = [r for r in results if r[2].startswith("FAIL")]
    print("\n=== summary ===")
    print(f"total={len(results)} ok={sum(1 for r in results if r[2]=='OK')} "
          f"redirect={sum(1 for r in results if r[2]=='REDIRECT')} fail={len(fails)}")
    if fails:
        out = ROOT / "data" / "careers-url-failures.json"
        out.write_text(
            json.dumps([{"name": r[0], "url": r[1], "status": r[2]} for r in fails],
                       indent=2),
            encoding="utf-8",
        )
        print(f"failures written to {out}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
