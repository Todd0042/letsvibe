"""Signing in creates an account, so the Terms of Use (and the 18+ requirement) must be accepted first."""
import letsvibe.web.server as server
from letsvibe.core.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, create_session_token, generate_oauth_state
from letsvibe.core.legal import TERMS_STATE_PREFIX, TERMS_VERSION
from letsvibe.storage.db import delete_user_account, export_user_data, get_user_by_github_id, upsert_user
import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(server.app)


def test_sign_in_without_accepting_goes_to_the_terms_page(client, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    for params in ({}, {"accept_terms": "2020-01-01"}):
        r = client.get("/api/auth/github/login", params=params, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/sign-in"
    r = client.get("/api/auth/github/login", params={"accept_terms": TERMS_VERSION}, follow_redirects=False)
    assert "github.com/login/oauth/authorize" in r.headers["location"]
    assert client.get("/sign-in").status_code == 200


def test_acceptance_is_stored_at_sign_in_and_exported(client, monkeypatch):
    async def fake_exchange(code, scope_level="minimal"):
        return {"github_id": "880001", "username": "terms-tester", "avatar_url": None, "email": None, "scope_level": "minimal"}

    monkeypatch.setattr(server, "exchange_github_code", fake_exchange)
    monkeypatch.setenv("MAX_REGISTERED_USERS", "50")

    def callback(state):
        client.cookies.clear()
        client.cookies.set(CSRF_COOKIE_NAME, state)
        return client.get("/api/auth/github/callback", params={"code": "c", "state": state}, follow_redirects=False)

    no_terms = callback(generate_oauth_state())                         # a state that never carried acceptance
    assert no_terms.headers["location"] == "/sign-in" and get_user_by_github_id("880001") is None

    ok = callback(generate_oauth_state(scope_level=TERMS_STATE_PREFIX + TERMS_VERSION))
    assert ok.status_code == 302 and ok.headers["location"] != "/sign-in"
    user = get_user_by_github_id("880001")
    assert user["terms_version"] == TERMS_VERSION and user["terms_accepted_at"]
    exported = export_user_data("880001", "terms-tester")
    assert TERMS_VERSION in str(exported)


def test_sessions_need_the_current_terms_and_deletion_removes_the_record(client):
    upsert_user(github_id="880002", username="old-terms", terms_version="2020-01-01")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token({"github_id": "880002", "username": "old-terms", "is_admin": False}))
    assert client.get("/api/auth/me").json()["authenticated"] is False     # must sign in again and accept

    upsert_user(github_id="880002", username="old-terms", terms_version=TERMS_VERSION)
    assert client.get("/api/auth/me").json()["authenticated"] is True

    delete_user_account("880002", "old-terms")
    assert get_user_by_github_id("880002") is None                          # acceptance is deleted with the account
