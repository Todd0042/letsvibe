"""
Background evaluation queue worker for LetsVibe.
Processes queued repository audits, manages state transitions, and supports
admin pause/resume, real-time batch progress tracking, and hard-stop CPU spillover protection.
"""

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..analyzers.complexity import ComplexityAnalyzer
from ..analyzers.functionality import FunctionalityAnalyzer
from ..analyzers.security import SecurityAnalyzer
from ..core.ingest import RepositoryIngestor
from ..analyzers.journey_signals import gather_journey_signals, full_readme as _full_readme
from ..evaluator.pipeline import build_batches, gather_metrics
from ..core.models import DeterministicMetrics
from ..core.platforms import detect_platforms
from ..core.vibe_detector import VibeDetector
from ..evaluator.classifier import classify_project
from ..evaluator.consensus import ConsensusEngine
from ..evaluator.ollama import CpuSpilloverError, OllamaClient
from ..storage.db import (
    DEFAULT_DB_PATH,
    create_appeal,
    get_db_connection,
    init_db,
    update_report_classification,
)
from ..storage.identity import parse_repo_identity
from ..storage.manager import ReportStorageManager

logger = logging.getLogger("letsvibe.worker")


class TaskStopped(CpuSpilloverError):
    """Raised inside a running task when the admin presses Stop (a CpuSpilloverError so the pipeline halts at once)."""


