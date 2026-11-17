"""
Client for the host-side LetsVibe llama-server supervisor and health endpoint.

Shared by the background worker, web API, and tests so wake/sleep/status logic
lives in one place.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional


def supervisor_url() -> str:
    return os.getenv("LETSVIBE_SUPERVISOR_URL", "http://127.0.0.1:11436").rstrip("/")


def llama_url() -> str:
    return os.getenv("LETSVIBE_LLAMA_URL", "http://127.0.0.1:11435").rstrip("/")


def supervisor_token() -> str:
    return os.getenv("LETSVIBE_SUPERVISOR_TOKEN", "").strip()


def _http_json(
    url: str,
    method: str = "GET",
    token: str = "",
    timeout: float = 2.0,
) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method=method, headers=headers, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - localhost supervisor/llama URLs only
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def check_llama_health(timeout: float = 1.5) -> bool:
    """Returns True if llama-server is loaded and responding ok on /health."""
    try:
        data = _http_json(f"{llama_url()}/health", method="GET", timeout=timeout)
        return str(data.get("status", "")).lower() == "ok"
    except Exception:
        return False


def get_engine_status(timeout: float = 1.5) -> Dict[str, Any]:
    """
    Returns unified engine status from the host supervisor, falling back to
    direct llama-server /health if the supervisor is not running.
    """
    try:
        data = _http_json(f"{supervisor_url()}/status", method="GET", timeout=timeout)
        data.setdefault("supervisor_online", True)
        return data
    except Exception:
        if check_llama_health(timeout=timeout):
            return {
                "state": "awake",
                "supervisor_online": False,
                "idle_minutes": 0,
                "idle_seconds_remaining": None,
                "last_error": None,
            }
        return {
            "state": "unavailable",
            "supervisor_online": False,
            "idle_minutes": 0,
            "idle_seconds_remaining": None,
            "last_error": "Host supervisor is not reachable.",
        }


def wake_engine(timeout: float = 5.0) -> Dict[str, Any]:
    return _http_json(f"{supervisor_url()}/wake", method="POST", token=supervisor_token(), timeout=timeout)


def sleep_engine(timeout: float = 5.0) -> Dict[str, Any]:
    return _http_json(f"{supervisor_url()}/sleep", method="POST", token=supervisor_token(), timeout=timeout)


def touch_engine(timeout: float = 1.5) -> None:
    try:
        _http_json(f"{supervisor_url()}/touch", method="POST", token=supervisor_token(), timeout=timeout)
    except Exception:
        pass


def ensure_llama_awake(
    on_progress: Optional[Callable[[str], None]] = None,
    is_cancelled: Optional[Callable[[], None]] = None,
    max_wait_sec: Optional[float] = None,
    poll_interval_sec: float = 2.0,
) -> Dict[str, Any]:
    """
    Ensures llama-server is awake and healthy. If dormant, triggers /wake and polls
    /health until ready or max_wait_sec expires.
    """
    if max_wait_sec is None:
        max_wait_sec = float(os.environ.get("LETSVIBE_LLAMA_WAKE_TIMEOUT_SEC", "180.0"))
    if check_llama_health():
        touch_engine()
        return {"ok": True, "state": "awake"}

    if on_progress:
        on_progress("Waking local AI engine (loading model into GPU/RAM)...")

    try:
        wake_res = wake_engine(timeout=5.0)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"Supervisor rejected wake request (HTTP {exc.code})."}
    except Exception as exc:
        return {"ok": False, "error": f"Could not reach host llama supervisor: {exc}"}

    if str(wake_res.get("state", "")).lower() == "error":
        return {"ok": False, "error": str(wake_res.get("last_error") or "Supervisor failed to start llama-server.")}

    deadline = time.monotonic() + max_wait_sec
    while time.monotonic() < deadline:
        if is_cancelled:
            is_cancelled()
        if check_llama_health(timeout=1.5):
            touch_engine()
            return {"ok": True, "state": "awake"}
        status = get_engine_status(timeout=1.5)
        if status.get("state") == "dormant" and status.get("last_error"):
            return {"ok": False, "error": str(status["last_error"])}
        time.sleep(poll_interval_sec)

    return {"ok": False, "error": f"Timed out waiting {int(max_wait_sec)}s for llama-server to load."}
