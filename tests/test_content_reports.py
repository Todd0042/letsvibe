"""Tests for visitor content/stolen-work reporting, auto-hiding, and admin review resolution."""

from fastapi.testclient import TestClient

from letsvibe.storage.db import get_db_connection
from letsvibe.web.server import app


def _seed_public_project(full_slug: str = "someone/cool-app"):
    owner, repo = full_slug.split("/", 1)
    conn = get_db_connection()
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO projects (owner, repo_name, full_slug, canonical_url, is_test, moderation_hidden)
                VALUES (?, ?, ?, ?, 0, 0)
                """,
                (owner, repo, full_slug, f"https://github.com/{full_slug}"),
            )
            pid = cur.lastrowid
            conn.execute(
                """
                INSERT INTO reports (
                    project_id, owner, repo_name, full_slug, final_score, letter_grade, archetype_name,
                    verdict_title, verdict_summary, general_category, platforms, total_loc, total_files,
                    rubric_version, rubric_rank, is_test, report_data_json
                )
                VALUES (?, ?, ?, ?, 80.0, 'B+', 'Web App', 'Solid App', 'Works well.', 'application', ',web,', 500, 10, 'v9.4', 904, 0, '{}')
                """,
                (pid, owner, repo, full_slug),
            )
    finally:
        conn.close()


def test_content_report_hides_project_and_admin_can_restore_or_remove():
    from conftest import sign_in

    _seed_public_project("alice/demo-app")
    anon = TestClient(app)
    user = TestClient(app)
    sign_in(user, "bob", "99", is_admin=False)
    admin = TestClient(app)
    sign_in(admin, "Todd0042", "1", is_admin=True)

    # Signed out -> 401
    assert anon.post("/api/content-reports", json={"full_slug": "alice/demo-app", "reason": "content_violation"}).status_code == 401

    # Invalid reason -> 400
    assert user.post("/api/content-reports", json={"full_slug": "alice/demo-app", "reason": "bad_vibes"}).status_code == 400

    # Visible before report
    assert any(it["full_slug"] == "alice/demo-app" for it in anon.get("/api/leaderboard").json()["items"])
    assert anon.get("/api/project/alice/demo-app").status_code == 200

    # User files report -> immediately hidden from public leaderboard & project endpoint
    res = user.post("/api/content-reports", json={
        "full_slug": "alice/demo-app",
        "reason": "stolen_work",
        "details": "Copied from my repo.",
    })
    assert res.status_code == 200
    rid = res.json()["report_id"]

    assert not any(it["full_slug"] == "alice/demo-app" for it in anon.get("/api/leaderboard").json()["items"])
    assert anon.get("/api/project/alice/demo-app").status_code == 404

    # Appears in admin review inbox & count
    reviews = admin.get("/api/admin/reviews").json()
    assert reviews["counts"]["content_reports"] == 1
    assert any(cr["id"] == rid and cr["reason"] == "stolen_work" for cr in reviews["content_reports"])

    # Non-admin cannot resolve
    assert user.post(f"/api/admin/content-reports/{rid}/restore").status_code == 403
    assert user.post(f"/api/admin/content-reports/{rid}/remove").status_code == 403

    # Admin restores -> visible again
    assert admin.post(f"/api/admin/content-reports/{rid}/restore").status_code == 200
    assert any(it["full_slug"] == "alice/demo-app" for it in anon.get("/api/leaderboard").json()["items"])
    assert anon.get("/api/project/alice/demo-app").status_code == 200

    # Report again & admin removes -> deleted from database
    rid2 = user.post("/api/content-reports", json={"full_slug": "alice/demo-app", "reason": "content_violation"}).json()["report_id"]
    assert admin.post(f"/api/admin/content-reports/{rid2}/remove").status_code == 200
    assert admin.get("/api/project/alice/demo-app").status_code == 404


def test_content_report_rate_limit():
    from conftest import sign_in

    for idx in range(1, 5):
        _seed_public_project(f"dev/app-{idx}")

    user = TestClient(app)
    sign_in(user, "reporter", "101", is_admin=False)

    for idx in range(1, 4):
        r = user.post("/api/content-reports", json={"full_slug": f"dev/app-{idx}", "reason": "stolen_work"})
        assert r.status_code == 200

    # 4th open report hits the per-user open report cap -> 429 and leaves the 4th project visible
    r4 = user.post("/api/content-reports", json={"full_slug": "dev/app-4", "reason": "stolen_work"})
    assert r4.status_code == 429
    assert user.get("/api/project/dev/app-4").status_code == 200


def test_enqueue_requires_confirmations(monkeypatch):
    import subprocess
    from conftest import sign_in

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "abc1234\tHEAD\n", "stderr": ""})(),
    )
    user = TestClient(app)
    sign_in(user, "alice", "102", is_admin=False)

    r_missing = user.post("/api/queue/enqueue", json={"target": "https://github.com/alice/my-app"})
    assert r_missing.status_code == 400

    r_partial = user.post(
        "/api/queue/enqueue",
        json={
            "target": "https://github.com/alice/my-app",
            "accepted_terms_and_ownership": True,
            "confirmed_no_explicit_content": False,
        },
    )
    assert r_partial.status_code == 400

    r_ok = user.post(
        "/api/queue/enqueue",
        json={
            "target": "https://github.com/alice/my-app",
            "accepted_terms_and_ownership": True,
            "confirmed_no_explicit_content": True,
        },
    )
    assert r_ok.status_code == 200
