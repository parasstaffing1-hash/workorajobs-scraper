# n8n → Google Sheets integration (daily 9 AM)

Pushes every collected job to a Google Sheet every morning at 9:00.

```
Schedule (cron 0 9 * * *)  →  [Collect (optional)]  →  Fetch /api/jobs  →  Prepare rows  →  Clear sheet  →  Append jobs
```

## What the workflow does

1. **Daily 9 AM** — n8n's Schedule Trigger fires (cron `0 9 * * *`).
2. **Collect (disabled by default)** — an `Execute Command` node that runs
   `jobcollect collect` right before the sync. Enable it **only if n8n runs on
   the same machine as the collector**. Otherwise the collector runs on its own
   schedule (cron/systemd) and you delete this node.
3. **Fetch jobs** — HTTP GET `http://127.0.0.1:8600/api/jobs?active=1&limit=5000`
   (the JSON API served by `jobcollect serve`). Change the host/port if the
   server runs elsewhere.
4. **Prepare rows** — a Code node flattens each job into one spreadsheet row
   (title, company, location, salary, source, posted_at, url, tags, is_active).
5. **Clear + Append** — the sheet is emptied and refilled, so the sheet is a
   fresh daily snapshot (no stale or duplicate rows).

## Setup

### 1. Start the collector's API server

The workflow reads jobs over HTTP, so the server must be running when the
workflow fires:

```bash
jobcollect serve --host 0.0.0.0 --port 8600
```

`--host 0.0.0.0` exposes it to other machines on your LAN (firewall
permitting); use `127.0.0.1` if n8n runs on this machine. You can also
register the server as a service/timer so it's always up (see
`systemd/jobcollect.*` or Task Scheduler).

### 2. Create the Google Sheet

- Create a spreadsheet in Google Sheets, e.g. **"Jobs"**.
- Rename the first tab to **`Jobs`** (or change `sheetName` in the workflow).
- Add a header row exactly matching the Code node's output:
  `title, company, location, salary, source, posted_at, url, tags, is_active`.
- Copy the spreadsheet ID from the URL:
  `https://docs.google.com/spreadsheets/d/<THIS_IS_THE_ID>/edit`

### 3. Set up n8n Google credentials

- In n8n: **Settings → Credentials → Add credential → Google Sheets OAuth2**.
- Follow the Google Cloud console steps n8n shows (enable the Sheets API,
  create OAuth client, add the redirect URI).
- After authorizing, note the credential's name.

### 4. Import the workflow

- n8n → **Workflows → Import from File** → `n8n/jobcollect-daily-sheets.json`.
- Open the workflow and fix the two Google Sheets nodes:
  - choose your **Google Sheets credential**,
  - set **Document ID** to the spreadsheet ID from step 2,
  - set **Sheet** to `Jobs`,
  - in the **Append** node, open **Columns** and click *Load columns* to map
    the auto-detected input fields.
- If your n8n version's Google Sheets node calls the clear operation
  something else, pick the "Clear" operation from its dropdown.
- If the collector runs elsewhere (or on its own schedule), delete the
  *"Collect jobs"* node and point the HTTP node at that machine:
  `http://<collector-ip>:8600/api/jobs?active=1&limit=5000`.

### 5. Test

- Click **Execute Workflow** once — the sheet should be cleared and refilled
  with today's jobs.
- Check **Executions** for the run log; the HTTP node shows how many jobs
  were fetched.

## The API

The workflow only needs two endpoints (both JSON, no auth — keep the server on
a trusted network):

| Endpoint | Purpose | Example |
|---|---|---|
| `GET /api/jobs` | All collected jobs | `/api/jobs?active=1&limit=5000&q=python` |
| `GET /api/items` | Engine items (RSS/scraped) | `/api/items?category=news&limit=500` |

Try it: `curl "http://127.0.0.1:8600/api/jobs?active=1&limit=5"`

## Variations

- **Only new jobs, appended** — delete the *Clear sheet* node; the Append
  node adds rows. You'll want a dedupe key: add a `dedupe_key` column in the
  Code node (`j.title + '|' + j.company` style) and use the Google Sheets
  *Upsert* operation.
- **Push engine items instead** — swap the HTTP node for `/api/items` and
  adjust the Code node's field names.
- **Different timezone** — cron `0 9 * * *` fires in n8n's timezone setting
  (Instance Settings → Timezone).
