# Stress Test & Verification Plan — 100,000 jobs in 24h

This document is the verification plan for the load test. It states **what** is
verified, **how** to reproduce it, the **acceptance criteria**, and how to get
the resulting dataset into **Google Sheets** and prove it landed intact.

---

## 1. Scope & method

The goal is proof that the collector's **pipeline** (not the network) handles a
100,000-job dataset where every job was posted within the last 24 hours, and a
Google-Sheets-ready file to import.

What the test does:

| # | Phase | What is exercised (real production code) |
|---|-------|-------------------------------------------|
| 1 | Ingest | `Store.upsert` (per-row) vs `Store.upsert_many` (batched) |
| 2 | Integrity | counts, uniqueness, activity, posted-at window, field non-emptiness |
| 3 | Queries | `Store.search` (q / source / since), `Store.stats`, full CSV export |
| 4 | API | live `ThreadingHTTPServer`: latency, paging coverage, concurrency |
| 5 | Dashboard | `render_dashboard` against the 100k-row DB |
| 6 | Sheets | write the Google-Sheets CSV (exact n8n header contract) |

> **What it deliberately does NOT do:** scrape 100k real jobs. That is neither
> feasible nor respectful of the sources (blocking/ToS), and the stress test's
> job is verifying the **pipeline at scale**, not the internet. The data is
> synthetic-but-realistic (seeded RNG, real title/company/location distributions)
> and clearly tagged with `stress:` sources so it can never be confused with
> real data.

## 2. How to run it

```bash
# 100k jobs, scratch DB, writes stress-sheets.csv + stress-results.json
jobcollect stress --rows 100000

# smaller sanity run
jobcollect stress --rows 5000
```

- Creates a fresh scratch DB (`stress.db`) every run — never touches `jobs.db`.
- Results land in `stress-results.json` (machine-readable metrics + thresholds + pass/fail).
- Exit code 0 = pass, 1 = fail.

## 3. Acceptance criteria — measured 2026-08-16 on this machine

| # | Criterion | Threshold | Measured | Status |
|---|-----------|-----------|----------|--------|
| 1 | Batched ingest rate | ≥ 2,000 jobs/s | **12,589 jobs/s** (100k in ~8s) | PASS |
| 2 | Search p95 (q=engineer, limit 50) | ≤ 250 ms | **105 ms** | PASS |
| 3 | `stats()` latency | ≤ 500 ms | **19 ms** | PASS |
| 4 | Full 100k-row CSV export | ≤ 60 s | **5.9 s** | PASS |
| 5 | API: one 10k-row page | ≤ 5 s | **1.25 s** | PASS |
| 6 | API p95 (limit=1000, serial) | ≤ 1 s | **375 ms** | PASS |
| 7 | API concurrency floor | ≥ 5 req/s | **13 req/s** (16 workers) | PASS |
| 8 | Dashboard render (1k embedded) | ≤ 30 s | **105 ms** | PASS |
| 9 | Row count == 100,000 | exact | **100,000** | PASS |
| 10 | Unique dedupe keys == 100,000 | exact | **100,000** | PASS |
| 11 | All rows active | 100% | **100%** | PASS |
| 12 | Zero empty title/company/url | 0 | **0** | PASS |
| 13 | posted_at within last 24h | 100% | **100% (span 24.0 h)** | PASS |
| 14 | API paging covers all 100k rows | full | **100,000** | PASS |
| 15 | Sheets CSV row count | 100,000 | **100,000** (22.2 MB) | PASS |

**Total run time: 129 s.** Overall verdict: **PASS**.

## 4. Bottlenecks found & fixed by this test

The stress test was not decorative — it found two real production defects:

1. **Per-row commit (≈1,500× slower).** The old ingestion path committed to
   SQLite once per job: **12 jobs/s** on this machine (≈ 2.3 h for 100k).
   Batched `upsert_many` (one transaction per source): **12,589 jobs/s**.
   `pipeline.collect` now uses the batched path. The per-row rate is still
   measured and reported so a regression back to per-row commits is visible.
   (There was also a latent SQLite bind-variable limit bug in the naive bulk
   implementation — the existence probe is chunked to stay under it.)

