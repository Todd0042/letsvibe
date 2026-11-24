"""Shared GitHub REST client with optional token auth, rate-limit cooldown, and TTL caching."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


@dataclass
class GitHubResponse:
    ok: bool
    status_code: int
    data: Any
    rate_limited: bool = False
    reset_in_sec: int = 0
    error: str = ""


_LOCK = threading.Lock()
_CACHE: Dict[str, tuple[float, Any]] = {}
_COOLDOWN_UNTIL: Dict[str, float] = {"search": 0.0, "core": 0.0}


def _bucket_for_url(url: str) -> str:
    return "search" if "/search/" in url else "core"


def _github_headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "LetsVibe/1.0 (+https://letsvibe.todd0042.dev)",
    }
    token = (os.environ.get("LETSVIBE_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_get_json(
    url: str,
    timeout: float = 8.0,
    cache_ttl: float = 900.0,
) -> GitHubResponse:
    """Fetches JSON from GitHub REST API with TTL caching and rate-limit protection."""
    now = time.monotonic()
    bucket = _bucket_for_url(url)

    with _LOCK:
        cached = _CACHE.get(url)
        if cached and cached[0] > now:
            return GitHubResponse(ok=True, status_code=200, data=cached[1])
        until = _COOLDOWN_UNTIL.get(bucket, 0.0)
        if until > now:
            wait_sec = max(1, int(until - now))
            return GitHubResponse(
                ok=False,
                status_code=429,
                data=None,
                rate_limited=True,
                reset_in_sec=wait_sec,
                error=f"GitHub API rate limit cooldown active ({wait_sec}s remaining).",
            )

    try:
        resp = httpx.get(url, headers=_github_headers(), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return GitHubResponse(ok=False, status_code=0, data=None, error=str(exc))

    rem = resp.headers.get("x-ratelimit-remaining")
    is_rl = resp.status_code == 429 or (resp.status_code == 403 and rem == "0")
    if is_rl:
        retry_after = resp.headers.get("retry-after")
        reset_hdr = resp.headers.get("x-ratelimit-reset")
        wait_sec = 60
        if retry_after and retry_after.isdigit():
            wait_sec = max(10, min(3600, int(retry_after)))
        elif reset_hdr and reset_hdr.isdigit():
            wait_sec = max(10, min(3600, int(reset_hdr) - int(time.time())))
        with _LOCK:
            _COOLDOWN_UNTIL[bucket] = time.monotonic() + wait_sec
        return GitHubResponse(
            ok=False,
            status_code=resp.status_code,
            data=None,
            rate_limited=True,
            reset_in_sec=wait_sec,
            error=f"GitHub API rate limit reached; resets in ~{wait_sec}s.",
        )

    if resp.status_code >= 400:
        return GitHubResponse(
            ok=False,
            status_code=resp.status_code,
            data=None,
            error=f"GitHub API HTTP {resp.status_code}",
        )

    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        return GitHubResponse(ok=False, status_code=resp.status_code, data=None, error=str(exc))

    if cache_ttl > 0:
        with _LOCK:
            if len(_CACHE) > 256:
                _CACHE.clear()
            _CACHE[url] = (time.monotonic() + cache_ttl, payload)

    return GitHubResponse(ok=True, status_code=resp.status_code, data=payload)


def clear_github_cache() -> None:
    """Clears in-memory GitHub cache and rate-limit cooldowns (for tests)."""
    with _LOCK:
        _CACHE.clear()
        _COOLDOWN_UNTIL["search"] = 0.0
        _COOLDOWN_UNTIL["core"] = 0.0
