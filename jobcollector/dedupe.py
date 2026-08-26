"""Cross-source duplicate detection using rapidfuzz.

The same posting often appears on several boards (Remotive + RemoteOK + the
company's own career page). Exact dedupe (dedupe_key) already prevents
*within-source* duplicates; this module finds *across-source* near-duplicates
by fuzzy-matching normalized title+company pairs and grouping them into
clusters, so you can see the overlap at a glance.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .storage import Store

_NORM = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return _NORM.sub(" ", (text or "").lower()).strip()


def find_duplicate_clusters(store: Store, threshold: int = 88, limit: int = 25) -> list[list[dict]]:
    """Return clusters of active jobs that look like the same posting."""
    rows = store.search(active_only=True, limit=100000)
    by_company: dict[str, list[dict]] = {}
    for r in rows:
        by_company.setdefault(_norm(r["company"]) or "?", []).append(r)

    # Union-find over fuzzy matches (only across different sources).
    parent = {r["dedupe_key"]: r["dedupe_key"] for r in rows}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for group in by_company.values():
        n = len(group)
        for i in range(n):
            a = group[i]
            for j in range(i + 1, n):
                b = group[j]
                if a["source"] == b["source"]:
                    continue
                if fuzz.ratio(_norm(a["title"]), _norm(b["title"])) >= threshold:
                    union(a["dedupe_key"], b["dedupe_key"])

    clusters: dict[str, list[dict]] = {}
    for r in rows:
        clusters.setdefault(find(r["dedupe_key"]), []).append(r)

    result = [c for c in clusters.values() if len(c) > 1]
    result.sort(key=len, reverse=True)
    return result[:limit]
