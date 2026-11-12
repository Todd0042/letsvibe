"""
Rolling database backups that honor deletion requests.

- A backup is taken weekly and only the newest KEEP_BACKUPS are kept (about two weeks of history).
- Backups are written with SQLite's VACUUM INTO, which produces a compacted copy with no free pages,
  so rows deleted before the backup cannot linger inside it.
- Every deletion (one project, all of an owner's projects, a whole account, or the entire database)
  is applied to the live database AND every backup while holding one lock, so a backup can never be
  taken half-way through a deletion. If a backup cannot be cleaned, it is deleted outright rather
  than kept with someone's data still in it.
"""

import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from .db import (
    DEFAULT_DB_PATH,
    clear_entire_database,
    delete_pending_tasks_for_owner,
    delete_project,
    delete_user_account,
    get_db_connection,
    list_projects_owned_by,
    recalculate_historical_ranks,
    resolve_db_path,
)

logger = logging.getLogger("letsvibe.backups")

BACKUP_INTERVAL_SECONDS = 7 * 24 * 3600
KEEP_BACKUPS = 2
MAX_BACKUP_AGE_SECONDS = 15 * 24 * 3600  # hard ceiling even if backups stop being taken
CHECK_EVERY_SECONDS = 3600
BACKUP_PREFIX = "letsvibe-backup-"

# Held for every backup and every deletion, so the two never interleave.
DATA_LOCK = threading.RLock()


def backup_dir(db_path: Path = DEFAULT_DB_PATH) -> Path:
    return resolve_db_path(db_path).parent / "backups"


