#!/usr/bin/env python3
"""CRM Integration — Push leads to HubSpot, Pipedrive, Airtable, Google Sheets.

Setup:
    export HUBSPOT_API_KEY=your_key
    export PIPEDRIVE_API_TOKEN=your_token
    export AIRTABLE_API_KEY=your_key
    export AIRTABLE_BASE_ID=your_base
    export AIRTABLE_TABLE_ID=your_table

Usage:
    python -m scripts.crm_integration --hubspot --limit 50
    python -m scripts.crm_integration --pipedrive --keyword "python"
    python -m scripts.crm_integration --airtable --limit 100
    python -m scripts.crm_integration --sheets --sheet-id your_sheet_id
"""
from __future__ import annotations
import json, os, sqlite3, time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "crm_integration.log"

# CRM Config
HUBSPOT_API_KEY = os.environ.get("HUBSPOT_API_KEY", "")
PIPEDRIVE_TOKEN = os.environ.get("PIPEDRIVE_API_TOKEN", "")
AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY", "")
AIRTABLE_BASE = os.environ.get("AIRTABLE_BASE_ID", "")
AIRTABLE_TABLE = os.environ.get("AIRTABLE_TABLE_ID", "")


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_leads(keyword="", limit=100):
    """Get leads from database."""
    conn = get_db()
    conn.row_factory = sqlite3.Row

    if keyword:
        leads = conn.execute(
            "SELECT * FROM jobs WHERE is_active = 1 AND "
            "(title LIKE ? OR company LIKE ? OR description LIKE ?) "
            "ORDER BY first_seen_at DESC LIMIT ?",
            (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit)
        ).fetchall()
    else:
        leads = conn.execute(
            "SELECT * FROM jobs WHERE is_active = 1 "
            "ORDER BY first_seen_at DESC LIMIT ?",
            (limit,)
        ).fetchall()

    conn.close()
    return [dict(l) for l in leads]


# ── HubSpot ──────────────────────────────────────────────────
def push_to_hubspot(leads):
    """Push leads to HubSpot as contacts."""
    if not HUBSPOT_API_KEY:
        log("HUBSPOT_API_KEY not set")
        return False

    try:
        import httpx
        url = "https://api.hubapi.com/crm/v3/objects/contacts"
        headers = {
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json"
        }

        created = 0
        for lead in leads:
            payload = {
                "properties": {
                    "email": f"{lead.get('company', 'unknown').lower().replace(' ', '')}@jobs.leadflow",
                    "firstname": lead.get("company", "Unknown"),
                    "lastname": lead.get("title", "Job Lead"),
                    "jobtitle": lead.get("title", ""),
                    "company": lead.get("company", ""),
                    "location": lead.get("location", ""),
                    "website": lead.get("url", ""),
                    "description": f"Source: {lead.get('source', '')} | Posted: {lead.get('posted_at', '')}",
                }
            }
            r = httpx.post(url, json=payload, headers=headers, timeout=10)
            if r.status_code in [200, 201]:
                created += 1
            time.sleep(0.1)  # Rate limit

        log(f"HubSpot: Created {created}/{len(leads)} contacts")
        return True
    except Exception as e:
        log(f"HubSpot error: {e}")
        return False


# ── Pipedrive ────────────────────────────────────────────────
def push_to_pipedrive(leads):
    """Push leads to Pipedrive as deals."""
    if not PIPEDRIVE_TOKEN:
        log("PIPEDRIVE_API_TOKEN not set")
        return False

    try:
        import httpx
        created = 0

        for lead in leads:
            # Create person
            person_url = f"https://api.pipedrive.com/v1/persons?api_token={PIPEDRIVE_TOKEN}"
            person_payload = {
                "name": lead.get("company", "Unknown"),
                "title": lead.get("title", ""),
            }
            r = httpx.post(person_url, json=person_payload, timeout=10)
            person_id = r.json().get("data", {}).get("id")

            if person_id:
                # Create deal
                deal_url = f"https://api.pipedrive.com/v1/deals?api_token={PIPEDRIVE_TOKEN}"
                deal_payload = {
                    "title": f"{lead.get('title', 'Job')} at {lead.get('company', 'Unknown')}",
                    "person_id": person_id,
                    "value": float(lead.get("salary", 0) or 0),
                    "status": "open",
                }
                r = httpx.post(deal_url, json=deal_payload, timeout=10)
                if r.status_code == 200:
                    created += 1

            time.sleep(0.1)

        log(f"Pipedrive: Created {created}/{len(leads)} deals")
        return True
    except Exception as e:
        log(f"Pipedrive error: {e}")
        return False


# ── Airtable ─────────────────────────────────────────────────
def push_to_airtable(leads):
    """Push leads to Airtable."""
    if not AIRTABLE_API_KEY or not AIRTABLE_BASE:
        log("AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set")
        return False

    try:
        import httpx
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}"
        headers = {
            "Authorization": f"Bearer {AIRTABLE_API_KEY}",
            "Content-Type": "application/json"
        }

        # Airtable batch insert (max 10 records per request)
        records = []
        for lead in leads:
            records.append({
                "fields": {
                    "Title": lead.get("title", ""),
                    "Company": lead.get("company", ""),
                    "Location": lead.get("location", ""),
                    "URL": lead.get("url", ""),
                    "Source": lead.get("source", ""),
                    "Posted": lead.get("posted_at", ""),
                    "Salary": lead.get("salary", ""),
                }
            })

        created = 0
        for i in range(0, len(records), 10):
            batch = records[i:i+10]
            payload = {"records": batch}
            r = httpx.post(url, json=payload, headers=headers, timeout=10)
            if r.status_code == 200:
                created += len(batch)
            time.sleep(0.2)

        log(f"Airtable: Created {created}/{len(leads)} records")
        return True
    except Exception as e:
        log(f"Airtable error: {e}")
        return False


# ── Google Sheets ────────────────────────────────────────────
def push_to_sheets(leads, sheet_id):
    """Push leads to Google Sheets (requires service account)."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
        creds = service_account.Credentials.from_service_account_file(
            os.environ.get('GOOGLE_SERVICE_ACCOUNT', 'service-account.json'),
            scopes=SCOPES
        )
        service = build('sheets', 'v4', credentials=creds)

        # Prepare data
        rows = [["Title", "Company", "Location", "URL", "Source", "Posted", "Salary"]]
        for lead in leads:
            rows.append([
                lead.get("title", ""),
                lead.get("company", ""),
                lead.get("location", ""),
                lead.get("url", ""),
                lead.get("source", ""),
                lead.get("posted_at", ""),
                lead.get("salary", ""),
            ])

        # Append to sheet
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range="Sheet1!A1",
            valueInputOption="USER_ENTERED",
            body={"values": rows}
        ).execute()

        log(f"Google Sheets: Pushed {len(leads)} rows")
        return True
    except Exception as e:
        log(f"Google Sheets error: {e}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--hubspot", action="store_true")
    parser.add_argument("--pipedrive", action="store_true")
    parser.add_argument("--airtable", action="store_true")
    parser.add_argument("--sheets", action="store_true")
    parser.add_argument("--sheet-id", default="")
    parser.add_argument("--keyword", default="")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    leads = get_leads(args.keyword, args.limit)
    log(f"Loaded {len(leads)} leads")

    if args.hubspot:
        push_to_hubspot(leads)
    if args.pipedrive:
        push_to_pipedrive(leads)
    if args.airtable:
        push_to_airtable(leads)
    if args.sheets:
        push_to_sheets(leads, args.sheet_id)


if __name__ == "__main__":
    main()
