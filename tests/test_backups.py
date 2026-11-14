"""Rolling backups: weekly schedule, 2-copy retention, and deletions that reach every copy of the data."""

import os
import time
from pathlib import Path

import pytest

from letsvibe.storage import backups as bk
from letsvibe.storage.db import (
    get_leaderboard, get_user_by_github_id, init_db, list_projects_owned_by, resolve_db_path, save_report_to_db,
    upsert_user,
)
from letsvibe.storage.identity import parse_repo_identity

MARKER = "SECRET-MARKER-7f3a9c"


def _add(slug, verdict="ok"):
    rep = {"final_vibe_score": 70.0, "letter_grade": "C", "archetype": {"name": slug}, "factor_evaluations": {},
           "verdict_summary": verdict}
    save_report_to_db(rep, parse_repo_identity(f"https://github.com/{slug}"))


def _age(path: Path, days: float) -> None:
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def _all_bytes() -> bytes:
    live = resolve_db_path()
    files = [live, Path(str(live) + "-wal")] + bk.list_backups()
    return b"".join(f.read_bytes() for f in files if f.exists())


def _slugs_in(path: Path):
    return sorted(i["full_slug"] for i in get_leaderboard(db_path=path))


def test_weekly_schedule_and_two_copy_retention():
    _add("bk-owner/a")
    first = bk.backup_if_due()
    assert first and first.exists()
    assert bk.backup_if_due() is None  # not due again within the week
    _age(first, 8)
    second = bk.backup_if_due()
    assert second and len(bk.list_backups()) == 2
    for p in bk.list_backups():
        _age(p, 8)
    bk.backup_if_due()
    assert len(bk.list_backups()) == 2  # oldest pruned; never more than two
    for p in bk.list_backups():
        _age(p, 16)
    bk.prune_backups()
    assert bk.list_backups() == []  # nothing older than ~2 weeks survives


def test_project_deletion_reaches_every_backup_and_leaves_no_bytes():
    _add("bk-owner/private-thing", verdict=MARKER)
    _add("bk-other/keep")
    b1 = bk.create_backup()
    b2 = bk.create_backup()
    assert MARKER.encode() in _all_bytes()

    assert bk.delete_project_everywhere("bk-owner/private-thing") is True
    for path in (resolve_db_path(), b1, b2):
        assert _slugs_in(path) == ["bk-other/keep"]
    # secure_delete + checkpoint + VACUUM: the text is gone from every file, not just unlinked from tables
    assert MARKER.encode() not in _all_bytes()
    assert bk.delete_project_everywhere("bk-owner/missing") is False


def test_owner_and_account_deletion_reach_backups():
    upsert_user(github_id="55501", username="bk-owner")
    _add("bk-owner/one")
    _add("bk-owner/two")
    _add("bk-other/keep")
    backup = bk.create_backup()
    # A project that only still exists in the backup is cleaned too (backups are purged by owner)
    from letsvibe.storage.db import delete_project
    delete_project("bk-owner/two")

    assert bk.delete_owner_projects_everywhere("bk-owner") == 1
    assert list_projects_owned_by("bk-owner", db_path=backup) == []
    assert _slugs_in(backup) == ["bk-other/keep"]

    bk.delete_account_everywhere("55501", "bk-owner")
    assert get_user_by_github_id("55501") is None
    assert get_user_by_github_id("55501", db_path=backup) is None


def test_backup_that_cannot_be_cleaned_is_removed(monkeypatch):
    _add("bk-owner/x")
    backup = bk.create_backup()
    real = bk.delete_project

    def flaky(slug, db_path=None, recalc=True):
        if Path(db_path) == backup:
            raise RuntimeError("disk error")
        return real(slug, db_path=db_path, recalc=recalc)

    monkeypatch.setattr(bk, "delete_project", flaky)
    bk.delete_project_everywhere("bk-owner/x")
    assert not backup.exists()


def test_clearing_the_database_deletes_all_backups():
    _add("bk-owner/x")
    bk.create_backup()
    bk.clear_database_everywhere()
    assert bk.list_backups() == [] and get_leaderboard() == []


def test_my_data_delete_all_reaches_backups(monkeypatch):
    from starlette.testclient import TestClient

    from conftest import sign_in
    from letsvibe.web.server import app

    monkeypatch.setattr("letsvibe.web.privacy_jobs.PAUSE_BETWEEN_ITEMS_SEC", 0)
    _add("bk-owner/one", verdict=MARKER)
    _add("bk-other/keep")
    backup = bk.create_backup()
    client = TestClient(app)
    sign_in(client, "bk-owner", "55502")
    job = client.post("/api/me/projects/delete-all").json()
    for _ in range(200):
        st = client.get(f"/api/me/jobs/{job['job_id']}").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st["status"] == "done"
    assert _slugs_in(backup) == ["bk-other/keep"]
    assert MARKER.encode() not in _all_bytes()
