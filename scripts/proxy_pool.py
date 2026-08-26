#!/usr/bin/env python3
"""Proxy Pool — Free proxy rotation for scraping.

Fetches and validates free proxies from multiple sources,
rotates them to avoid IP bans.

Usage:
    python -m scripts.proxy_pool --refresh
    python -m scripts.proxy_pool --test
"""
from __future__ import annotations
import random, time, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent
PROXY_FILE = ROOT / "proxy_pool.json"

PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt",
    "https://raw.githubusercontent.com/MuRongIPK/IPangel/master/pool.txt",
    "https://raw.githubusercontent.com/Roptus/CronTask每日更新/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
]

TEST_URL = "http://httpbin.org/ip"
TEST_TIMEOUT = 5


def load_proxies():
    """Load proxies from file."""
    try:
        with open(PROXY_FILE, "r") as f:
            data = json.load(f)
            return data.get("proxies", [])
    except:
        return []


def save_proxies(proxies):
    """Save proxies to file."""
    with open(PROXY_FILE, "w") as f:
        json.dump({
            "updated": time.time(),
            "count": len(proxies),
            "proxies": proxies
        }, f)


def fetch_proxies_from_url(url):
    """Fetch proxy list from a URL."""
    try:
        import httpx
        r = httpx.get(url, timeout=10)
        if r.status_code == 200:
            lines = r.text.strip().split('\n')
            proxies = []
            for line in lines:
                line = line.strip()
                if line and ':' in line and not line.startswith('#'):
                    # Ensure it has protocol
                    if not line.startswith(('http://', 'https://', 'socks4://', 'socks5://')):
                        line = 'http://' + line
                    proxies.append(line)
            return proxies
    except:
        pass
    return []


def fetch_all_proxies():
    """Fetch proxies from all sources."""
    all_proxies = set()
    for url in PROXY_SOURCES:
        try:
            proxies = fetch_proxies_from_url(url)
            all_proxies.update(proxies)
        except:
            pass
    return list(all_proxies)


def test_proxy(proxy):
    """Test if a proxy works."""
    try:
        import httpx
        proxies = {"http://": proxy, "https://": proxy}
        r = httpx.get(TEST_URL, proxies=proxies, timeout=TEST_TIMEOUT)
        if r.status_code == 200:
            return proxy
    except:
        pass
    return None


def refresh_proxies(max_test=100):
    """Fetch and validate proxies."""
    print("Fetching proxies from sources...")
    all_proxies = fetch_all_proxies()
    print(f"Found {len(all_proxies)} raw proxies")

    # Test proxies in parallel
    valid = []
    to_test = all_proxies[:max_test]
    print(f"Testing {len(to_test)} proxies...")

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(test_proxy, p): p for p in to_test}
        for future in as_completed(futures):
            result = future.result()
            if result:
                valid.append(result)

    print(f"Valid proxies: {len(valid)}")
    save_proxies(valid)
    return valid


def get_proxy():
    """Get a random proxy from the pool."""
    proxies = load_proxies()
    if not proxies:
        # Try to fetch new ones
        proxies = refresh_proxies(max_test=50)
    if proxies:
        return random.choice(proxies)
    return None


def get_proxies(count=5):
    """Get multiple random proxies."""
    proxies = load_proxies()
    if not proxies:
        proxies = refresh_proxies(max_test=50)
    if proxies:
        return random.sample(proxies, min(count, len(proxies)))
    return []


def test_all_proxies():
    """Test all saved proxies and remove dead ones."""
    proxies = load_proxies()
    if not proxies:
        print("No proxies to test. Run --refresh first.")
        return

    print(f"Testing {len(proxies)} proxies...")
    valid = []

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(test_proxy, p): p for p in proxies}
        for future in as_completed(futures):
            result = future.result()
            if result:
                valid.append(result)

    print(f"Valid: {len(valid)}/{len(proxies)}")
    save_proxies(valid)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Fetch and validate new proxies")
    parser.add_argument("--test", action="store_true", help="Test saved proxies")
    parser.add_argument("--count", type=int, default=50, help="Max proxies to test")
    args = parser.parse_args()

    if args.refresh:
        refresh_proxies(args.count)
    elif args.test:
        test_all_proxies()
    else:
        proxies = load_proxies()
        print(f"Proxy pool: {len(proxies)} proxies")
        if proxies:
            print(f"Sample: {random.choice(proxies)}")


if __name__ == "__main__":
    main()
