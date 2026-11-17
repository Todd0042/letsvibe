"""
Client for the host-side LetsVibe llama-server supervisor and health endpoint.

Shared by the background worker, web API, and tests so wake/sleep/status logic
lives in one place.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, Optional


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
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data: Optional[bytes] = None
    if method == "POST":
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        else:
            data = b""
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
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


def get_llm_lock_status(timeout: float = 1.5) -> Dict[str, Any]:
    """Returns current shared LLM lock status and round-robin queue depths."""
    try:
        return _http_json(f"{supervisor_url()}/lock/status", method="GET", timeout=timeout)
    except Exception:
        return {"busy": False, "holder_app": None, "queue_depths": {"letsvibe": 0, "letsvibe_rc": 0}}


@contextmanager
def llm_lease(
    app_id: str,
    job_id: str = "",
    ttl_sec: float = 300.0,
    wait_timeout_sec: float = 240.0,
    on_wait: Optional[Callable[[str], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """
    Acquires an exclusive round-robin lease on the shared llama-server via
    letsvibe-llama-supervisor, keeps it alive with a background heartbeat, and
    releases it cleanly on exit. Falls back to no-op if supervisor is offline.
    """
    lease_id = ""
    stop_hb = threading.Event()
    hb_thread: Optional[threading.Thread] = None

    try:
        st = get_llm_lock_status(timeout=1.5)
        if st.get("busy") and on_wait:
            holder = "LetsVibe Audit" if st.get("holder_app") == "letsvibe" else "LetsVibe Reality Check"
            on_wait(f"Waiting for shared AI engine turn (currently serving {holder})...")
        res = _http_json(
            f"{supervisor_url()}/lock/acquire",
            method="POST",
            token=supervisor_token(),
            timeout=wait_timeout_sec + 5.0,
            payload={
                "app_id": app_id,
                "job_id": job_id,
                "ttl_sec": ttl_sec,
                "wait_timeout_sec": wait_timeout_sec,
            },
        )
        if not res.get("acquired"):
            raise TimeoutError(res.get("detail") or "Timed out waiting for shared LLM lease.")
        lease_id = str(res.get("lease_id") or "")

        def _hb_loop() -> None:
            interval = max(15.0, ttl_sec / 3.0)
            while not stop_hb.wait(interval):
                try:
                    _http_json(
                        f"{supervisor_url()}/lock/heartbeat",
                        method="POST",
                        token=supervisor_token(),
                        timeout=3.0,
                        payload={"lease_id": lease_id, "ttl_sec": ttl_sec},
                    )
                except Exception:
                    break

        if lease_id:
            hb_thread = threading.Thread(target=_hb_loop, daemon=True)
            hb_thread.start()
        yield res
    except urllib.error.URLError:
        # Supervisor unreachable (e.g. unit test / direct llama-server): proceed without blocking
        yield {"acquired": True, "lease_id": "", "fallback": True}
    finally:
        stop_hb.set()
        if hb_thread is not None:
            hb_thread.join(timeout=0.5)
        if lease_id:
            try:
                _http_json(
                    f"{supervisor_url()}/lock/release",
                    method="POST",
                    token=supervisor_token(),
                    timeout=3.0,
                    payload={"lease_id": lease_id},
                )
            except Exception:
                pass
