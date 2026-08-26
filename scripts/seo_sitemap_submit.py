"""Submit sitemap to Google Search Console for fast indexing.

Requires:
    pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client

Usage:
    export GOOGLE_CREDENTIALS=path/to/service-account.json
    python -m scripts.seo_sitemap_submit
"""
from __future__ import annotations
import json
import os
import subprocess
import sys

SITEMAP_URL = "https://workorajobs.com/sitemap.xml"
SITE_URL = "https://workorajobs.com"


def ensure_deps():
    try:
        import google.auth
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install",
                       "google-auth", "google-auth-oauthlib",
                       "google-api-python-client"], check=True)


def submit_sitemap():
    """Submit sitemap to Google Search Console using service account."""
    ensure_deps()

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_path = os.environ.get("GOOGLE_CREDENTIALS", "google-service-account.json")

    if not os.path.exists(creds_path):
        print(f"Google credentials not found at: {creds_path}")
        print()
        print("Setup instructions:")
        print("1. Go to https://console.cloud.google.com/")
        print("2. Create project 'Workora Jobs'")
        print("3. Enable 'Search Console API'")
        print("4. Create Service Account -> Download JSON key")
        print(f"5. Save as {creds_path}")
        print("6. Add service account email as user in Search Console")
        print()
        print("Or submit manually at: https://search.google.com/search-console")
        return

    # Load credentials
    creds = service_account.Credentials.from_service_account_file(
        creds_path,
        scopes=["https://www.googleapis.com/auth/webmasters"]
    )

    # Build Search Console API client
    service = build('searchconsole', 'v1', credentials=creds)

    # Submit sitemap
    print(f"Submitting sitemap: {SITEMAP_URL}")
    try:
        result = service.sitemaps().submit(
            siteUrl=SITE_URL,
            feedpath=SITEMAP_URL
        ).execute()
        print(f"✅ Sitemap submitted successfully!")
    except Exception as e:
        if "already submitted" in str(e).lower():
            print(f"ℹ️ Sitemap already submitted: {SITEMAP_URL}")
        else:
            print(f"❌ Error: {e}")

    # List current sitemaps
    try:
        sitemaps = service.sitemaps().list(siteUrl=SITE_URL).execute()
        print("\nCurrent sitemaps:")
        for s in sitemaps.get('sitemapEntry', []):
            print(f"  - {s.get('path')} (last submitted: {s.get('lastSubmitted', 'N/A')})")
    except Exception as e:
        print(f"Could not list sitemaps: {e}")


def submit_url_indexing(url: str):
    """Request indexing for a specific URL (requires user credentials)."""
    print(f"Requesting indexing for: {url}")
    print("Note: Use Google Search Console UI for batch URL inspection")
    print(f"  https://search.google.com/search-console/inspect?resource_id={url}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        submit_url_indexing(sys.argv[1])
    else:
        submit_sitemap()
