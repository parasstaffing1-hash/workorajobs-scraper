# WorkoraJobs 24/7 scraper stack

This deployment moves memory-heavy scraping off the always-on Render process.

## Architecture

- **Modal** — scheduler + isolated scraper workers + health endpoint.
- **Modal Dict** — persistent cursors and recent run status.
- **Any S3-compatible object storage** — compressed JSONL snapshots. Tigris, Backblaze B2, Cloudflare R2, AWS S3, and compatible providers work through the same environment variables.
- **Better Stack (optional)** — heartbeat monitoring and alerts.
- **Render** — keep only the lightweight web/UI service if still needed. Do not keep the old long-running scraper enabled after Modal is verified.

The ATS worker runs every 15 minutes. With the default `ATS_BATCH_SIZE=110`, it traverses roughly 10,560 company-list entries per day, so a 10,000-company list is refreshed in about a day. The JobSpy worker is isolated in a separate 3 GiB container every two hours so a spike there cannot take down ATS collection.

Each run is intentionally short lived. It loads only one shard, uses bounded HTTP concurrency, uploads one gzip-compressed JSONL object, updates its cursor, and exits. This eliminates the main failure mode of a long-lived process accumulating memory.

## 1. Create a Modal account

On a local machine or a shell with Python 3.10+:

```bash
python -m pip install -U modal
modal setup
```

## 2. Create the runtime secret

Create one S3-compatible bucket first, then put its credentials in Modal.

```bash
modal secret create workorajobs-runtime \
  S3_ENDPOINT_URL="YOUR_PROVIDER_ENDPOINT" \
  S3_ACCESS_KEY_ID="YOUR_ACCESS_KEY" \
  S3_SECRET_ACCESS_KEY="YOUR_SECRET_KEY" \
  S3_BUCKET="YOUR_BUCKET" \
  S3_REGION="YOUR_REGION" \
  S3_OBJECT_PREFIX="workorajobs" \
  ATS_BATCH_SIZE="110" \
  ATS_CONCURRENCY="10" \
  MAX_JOBS_PER_COMPANY="50" \
  JOBBOARD_QUERIES_PER_RUN="6" \
  JOBSPY_SITES="indeed,linkedin" \
  JOBSPY_RESULTS_WANTED="20" \
  JOBSPY_HOURS_OLD="72"
```

`S3_ENDPOINT_URL` may be omitted only for AWS S3. For Tigris or Backblaze B2, use the endpoint and region shown by that provider. Do not commit these credentials to Git.

Optional JobSpy settings:

- `JOBSPY_PROXIES` — comma-separated proxy URLs.
- `JOBSPY_COUNTRY_INDEED` — set only when you intentionally want one Indeed country.
- `S3_ARCHIVE_DAILY=1` — also retain timestamped copies of every run. Leave this off for the lowest storage/request usage.

## 3. Smoke test

Run one ATS shard:

```bash
modal run modal_app.py --source ats
```

Then test the isolated job-board worker:

```bash
modal run modal_app.py --source jobboard
```

Both commands should return a JSON-like result containing a storage bucket/key and record count.

## 4. Deploy

```bash
modal deploy modal_app.py --strategy rolling
```

After deployment, Modal owns both schedules. Closing your computer does not stop them.

The CLI output also shows the public `health` Web Function URL. Open it and confirm that `ats_last_ok` and `jobboard_last_ok` begin updating.

## 5. Optional Better Stack monitoring

Create two heartbeat monitors:

- ATS heartbeat: expected every 15 minutes with a reasonable grace period.
- Job-board heartbeat: expected every 2 hours with a reasonable grace period.

Add their secret heartbeat URLs to `workorajobs-runtime` as:

```text
BETTERSTACK_ATS_HEARTBEAT_URL
BETTERSTACK_JOBBOARD_HEARTBEAT_URL
```

Then redeploy. The scraper deliberately ignores heartbeat-delivery failures so a monitoring outage cannot break collection.

## 6. Retire the Render scraper

Only after Modal has completed successful runs and objects are appearing in storage:

1. Disable the `workorajobs-scraper` worker/service on Render.
2. Keep the lightweight WorkoraJobs web/API service on Render only if it is still useful.
3. Do not run both scrapers indefinitely, or you will duplicate traffic and waste free compute.

## Storage layout

Current snapshots are overwritten by shard:

```text
workorajobs/
  current/
    ats/
      companies-00000.jsonl.gz
      companies-00110.jsonl.gz
      ...
    jobboards/
      queries-000000.jsonl.gz
      ...
```

Each object is newline-delimited JSON compressed with gzip. This is cheap to store, stream, import into DuckDB/Polars/Postgres, or feed into the later API-serving database.

## Reliability behavior

- ATS and JobSpy have separate memory budgets and schedules.
- Modal retries failed functions.
- `max_containers=1` on each scheduler prevents that source from multiplying containers unexpectedly.
- Cursor state lives in Modal Dict rather than a local JSON checkpoint.
- No SQLite database is kept inside the worker.
- HTTP connections are reused with an async client instead of opening one client per company.
- ATS concurrency is bounded.
- The process exits after every shard, so all RAM is reclaimed.
- Storage writes happen before the cursor advances, making retries safe to repeat because the same current-snapshot key is overwritten.

## Free-tier guardrails

The defaults are designed to be conservative, but "free forever" cannot be guaranteed because workload volume and provider pricing can change. Watch Modal usage during the first week. If usage approaches the monthly free-credit allowance, lower `JOBBOARD_QUERIES_PER_RUN`, reduce `JOBSPY_RESULTS_WANTED`, or increase the cron interval in `modal_app.py`.

Do not increase browser automation or JobSpy concurrency until the ATS path has been stable for at least a day. The structured ATS APIs are the cheapest and most memory-efficient collection path.
