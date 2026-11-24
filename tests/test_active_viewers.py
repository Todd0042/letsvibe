import pytest
from fastapi.testclient import TestClient

from letsvibe.web import server as srv



@pytest.fixture(autouse=True)
def fresh_viewer_state():
    with srv._ACTIVE_VIEWERS_LOCK:
        srv._IGNORED_VIEWERS.clear()
        srv._ACTIVE_VIEWERS.clear()
    yield

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


def test_visitor_paths_are_not_recorded(monkeypatch):
    """Since 2026-11-17 the site keeps only a live count of who is online, not each visitor's path (privacy)."""
    recorded = []
    monkeypatch.setattr(srv, "record_visitor_journey_step", lambda *a, **k: recorded.append(a))
    client = TestClient(srv.app)
    hdrs = {"cf-connecting-ip": "203.0.113.77", "cf-ipcountry": "DE", "user-agent": "JourneyBot/1.0"}
    for path in ("/", "/faq", "/nonexistent-page-xyz"):
        client.get(path, headers=hdrs)
    assert recorded == []

def test_scraper_probes_ignored_for_cooldown(tmp_path, monkeypatch):
    from letsvibe.storage import db as db_mod

    test_db = tmp_path / "scraper.db"
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
    srv._ACTIVE_VIEWERS.clear()
    srv._IGNORED_VIEWERS.clear()

    client = TestClient(srv.app)

    # 1) Direct /api/leaderboard scraper (no page load, no same-origin headers) -> ignored
    scraper_hdrs = {"cf-connecting-ip": "198.51.100.10", "user-agent": "Scraper/1.0"}
    assert client.get("/api/leaderboard", headers=scraper_hdrs).status_code == 200
    assert client.get("/", headers=scraper_hdrs).status_code == 200

    # 2) Visitor loads / then probes nonexistent /api/v1/users (404) -> ignored & purged
    probe_hdrs = {"cf-connecting-ip": "198.51.100.20", "user-agent": "Prober/1.0"}
    assert client.get("/", headers=probe_hdrs).status_code == 200
    assert client.get("/api/v1/users", headers=probe_hdrs).status_code == 404
    assert client.get("/faq", headers=probe_hdrs).status_code == 200

    # 2b) Crawler hits /robots.txt (200) -> ignored & follow-up / not counted
    bot_hdrs = {"cf-connecting-ip": "198.51.100.25", "user-agent": "CrawlBot/1.0"}
    robots_res = client.get("/robots.txt", headers=bot_hdrs)
    assert robots_res.status_code == 200
    assert "Disallow: /api/" in robots_res.text
    assert client.get("/", headers=bot_hdrs).status_code == 200

    # 3) Normal visitor loads / then /api/creators -> still tracked
    real_hdrs = {"cf-connecting-ip": "198.51.100.30", "user-agent": "RealBrowser/1.0"}
    assert client.get("/", headers=real_hdrs).status_code == 200
    assert client.get("/api/creators", headers=real_hdrs).status_code == 200

    srv.app.dependency_overrides[srv.require_admin] = lambda: {"username": "Todd0042", "is_admin": True}
    try:
        res = client.get("/api/admin/active-viewers")
    finally:
        srv.app.dependency_overrides.pop(srv.require_admin, None)

    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 1
    assert len(data["viewers"]) == 1
    assert data["viewers"][0]["path"] == "/?view=creators"
    srv._IGNORED_VIEWERS.clear()
