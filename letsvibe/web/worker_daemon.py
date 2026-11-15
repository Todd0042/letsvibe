"""
Standalone background worker daemon for LetsVibe blue-green deployments.
Owns the single AuditQueueWorker and BackupScheduler so web slots (blue/green)
can restart or swap independently without interrupting an active repo audit.
"""

from contextlib import asynccontextmanager
import glob
import os
import shutil
import tempfile
from typing import Optional

from fastapi import FastAPI
import uvicorn

from ..evaluator.ollama import OllamaClient
from ..storage.backups import BackupScheduler
from ..storage.db import get_db_connection, init_db
from .worker import AuditQueueWorker

_worker: Optional[AuditQueueWorker] = None
_backups = BackupScheduler()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _worker
    for orphan_dir in glob.glob(os.path.join(tempfile.gettempdir(), "letsvibe_clone_*")):
        shutil.rmtree(orphan_dir, ignore_errors=True)

    init_db()
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                UPDATE audit_queue
                SET status = 'queued', progress_message = 'Recovered after worker restart'
                WHERE status = 'running'
                """
            )
    except Exception:  # nosec B110 - best-effort zombie recovery
        pass
    finally:
        conn.close()

    client = OllamaClient()
    if not client.is_running():
        client.ensure_server_running(timeout_sec=5.0)

    _worker = AuditQueueWorker()
    _worker.start()
    _backups.start()
    yield
    _backups.stop()
    if _worker:
        _worker.stop()
        _worker = None


worker_app = FastAPI(title="LetsVibe Worker Daemon", lifespan=lifespan)


@worker_app.get("/health")
async def health():
    return {"status": "ok"}


@worker_app.get("/status")
async def status():
    return {"worker_running": _worker.current_task_info if _worker else None}


@worker_app.post("/stop/{task_id}")
async def stop_task(task_id: int):
    ok = bool(_worker and _worker.request_stop(task_id))
    return {"ok": ok}


@worker_app.post("/run-now/{task_id}")
async def run_now(task_id: int):
    ok = bool(_worker and _worker.process_task_now(task_id))
    return {"ok": ok}


def main() -> None:
    host = os.environ.get("LETSVIBE_WORKER_HOST", "127.0.0.1")
    port = int(os.environ.get("LETSVIBE_WORKER_PORT", "8099"))
    uvicorn.run(worker_app, host=host, port=port, no_access_log=True)


if __name__ == "__main__":
    main()
