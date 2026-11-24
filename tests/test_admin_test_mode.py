"""
TEST MODE (remove before launch): the admin may grade other GitHub accounts' repositories to build a larger
evaluation dataset. Those projects are visible only to the admin and never touch public grades, ranks or badges.
Also covers the report page's scan history (every grade of a project, newest first).
"""

import subprocess

import pytest
from fastapi.testclient import TestClient

from letsvibe.storage.db import (
    get_creators_leaderboard,
    get_db_connection,
    get_hall_of_fame,
    get_leaderboard,
    get_project_detail,
    save_report_to_db,
)
from letsvibe.storage.identity import parse_repo_identity
from letsvibe.web import server
from letsvibe.web.server import app


@pytest.fixture
def client():
    return TestClient(app)


def _add(slug, score, is_test=False, cat="game"):
    rep = {"final_vibe_score": score, "letter_grade": "?", "archetype": {"name": slug, "general_category": cat},
           "factor_evaluations": {}, "creativity_score": score}
    return save_report_to_db(rep, parse_repo_identity(f"https://github.com/{slug}"), is_test=is_test)


def _public_grades():
    return {i["full_slug"]: (i["letter_grade"], i["curved_score"]) for i in get_leaderboard()}


def test_test_projects_are_hidden_from_the_public_and_shown_to_the_admin():
    _add("Todd0042/mine", 70.0)
    _add("stranger/theirs", 95.0, is_test=True)

    assert [i["full_slug"] for i in get_leaderboard()] == ["Todd0042/mine"]
    admin_view = get_leaderboard(include_test=True)
    assert [i["full_slug"] for i in admin_view] == ["stranger/theirs", "Todd0042/mine"]
    assert [bool(i["is_test"]) for i in admin_view] == [True, False]

    assert get_project_detail("stranger/theirs") is None
    assert get_project_detail("stranger/theirs", include_test=True)["is_test"] == 1
    assert [c["owner"] for c in get_creators_leaderboard()] == ["Todd0042"]


def test_test_projects_never_win_badges_or_move_public_grades():
    _add("Todd0042/a", 60.0)
    _add("Todd0042/b", 80.0)
    before = _public_grades()

    _add("stranger/huge", 99.0, is_test=True)  # would be #1 everywhere if it counted

    assert _public_grades() == before
    conn = get_db_connection()
    holders = {r["full_slug"] for r in conn.execute(
        "SELECT p.full_slug FROM ranking_history h JOIN projects p ON p.id = h.project_id")}
    conn.close()
    assert "stranger/huge" not in holders and "Todd0042/b" in holders
    assert all(r["full_slug"] != "stranger/huge" for r in get_hall_of_fame())
    stats = get_project_detail("Todd0042/a")["factor_stats"]
    assert stats["count"] == 2  # the radar's average and best ignore test projects
    # The test project is still graded (against the public projects plus itself) for the admin to read
    assert get_project_detail("stranger/huge", include_test=True)["letter_grade"] != "?"


def test_test_projects_stay_hidden_until_their_owner_submits_them():
    _add("stranger/theirs", 50.0, is_test=True)
    _add("stranger/theirs", 52.0, is_test=True)  # admin re-scans carry the project's flag (server: rescan endpoint)
    assert get_project_detail("stranger/theirs") is None
    _add("stranger/theirs", 55.0, is_test=False)  # the owner submits their own repo: now it's a public project
    assert get_project_detail("stranger/theirs")["final_score"] == 55.0


def test_scan_history_lists_every_grade_and_opens_an_earlier_one():
    first = _add("Todd0042/app", 60.0)
    second = _add("Todd0042/app", 72.0)
    other = _add("Todd0042/other", 40.0)

    latest = get_project_detail("Todd0042/app")
    assert latest["report_id"] == second and latest["is_latest"] is True
    assert [h["report_id"] for h in latest["history"]] == [second, first]

    older = get_project_detail("Todd0042/app", report_id=first)
    assert older["report_id"] == first and older["final_score"] == 60.0 and older["is_latest"] is False
    assert get_project_detail("Todd0042/app", report_id=other) is None  # another project's scan is never shown


def test_api_serves_an_earlier_scan(client):
    first = _add("Todd0042/app", 60.0)
    _add("Todd0042/app", 72.0)
    r = client.get(f"/api/project/Todd0042/app?report={first}")
    assert r.status_code == 200 and r.json()["project"]["final_score"] == 60.0
    assert client.get("/api/project/Todd0042/app?report=0").status_code == 422


def _fake_ls_remote(monkeypatch):
    monkeypatch.setattr(server.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="abc123\tHEAD\n", stderr=""))


