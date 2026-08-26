"""Extract the full YC company directory (all batches).

ycombinator.com/companies renders its list through Algolia, and the Algolia
secured key only works from the page's own browser context (direct HTTP is
403'd from datacenter IPs). Algolia caps a single query at 1000 hits, but the
index (~6,200 companies) partitions cleanly by the ``batch`` facet, so this
one-time tool drives the real page once (Playwright) and runs one query per
batch from inside the page.

Output: data/yc-companies.json — used by scripts/generate_yc_config.py to
produce yc.yaml (hiring companies only) for the daily pipeline.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("data/yc-companies.json")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        cap: dict = {}

        def on_request(req) -> None:
            if "algolia" in req.url and "/queries" in req.url and "page=" in (req.post_data or ""):
                cap.setdefault("url", req.url)

        page.on("request", on_request)
        page.goto("https://www.ycombinator.com/companies", wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2000)
        if "url" not in cap:
            print("no algolia request captured", file=sys.stderr)
            return 1
        url = cap["url"]

        script = """
        async (args) => {
            const q = async (params) => {
                const r = await fetch(args.url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({requests: [{indexName: 'YCCompany_production', params}]})});
                const d = await r.json();
                return d.results[0] || null;
            };
            const facetRes = await q('hitsPerPage=0&facets=batch&maxValuesPerFacet=1000&query=&tagFilters=');
            const batches = Object.keys(facetRes.facets.batch).sort();
            const out = {};
            for (const b of batches) {
                const filters = 'batch:' + JSON.stringify(b);
                const res = await q('hitsPerPage=1000&page=0&query=&filters=' + encodeURIComponent(filters));
                for (const h of (res.hits || [])) {
                    out[h.slug] = {
                        name: h.name || h.slug,
                        slug: h.slug,
                        website: h.website || '',
                        batch: h.batch || b,
                        isHiring: !!h.isHiring,
                        team_size: h.team_size || 0,
                        regions: h.regions || [],
                    };
                }
            }
            return out;
        }
        """
        companies = page.evaluate(script, {"url": url})

        browser.close()

    OUT.parent.mkdir(exist_ok=True)
    data = sorted(companies.values(), key=lambda c: (c["batch"], c["slug"]))
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    hiring = sum(1 for c in data if c["isHiring"])
    with_website = sum(1 for c in data if c.get("website"))
    print(f"saved {len(data)} companies -> {OUT}  (hiring: {hiring}, with website: {with_website})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
