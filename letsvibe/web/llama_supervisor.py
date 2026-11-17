"""
Lightweight host-side supervisor for letsvibe-llama-server.

Runs on bare metal (outside Docker) and binds strictly to 127.0.0.1:11436.
Exposes /status, /wake, /sleep, and /touch so containerized workers and the
web server can wake Qwen3 30B-A3B on demand and automatically reclaim ~18 GB RAM
+ ~2.8 GB VRAM after an idle timeout.
"""

from __future__ import annotations

import hmac
import json
import os
import signal
import subprocess  # nosec B404 - launches fixed letsvibe-llama-server binary path only
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional


class LlamaSupervisor:
    def __init__(
        self,
        llama_bin: str = "/home/todd/.local/bin/letsvibe-llama-server",
        llama_url: str = "http://127.0.0.1:11435",
        idle_minutes: float = 10.0,
        token: str = "",
    ) -> None:
        self.llama_bin = llama_bin
        self.llama_url = llama_url.rstrip("/")
        self.idle_minutes = max(0.0, float(idle_minutes))
        self.token = token.strip()

        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._last_activity: float = time.monotonic()
        self._last_error: Optional[str] = None
        self._stop_event = threading.Event()
        self._watcher = threading.Thread(target=self._idle_loop, daemon=True)
        self._watcher.start()

    def _is_llama_healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.llama_url}/health", timeout=1.2) as resp:  # nosec B310
                data = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
                return str(data.get("status", "")).lower() == "ok"
        except Exception:
            return False

    def _proc_running(self) -> bool:
        if self._proc is None:
            return False
        if self._proc.poll() is not None:
            rc = self._proc.returncode
            if rc not in (0, -signal.SIGTERM, -signal.SIGKILL):
                self._last_error = f"llama-server exited with code {rc} (check GPU VRAM availability)."
            self._proc = None
            return False
        return True

    def status(self) -> Dict[str, Any]:
        with self._lock:
            running = self._proc_running()
            healthy = self._is_llama_healthy()
            if healthy:
                state = "awake"
            elif running:
                state = "waking"
            else:
                state = "dormant"

            remaining: Optional[int] = None
            if (running or healthy) and self.idle_minutes > 0:
                elapsed = time.monotonic() - self._last_activity
                remaining = max(0, int(self.idle_minutes * 60.0 - elapsed))

            return {
                "state": state,
                "pid": self._proc.pid if running and self._proc else None,
                "idle_minutes": self.idle_minutes,
                "idle_seconds_remaining": remaining,
                "last_error": self._last_error,
            }

    def touch(self) -> Dict[str, Any]:
        with self._lock:
            self._last_activity = time.monotonic()
        return self.status()

    def wake(self) -> Dict[str, Any]:
        with self._lock:
            self._last_activity = time.monotonic()
            self._last_error = None
            if self._is_llama_healthy() or self._proc_running():
                return self._unlocked_status_hint("waking" if not self._is_llama_healthy() else "awake")

            if not Path(self.llama_bin).is_file():
                self._last_error = f"Missing binary: {self.llama_bin}"
                return {"state": "error", "last_error": self._last_error}

            self._proc = subprocess.Popen(
                [self.llama_bin],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {"state": "waking", "pid": self._proc.pid, "idle_minutes": self.idle_minutes}

    def _unlocked_status_hint(self, state: str) -> Dict[str, Any]:
        return {
            "state": state,
            "pid": self._proc.pid if self._proc else None,
            "idle_minutes": self.idle_minutes,
        }

    def sleep(self) -> Dict[str, Any]:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._last_error = None

        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            for _ in range(25):
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        return self.status()

    def _idle_loop(self) -> None:
        while not self._stop_event.wait(15.0):
            if self.idle_minutes <= 0:
                continue
            should_sleep = False
            with self._lock:
                if self._proc_running():
                    idle_sec = time.monotonic() - self._last_activity
                    if idle_sec >= self.idle_minutes * 60.0:
                        should_sleep = True
            if should_sleep:
                self.sleep()

    def shutdown(self) -> None:
        self._stop_event.set()
        self.sleep()


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def make_handler(sup: LlamaSupervisor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if not sup.token:
                return True
            hdr = self.headers.get("Authorization", "")
            if not hdr.startswith("Bearer "):
                return False
            return hmac.compare_digest(hdr[7:].strip(), sup.token)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] == "/status":
                self._send_json(200, sup.status())
                return
            self._send_json(404, {"detail": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._send_json(401, {"detail": "Unauthorized"})
                return
            path = self.path.split("?")[0]
            if path == "/wake":
                self._send_json(200, sup.wake())
            elif path == "/sleep":
                self._send_json(200, sup.sleep())
            elif path == "/touch":
                self._send_json(200, sup.touch())
            else:
                self._send_json(404, {"detail": "Not found"})

    return Handler


def main() -> None:
    env_hint = os.getenv("LETSVIBE_ENV_FILE", "/home/todd/storage/letsvibe-docker/app/.env")
    _load_env_file(Path(env_hint))

    host = os.getenv("LETSVIBE_SUPERVISOR_HOST", "127.0.0.1")
    port = int(os.getenv("LETSVIBE_SUPERVISOR_PORT", "11436"))
    idle_min = float(os.getenv("LETSVIBE_IDLE_MINUTES", "10"))
    llama_bin = os.getenv("LETSVIBE_LLAMA_BIN", "/home/todd/.local/bin/letsvibe-llama-server")
    llama_url = os.getenv("LETSVIBE_LLAMA_URL", "http://127.0.0.1:11435")
    token = os.getenv("LETSVIBE_SUPERVISOR_TOKEN", "")

    sup = LlamaSupervisor(
        llama_bin=llama_bin,
        llama_url=llama_url,
        idle_minutes=idle_min,
        token=token,
    )
    server = ThreadingHTTPServer((host, port), make_handler(sup))

    def _handle_sig(_signum: int, _frame: Any) -> None:
        sup.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)
    print(f"LetsVibe llama supervisor listening on http://{host}:{port} (idle={idle_min}m)", flush=True)
    try:
        server.serve_forever()
    finally:
        sup.shutdown()


if __name__ == "__main__":
    main()
