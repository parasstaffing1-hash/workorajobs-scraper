"""Proxy rotation pool for scraper workers.

Supports:
- Free proxy lists (scraped from public sources)
- Paid proxy services (BrightData, SmartProxy, etc.)
- Built-in health checking
- Automatic failover

Usage:
    pool = ProxyPool()
    proxy = pool.get_proxy()  # Returns {"http": "http://...", "https": "http://..."}
    pool.report_success(proxy)  # Good proxy
    pool.report_failure(proxy)  # Bad proxy, will be deprioritized
"""
from __future__ import annotations

import os
import random
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── Configuration ──────────────────────────────────────────────
USE_PROXIES = os.environ.get("USE_PROXIES", "false").lower() == "true"
PROXY_SOURCE = os.environ.get("PROXY_SOURCE", "free")  # free | env | brightdata | smartproxy
BRIGHTDATA_USERNAME = os.environ.get("BRIGHTDATA_USERNAME", "")
BRIGHTDATA_PASSWORD = os.environ.get("BRIGHTDATA_PASSWORD", "")
SMARTPROXY_API_KEY = os.environ.get("SMARTPROXY_API_KEY", "")
MAX_PROXY_FAILURES = 5
PROXY_COOLDOWN_SECONDS = 300  # 5 min cooldown after failure


class ProxyPool:
    """Thread-safe proxy pool with health checking."""

    def __init__(self):
        self._proxies: list[dict] = []
        self._scores: dict[str, float] = defaultdict(lambda: 1.0)  # 0.0 = bad, 1.0 = good
        self._failures: dict[str, int] = defaultdict(int)
        self._last_used: dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()
        self._enabled = USE_PROXIES
        self._free_proxies_fetched = False

        if self._enabled:
            self._load_proxies()
            print(f"[PROXY] Pool initialized with {len(self._proxies)} proxies (source={PROXY_SOURCE})")

    def _load_proxies(self):
        if PROXY_SOURCE == "env":
            self._load_from_env()
        elif PROXY_SOURCE == "brightdata":
            self._load_brightdata()
        elif PROXY_SOURCE == "smartproxy":
            self._load_smartproxy()
        else:
            self._load_free_proxies()

    def _load_from_env(self):
        """Load from PROXY_LIST env var (comma-separated)."""
        proxy_str = os.environ.get("PROXY_LIST", "")
        for p in proxy_str.split(","):
            p = p.strip()
            if p:
                self._proxies.append({"http": p, "https": p})

    def _load_brightdata(self):
        """BrightData residential proxy rotation."""
        if not BRIGHTDATA_USERNAME:
            return
        # BrightData endpoint with session-based rotation
        endpoint = f"http://brd.superproxy.io:22225"
        for zone in ["residential", "datacenter"]:
            proxy_url = f"http://{BRIGHTDATA_USERNAME}-zone-{zone}:{BRIGHTDATA_PASSWORD}@{endpoint}"
            self._proxies.append({"http": proxy_url, "https": proxy_url})

    def _load_smartproxy(self):
        """SmartProxy endpoint."""
        if not SMARTPROXY_API_KEY:
            return
        for country in ["us", "uk", "de", "in", "br"]:
            proxy_url = f"http://{SMARTPROXY_API_KEY}:{SMARTPROXY_API_KEY}@gate.smartproxy.com:7000"
            self._proxies.append({"http": proxy_url, "https": proxy_url})

    def _load_free_proxies(self):
        """Fetch free proxies from public APIs."""
        import httpx
        urls = [
            "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&protocol=http&timeout=5000",
            "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
            "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
        ]
        for url in urls:
            try:
                resp = httpx.get(url, timeout=10)
                if resp.status_code == 200:
                    for line in resp.text.strip().split("\n"):
                        line = line.strip()
                        if line and ":" in line:
                            if not line.startswith("http"):
                                line = f"http://{line}"
                            self._proxies.append({"http": line, "https": line})
            except Exception:
                continue
        self._free_proxies_fetched = True

    def get_proxy(self) -> dict | None:
        """Get a proxy dict for httpx. Returns None if no proxies or pool disabled."""
        if not self._enabled or not self._proxies:
            return None

        with self._lock:
            # Filter out bad proxies
            now = time.time()
            good = []
            for p in self._proxies:
                url = p.get("http", "")
                score = self._scores[url]
                failures = self._failures[url]
                last_used = self._last_used[url]

                if failures >= MAX_PROXY_FAILURES:
                    if now - last_used < PROXY_COOLDOWN_SECONDS * 10:
                        continue  # skip long-cooldown proxies
                elif now - last_used < PROXY_COOLDOWN_SECONDS and failures > 0:
                    continue  # skip recently-failed proxies

                good.append((p, score))

            if not good:
                # Reset failures and try again
                self._failures.clear()
                good = [(p, self._scores[p.get("http", "")]) for p in self._proxies]

            if not good:
                return None

            # Weighted random selection
            total = sum(s for _, s in good)
            if total <= 0:
                proxy = random.choice(good)[0]
            else:
                r = random.uniform(0, total)
                cumul = 0
                proxy = good[-1][0]
                for p, s in good:
                    cumul += s
                    if cumul >= r:
                        proxy = p
                        break

            self._last_used[proxy.get("http", "")] = time.time()
            return proxy

    def report_success(self, proxy: dict):
        """Report successful use of a proxy."""
        if not proxy:
            return
        url = proxy.get("http", "")
        with self._lock:
            self._scores[url] = min(1.0, self._scores[url] + 0.1)
            self._failures[url] = 0

    def report_failure(self, proxy: dict):
        """Report failed use of a proxy."""
        if not proxy:
            return
        url = proxy.get("http", "")
        with self._lock:
            self._failures[url] += 1
            self._scores[url] = max(0.0, self._scores[url] - 0.3)

    def get_proxies_for_httpx(self) -> dict | None:
        """Get proxy dict in httpx format."""
        return self.get_proxy()

    @property
    def count(self) -> int:
        return len(self._proxies)

    @property
    def enabled(self) -> bool:
        return self._enabled and len(self._proxies) > 0


# ── Singleton ──────────────────────────────────────────────────
_pool = None


def get_proxy_pool() -> ProxyPool:
    global _pool
    if _pool is None:
        _pool = ProxyPool()
    return _pool
