#!/usr/bin/env python3
"""Discover company slugs from ATS customer directories using Playwright.
Outputs slug list to file for fast_probe.py to consume.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SLUG_FILE = ROOT / "scripts" / "discovered_slugs.json"


def discover_greenhouse(page) -> set[str]:
    """Scrape Greenhouse's website for company board slugs."""
    slugs = set()
    
    # Try multiple Greenhouse pages that list companies
    urls = [
        "https://www.greenhouse.com/company-directory",
        "https://www.greenhouse.com/partners",
        "https://www.greenhouse.com/customers",
        "https://www.greenhouse.com/use-cases",
    ]
    
    for url in urls:
        try:
            page.goto(url, timeout=20000, wait_until="networkidle")
            time.sleep(2)
            
            # Scroll to load lazy content
            for _ in range(5):
                page.evaluate("window.scrollBy(0, 1000)")
                time.sleep(0.5)
            
            html = page.content()
            
            # Extract greenhouse slugs from all links
            patterns = [
                r'boards\.greenhouse\.io/([a-zA-Z0-9_-]+)',
                r'boards-api\.greenhouse\.io/v1/boards/([a-zA-Z0-9_-]+)',
                r'greenhouse\.com/([a-zA-Z0-9_-]+)',
            ]
            for pat in patterns:
                matches = re.findall(pat, html)
                for m in matches:
                    if m not in ("company-directory", "partners", "customers", "pricing",
                                 "solutions", "about", "blog", "resources", "careers",
                                 "trust", "security", "compliance", "integrations",
                                 "api", "developers", "support", "login", "signup"):
                        slugs.add(m.lower())
            
            # Also get all href links and extract slugs
            links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            for link in links:
                m = re.search(r'boards\.greenhouse\.io/([a-zA-Z0-9_-]+)', link)
                if m:
                    slugs.add(m.group(1).lower())
        except Exception as e:
            print(f"  Failed {url}: {e}")
    
    print(f"  Greenhouse: {len(slugs)} slugs")
    return slugs


def discover_lever(page) -> set[str]:
    """Scrape Lever's website for company slugs."""
    slugs = set()
    
    urls = [
        "https://www.lever.co/customers",
        "https://www.lever.co/customer-stories",
        "https://jobs.lever.co/",
        "https://www.lever.co/",
    ]
    
    for url in urls:
        try:
            page.goto(url, timeout=20000, wait_until="networkidle")
            time.sleep(2)
            
            for _ in range(5):
                page.evaluate("window.scrollBy(0, 1000)")
                time.sleep(0.5)
            
            html = page.content()
            
            patterns = [
                r'jobs\.lever\.co/([a-zA-Z0-9_-]+)',
                r'lever\.co/([a-zA-Z0-9_-]+)',
            ]
            for pat in patterns:
                matches = re.findall(pat, html)
                for m in matches:
                    if m not in ("customers", "lever", "about", "blog", "pricing",
                                 "solutions", "careers", "resources", "support"):
                        slugs.add(m.lower())
            
            links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            for link in links:
                m = re.search(r'lever\.co/([a-zA-Z0-9_-]+)', link)
                if m:
                    slugs.add(m.group(1).lower())
        except Exception as e:
            print(f"  Failed {url}: {e}")
    
    print(f"  Lever: {len(slugs)} slugs")
    return slugs


def discover_ashby(page) -> set[str]:
    """Scrape Ashby's website for company slugs."""
    slugs = set()
    
    urls = [
        "https://www.ashbyhq.com/customers",
        "https://www.ashbyhq.com/",
        "https://jobs.ashbyhq.com/",
    ]
    
    for url in urls:
        try:
            page.goto(url, timeout=20000, wait_until="networkidle")
            time.sleep(2)
            
            for _ in range(5):
                page.evaluate("window.scrollBy(0, 1000)")
                time.sleep(0.5)
            
            html = page.content()
            
            patterns = [
                r'jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)',
                r'ashbyhq\.com/([a-zA-Z0-9_-]+)',
            ]
            for pat in patterns:
                matches = re.findall(pat, html)
                for m in matches:
                    if m not in ("customers", "ashby", "about", "blog", "pricing",
                                 "solutions", "careers", "resources", "support"):
                        slugs.add(m.lower())
            
            links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            for link in links:
                m = re.search(r'ashbyhq\.com/([a-zA-Z0-9_-]+)', link)
                if m:
                    slugs.add(m.group(1).lower())
        except Exception as e:
            print(f"  Failed {url}: {e}")
    
    print(f"  Ashby: {len(slugs)} slugs")
    return slugs


