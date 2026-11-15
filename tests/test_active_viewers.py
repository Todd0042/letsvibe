from fastapi.testclient import TestClient

from letsvibe.web import server as srv


def test_active_viewers_requires_admin():
    client = TestClient(srv.app)
    res = client.get("/api/admin/active-viewers")
    assert res.status_code in (401, 403)


def test_active_viewers_records_guest_and_returns_anonymized():
    srv._ACTIVE_VIEWERS.clear()
    client = TestClient(srv.app)

    guest_res = client.get("/", headers={"cf-connecting-ip": "203.0.113.42", "cf-ipcountry": "US", "user-agent": "TestBrowser/1.0"})
    assert guest_res.status_code == 200

    srv.app.dependency_overrides[srv.require_admin] = lambda: {"username": "admin", "is_admin": True}
    try:
        res = client.get("/api/admin/active-viewers")
    finally:
        srv.app.dependency_overrides.pop(srv.require_admin, None)

    assert res.status_code == 200
    data = res.json()
    assert data["count"] >= 1
    labels = [v["label"] for v in data["viewers"]]
    assert any(lbl.startswith("visitor-") for lbl in labels)
    assert "203.0.113.42" not in str(data)


def test_active_viewers_excludes_requesting_admin():
    srv._ACTIVE_VIEWERS.clear()
    srv._ACTIVE_VIEWERS["u:Todd0042"] = {
        "label": "@Todd0042",
        "country": "US",
        "path": "/api/queue",
        "last_seen": srv.time.monotonic(),
        "hits": 5,
    }
    srv._ACTIVE_VIEWERS["u:otheruser"] = {
        "label": "@otheruser",
        "country": "CA",
        "path": "/api/leaderboard",
        "last_seen": srv.time.monotonic(),
        "hits": 2,
    }

    client = TestClient(srv.app)
    srv.app.dependency_overrides[srv.require_admin] = lambda: {"username": "Todd0042", "is_admin": True}
    try:
        res = client.get("/api/admin/active-viewers")
    finally:
        srv.app.dependency_overrides.pop(srv.require_admin, None)

    assert res.status_code == 200
    data = res.json()
    labels = [v["label"] for v in data["viewers"]]
    assert "@Todd0042" not in labels
    assert "@otheruser" in labels
    assert data["count"] == 1
