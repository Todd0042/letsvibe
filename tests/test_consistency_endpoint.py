"""Admin 5x consistency test endpoint and results comparison tests."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from letsvibe.storage.db import get_db_connection
from letsvibe.web import server
from letsvibe.web.server import app


@pytest.fixture
def admin_client(monkeypatch):
    from auth_helpers import sign_in
    c = TestClient(app)
    sign_in(c, "Todd0042", "1", is_admin=True)
    monkeypatch.setenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", "1")
    return c


def test_consistency_test_requires_admin_and_test_mode(monkeypatch, admin_client):
    # Signed out -> 401/403
    anon = TestClient(app)
    assert anon.post("/api/admin/queue/consistency-test", json={"target": "https://github.com/Todd0042/deciflux"}).status_code in (401, 403)
    assert anon.get("/api/admin/consistency-results").status_code in (401, 403)

    # Non-admin user -> 403
    from auth_helpers import sign_in
    user_client = TestClient(app)
    sign_in(user_client, "someoneelse", "2", is_admin=False)
    assert user_client.post("/api/admin/queue/consistency-test", json={"target": "https://github.com/Todd0042/deciflux"}).status_code == 403
    assert user_client.get("/api/admin/consistency-results").status_code == 403

    # Test mode off -> 403
    monkeypatch.delenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", raising=False)
    assert admin_client.post("/api/admin/queue/consistency-test", json={"target": "https://github.com/Todd0042/deciflux"}).status_code == 403


def test_consistency_test_rejects_non_github_targets(admin_client):
    res = admin_client.post("/api/admin/queue/consistency-test", json={"target": "/etc/passwd"})
    assert res.status_code == 400

    res2 = admin_client.post("/api/admin/queue/consistency-test", json={"target": "https://gitlab.com/foo/bar"})
    assert res2.status_code == 400


def test_consistency_test_queues_five_runs_and_computes_results(monkeypatch, admin_client):
    # Stub git ls-remote so test does not hit the network
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="deadbeef\tHEAD\n", stderr=""),
    )

    res = admin_client.post(
        "/api/admin/queue/consistency-test",
        json={
            "target": "https://github.com/Todd0042/deciflux",
            "mode": "blend",
            "project_profile": "desktop_app",
            "provenance": "vibe_coded",
            "guest_access": "not_needed",
            "user_hook": "Audio analyzer",
            "count": 5,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 5
    assert len(body["task_ids"]) == 5
    assert body["full_slug"] == "Todd0042/deciflux"

    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id, mode, force_override, is_test FROM audit_queue WHERE id IN ({}) ORDER BY id".format(
                ",".join("?" for _ in body["task_ids"])
            ),
            body["task_ids"],
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 5
    assert all(r["force_override"] == 1 and r["is_test"] == 1 and r["mode"] == "blend" for r in rows)

    results_res = admin_client.get(f"/api/admin/consistency-results?batch_id={body['batch_id']}")
    assert results_res.status_code == 200
    rdata = results_res.json()
    assert rdata["batch"]["total_runs"] == 5
    assert [r["run_index"] for r in rdata["runs"]] == [1, 2, 3, 4, 5]
