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
