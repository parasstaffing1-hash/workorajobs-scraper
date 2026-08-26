"""Production middleware — API key auth + per-IP rate limiting.

Usage in api_server.py:
    from jobcollector.middleware import AuthRateMiddleware
    app.add_middleware(AuthRateMiddleware)
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

ROOT = Path(__file__).resolve().parent.parent

# ── Configuration ──────────────────────────────────────────────
# Set via environment variables
REQUIRE_API_KEY = os.environ.get("REQUIRE_API_KEY", "false").lower() == "true"
DEFAULT_RATE_LIMIT = int(os.environ.get("RATE_LIMIT_RPM", "120"))  # requests per minute
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")  # bypass rate limits


# ── In-memory rate limiter ─────────────────────────────────────
class RateLimiter:
    """Sliding window rate limiter per API key or IP."""

    def __init__(self, default_limit: int = 120, window_seconds: int = 60):
        self.default_limit = default_limit
        self.window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._limits: dict[str, int] = {}
        self._lock = threading.Lock()

    def set_limit(self, key: str, limit: int):
        with self._lock:
            self._limits[key] = limit

    def check(self, identifier: str) -> tuple[bool, int, int]:
        """Returns (allowed, remaining, retry_after_seconds)."""
        now = time.time()
        cutoff = now - self.window

        with self._lock:
            limit = self._limits.get(identifier, self.default_limit)
            # Prune old entries
            self._requests[identifier] = [
                t for t in self._requests[identifier] if t > cutoff
            ]
            current = len(self._requests[identifier])

            if current >= limit:
                oldest = self._requests[identifier][0] if self._requests[identifier] else now
                retry_after = int(oldest + self.window - now) + 1
                return False, 0, retry_after

            self._requests[identifier].append(now)
            return True, limit - current - 1, 0

    def cleanup(self):
        """Remove entries older than window."""
        cutoff = time.time() - self.window
        with self._lock:
            for key in list(self._requests.keys()):
                self._requests[key] = [t for t in self._requests[key] if t > cutoff]
                if not self._requests[key]:
                    del self._requests[key]


# ── API Key Store ──────────────────────────────────────────────
class APIKeyStore:
    """Load API keys from DB or environment."""

    def __init__(self):
        self._keys: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load_from_env()

    def _load_from_env(self):
        """Load keys from API_KEYS env var (comma-separated)."""
        keys_str = os.environ.get("API_KEYS", "")
        if keys_str:
            for k in keys_str.split(","):
                k = k.strip()
                if k:
                    self._keys[k] = {
                        "name": f"env-{k[:8]}",
                        "tier": "pro",
                        "rate_limit": 600,
                    }

    def _load_from_db(self):
        """Load keys from database."""
        try:
            db_path = ROOT / "jobs.db"
            if not db_path.exists():
                return
            conn = sqlite3.connect(str(db_path), timeout=5)
            for row in conn.execute(
                "SELECT key, name, tier, rate_limit FROM api_keys WHERE active = 1"
            ):
                self._keys[row[0]] = {
                    "name": row[1], "tier": row[2], "rate_limit": row[3],
                }
            conn.close()
        except Exception:
            pass

    def validate(self, key: str) -> dict | None:
        """Validate an API key. Returns key info or None."""
        with self._lock:
            if not self._keys:
                self._load_from_db()
            return self._keys.get(key)

    def is_valid(self, key: str) -> bool:
        return self.validate(key) is not None


# ── Combined Middleware ────────────────────────────────────────
class AuthRateMiddleware(BaseHTTPMiddleware):
    """API key auth (optional) + rate limiting + CORS headers."""

    def __init__(self, app):
        super().__init__(app)
        self.rate_limiter = RateLimiter(default_limit=DEFAULT_RATE_LIMIT)
        self.key_store = APIKeyStore()
        # Allowed paths without auth
        self._public_paths = {"/", "/api/health", "/docs", "/openapi.json", "/redoc"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth for public paths
        if path in self._public_paths or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)

        # ── API Key Auth ───────────────────────────────────────
        api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")

        if REQUIRE_API_KEY:
            if not api_key:
                return JSONResponse(
                    {"error": "Missing API key. Set X-API-Key header or ?api_key= param."},
                    status_code=401
                )
            if api_key == ADMIN_API_KEY:
                key_info = {"name": "admin", "tier": "admin", "rate_limit": 999999}
            elif not self.key_store.is_valid(api_key):
                return JSONResponse(
                    {"error": "Invalid API key."},
                    status_code=403
                )
            else:
                key_info = self.key_store.validate(api_key)
        else:
            key_info = {"name": "anonymous", "tier": "free", "rate_limit": DEFAULT_RATE_LIMIT}

        # ── Rate Limiting ──────────────────────────────────────
        identifier = api_key or _get_client_ip(request)
        limit = key_info.get("rate_limit", DEFAULT_RATE_LIMIT) if key_info else DEFAULT_RATE_LIMIT
        self.rate_limiter.set_limit(identifier, limit)

        allowed, remaining, retry_after = self.rate_limiter.check(identifier)
        if not allowed:
            return JSONResponse(
                {"error": "Rate limit exceeded", "retry_after_seconds": retry_after,
                 "limit": limit, "window": "60s"},
                status_code=429,
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + retry_after),
                    "Retry-After": str(retry_after),
                }
            )

        # ── Process request ────────────────────────────────────
        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Key management helpers ─────────────────────────────────────
def generate_api_key() -> str:
    """Generate a random API key."""
    return hashlib.sha256(os.urandom(32)).hexdigest()[:48]


def create_api_key(name: str = "", tier: str = "free", rate_limit: int = 60) -> str:
    """Create and persist a new API key."""
    key = generate_api_key()
    try:
        conn = sqlite3.connect(str(ROOT / "jobs.db"), timeout=5)
        conn.execute("""
            INSERT INTO api_keys (key, name, tier, rate_limit, created_at, active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (key, name, tier, rate_limit, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
        print(f"[AUTH] Created API key: {key} (name={name}, tier={tier})")
    except Exception as e:
        print(f"[AUTH] Failed to create key: {e}")
    return key


def revoke_api_key(key: str) -> bool:
    """Revoke an API key."""
    try:
        conn = sqlite3.connect(str(ROOT / "jobs.db"), timeout=5)
        cur = conn.execute("UPDATE api_keys SET active = 0 WHERE key = ?", (key,))
        conn.commit()
        conn.close()
        return cur.rowcount > 0
    except Exception:
        return False
