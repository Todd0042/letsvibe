"""Tests for admin batch-own-repos listing and queueing endpoints."""

import httpx
from fastapi.testclient import TestClient

from letsvibe.storage.db import get_queue_items
from letsvibe.web.server import app


def test_admin_batch_own_repos_auth_and_queue(monkeypatch):
    from auth_helpers import sign_in

    anon = TestClient(app)
    user = TestClient(app)
    sign_in(user, "bob", "99", is_admin=False)
    admin = TestClient(app)
    sign_in(admin, "Todd0042", "1", is_admin=True)

    assert anon.get("/api/admin/my-repos").status_code == 403
    assert user.get("/api/admin/my-repos").status_code == 403
    assert user.post("/api/admin/queue/batch-own-repos", json={"targets": ["https://github.com/bob/a"]}).status_code == 403

    class _FakeResp:
        status_code = 200

        def json(self):
            return [
                {"name": "deciflux", "full_name": "Todd0042/deciflux", "html_url": "https://github.com/Todd0042/deciflux", "language": "Python", "fork": False, "private": False, "archived": False, "stargazers_count": 5},
                {"name": "old-fork", "full_name": "Todd0042/old-fork", "html_url": "https://github.com/Todd0042/old-fork", "language": "JavaScript", "fork": True, "private": False, "archived": False, "stargazers_count": 0},
                {"name": "archived-app", "full_name": "Todd0042/archived-app", "html_url": "https://github.com/Todd0042/archived-app", "language": "Go", "fork": False, "private": False, "archived": True, "stargazers_count": 1},
            ]

    async def _fake_get(self, url, **kwargs):
        return _FakeResp()

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

    r_list = admin.get("/api/admin/my-repos")
    assert r_list.status_code == 200
    repos = r_list.json()["repos"]
    assert [r["name"] for r in repos] == ["deciflux", "old-fork"]

    assert admin.post("/api/admin/queue/batch-own-repos", json={"targets": ["https://github.com/Todd0042/deciflux"], "mode": "no_such_engine"}).status_code == 400

    r_q = admin.post(
        "/api/admin/queue/batch-own-repos",
        json={
            "targets": [
                "https://github.com/Todd0042/deciflux",
                "https://github.com/stranger/not-mine",
            ],
            "mode": "blend",
        },
    )
    assert r_q.status_code == 200
    assert r_q.json()["queued_count"] == 1
    items = get_queue_items(limit=10)
    assert any("Todd0042/deciflux" in str(it.get("full_slug") or it.get("target")) for it in items)
    assert not any("stranger/not-mine" in str(it.get("target")) for it in items)