def test_admin_test_submissions_need_the_setting(client, monkeypatch):
    from auth_helpers import sign_in

    _fake_ls_remote(monkeypatch)
    sign_in(client, "Todd0042", "1", is_admin=True)
    monkeypatch.delenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", raising=False)
    assert client.post("/api/queue/enqueue", json={"target": "https://github.com/stranger/app"}).status_code == 403

    monkeypatch.setenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", "1")
    r = client.post(
        "/api/queue/enqueue",
        json={
            "target": "https://github.com/stranger/app",
            "accepted_terms_and_ownership": True,
            "confirmed_no_explicit_content": True,
        },
    )
    assert r.status_code == 200 and r.json()["is_test"] is True
    mine = client.post(
        "/api/queue/enqueue",
        json={
            "target": "https://github.com/Todd0042/app",
            "accepted_terms_and_ownership": True,
            "confirmed_no_explicit_content": True,
        },
    )
    assert mine.status_code == 200 and mine.json()["is_test"] is False

    # The public queue keeps the test grading's place in line but never names the repository
    client.cookies.clear()
    public = client.get("/api/queue").json()["items"]
    assert not any("stranger" in str(t) for t in public)
    assert any(t.get("target") == "Admin test grading" for t in public)
    task = client.get(f"/api/queue/task/{r.json()['task_id']}").json()["task"]
    assert "stranger" not in str(task)


def test_only_the_admin_gets_test_mode(client, monkeypatch):
    from auth_helpers import sign_in

    _fake_ls_remote(monkeypatch)
    monkeypatch.setenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", "1")
    sign_in(client, "someone", "77")
    assert client.post("/api/queue/enqueue", json={"target": "https://github.com/stranger/app"}).status_code == 403


def test_sharing_a_test_project_opens_its_badge_and_report_by_link_only(client):
    from auth_helpers import sign_in

    _add("Todd0042/mine", 70.0)
    _add("stranger/theirs", 95.0, is_test=True)
    assert client.get("/api/project/stranger/theirs").status_code == 404
    assert "not graded" in client.get("/badge/stranger/theirs.svg").text.lower()

    sign_in(client, "Todd0042", "1", is_admin=True)
    r = client.post("/api/admin/project/share", json={"full_slug": "stranger/theirs", "shared": True})
    assert r.status_code == 200 and r.json()["shared"] is True
    client.cookies.clear()

    assert client.get("/api/project/stranger/theirs").status_code == 200           # report opens by link
    assert "not graded" not in client.get("/badge/stranger/theirs.svg").text.lower()  # badge shows its grade
    assert [i["full_slug"] for i in client.get("/api/leaderboard").json()["items"]] == ["Todd0042/mine"]
    assert all(c["owner"] != "stranger" for c in client.get("/api/creators").json()["items"])
    conn = get_db_connection()
    holders = {r["full_slug"] for r in conn.execute(
        "SELECT p.full_slug FROM ranking_history h JOIN projects p ON p.id = h.project_id")}
    conn.close()
    assert "stranger/theirs" not in holders                                         # never wins a #1 badge

    sign_in(client, "Todd0042", "1", is_admin=True)
    client.post("/api/admin/project/share", json={"full_slug": "stranger/theirs", "shared": False})
    client.cookies.clear()
    assert client.get("/api/project/stranger/theirs").status_code == 404           # private again


def test_only_the_admin_shares_and_only_test_projects(client):
    from auth_helpers import sign_in

    _add("Todd0042/mine", 70.0)
    _add("stranger/theirs", 95.0, is_test=True)
    assert client.post("/api/admin/project/share", json={"full_slug": "stranger/theirs", "shared": True}).status_code == 403
    sign_in(client, "someone", "77")
    assert client.post("/api/admin/project/share", json={"full_slug": "stranger/theirs", "shared": True}).status_code == 403
    sign_in(client, "Todd0042", "1", is_admin=True)
    assert client.post("/api/admin/project/share", json={"full_slug": "Todd0042/mine", "shared": True}).status_code == 400
    assert client.post("/api/admin/project/share", json={"full_slug": "nobody/none", "shared": True}).status_code == 404


def test_engine_update_regrade_keeps_test_gradings_private(client):
    """'Re-grade everything' after an engine update: every project is queued once more with the new default engine,
    and an admin test grading of someone else's repo stays a test grading (a normal one would publish it)."""
    from auth_helpers import sign_in
    from letsvibe.evaluator.pipeline import get_default_engine_id

    _add("Todd0042/mine", 70.0)
    _add("stranger/theirs", 60.0, is_test=True)
    sign_in(client, "Todd0042", "1", is_admin=True)
    r = client.post("/api/admin/rescan-all", json={})
    assert r.status_code == 200 and r.json()["requeued_count"] == 2
    conn = get_db_connection()
    queued = {row["target"].rsplit("/", 2)[-2] + "/" + row["target"].rsplit("/", 1)[-1]: (bool(row["is_test"]), row["mode"])
              for row in conn.execute("SELECT target, is_test, mode FROM audit_queue")}
    conn.close()
    engine = get_default_engine_id()
    assert queued == {"Todd0042/mine": (False, engine), "stranger/theirs": (True, engine)}
    assert get_project_detail("stranger/theirs") is None  # still not public
