"""
Background jobs for self-service data deletion.

Deleting many projects (or a whole account) runs in batches on one background thread so a large
request never blocks the web server or competes with itself, and the browser polls progress.
Job ids are long random tokens, so after an account is deleted (and its session is gone) the
browser can still see the job finish without any login.

Nothing is logged about who deleted what: the job log line only records counts.
"""

import logging
import queue
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..storage.backups import delete_account_everywhere, delete_owner_projects_everywhere
from ..storage.db import DEFAULT_DB_PATH, list_projects_owned_by
from . import image_proxy

logger = logging.getLogger("letsvibe.privacy")

PAUSE_BETWEEN_ITEMS_SEC = 0.15  # keeps a big deletion from hogging the database
JOB_RETENTION_SEC = 3600


@dataclass
class DeletionJob:
    id: str
    kind: str  # "projects" or "account"
    username: str
    github_id: str
    slugs: List[str]
    avatar_url: str = ""
    status: str = "queued"  # queued, running, done, failed
    done: int = 0
    message: str = "Waiting to start..."
    finished_at: Optional[float] = None
    created_at: float = field(default_factory=time.time)

    def public(self) -> Dict[str, object]:
        total = len(self.slugs) + (1 if self.kind == "account" else 0)
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "done": self.done,
            "total": total,
            "percent": 100 if self.status == "done" else int(100 * self.done / total) if total else 0,
            "message": self.message,
        }


class DeletionJobRunner:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._jobs: Dict[str, DeletionJob] = {}
        self._lock = threading.Lock()
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None

    # ---- public API -------------------------------------------------------------------------
    def submit(self, kind: str, username: str, github_id: str, avatar_url: str = "") -> DeletionJob:
        with self._lock:
            # One active job per account: return the existing one instead of starting a duplicate
            for job in self._jobs.values():
                if job.github_id == github_id and job.status in ("queued", "running"):
                    return job
            slugs = [p["full_slug"] for p in list_projects_owned_by(username, db_path=self.db_path)]
            job = DeletionJob(
                id=secrets.token_urlsafe(18), kind=kind, username=username, github_id=github_id,
                slugs=slugs, avatar_url=avatar_url,
            )
            self._jobs[job.id] = job
            self._prune()
        self._queue.put(job.id)
        self._ensure_thread()
        return job

    def get(self, job_id: str) -> Optional[DeletionJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def run_until_idle(self) -> None:
        """Processes queued jobs synchronously (used by tests)."""
        while not self._queue.empty():
            self._run(self._queue.get())

    # ---- internals --------------------------------------------------------------------------
    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="letsvibe-privacy-jobs", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                job_id = self._queue.get(timeout=30)
            except queue.Empty:
                return  # thread exits when idle; restarted on the next submit
            self._run(job_id)

    def _prune(self) -> None:
        cutoff = time.time() - JOB_RETENTION_SEC
        for jid in [j.id for j in self._jobs.values() if j.finished_at and j.finished_at < cutoff]:
            del self._jobs[jid]

    def _run(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job or job.status != "queued":
            return
        job.status = "running"
        try:
            # Live database and every backup are cleaned together, under one lock (storage/backups.py)
            def progress(slug: str) -> None:
                job.message = "Deleting your projects..."
                job.done += 1

            job.message = "Deleting your projects..."
            delete_owner_projects_everywhere(
                job.username, db_path=self.db_path, on_progress=progress, pause_seconds=PAUSE_BETWEEN_ITEMS_SEC,
            )
            image_proxy.purge_owner(job.username)
            if job.avatar_url:
                image_proxy.purge_url(job.avatar_url)
            if job.kind == "account":
                job.message = "Deleting your account..."
                delete_account_everywhere(job.github_id, job.username, db_path=self.db_path)
                job.done += 1
            job.status = "done"
            job.message = (
                "Your account and all of its data have been deleted."
                if job.kind == "account"
                else f"Deleted {len(job.slugs)} project{'s' if len(job.slugs) != 1 else ''}."
            )
            logger.info(f"Privacy deletion job finished ({job.kind}, {len(job.slugs)} projects).")
        except Exception as exc:  # pragma: no cover - surfaced to the user, retried by them
            job.status = "failed"
            job.message = "Something went wrong while deleting. Nothing was left half-published; please try again."
            logger.error(f"Privacy deletion job failed ({job.kind}): {exc}")
        finally:
            job.finished_at = time.time()
            # Drop the slug list once finished so names are not kept in memory longer than needed
            job.slugs = [""] * len(job.slugs)
