#!/usr/bin/env python3
"""JWT Authentication + Redis Rate Limiter for API protection.

Usage:
    export JWT_SECRET=your-secret-key
    export REDIS_URL=redis://localhost:6379/0
    python -m scripts.auth_jwt --create-user admin password
    python -m scripts.auth_jwt --verify <token>
"""
from __future__ import annotations
import hashlib, json, os, time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JWT_SECRET = os.environ.get("JWT_SECRET", "leadflow-default-secret-change-me")
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))
RATE_LIMIT_RPM = int(os.environ.get("RATE_LIMIT_RPM", "60"))
USERS_FILE = ROOT / "users.json"
LOG = ROOT / "auth.log"


def log(msg):
    ts = datetime.now().strftime("%H-%M-%S")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


# ── JWT Token Management ─────────────────────────────────────
def _base64url_encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _base64url_decode(s: str) -> bytes:
    import base64
    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * padding)


def create_token(username: str, role: str = "user", expires_hours: int = 0) -> str:
    """Create a JWT token."""
    if expires_hours <= 0:
        expires_hours = JWT_EXPIRY_HOURS

    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": username,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + (expires_hours * 3600),
    }

    header_b64 = _base64url_encode(json.dumps(header).encode())
    payload_b64 = _base64url_encode(json.dumps(payload).encode())
    signature = hashlib.sha256(f"{header_b64}.{payload_b64}.{JWT_SECRET}".encode()).digest()
    sig_b64 = _base64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{sig_b64}"


def verify_token(token: str) -> dict | None:
    """Verify and decode a JWT token."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header_b64, payload_b64, sig_b64 = parts
        expected_sig = _base64url_encode(
            hashlib.sha256(f"{header_b64}.{payload_b64}.{JWT_SECRET}".encode()).digest()
        )

        if sig_b64 != expected_sig:
            return None

        payload = json.loads(_base64url_decode(payload_b64))

        if payload.get("exp", 0) < time.time():
            return None

        return payload
    except:
        return None


# ── User Management ──────────────────────────────────────────
def load_users() -> dict:
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_users(users: dict):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def create_user(username: str, password: str, role: str = "user") -> str:
    """Create a new user and return their API token."""
    users = load_users()
    if username in users:
        raise ValueError(f"User '{username}' already exists")

    password_hash = hashlib.sha256(password.encode()).hexdigest()
    users[username] = {
        "password_hash": password_hash,
        "role": role,
        "created": datetime.now(timezone.utc).isoformat(),
        "api_key": hashlib.sha256(f"{username}:{time.time()}".encode()).hexdigest()[:32],
    }
    save_users(users)

    token = create_token(username, role)
    log(f"Created user: {username} (role={role})")
    return token


def authenticate_user(username: str, password: str) -> str | None:
    """Authenticate user and return JWT token."""
    users = load_users()
    user = users.get(username)
    if not user:
        return None

    password_hash = hashlib.sha256(password.encode()).hexdigest()
    if user["password_hash"] != password_hash:
        return None

    token = create_token(username, user["role"])
    log(f"Authenticated user: {username}")
    return token


def delete_user(username: str) -> bool:
    users = load_users()
    if username in users:
        del users[username]
        save_users(users)
        log(f"Deleted user: {username}")
        return True
    return False


def get_user_api_key(username: str) -> str | None:
    users = load_users()
    user = users.get(username)
    return user.get("api_key") if user else None


# ── Rate Limiter (Redis-backed) ──────────────────────────────
_rate_buckets = {}

def check_rate_limit(identifier: str, max_requests: int = 0) -> bool:
    """Check if request is within rate limit."""
    if max_requests <= 0:
        max_requests = RATE_LIMIT_RPM

    # Try Redis first
    try:
        import redis
        r = redis.from_url(os.environ.get("REDIS_URL", ""), decode_responses=True)
        key = f"rate:{identifier}"
        pipe = r.pipeline()
        now = time.time()
        window = 60
        pipe.zremrangebyscore(key, 0, now - window)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, window)
        results = pipe.execute()
        count = results[1]
        return count < max_requests
    except:
        pass

    # Fallback to in-memory
    now = time.time()
    window = 60
    if identifier not in _rate_buckets:
        _rate_buckets[identifier] = []
    _rate_buckets[identifier] = [t for t in _rate_buckets[identifier] if now - t < window]
    if len(_rate_buckets[identifier]) >= max_requests:
        return False
    _rate_buckets[identifier].append(now)
    return True


def get_rate_limit_info(identifier: str) -> dict:
    """Get rate limit info for an identifier."""
    try:
        import redis
        r = redis.from_url(os.environ.get("REDIS_URL", ""), decode_responses=True)
        key = f"rate:{identifier}"
        now = time.time()
        count = r.zcard(key)
        ttl = r.ttl(key)
        return {"remaining": max(0, RATE_LIMIT_RPM - count), "reset_in": ttl or 60}
    except:
        return {"remaining": RATE_LIMIT_RPM, "reset_in": 60}


# ── FastAPI Middleware ────────────────────────────────────────
def auth_middleware():
    """FastAPI dependency for authentication."""
    from fastapi import Request, HTTPException

    async def verify(request: Request):
        # Skip auth for public endpoints
        if request.url.path in ("/", "/api/health", "/api/health"):
            return {"role": "admin"}

        # Check API key header
        api_key = request.headers.get("X-API-Key")
        if api_key:
            users = load_users()
            for user_data in users.values():
                if user_data.get("api_key") == api_key:
                    return {"role": user_data.get("role", "user"), "username": "api-key"}
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Check JWT token
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = verify_token(token)
            if payload:
                return payload

        # Rate limit check
        client_ip = request.client.host if request.client else "unknown"
        if not check_rate_limit(client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        # Allow unauthenticated access for public endpoints
        return {"role": "anonymous"}

    return verify


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--create-user", nargs=2, metavar=("USERNAME", "PASSWORD"))
    parser.add_argument("--verify", help="Verify JWT token")
    parser.add_argument("--list-users", action="store_true")
    parser.add_argument("--delete-user", help="Delete a user")
    parser.add_argument("--rate-status", help="Check rate limit for IP")
    args = parser.parse_args()

    if args.create_user:
        username, password = args.create_user
        token = create_user(username, password)
        print(f"User '{username}' created")
        print(f"JWT Token: {token}")
        print(f"API Key: {get_user_api_key(username)}")
    elif args.verify:
        result = verify_token(args.verify)
        if result:
            print(f"Valid token: {json.dumps(result, indent=2)}")
        else:
            print("Invalid or expired token")
    elif args.list_users:
        users = load_users()
        for username, data in users.items():
            print(f"  {username} (role={data.get('role', 'user')})")
    elif args.delete_user:
        if delete_user(args.delete_user):
            print(f"Deleted user: {args.delete_user}")
    elif args.rate_status:
        info = get_rate_limit_info(args.rate_status)
        print(f"Rate limit: {json.dumps(info)}")
    else:
        print("JWT Auth Manager")
        print("  --create-user <user> <pass>  Create a new user")
        print("  --verify <token>             Verify JWT token")
        print("  --list-users                 List all users")
        print("  --delete-user <user>         Delete a user")


if __name__ == "__main__":
    main()
