#!/usr/bin/env python3
"""Auto-Apply Bot — Playwright auto-fills job applications on company sites.

Automates the application process:
1. Navigates to job application page
2. Fills in common form fields (name, email, phone, resume)
3. Submits the application

⚠️  DISCLAIMER: Use responsibly. Some sites prohibit automated applications.
Always review applications before final submission.

Usage:
    python -m scripts.auto_apply_bot --resume path/to/resume.pdf --email you@email.com
    python -m scripts.auto_apply_bot --url "https://company.com/apply/123"
"""
from __future__ import annotations
import asyncio, json, os, re, sqlite3, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "jobs.db"
LOG = ROOT / "auto_apply.log"
CONFIG = ROOT / "apply_config.json"


def get_db():
    return sqlite3.connect(str(DB), timeout=10)


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config():
    try:
        with open(CONFIG, "r") as f:
            return json.load(f)
    except:
        return {}


def save_config(config):
    with open(CONFIG, "w") as f:
        json.dump(config, f, indent=2)


# Common form field selectors and patterns
FIELD_SELECTORS = {
    "email": [
        'input[name*="email"]', 'input[type="email"]',
        'input[placeholder*="email" i]', 'input[id*="email"]',
    ],
    "phone": [
        'input[name*="phone"]', 'input[type="tel"]',
        'input[placeholder*="phone" i]', 'input[name*="mobile"]',
    ],
    "first_name": [
        'input[name*="first" i]', 'input[name*="fname"]',
        'input[placeholder*="first" i]', 'input[id*="first"]',
    ],
    "last_name": [
        'input[name*="last" i]', 'input[name*="lname"]',
        'input[placeholder*="last" i]', 'input[id*="last"]',
    ],
    "name": [
        'input[name="name"]', 'input[name="fullName"]',
        'input[name="full_name"]', 'input[placeholder*="full name" i]',
    ],
    "resume": [
        'input[type="file"][name*="resume" i]',
        'input[type="file"][name*="cv"]',
        'input[type="file"]',
    ],
    "cover_letter": [
        'textarea[name*="cover" i]', 'textarea[name*="message"]',
        'textarea[name*="note"]', 'textarea[placeholder*="cover" i]',
    ],
    "linkedin": [
        'input[name*="linkedin"]', 'input[placeholder*="linkedin" i]',
    ],
    "website": [
        'input[name*="website"]', 'input[name*="portfolio"]',
        'input[placeholder*="website" i]',
    ],
}

SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button[data-testid*="submit"]',
    'button[data-testid*="apply"]',
    'button:has-text("Submit")',
    'button:has-text("Apply")',
    'button:has-text("Send")',
    'a:has-text("Apply")',
]


async def find_field(page, field_type):
    """Find a form field by type."""
    selectors = FIELD_SELECTORS.get(field_type, [])
    for selector in selectors:
        try:
            element = await page.query_selector(selector)
            if element:
                return element
        except:
            continue
    return None


async def fill_field(page, field_type, value):
    """Fill a form field with a value."""
    if not value:
        return False
    element = await find_field(page, field_type)
    if element:
        try:
            await element.click()
            await element.fill(value)
            return True
        except:
            pass
    return False


async def handle_file_upload(page, file_path):
    """Handle file upload for resume."""
    if not file_path or not os.path.exists(file_path):
        return False
    element = await find_field(page, "resume")
    if element:
        try:
            await element.set_input_files(file_path)
            return True
        except:
            pass
    return False


async def handle_captcha(page):
    """Detect and handle CAPTCHA (basic detection)."""
    captcha_indicators = [
        'iframe[src*="recaptcha"]',
        '.g-recaptcha',
        '#captcha',
        '[data-sitekey]',
    ]
    for selector in captcha_indicators:
        try:
            element = await page.query_selector(selector)
            if element:
                log("⚠️ CAPTCHA detected! Manual intervention required.")
                return True
        except:
            continue
    return False


async def submit_application(page):
    """Click the submit button."""
    for selector in SUBMIT_SELECTORS:
        try:
            button = await page.query_selector(selector)
            if button:
                await button.click()
                await page.wait_for_timeout(2000)
                return True
        except:
            continue
    return False