def discover_workable(page) -> set[str]:
    """Scrape Workable's website for company slugs."""
    slugs = set()
    
    urls = [
        "https://www.workable.com/customers",
        "https://www.workable.com/",
        "https://apply.workable.com/",
    ]
    
    for url in urls:
        try:
            page.goto(url, timeout=20000, wait_until="networkidle")
            time.sleep(2)
            
            for _ in range(5):
                page.evaluate("window.scrollBy(0, 1000)")
                time.sleep(0.5)
            
            html = page.content()
            
            patterns = [
                r'apply\.workable\.com/([a-zA-Z0-9_-]+)',
                r'workable\.com/([a-zA-Z0-9_-]+)',
            ]
            for pat in patterns:
                matches = re.findall(pat, html)
                for m in matches:
                    if m not in ("customers", "workable", "about", "blog", "pricing",
                                 "solutions", "careers", "resources", "support", "demo"):
                        slugs.add(m.lower())
        except Exception as e:
            print(f"  Failed {url}: {e}")
    
    print(f"  Workable: {len(slugs)} slugs")
    return slugs


def discover_from_job_boards(page) -> set[str]:
    """Scrape job board aggregator sites that list companies."""
    slugs = set()
    
    # BuiltIn - lists companies
    builtin_urls = [
        "https://builtin.com/companies/tech/companies",
        "https://builtin.com/companies/companies",
    ]
    
    for url in builtin_urls:
        try:
            page.goto(url, timeout=20000, wait_until="networkidle")
            time.sleep(3)
            
            for _ in range(10):
                page.evaluate("window.scrollBy(0, 2000)")
                time.sleep(0.5)
            
            # Extract company links
            links = page.eval_on_selector_all("a[href*='/companies/']", "els => els.map(e => e.href)")
            for link in links:
                m = re.search(r'/companies/([a-zA-Z0-9_-]+)', link)
                if m and len(m.group(1)) > 2:
                    slugs.add(m.group(1).lower())
        except Exception as e:
            print(f"  BuiltIn failed: {e}")
    
    # Wellfound (AngelList) - company directory
    try:
        page.goto("https://wellfound.com/hiring", timeout=20000, wait_until="networkidle")
        time.sleep(3)
        
        for _ in range(10):
            page.evaluate("window.scrollBy(0, 2000)")
            time.sleep(0.5)
        
        links = page.eval_on_selector_all("a[href*='/company/']", "els => els.map(e => e.href)")
        for link in links:
            m = re.search(r'/company/([a-zA-Z0-9_-]+)', link)
            if m and len(m.group(1)) > 2:
                slugs.add(m.group(1).lower())
    except Exception as e:
        print(f"  Wellfound failed: {e}")
    
    print(f"  Job boards: {len(slugs)} slugs")
    return slugs


def main():
    print("Discovering company slugs via Playwright...")
    
    all_slugs = set()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
        ])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        
        print("\n--- Greenhouse ---")
        all_slugs |= discover_greenhouse(page)
        
        print("\n--- Lever ---")
        all_slugs |= discover_lever(page)
        
        print("\n--- Ashby ---")
        all_slugs |= discover_ashby(page)
        
        print("\n--- Workable ---")
        all_slugs |= discover_workable(page)
        
        print("\n--- Job Board Aggregators ---")
        all_slugs |= discover_from_job_boards(page)
        
        browser.close()
    
    # Filter out navigation slugs
    exclude = {
        "company-directory", "partners", "customers", "pricing", "solutions",
        "about", "blog", "resources", "careers", "trust", "security",
        "compliance", "integrations", "api", "developers", "support",
        "login", "signup", "demo", "contact", "terms", "privacy",
        "press", "events", "webinars", "case-studies", "roi",
        "products", "platform", "enterprise", "startups", "hire",
        "recruiting", "ats", "hris", "onboarding", "performance",
        "engagement", "analytics", "reporting", "compliance",
        "greenhouse", "lever", "ashby", "workable",
        "teamtailor", "breezy", "smartrecruiters",
    }
    
    all_slugs = {s for s in all_slugs if len(s) >= 3 and s not in exclude}
    
    # Save
    SLUG_FILE.write_text(json.dumps(sorted(all_slugs), indent=2), encoding="utf-8")
    print(f"\nTotal discovered: {len(all_slugs)} unique slugs")
    print(f"Saved to: {SLUG_FILE}")
    
    # Also append to the mega_probe COMPANIES list
    probe_file = ROOT / "scripts" / "discovered_slugs.txt"
    probe_file.write_text("\n".join(sorted(all_slugs)), encoding="utf-8")
    print(f"Saved text list to: {probe_file}")


if __name__ == "__main__":
    main()
