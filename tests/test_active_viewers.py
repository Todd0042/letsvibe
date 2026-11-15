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
    assert client.get("/faq", headers={"cf-connecting-ip": "203.0.113.42", "cf-ipcountry": "US", "user-agent": "TestBrowser/1.0"}).status_code == 200

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
    visitor = next(v for v in data["viewers"] if v["label"].startswith("visitor-"))
    viewed_paths = [p["path"] for p in visitor["pages"]]
    assert "/" in viewed_paths
    assert "/faq" in viewed_paths
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


def test_visitor_journeys_record_ordered_steps_and_errors(tmp_path, monkeypatch):
    from letsvibe.storage import db as db_mod

    test_db = tmp_path / "journeys.db"
    db_mod.init_db(test_db)
    monkeypatch.setattr(
        srv,
        "record_visitor_journey_step",
        lambda vk, lbl, cty, p, sc=200: db_mod.record_visitor_journey_step(vk, lbl, cty, p, sc, db_path=test_db),
    )
    monkeypatch.setattr(
        srv,
        "get_recent_visitor_journeys",
        lambda limit_visitors=15, steps_per_visitor=12: db_mod.get_recent_visitor_journeys(
            limit_visitors=limit_visitors, steps_per_visitor=steps_per_visitor, db_path=test_db
        ),
    )

    client = TestClient(srv.app)
    hdrs = {"cf-connecting-ip": "203.0.113.77", "cf-ipcountry": "DE", "user-agent": "JourneyBot/1.0"}
    assert client.get("/", headers=hdrs).status_code == 200
    assert client.get("/faq", headers=hdrs).status_code == 200
    assert client.get("/nonexistent-page-xyz", headers=hdrs).status_code == 404

    srv.app.dependency_overrides[srv.require_admin] = lambda: {"username": "Todd0042", "is_admin": True}
    try:
        res = client.get("/api/admin/active-viewers")
    finally:
        srv.app.dependency_overrides.pop(srv.require_admin, None)

    assert res.status_code == 200
    journeys = res.json()["journeys"]
    assert len(journeys) >= 1
    j = next(item for item in journeys if item["country"] == "DE")
    assert j["has_error"] is True
    assert [(s["path"], s["status_code"]) for s in j["steps"]] == [
        ("/", 200),
        ("/faq", 200),
        ("/nonexistent-page-xyz", 404),
    ]
