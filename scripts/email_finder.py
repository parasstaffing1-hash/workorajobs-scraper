#!/usr/bin/env python3
"""Email Finder — Extract contact emails from job postings and company pages.

Scrapes emails from:
1. Job posting descriptions (recruiter emails, apply emails)
2. Company career pages (hiring team contacts)
3. Company about/contact pages (general contacts)
4. LinkedIn profile patterns (first.last@company.com)

Usage:
    python -m scripts.email_finder --limit 100
    python -m scripts.email_finder --company stripe
"""
from __future__ import annotations
import hashlib, json, os, re, sqlite3, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "email_finder.log"

# Email patterns to look for
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
IGNORE_DOMAINS = {
    'example.com', 'test.com', 'sentry.io', 'github.com', 'wixpress.com',
    'googleapis.com', 'cloudflare.com', 'w3.org', 'schema.org',
    'localhost', '127.0.0.1', 'sentry-next.wixpress.com',
    'wix.com', 'sentry.io', 'github.com', 'npmjs.com',
    'example.org', 'test.org', 'placeholder.com',
}

# Common name patterns for email guessing
NAME_PATTERNS = [
    "{first}.{last}@{domain}",
    "{first}{last}@{domain}",
    "{f}{last}@{domain}",
    "{first}_{last}@{domain}",
    "{last}.{first}@{domain}",
    "{last}{first}@{domain}",
]

# Company email domain patterns
DOMAIN_PATTERNS = [
    "{company}.com",
    "{company}.io",
    "{company}.co",
    "{company}.org",
    "{company}.ai",
]


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def extract_emails_from_text(text):
    """Extract all valid email addresses from text."""
    if not text:
        return set()
    emails = set(EMAIL_REGEX.findall(text.lower()))
    # Filter out junk emails
    valid = set()
    for email in emails:
        domain = email.split('@')[1] if '@' in email else ''
        if domain in IGNORE_DOMAINS:
            continue
        if len(email) < 5 or len(email) > 254:
            continue
        if '..' in email:
            continue
        valid.add(email)
    return valid


def extract_emails_from_html(html):
    """Extract emails from HTML content, including mailto: links."""
    if not html:
        return set()
    emails = extract_emails_from_text(html)
    # Also check mailto links
    mailto_pattern = re.compile(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})')
    for match in mailto_pattern.findall(html):
        email = match.lower()
        domain = email.split('@')[1] if '@' in email else ''
        if domain not in IGNORE_DOMAINS and len(email) >= 5:
            emails.add(email)
    return emails


def fetch_url(url):
    """Fetch a URL and return HTML content."""
    try:
        import httpx
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        }
        r = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
        if r.status_code == 200:
            return r.text
    except:
        pass
    return ""


def guess_company_email_domain(company_name):
    """Guess the email domain for a company."""
    name = company_name.lower().strip()
    name = re.sub(r'[^a-z0-9]', '', name)
    domains = []
    for pattern in DOMAIN_PATTERNS:
        domains.append(pattern.format(company=name))
    # Also try common variations
    domains.extend([
        f"{name}.com",
        f"{name}.io",
        f"{name}.co",
        f"contact@{name}.com",
        f"careers@{name}.com",
        f"hiring@{name}.com",
    ])
    return list(set(domains))


def guess_emails_from_names(names, domain):
    """Generate likely email addresses from names."""
    emails = set()
    for name in names:
        parts = name.lower().split()
        if len(parts) >= 2:
            first = parts[0]
            last = parts[-1]
            f = first[0] if first else ""
            for pattern in NAME_PATTERNS:
                email = pattern.format(first=first, last=last, f=f, domain=domain)
                emails.add(email)
    return emails


def find_emails_for_job(job):
    """Find emails for a specific job posting."""
    urls_to_check = []
    job_url = job.get("url", "")
    company = job.get("company", "")
    description = job.get("description", "")

    # Check job URL
    if job_url:
        urls_to_check.append(job_url)

    # Try common career page URLs
    if company:
        slug = company.lower().replace(" ", "").replace(".", "").replace(",", "")
        urls_to_check.extend([
            f"https://{slug}.com/careers",
            f"https://{slug}.com/about",
            f"https://{slug}.com/contact",
            f"https://{slug}.io/careers",
            f"https://{slug}.io/about",
            f"https://www.{slug}.com/careers",
            f"https://www.{slug}.com/about",
        ])

    all_emails = set()

    # Extract from job description
    desc_emails = extract_emails_from_text(description)
    all_emails.update(desc_emails)

    # Fetch and extract from career pages
    for url in urls_to_check[:5]:  # Limit to 5 URLs per job
        try:
            html = fetch_url(url)
            if html:
                page_emails = extract_emails_from_html(html)
                all_emails.update(page_emails)
        except:
            pass

    # Filter to likely company emails
    company_emails = set()
    if company:
        slug = company.lower().replace(" ", "").replace(".", "").replace(",", "")
        for email in all_emails:
            domain = email.split("@")[1] if "@" in email else ""
            # Keep if it's from the company's domain
            if slug in domain:
                company_emails.add(email)
            # Keep generic hiring emails
            if any(x in email for x in ["hire", "recruit", "apply", "hr@", "talent"]):
                company_emails.add(email)

    # If no company emails found, return all extracted
    if not company_emails:
        company_emails = all_emails

    return list(company_emails)[:10]  # Limit to 10 emails per job


def find_emails_batch(jobs, workers=10):
    """Find emails for multiple jobs in parallel."""
    total_found = 0
    jobs_updated = 0

    def process_job(job):
        emails = find_emails_for_job(job)
        if emails:
            return (job["url"], emails)
        return None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_job, job): job for job in jobs}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    url, emails = result
                    # Update job in database
                    conn = get_db()
                    try:
                        existing = conn.execute(
                            "SELECT tags FROM jobs WHERE url = ?", (url,)
                        ).fetchone()
                        if existing:
                            tags = json.loads(existing[0]) if existing[0] else []
                            email_tags = [f"email:{e}" for e in emails]
                            new_tags = list(set(tags + email_tags))
                            conn.execute(
                                "UPDATE jobs SET tags = ? WHERE url = ?",
                                (json.dumps(new_tags), url)
                            )
                            jobs_updated += 1
                            total_found += len(emails)
                    except:
                        pass
                    finally:
                        conn.close()
            except:
                pass

    return total_found, jobs_updated


def run_email_finder(keyword="", limit=100):
    """Main email finder entry point."""
    log(f"Email finder starting (keyword={keyword}, limit={limit})")

    conn = get_db()
    if keyword:
        jobs = conn.execute(
            "SELECT url, company, description FROM jobs WHERE "
            "(title LIKE ? OR company LIKE ? OR description LIKE ?) "
            "AND is_active = 1 ORDER BY first_seen_at DESC LIMIT ?",
            (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit)
        ).fetchall()
    else:
        jobs = conn.execute(
            "SELECT url, company, description FROM jobs "
            "WHERE is_active = 1 ORDER BY first_seen_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()

    job_dicts = [{"url": j[0], "company": j[1] or "", "description": j[2] or ""} for j in jobs]
    log(f"Processing {len(job_dicts)} jobs for email discovery")

    total_found, updated = find_emails_batch(job_dicts, workers=10)
    log(f"Email finder complete: {total_found} emails found, {updated} jobs updated")
    return total_found, updated


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", default="")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    run_email_finder(args.keyword, args.limit)


if __name__ == "__main__":
    main()