def list_backups(db_path: Path = DEFAULT_DB_PATH) -> List[Path]:
    """Backups, newest first."""
    folder = backup_dir(db_path)
    if not folder.exists():
        return []
    return sorted(folder.glob(f"{BACKUP_PREFIX}*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


def _checkpoint(path: Path) -> None:
    """Flushes the write-ahead log into the database file and truncates it, so deleted rows do not linger there."""
    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def create_backup(db_path: Path = DEFAULT_DB_PATH) -> Path:
    live = resolve_db_path(db_path)
    folder = backup_dir(db_path)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{BACKUP_PREFIX}{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.db"
    with DATA_LOCK:
        conn = get_db_connection(live)
        try:
            conn.execute("VACUUM INTO ?", (str(target),))
        finally:
            conn.close()
    try:
        target.chmod(0o600)
    except OSError:
        pass
    logger.info("Database backup written.")
    return target


def prune_backups(db_path: Path = DEFAULT_DB_PATH) -> int:
    """Keeps the newest KEEP_BACKUPS (and nothing older than MAX_BACKUP_AGE_SECONDS). Returns files removed."""
    removed = 0
    now = time.time()
    with DATA_LOCK:
        for i, path in enumerate(list_backups(db_path)):
            if i >= KEEP_BACKUPS or now - path.stat().st_mtime > MAX_BACKUP_AGE_SECONDS:
                path.unlink(missing_ok=True)
                removed += 1
    return removed


def backup_if_due(db_path: Path = DEFAULT_DB_PATH) -> Optional[Path]:
    """Takes a backup if the newest one is a week old (or none exists), then prunes."""
    if not resolve_db_path(db_path).exists():
        return None
    newest = list_backups(db_path)
    made = None
    if not newest or time.time() - newest[0].stat().st_mtime >= BACKUP_INTERVAL_SECONDS:
        made = create_backup(db_path)
    prune_backups(db_path)
    return made


def _apply_everywhere(action: Callable[[Path], object], db_path: Path = DEFAULT_DB_PATH) -> object:
    """Runs `action` on the live database and every backup (returns the live result). Hold DATA_LOCK."""
    live = resolve_db_path(db_path)
    result = action(live)
    _checkpoint(live)
    for backup in list_backups(db_path):
        try:
            action(backup)
            _checkpoint(backup)
            # Rewrite the backup compactly so nothing deleted survives in free pages
            conn = sqlite3.connect(str(backup), timeout=10.0)
            try:
                conn.execute("VACUUM")
            finally:
                conn.close()
        except Exception as exc:  # never keep a backup we could not clean
            logger.error(f"Could not apply a deletion to a backup; removing that backup ({exc}).")
            backup.unlink(missing_ok=True)
    return result


# ---------------------------------------------------------------------------------------------------
# Deletions that must reach every copy of the data
# ---------------------------------------------------------------------------------------------------

def delete_project_everywhere(full_slug: str, db_path: Path = DEFAULT_DB_PATH, recalc: bool = True) -> bool:
    """Deletes a project from live and every backup. Returns whether it existed in the live database."""
    with DATA_LOCK:
        return bool(_apply_everywhere(lambda p: delete_project(full_slug, db_path=p, recalc=recalc), db_path))


def delete_owner_projects_everywhere(
    username: str, db_path: Path = DEFAULT_DB_PATH, on_progress: Optional[Callable[[str], None]] = None,
    pause_seconds: float = 0.0,
) -> int:
    """
    Deletes every project owned by `username` (plus its queued work) in live and in every backup.
    Backups are cleaned by owner, not by the live list, so nothing the backups hold is missed.
    Returns the number of live projects deleted.
    """
    with DATA_LOCK:
        live_slugs = [p["full_slug"] for p in list_projects_owned_by(username, db_path=db_path)]

        def purge(path: Path) -> None:
            delete_pending_tasks_for_owner(username, db_path=path)
            for slug in [p["full_slug"] for p in list_projects_owned_by(username, db_path=path)]:
                delete_project(slug, db_path=path, recalc=False)
                if path == resolve_db_path(db_path):
                    if on_progress:
                        on_progress(slug)
                    if pause_seconds:
                        time.sleep(pause_seconds)
            recalculate_historical_ranks(path)

        _apply_everywhere(purge, db_path)
        return len(live_slugs)


def delete_account_everywhere(github_id: str, username: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    with DATA_LOCK:
        _apply_everywhere(lambda p: delete_user_account(github_id, username, db_path=p), db_path)


def delete_held_everywhere(held_id: int, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Removes a held report from live and every backup (unanswered, removed by the admin, or published)."""
    from .integrity import delete_held

    with DATA_LOCK:
        return bool(_apply_everywhere(lambda p: delete_held(held_id, db_path=p), db_path))


def expire_overdue_holds(db_path: Path = DEFAULT_DB_PATH) -> int:
    """Deletes held reports whose owner did not answer in time, from live and every backup."""
    from .integrity import overdue_held_ids

    ids = overdue_held_ids(db_path)
    for held_id in ids:
        delete_held_everywhere(held_id, db_path)
    if ids:
        logger.info(f"Removed {len(ids)} unanswered held report(s).")
    return len(ids)


def correct_statement_everywhere(full_slug: str, path: str, original: str, new_text: Optional[str],
                                 db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Corrects one statement of a published report in live and every backup. Returns the live result."""
    from .integrity import correct_report_in_db

    with DATA_LOCK:
        return bool(_apply_everywhere(lambda p: correct_report_in_db(full_slug, path, original, new_text, db_path=p), db_path))


def clear_database_everywhere(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Admin reset: wipes the live database and deletes every backup."""
    with DATA_LOCK:
        clear_entire_database(db_path)
        _checkpoint(resolve_db_path(db_path))
        for backup in list_backups(db_path):
            backup.unlink(missing_ok=True)


# ---------------------------------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------------------------------

class BackupScheduler:
    """Background thread that checks hourly whether a weekly backup is due."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="letsvibe-backups", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                backup_if_due(self.db_path)
            except Exception as exc:
                logger.error(f"Scheduled backup failed: {exc}")
            try:
                expire_overdue_holds(self.db_path)
            except Exception as exc:
                logger.error(f"Expiring held reports failed: {exc}")
            self._stop.wait(CHECK_EVERY_SECONDS)