class AuditQueueWorker:
    """Background worker processing repo audits from SQLite queue."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, poll_interval: float = 2.0):
        self.db_path = db_path
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.current_task_info: Optional[dict] = None
        self._stop_task_id: Optional[int] = None  # set by request_stop(); checked at every safe point

    def start(self) -> None:
        """Starts worker background thread."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="letsvibe-worker")
            self._thread.start()
            logger.info("Audit queue worker started.")

    def stop(self) -> None:
        """Signals worker to stop."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("Audit queue worker stopped.")

    def _run_loop(self) -> None:
        init_db(self.db_path)
        self._recover_orphaned_tasks()
        while not self._stop_event.is_set():
            try:
                task = self._fetch_next_task()
                if task:
                    self._process_task(task)
                else:
                    time.sleep(self.poll_interval)
            except Exception as e:
                logger.error(f"Error in queue worker loop: {e}", exc_info=True)
                time.sleep(self.poll_interval)

    def _recover_orphaned_tasks(self) -> None:
        """Resets any tasks left in 'running' state from previous server runs back to 'queued'."""
        conn = get_db_connection(self.db_path)
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE audit_queue
                    SET status = 'queued', progress_message = 'Recovered from restart; waiting in queue...'
                    WHERE status = 'running'
                    """
                )
        except Exception as e:
            logger.warning(f"Failed to recover orphaned tasks: {e}")
        finally:
            conn.close()

    def _fetch_next_task(self) -> Optional[dict]:
        """Checks if queue is unpaused and auto_process is active, then retrieves next task."""
        conn = get_db_connection(self.db_path)
        try:
            with conn:
                row = None
                next_id, self._next_task_id = getattr(self, "_next_task_id", None), None
                if next_id is not None:  # admin "Run next" goes first, even while paused
                    row = conn.execute("SELECT * FROM audit_queue WHERE id = ? AND status = 'queued'", (next_id,)).fetchone()
                if row is None:
                    settings = conn.execute("SELECT * FROM queue_settings WHERE id = 1").fetchone()
                    if not settings or settings["is_paused"] or not settings["auto_process"]:
                        return None
                    # A task the admin stopped resumes first; otherwise first come, first served (oldest first)
                    front = settings["front_task_id"] if "front_task_id" in settings.keys() else None
                    if front is not None:
                        row = conn.execute("SELECT * FROM audit_queue WHERE id = ? AND status = 'queued'", (front,)).fetchone()
                        conn.execute("UPDATE queue_settings SET front_task_id = NULL WHERE id = 1")
                    if row is None:
                        row = conn.execute("SELECT * FROM audit_queue WHERE status = 'queued' ORDER BY id ASC LIMIT 1").fetchone()
                if not row:
                    return None

                task = dict(row)
                now_iso = datetime.now().isoformat()
                conn.execute(
                    """
                    UPDATE audit_queue
                    SET status = 'running', started_at = ?, progress_message = '[PROGRESS:5%] Starting analysis...'
                    WHERE id = ?
                    """,
                    (now_iso, task["id"]),
                )
                return task
        finally:
            conn.close()

    def process_task_now(self, task_id: int) -> bool:
        """
        Admin "Run next": moves a waiting task to the front of the line. It starts as soon as the current
        grading finishes (even if the queue is paused), never alongside it: two gradings at once would
        fight over the one GPU and unload each other's models.
        """
        conn = get_db_connection(self.db_path)
        try:
            row = conn.execute("SELECT status FROM audit_queue WHERE id = ?", (task_id,)).fetchone()
        finally:
            conn.close()
        if not row or row["status"] != "queued":
            return False
        self._next_task_id = task_id
        return True

    def request_stop(self, task_id: int) -> bool:
        """
        Admin "Stop": pauses the queue at once (so nothing else starts), then the running task stops at its
        next safe point -- before its next AI call or progress step; an AI call already under way finishes
        first. The task goes back to the queue, first in line, and runs when the queue is resumed.
        """
        conn = get_db_connection(self.db_path)
        try:
            row = conn.execute("SELECT status FROM audit_queue WHERE id = ?", (task_id,)).fetchone()
            if not row or row["status"] != "running":
                return False
            with conn:
                conn.execute("UPDATE queue_settings SET is_paused = 1, front_task_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                             (task_id,))
                conn.execute("UPDATE audit_queue SET progress_message = ? WHERE id = ?",
                             ("Stopping... (finishing the current AI step)", task_id))
        finally:
            conn.close()
        current = (self.current_task_info or {}).get("id")
        if current == task_id:
            self._stop_task_id = task_id
        else:  # marked running but not being worked on (e.g. left over from a restart): requeue it now
            self._requeue_stopped(task_id)
        return True

    def _check_stop(self, task_id: int) -> None:
        if self._stop_task_id is not None and self._stop_task_id == task_id:
            raise TaskStopped(f"Task {task_id} stopped by the admin.")

    def _requeue_stopped(self, task_id: int) -> None:
        self._stop_task_id = None
        conn = get_db_connection(self.db_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE audit_queue SET status = 'queued', started_at = NULL, progress_message = ? WHERE id = ?",
                    ("Stopped by the admin. It runs first when the queue is resumed.", task_id),
                )
                conn.execute("UPDATE queue_settings SET is_paused = 1, front_task_id = ? WHERE id = 1", (task_id,))
        finally:
            conn.close()

    def _update_task_progress(self, task_id: int, message: str, pct: Optional[int] = None) -> None:
        self._check_stop(task_id)
        formatted = f"[PROGRESS:{pct}%] {message}" if pct is not None else message
        conn = get_db_connection(self.db_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE audit_queue SET progress_message = ? WHERE id = ?",
                    (formatted, task_id),
                )
        finally:
            conn.close()

    def _process_task(self, task: dict) -> None:
        task_id = task["id"]
        target = task["target"]
        mode = task.get("mode", "blend")
        force_override = bool(task.get("force_override", 0))
        self.current_task_info = {"id": task_id, "target": target, "mode": mode}
        packed_repo = None
        last_step_msg = "Initialization"

        try:
            # 1. Ingestion (Fast: 5% -> 10%)
            last_step_msg = f"Ingesting repository target: {target}"
            self._update_task_progress(task_id, last_step_msg, pct=6)
            packed_repo = RepositoryIngestor.ingest(target)

            vendor_note = (
                f" (skipped {packed_repo.skipped_vendor_files} untouched vendor/SDK files)"
                if getattr(packed_repo, "skipped_vendor_files", 0) > 0
                else ""
            )

            if mode == "reclassify":
                self._reclassify(task, packed_repo)
                return
            if mode == "error_review":
                self._review_error(task, packed_repo)
                return

            # 1.5 Pre-Flight AI/Vibe-Coding Gatekeeper Check (10% -> 14%)
            if not force_override:
                last_step_msg = f"Checking vibe-coding signatures{vendor_note}..."
                self._update_task_progress(task_id, last_step_msg, pct=10)
                vibe_verdict = VibeDetector.evaluate(packed_repo.source_path, packed_repo.files)
                if not vibe_verdict.is_vibe:
                    self._reject_non_vibe_task(task_id, vibe_verdict)
                    return
            else:
                logger.info(f"Task #{task_id} has force_override=1: bypassing AI/vibe-coding gatekeeper.")

            client = OllamaClient()
            # Every AI call first checks whether the admin pressed Stop
            _generate = client.generate_json

            def _guarded_generate(*args, **kwargs):
                self._check_stop(task_id)
                return _generate(*args, **kwargs)

            client.generate_json = _guarded_generate
            if not client.is_running():
                last_step_msg = "Starting local Ollama daemon..."
                self._update_task_progress(task_id, last_step_msg, pct=12)
                if not client.ensure_server_running(timeout_sec=15.0):
                    self._fail_task(task_id, "Ollama service is offline and could not be auto-started.")
                    return

            # 2. Deterministic AST Analysis & CLI/TUI Telemetry (14% -> 18%)
            last_step_msg = f"Running deterministic static AST analysis on {len(packed_repo.files)} files{vendor_note}..."
            self._update_task_progress(task_id, last_step_msg, pct=15)
            # Same recipe as the curve calibration (evaluator/pipeline.py)
            metrics, platform_info = gather_metrics(packed_repo)
            readme_excerpt = metrics.readme_excerpt

            # 2.5 Focused classification: category, platforms, device profile (graders depend on it)
            last_step_msg = "Classifying project category & target hardware..."
            self._update_task_progress(task_id, last_step_msg, pct=17)
            classification = classify_project(
                client, packed_repo.source_path, platform_info,
                readme=readme_excerpt, file_tree=packed_repo.file_tree,
            )

            # 3. Whole-Repo Manifest Batches (18% -> 20%)
            manifest_batches, _coverage = build_batches(packed_repo)
            last_step_msg = f"Partitioned {len(packed_repo.files)} files into {len(manifest_batches)} VRAM-safe batch(es){vendor_note}..."
            self._update_task_progress(task_id, last_step_msg, pct=20)

            # 4. Consensus Engine Evaluation (20% -> 95% with live batch callbacks)
            def on_llm_progress(step_text: str, pct: int) -> None:
                nonlocal last_step_msg
                last_step_msg = step_text
                self._update_task_progress(task_id, step_text, pct=pct)

            engine = ConsensusEngine(client=client)
            report = engine.evaluate_pipeline(
                repo_name=packed_repo.repo_name,
                repo_source=target,
                manifest_batches=manifest_batches,
                metrics=metrics,
                mode=mode,
                progress_callback=on_llm_progress,
                classification=classification,
                file_tree=packed_repo.file_tree,
            )

            # 4.5 Privacy: if the owner deleted their data while this audit ran, the queue row is gone.
            # Discard the result instead of publishing it.
            if not self._task_exists(task_id):
                logger.info(f"Task #{task_id} was removed during evaluation; result discarded.")
                return

            # 4.6 Integrity hold: never publish; ask the owner to explain specific lines instead.
            if report.integrity_hold:
                self._hold_for_explanation(task, target, report, packed_repo)
                return

            # 5. Persist to Disk and Database (96% -> 100%)
            last_step_msg = "Saving scorecard and updating rankings..."
            self._update_task_progress(task_id, last_step_msg, pct=97)
            identity, json_p, html_p, md_p, report_id = ReportStorageManager.persist_audit(
                report=report,
                target=target,
                source_path=packed_repo.source_path,
                file_list=list(packed_repo.files.keys()),
                db_path=self.db_path,
            )

            # 6. Complete task
            conn = get_db_connection(self.db_path)
            try:
                with conn:
                    now_iso = datetime.now().isoformat()
                    conn.execute(
                        """
                        UPDATE audit_queue
                        SET status = 'completed',
                            progress_message = '[PROGRESS:100%] Audit completed successfully',
                            completed_at = ?,
                            report_id = ?
                        WHERE id = ?
                        """,
                        (now_iso, report_id, task_id),
                    )
            finally:
                conn.close()

        except TaskStopped:
            logger.info(f"Task {task_id} stopped by the admin; requeued first in line, queue paused.")
            self._requeue_stopped(task_id)
        except CpuSpilloverError as spill_exc:
            logger.error(f"Task {task_id} aborted by CPU spillover / hang protection: {spill_exc}")
            self._abort_and_route_to_approval_inbox(
                task=task,
                packed_repo=packed_repo,
                last_step=last_step_msg,
                reason=str(spill_exc),
            )
        except Exception as exc:
            logger.error(f"Task {task_id} failed: {exc}", exc_info=True)
            self._fail_task(task_id, str(exc))
        finally:
            if packed_repo and packed_repo.is_temporary:
                try:
                    packed_repo.cleanup()
                except Exception as clean_err:
                    logger.warning(f"Error during repository cleanup: {clean_err}")
            try:
                client = OllamaClient()
                client.unload_all_models()
            except Exception as vram_err:
                logger.warning(f"Error releasing GPU memory: {vram_err}")
            self.current_task_info = None

    def _complete_task(self, task_id: int, message: str, status: str = "completed", report_id: Optional[int] = None) -> None:
        conn = get_db_connection(self.db_path)
        try:
            with conn:
                conn.execute(
                    "UPDATE audit_queue SET status = ?, progress_message = ?, completed_at = ?, report_id = ? WHERE id = ?",
                    (status, f"[PROGRESS:100%] {message}"[:300], datetime.now().isoformat(), report_id, task_id),
                )
        finally:
            conn.close()
        self.current_task_info = None

    def _hold_for_explanation(self, task: dict, target: str, report, packed_repo) -> None:
        """Stores the report privately and asks the owner to explain the flagged lines (neutral wording)."""
        from ..core.icons import discover_launcher_icon_relpath, get_github_raw_icon_url
        from ..storage.integrity import hold_report, locate_flagged_lines

        identity = parse_repo_identity(target)
        lines = locate_flagged_lines(packed_repo.source_path, packed_repo.files, report.integrity_concerns)
        if not lines:
            lines = [{"file": "(see repository)", "start_line": None, "end_line": None, "code": ""}]
        commit_sha = None
        try:
            import subprocess

            res = subprocess.run(["git", "-C", str(packed_repo.source_path), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=3)
            commit_sha = res.stdout.strip() or None
        except Exception:
            pass
        icon_url = None
        try:
            rel_icon = discover_launcher_icon_relpath(packed_repo.source_path, list(packed_repo.files.keys()))
            if rel_icon and identity.provider == "github":
                icon_url = get_github_raw_icon_url(identity.owner, identity.repo_name, rel_icon)
        except Exception:
            pass
        hold_report(
            report.model_dump(mode="json"), identity.owner, identity.repo_name, identity.full_slug,
            identity.canonical_url, lines, commit_sha=commit_sha, repo_icon_url=icon_url, db_path=self.db_path,
        )
        self._complete_task(
            task["id"],
            "Action required: before this report can be published, please explain a few lines of code on My Projects.",
            status="action_required",
        )

    def _review_error(self, task: dict, packed_repo) -> None:
        """One AI re-check of a disputed statement against the code at the graded commit."""
        from ..evaluator.error_review import review_disputed_statement
        from ..storage.backups import correct_statement_everywhere
        from ..storage.db import get_latest_report_for_slug
        from ..storage.integrity import get_error_report_by_task, update_error_report

        task_id = task["id"]
        er = get_error_report_by_task(task_id, db_path=self.db_path)
        if not er:
            self._fail_task(task_id, "The error report no longer exists.")
            return
        client = OllamaClient()
        if not client.is_running() and not client.ensure_server_running(timeout_sec=15.0):
            self._fail_task(task_id, "Ollama service is offline and could not be auto-started.")
            return
        latest = get_latest_report_for_slug(er["full_slug"], db_path=self.db_path) or {}
        self._update_task_progress(task_id, "Checking the disputed statement against the code...", pct=40)
        result = review_disputed_statement(client, packed_repo, latest.get("commit_sha"), er)
        now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        if not self._task_exists(task_id):
            return  # owner deleted their data meanwhile
        if result["verdict"] == "owner_is_right":
            ok = correct_statement_everywhere(
                er["full_slug"], er["statement_path"], er["statement_text"], result.get("corrected_statement"),
                db_path=self.db_path,
            )
            update_error_report(
                er["id"], db_path=self.db_path, status="corrected" if ok else "needs_review",
                ai_verdict=result["verdict"], ai_explanation=result["explanation"],
                ai_corrected_text=result.get("corrected_statement"), resolved_at=now if ok else None,
            )
            message = "The review was corrected." if ok else "Sent for manual review."
        else:
            update_error_report(
                er["id"], db_path=self.db_path, status="needs_review", ai_verdict=result["verdict"],
                ai_explanation=result["explanation"], ai_corrected_text=result.get("corrected_statement"),
            )
            message = "Sent for manual review."
        self._complete_task(task_id, message)

    def _task_exists(self, task_id: int) -> bool:
        conn = get_db_connection(self.db_path)
        try:
            return conn.execute("SELECT 1 FROM audit_queue WHERE id = ?", (task_id,)).fetchone() is not None
        finally:
            conn.close()

    def _reclassify(self, task: dict, packed_repo) -> None:
        """Reclassify-only task: re-derives category/platforms/device profile without regrading."""
        task_id = task["id"]
        client = OllamaClient()
        if not client.is_running() and not client.ensure_server_running(timeout_sec=15.0):
            self._fail_task(task_id, "Ollama service is offline and could not be auto-started.")
            return
        self._update_task_progress(task_id, "Reclassifying: detecting target platforms from build files...", pct=30)
        detection = detect_platforms(packed_repo.source_path)
        readme_excerpt = FunctionalityAnalyzer.evaluate(packed_repo.files, packed_repo.total_loc)[7]
        self._update_task_progress(task_id, "Reclassifying: running focused category & hardware classification...", pct=55)
        result = classify_project(
            client, packed_repo.source_path, detection, readme=readme_excerpt, file_tree=packed_repo.file_tree,
        )
        full_slug = task.get("full_slug") or parse_repo_identity(task["target"]).full_slug
        if not self._task_exists(task_id):
            return
        report_id = update_report_classification(
            full_slug, result.general_category, result.platforms, result.device_profile, db_path=self.db_path,
        )
        if report_id is None:
            self._fail_task(task_id, f"No graded report exists for {full_slug}; run a full audit first.")
            return
        summary = f"Reclassified as {result.general_category} on {', '.join(result.platforms) or 'unknown platforms'}"
        if result.issues:
            summary += f" ({result.issues[0]})"
        conn = get_db_connection(self.db_path)
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE audit_queue
                    SET status = 'completed', progress_message = ?, completed_at = ?, report_id = ?
                    WHERE id = ?
                    """,
                    (f"[PROGRESS:100%] {summary}"[:300], datetime.now().isoformat(), report_id, task_id),
                )
        finally:
            conn.close()
        logger.info(f"Task #{task_id}: {summary}")

    def _abort_and_route_to_approval_inbox(
        self,
        task: dict,
        packed_repo: Optional[object],
        last_step: str,
        reason: str,
    ) -> None:
        """
        Hard-Stop Failure Protection:
        1. Immediately stops the hung/CPU-spilled item and removes it from the active queue.
        2. Sends a diagnostic ticket into the Admin Approval/Appeals Inbox with full details
           on why it hung so the admin can inspect and fix/approve it.
        """
        task_id = task["id"]
        target = task["target"]
        requested_by = task.get("requested_by") or "system"
        identity = parse_repo_identity(target)
        full_slug = task.get("full_slug") or identity.full_slug

        repo_diag = ""
        if packed_repo is not None:
            files_count = len(getattr(packed_repo, "files", {}))
            total_loc = getattr(packed_repo, "total_loc", 0)
            skipped_v = getattr(packed_repo, "skipped_vendor_files", 0)
            langs = getattr(packed_repo, "language_stats", {})
            repo_diag = (
                f"\n• Ingested Files: {files_count} ({total_loc} LOC, {skipped_v} untouched vendor files skipped)"
                f"\n• Languages: {langs}"
            )

        diagnostic_message = (
            f"🛑 [AUTO-DIAGNOSTIC HARD STOP — GPU/TIMEOUT PROTECTION]\n"
            f"• Reason: {reason}\n"
            f"• Hung At Step: {last_step}\n"
            f"• Submitted By: @{requested_by}"
            f"{repo_diag}\n\n"
            f"This item was automatically stopped and removed from the active queue to prevent "
            f"multi-hour CPU offloading. Click 'Approve & Queue' above once ready to retry."
        )

        try:
            create_appeal(
                target=target,
                submitter_email=f"{requested_by}@diagnostic.letsvibe.local",
                message=diagnostic_message,
                task_id=None,
                full_slug=full_slug,
                is_profane=False,
                is_violent=False,
                db_path=self.db_path,
            )
        except Exception as e:
            logger.error(f"Failed to create diagnostic inbox entry for task #{task_id}: {e}")

        # Remove from active queue so it doesn't block subsequent tasks
        conn = get_db_connection(self.db_path)
        try:
            with conn:
                conn.execute("DELETE FROM audit_queue WHERE id = ?", (task_id,))
        except Exception as e:
            logger.error(f"Failed to remove aborted task #{task_id} from queue: {e}")
        finally:
            conn.close()
        self.current_task_info = None

    def _reject_non_vibe_task(self, task_id: int, verdict) -> None:
        conn = get_db_connection(self.db_path)
        reason_summary = "; ".join(verdict.reasons)
        now_iso = datetime.now().isoformat()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE audit_queue
                    SET status = 'rejected_non_vibe',
                        progress_message = 'Rejected: no signs that this project was vibe-coded (written by AI) were found.',
                        rejection_reason = ?,
                        completed_at = ?
                    WHERE id = ?
                    """,
                    (reason_summary, now_iso, task_id),
                )
            logger.info(f"Task #{task_id} rejected as non-vibe: {reason_summary}")
        finally:
            conn.close()
        self.current_task_info = None

    def _fail_task(self, task_id: int, error_msg: str) -> None:
        conn = get_db_connection(self.db_path)
        try:
            with conn:
                now_iso = datetime.now().isoformat()
                conn.execute(
                    """
                    UPDATE audit_queue
                    SET status = 'failed',
                        progress_message = 'Failed: ' || ?,
                        error_message = ?,
                        completed_at = ?
                    WHERE id = ?
                    """,
                    (error_msg[:100], error_msg, now_iso, task_id),
                )
        finally:
            conn.close()
        self.current_task_info = None