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


def test_page_views_recorded_for_non_admin_and_excluded_for_admin(tmp_path, monkeypatch):
    from letsvibe.storage import db as db_mod
    from letsvibe.core import auth as auth_mod

    test_db = tmp_path / "views.db"
    db_mod.init_db(test_db)
    monkeypatch.setattr(srv, "record_page_view", lambda path: db_mod.record_page_view(path, db_path=test_db))
    monkeypatch.setattr(srv, "get_page_views", lambda limit=30: db_mod.get_page_views(limit=limit, db_path=test_db))

    client = TestClient(srv.app)

    # Guest hits /, /faq, and /api/creators
    assert client.get("/").status_code == 200
    assert client.get("/faq").status_code == 200
    assert client.get("/api/creators").status_code == 200

    # Admin hits / and /faq — should NOT increment counts
    monkeypatch.setattr(srv, "is_admin_request", lambda req: req.cookies.get("test_admin") == "1")
    client.cookies.set("test_admin", "1")
    assert client.get("/").status_code == 200
    assert client.get("/faq").status_code == 200
    client.cookies.clear()

    srv.app.dependency_overrides[srv.require_admin] = lambda: {"username": "Todd0042", "is_admin": True}
    try:
        res = client.get("/api/admin/active-viewers")
    finally:
        srv.app.dependency_overrides.pop(srv.require_admin, None)

    assert res.status_code == 200
    pv_map = {item["path"]: item["views"] for item in res.json()["page_views"]}
    assert pv_map.get("/") == 1
    assert pv_map.get("/faq") == 1
    assert pv_map.get("/?view=creators") == 1