async def apply_to_job(page, url, user_info):
    """Apply to a single job posting."""
    log(f"Navigating to: {url}")

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
    except Exception as e:
        log(f"Failed to load page: {e}")
        return False

    # Check for CAPTCHA
    if await handle_captcha(page):
        log("CAPTCHA detected - skipping this application")
        return False

    # Fill in form fields
    filled = 0
    await fill_field(page, "first_name", user_info.get("first_name", ""))
    await fill_field(page, "last_name", user_info.get("last_name", ""))
    await fill_field(page, "email", user_info.get("email", ""))
    await fill_field(page, "phone", user_info.get("phone", ""))
    await fill_field(page, "linkedin", user_info.get("linkedin", ""))
    await fill_field(page, "website", user_info.get("website", ""))

    # Upload resume
    if user_info.get("resume_path"):
        await handle_file_upload(page, user_info["resume_path"])

    # Fill cover letter if there's a text area
    if user_info.get("cover_letter"):
        await fill_field(page, "cover_letter", user_info["cover_letter"])

    # Try to submit
    submitted = await submit_application(page)

    if submitted:
        log(f"✅ Application submitted: {url}")
        return True
    else:
        log(f"⚠️ Could not find submit button: {url}")
        return False


async def auto_apply_batch(jobs, user_info, dry_run=True):
    """Apply to multiple jobs."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("Playwright not installed")
        return 0, 0

    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
        use_stealth = True
    except:
        use_stealth = False

    success = 0
    failed = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for job in jobs:
            url = job.get("url", "")
            if not url:
                continue

            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
            )

            if use_stealth:
                try:
                    await stealth.apply_stealth_async(context)
                except:
                    pass

            page = await context.new_page()

            try:
                if dry_run:
                    log(f"[DRY RUN] Would apply to: {job.get('title', '')} at {job.get('company', '')}")
                    success += 1
                else:
                    result = await apply_to_job(page, url, user_info)
                    if result:
                        success += 1
                        # Mark as applied in database
                        conn = get_db()
                        try:
                            conn.execute(
                                "UPDATE jobs SET tags = json_insert(COALESCE(tags, '[]'), '$[#]', 'applied') WHERE url = ?",
                                (url,)
                            )
                            conn.commit()
                        except:
                            pass
                        conn.close()
                    else:
                        failed += 1
            except Exception as e:
                log(f"Error: {e}")
                failed += 1
            finally:
                await page.close()
                await context.close()

            await asyncio.sleep(2)  # Delay between applications

        await browser.close()

    return success, failed


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="URL to apply to")
    parser.add_argument("--resume", help="Path to resume PDF")
    parser.add_argument("--email", help="Email address")
    parser.add_argument("--name", help="Full name (First Last)")
    parser.add_argument("--phone", help="Phone number")
    parser.add_argument("--linkedin", help="LinkedIn URL")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--keyword", default="")
    args = parser.parse_args()

    # Build user info
    name_parts = (args.name or "").split()
    user_info = {
        "first_name": name_parts[0] if name_parts else "",
        "last_name": " ".join(name_parts[1:]) if len(name_parts) > 1 else "",
        "email": args.email or "",
        "phone": args.phone or "",
        "linkedin": args.linkedin or "",
        "resume_path": args.resume or "",
    }

    # Save config for future use
    if args.email:
        save_config(user_info)

    # Load saved config if not provided
    config = load_config()
    for k, v in config.items():
        if not user_info.get(k):
            user_info[k] = v

    if args.url:
        # Apply to single URL
        asyncio.run(apply_to_job(None, args.url, user_info))
    else:
        # Apply to batch
        conn = get_db()
        jobs = conn.execute(
            "SELECT title, company, url FROM jobs WHERE is_active = 1 "
            "AND url != '' AND tags NOT LIKE '%applied%' "
            "ORDER BY first_seen_at DESC LIMIT ?",
            (args.limit,)
        ).fetchall()
        conn.close()

        job_dicts = [{"title": j[0], "company": j[1], "url": j[2]} for j in jobs]
        log(f"Found {len(job_dicts)} jobs to apply to")

        success, failed = asyncio.run(auto_apply_batch(job_dicts, user_info, not args.execute))
        log(f"Results: {success} applied, {failed} failed")


if __name__ == "__main__":
    main()