2. **Missing covering index — `stats()` 527 ms → 19 ms.** `GROUP BY source`
   with `SUM(is_active)` was doing 100k random table lookups because the source
   index didn't cover `is_active`. Added `idx_jobs_source_active (source,
   is_active)` (27× faster). Dashboard render dropped 581 ms → 105 ms as a
   side effect. The threshold is set to catch any regression of this fix.

3. **API had no paging.** `/api/jobs` capped at 10k rows with no `offset`, so
   >10k rows could never be pulled out. Added `offset` to `/api/jobs`,
   `/api/items`, and `Store.search`/`search_items`. The test verifies a full
   100k-row paging sweep covers every row exactly once.

### Known limits (accepted, not defects)

- The stdlib reader server opens a fresh `Store` (schema init) per request:
  ~13 req/s under 16-way load at 100k rows, p95 ~1.75 s. Fine for the n8n
  daily pull (10 pages); if you ever need real concurrency, pool connections
  per thread or move to a real server. Threshold 7 is a regression floor.
- `search LIKE %q%` scans the table (no FTS). 105 ms p95 at 100k rows is fine
  today; at 1M+ rows consider FTS5.
- Google Sheets caps a spreadsheet at **10,000,000 cells** — 100k rows × 9
  cols = 900k cells, comfortably within limits.

## 5. Integrity checks you can re-run yourself

```bash
# against the scratch DB produced by `jobcollect stress`
sqlite3 stress.db "
  SELECT COUNT(*)                                   AS total       FROM jobs;
  SELECT COUNT(DISTINCT dedupe_key)                 AS unique_keys FROM jobs;
  SELECT COUNT(*)                                   AS active      FROM jobs WHERE is_active = 1;
  SELECT COUNT(*)                                   AS empty       FROM jobs WHERE title='' OR company='' OR url='';
  SELECT COUNT(*)                                   AS in_24h
    FROM jobs WHERE posted_at >= datetime('now','-1 day') AND posted_at <= datetime('now');
"
# verify the CSV contract
head -1 stress-sheets.csv   # header must be:
# title,company,location,salary,source,posted_at,url,tags,is_active
wc -l stress-sheets.csv     # 100,001 lines (1 header + 100,000 rows)
```

## 6. Getting the 100k rows into Google Sheets

Three options, from simplest to most automated:

### Option A — Direct import (one-time, simplest)
1. Open [sheets.new](https://sheets.new).
2. **File → Import → Upload**, choose `stress-sheets.csv`, pick "Replace current sheet".
3. Done. Verify with the SQL below.

### Option B — n8n workflow (daily, automated) — recommended for production
The existing `n8n/jobcollect-daily-sheets.json` pulls `/api/jobs` (with the new
`offset` paging) and appends to your sheet at 9:00 daily. For 100k rows:

- The workflow's HTTP node pages through `offset=0,10000,...` (10 pages).
- **Batching note:** appending 100k rows through n8n in one node execution is
  slow and can hit n8n timeouts. Two proven patterns:
  1. **Clear + import CSV** (fastest): have n8n run `jobcollect stress --rows
     100000 --out stress-sheets.csv` via the Execute Command node, then use
     the Google Drive node to **import** the CSV into the sheet (Sheets import
     handles 100k rows natively).
  2. **Batched append**: loop the Append node 10× with 10k-row pages and a
     `Wait` node between batches.
- See `n8n/README.md` for credential + import steps.

### Option C — Google Apps Script (serverless)
A small Apps Script can fetch `http://<host>:8600/api/jobs?limit=10000&offset=N`
and write rows with `SpreadsheetApp.Range.setValues()` in 10k chunks (the 6-min
execution limit fits ~30k-50k rows per run; schedule it daily).

## 7. Verification checklist for the sheet

After the data is in Sheets, confirm it landed intact:

```text
1. Row count    = 100,000  (COUNTA(A2:A) == 100000, or check the sheet tab size)
2. No duplicates = UNIQUE(A2:F) has 100,000 rows (url column is unique)
3. Freshness     = MAX(posted_at) within the last 24h of generation;
                   MIN(posted_at) ≥ 24h before MAX
4. Completeness  = 0 empty cells in title/company/url columns
5. Spot-check    = a few random titles read sensibly (e.g. filter source = 'stress:greenhouse:acme')
6. Types         = is_active column is all 1; posted_at parses as dates in Sheets
7. Links         = sample 5 urls in a browser; all resolve (example.com/jobs/...)
```

One command to cross-check the CSV itself:

```bash
python - <<'EOF'
import csv
rows = list(csv.DictReader(open('stress-sheets.csv', encoding='utf-8')))
urls  = {r['url'] for r in rows}
print(len(rows), 'rows |', len(urls), 'unique urls |',
      all(r['title'] and r['company'] for r in rows), 'no empty title/company')
EOF
```

## 8. Re-running and regression signal

- `stress-results.json` is the machine-readable artifact — diff it across runs
  to watch for regressions (e.g. a change that reintroduces per-row commits or
  drops the covering index shows up immediately in metrics 1 and 3).
- Thresholds are floors, not aspirations: they exist to catch **defects**,
  not to fail on machine variance. When you move to a different machine, re-run
  once and adjust only if the numbers are consistently different.

## 9. Files

| File | Purpose |
|------|---------|
| `stress-sheets.csv` | 100,000-row Google-Sheets-ready export (22.2 MB) |
| `stress-results.json` | machine-readable metrics + thresholds + pass/fail |
| `stress.db` | scratch database from the run (recreated each run) |
| `jobcollector/stress.py` | the harness (`jobcollect stress`) |
| `n8n/jobcollect-daily-sheets.json` | daily 9 AM → Sheets workflow |
