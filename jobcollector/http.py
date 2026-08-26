"""Shared HTTP client with polite user agent and bounded retries."""
from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

USER_AGENT = "JobCollector/0.1 (+open-source job aggregator; contact: you@example.com)"

_RETRYABLE = (
    httpx.TimeoutException,
    httpx.TransportError,
    httpx.RemoteProtocolError,
    httpx.ConnectError,
)


def make_client(timeout: float = 30.0, **kwargs) -> httpx.Client:
    kwargs.setdefault("headers", {"User-Agent": USER_AGENT})
    kwargs.setdefault("follow_redirects", True)
    kwargs.setdefault("timeout", timeout)
    return httpx.Client(**kwargs)


def _retry_call(fn, *args, **kwargs):
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=8),
        retry=retry_if_exception_type(_RETRYABLE),
        reraise=True,
    )(fn)(*args, **kwargs)


def retry_get(client: httpx.Client, url: str, **kwargs):
    """GET with exponential backoff retries on transport errors (not 4xx/5xx)."""
    return _retry_call(client.get, url, **kwargs)


def retry_post(client: httpx.Client, url: str, **kwargs):
    """POST with exponential backoff retries on transport errors (not 4xx/5xx)."""
    return _retry_call(client.post, url, **kwargs)
