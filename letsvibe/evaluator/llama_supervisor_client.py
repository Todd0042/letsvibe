"""
Client for the host GPU scheduler (gpu-scheduler, 127.0.0.1:11436; formerly the LetsVibe
llama supervisor) and the llama-server health endpoint.

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
        # Within a GPU turn llama-server may be reloading after another app's turn (18 GB model).
        max_wait_sec = float(os.environ.get("LETSVIBE_LLAMA_WAKE_TIMEOUT_SEC", "600.0"))
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


_APP_NAMES = {
    "letsvibe": "LetsVibe Audit",
    "letsvibe_rc": "LetsVibe Reality Check",
    "noir": "Noir Studios",
    "graphical_studio": "Graphical Studio",
    "tiki": "Tiki Rehearsal",
}


@contextmanager
def llm_lease(
    app_id: str,
    job_id: str = "",
    ttl_sec: float = 300.0,
    wait_timeout_sec: float = 24 * 3600.0,
    on_wait: Optional[Callable[[str], None]] = None,
    is_cancelled: Optional[Callable[[], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """
    Waits for this app's round-robin turn from the host GPU scheduler (gpu-scheduler on
    127.0.0.1:11436, shared with Noir Studios and Graphical Studio), keeps it alive with a
    background heartbeat, and releases it on exit. A turn lasts as long as the work takes.

    Waiting polls with a kept ticket (place in line survives between polls) so `is_cancelled`
    (which should raise) is checked regularly. If the scheduler is unreachable, proceeds
    without a lease (unit tests / direct llama-server).
    """
    lease_id = ""
    ticket_id = ""
    stop_hb = threading.Event()
    hb_thread: Optional[threading.Thread] = None
    deadline = time.monotonic() + max(1.0, wait_timeout_sec)
    last_note = ""
    res: Dict[str, Any] = {"acquired": True, "lease_id": "", "fallback": True}

    try:
        try:
            while True:
                if is_cancelled:
                    is_cancelled()
                chunk = max(1.0, min(15.0, deadline - time.monotonic()))
                try:
                    res = _http_json(
                        f"{supervisor_url()}/lock/acquire",
                        method="POST",
                        token=supervisor_token(),
                        timeout=chunk + 10.0,
                        payload={
                            "app_id": app_id, "job_id": job_id, "ttl_sec": ttl_sec,
                            "wait_timeout_sec": chunk, "ticket_id": ticket_id, "keep_ticket": True,
                        },
                    )
                except urllib.error.HTTPError as exc:
                    if exc.code != 409:
                        raise RuntimeError(f"GPU scheduler refused the turn (HTTP {exc.code}).") from exc
                    res = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
                if res.get("acquired"):
                    lease_id = str(res.get("lease_id") or "")
                    ticket_id = ""
                    break
                ticket_id = str(res.get("ticket_id") or ticket_id)
                if on_wait:
                    holder = get_llm_lock_status(timeout=1.5).get("holder_app")
                    note = (f"Waiting for the shared AI engine (#{res.get('position', '?')} in line; "
                            f"now serving {_APP_NAMES.get(holder, holder or 'nobody')})...")
                    if note != last_note:
                        on_wait(note)
                        last_note = note
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for a GPU turn.")
        except urllib.error.URLError as exc:
            if isinstance(exc, urllib.error.HTTPError):
                raise
            # Scheduler unreachable (e.g. unit test / direct llama-server): proceed without a lease
            res = {"acquired": True, "lease_id": "", "fallback": True}

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
                    pass

        if lease_id:
            hb_thread = threading.Thread(target=_hb_loop, daemon=True)
            hb_thread.start()
        yield res
    finally:
        stop_hb.set()
        if hb_thread is not None:
            hb_thread.join(timeout=0.5)
        if ticket_id:
            try:
                _http_json(f"{supervisor_url()}/lock/cancel", method="POST", token=supervisor_token(),
                           timeout=3.0, payload={"ticket_id": ticket_id})
            except Exception:
                pass
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
