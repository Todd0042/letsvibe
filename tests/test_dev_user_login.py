"""Tests for opt-in non-admin dev test user login."""

from fastapi.testclient import TestClient

from letsvibe.web.server import app


def test_dev_user_login_disabled_without_secret(monkeypatch):
    monkeypatch.delenv("LETSVIBE_DEV_TEST_USER_SECRET", raising=False)
    c = TestClient(app)
    assert c.get("/dev-user-login").status_code == 404
    assert c.post("/api/auth/dev-user-login", json={"secret": "anything"}).status_code == 404

    # Too short (<16 chars) also stays disabled
    monkeypatch.setenv("LETSVIBE_DEV_TEST_USER_SECRET", "short")
    assert c.post("/api/auth/dev-user-login", json={"secret": "short"}).status_code == 404


def test_dev_user_login_rejects_bad_secret_and_admin_impersonation(monkeypatch):
    monkeypatch.setenv("LETSVIBE_DEV_TEST_USER_SECRET", "super-secret-dev-test-token-1234")
    c = TestClient(app)

    assert c.post("/api/auth/dev-user-login", json={"secret": "wrong"}).status_code == 403

    # Refuse if misconfigured to match admin username
    monkeypatch.setenv("LETSVIBE_DEV_TEST_USER_NAME", "Todd0042")
    assert c.post("/api/auth/dev-user-login", json={"secret": "super-secret-dev-test-token-1234"}).status_code == 403


def test_dev_user_login_creates_non_admin_session(monkeypatch):
    monkeypatch.setenv("LETSVIBE_DEV_TEST_USER_SECRET", "super-secret-dev-test-token-1234")
    monkeypatch.setenv("LETSVIBE_DEV_TEST_USER_NAME", "qa_regular_user")
    c = TestClient(app)

    assert c.get("/dev-user-login").status_code == 200
    r = c.post("/api/auth/dev-user-login", json={"secret": "super-secret-dev-test-token-1234"})
    assert r.status_code == 200
    assert r.json()["is_admin"] is False
    assert r.json()["username"] == "qa_regular_user"

    me = c.get("/api/auth/me").json()
    assert me["authenticated"] is True
    assert me["user"]["is_admin"] is False
    assert me["user"]["username"] == "qa_regular_user"

    # Cannot access admin routes
    assert c.get("/api/admin/system").status_code == 403
