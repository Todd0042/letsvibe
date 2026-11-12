"""Admin "Delete all user data": removes a developer's projects and account, like their own account deletion."""
import pytest
from starlette.testclient import TestClient

import letsvibe.web.server as server
from letsvibe.core.auth import ADMIN_GITHUB_USER, SESSION_COOKIE_NAME
from letsvibe.storage.db import get_user_by_github_id, list_projects_owned_by, save_report_to_db, upsert_user
from letsvibe.storage.identity import parse_repo_identity
from tests.conftest import accepted_token


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("letsvibe.web.privacy_jobs.PAUSE_BETWEEN_ITEMS_SEC", 0)
    return TestClient(server.app)


def _add(slug):
    rep = {"final_vibe_score": 60.0, "letter_grade": "C", "archetype": {"name": slug}, "factor_evaluations": {}}
    save_report_to_db(rep, parse_repo_identity(f"https://github.com/{slug}"))


def test_admin_deletes_everything_for_a_developer(client):
    upsert_user(github_id="7101", username="grumpy-dev")
    _add("grumpy-dev/one")
    _add("grumpy-dev/two")
    client.cookies.set(SESSION_COOKIE_NAME, accepted_token("1", ADMIN_GITHUB_USER, True))

    tree = client.get("/api/admin/projects-tree").json()["tree"]
    assert any(d["developer"] == "grumpy-dev" and d["is_admin"] is False for d in tree)

    bad = client.post("/api/admin/developers/grumpy-dev/delete-all", json={"confirm_username": "grumpy"})
    assert bad.status_code == 400 and len(list_projects_owned_by("grumpy-dev")) == 2

    r = client.post("/api/admin/developers/grumpy-dev/delete-all", json={"confirm_username": "Grumpy-Dev"})
    assert r.status_code == 200 and r.json()["kind"] == "account"
    for _ in range(100):  # the deletion runs in the background; wait for it like the page does
        st = client.get(f"/api/me/jobs/{r.json()['job_id']}").json()
        if st["status"] in ("done", "failed"):
            break
        __import__("time").sleep(0.1)
    assert st["status"] == "done"
    assert list_projects_owned_by("grumpy-dev") == [] and get_user_by_github_id("7101") is None


def test_only_admins_and_never_the_admins_own_account(client):
    client.cookies.set(SESSION_COOKIE_NAME, accepted_token("7102", "regular-user"))
    assert client.post("/api/admin/developers/x/delete-all", json={"confirm_username": "x"}).status_code in (401, 403)
    client.cookies.set(SESSION_COOKIE_NAME, accepted_token("1", ADMIN_GITHUB_USER, True))
    r = client.post(f"/api/admin/developers/{ADMIN_GITHUB_USER}/delete-all", json={"confirm_username": ADMIN_GITHUB_USER})
    assert r.status_code == 400
