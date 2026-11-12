"""Privacy and security guarantees: ownership, self-service deletion, revocation, export, XSS, image proxy."""

import time

import pytest

from letsvibe.core.legal import TERMS_VERSION
from starlette.testclient import TestClient

from conftest import sign_in
from letsvibe.storage.db import (
    create_appeal, enqueue_task, get_leaderboard, get_user_by_github_id, list_projects_owned_by, save_report_to_db,
)
from letsvibe.storage.identity import parse_repo_identity
from letsvibe.web import server
from letsvibe.web.server import app

OWNER, OWNER_ID = "privacy-owner-x1", "910001"
OTHER = "privacy-other-x2"


def _add(slug: str, score: float = 70.0) -> None:
    rep = {"final_vibe_score": score, "letter_grade": "C", "archetype": {"name": slug}, "factor_evaluations": {}}
    save_report_to_db(rep, parse_repo_identity(f"https://github.com/{slug}"))


def _wait(client, job_id, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/api/me/jobs/{job_id}").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError("deletion job did not finish")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("letsvibe.web.privacy_jobs.PAUSE_BETWEEN_ITEMS_SEC", 0)
    return TestClient(app)


def test_my_projects_lists_only_own(client):
    _add(f"{OWNER}/alpha")
    _add(f"{OTHER}/beta")
    assert client.get("/api/me/projects").status_code == 401
    sign_in(client, OWNER, OWNER_ID)
    items = client.get("/api/me/projects").json()["items"]
    assert [i["full_slug"] for i in items] == [f"{OWNER}/alpha"]


def test_delete_single_project_only_if_owned(client):
    _add(f"{OWNER}/alpha")
    _add(f"{OTHER}/beta")
    sign_in(client, OWNER, OWNER_ID)
    # Someone else's project looks exactly like a missing one (no probing)
    r = client.delete(f"/api/me/projects/{OTHER}/beta")
    assert r.status_code == 404
    assert client.delete(f"/api/me/projects/{OWNER.upper()}/ALPHA").status_code == 200  # case-insensitive
    slugs = [i["full_slug"] for i in get_leaderboard()]
    assert slugs == [f"{OTHER}/beta"]


def test_delete_all_runs_as_a_batched_job(client):
    for i in range(5):
        _add(f"{OWNER}/repo{i}", 60 + i)
    _add(f"{OTHER}/keep")
    enqueue_task(f"https://github.com/{OWNER}/pending")
    sign_in(client, OWNER, OWNER_ID)
    start = client.post("/api/me/projects/delete-all").json()
    assert start["total"] == 5 and start["job_id"]
    st = _wait(client, start["job_id"])
    assert st["status"] == "done" and st["percent"] == 100
    assert list_projects_owned_by(OWNER) == []
    assert [i["full_slug"] for i in get_leaderboard()] == [f"{OTHER}/keep"]
    # Queued audits for the account are gone too, so nothing is re-published later
    from letsvibe.storage.db import get_db_connection
    conn = get_db_connection()
    assert conn.execute("SELECT COUNT(*) c FROM audit_queue WHERE owner = ?", (OWNER,)).fetchone()["c"] == 0
    conn.close()
    # The account itself still exists
    assert get_user_by_github_id(OWNER_ID)


def test_delete_account_removes_everything_and_revokes_sessions(client):
    _add(f"{OWNER}/alpha")
    _add(f"{OTHER}/beta")
    create_appeal(target=f"https://github.com/{OWNER}/alpha", submitter_email="me@example.com", message="please",
                  full_slug=f"{OWNER}/alpha")
    sign_in(client, OWNER, OWNER_ID)
    token = client.cookies.get(server.SESSION_COOKIE_NAME)

    bad = client.post("/api/me/account/delete", json={"confirm_username": "wrong"})
    assert bad.status_code == 400

    r = client.post("/api/me/account/delete", json={"confirm_username": OWNER})
    assert r.status_code == 200
    st = _wait(client, r.json()["job_id"])
    assert st["status"] == "done" and "account" in st["message"]

    assert get_user_by_github_id(OWNER_ID) is None
    assert list_projects_owned_by(OWNER) == []
    assert [i["full_slug"] for i in get_leaderboard()] == [f"{OTHER}/beta"]
    # Old session token is dead, and cannot be restored from localStorage either
    client.cookies.set(server.SESSION_COOKIE_NAME, token)
    assert client.get("/api/me/projects").status_code == 401
    # The old localStorage restore endpoint no longer exists
    assert client.post("/api/auth/restore-session", json={"token": token}).status_code in (404, 405)
    from letsvibe.storage.db import get_db_connection
    conn = get_db_connection()
    assert conn.execute("SELECT COUNT(*) c FROM appeals").fetchone()["c"] == 0
    conn.close()


def test_export_contains_my_data_only(client):
    _add(f"{OWNER}/alpha")
    _add(f"{OTHER}/beta")
    sign_in(client, OWNER, OWNER_ID)
    r = client.get("/api/me/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["account"]["github_username"] == OWNER
    assert [g["full_slug"] for g in data["graded_reports"]] == [f"{OWNER}/alpha"]


def test_reanalyze_requires_ownership(client):
    sign_in(client, OWNER, OWNER_ID)
    r = client.post("/api/project/reanalyze", json={"full_slug": f"{OTHER}/beta"})
    assert r.status_code == 403


def test_project_slug_cannot_inject_script():
    import json
    import re

    from letsvibe.web.ui import render_dashboard_html

    evil = "x';alert(1);//</script><script>alert(2)</script>"
    html = render_dashboard_html(view="project", project_slug=evil)
    m = re.search(r"<script>window\.__INITIAL_VIEW__ = (.*?); window\.__PROJECT_SLUG__ = (.*?);</script>", html)
    assert m, "injection script not found"
    # The slug is a single JSON string literal containing no raw angle brackets, and decodes back exactly
    assert "<" not in m.group(2) and ">" not in m.group(2)
    assert json.loads(m.group(2)) == evil and json.loads(m.group(1)) == "project"
    assert html.count("<script>alert(2)") == 0


def test_image_proxy_allowlist_and_purge(tmp_path, monkeypatch):
    from letsvibe.web import image_proxy as ip

    assert ip.is_allowed("https://github.com/someone.png")
    assert ip.is_allowed("https://raw.githubusercontent.com/someone/repo/HEAD/assets/icon.png")
    for bad in ("https://evil.example/a.png", "http://github.com/a.png", "https://github.com/a.png/../../etc",
                "https://raw.githubusercontent.com/a/b/HEAD/run.sh", "file:///etc/passwd", "https://127.0.0.1/a.png"):
        assert not ip.is_allowed(bad)

    cache = tmp_path / "img_cache"
    cache.mkdir()
    monkeypatch.setattr(ip, "CACHE_DIR", cache)
    tmp_path = cache
    for name, owner in (("a", "someone"), ("b", "someone"), ("c", "else")):
        (tmp_path / f"{name}.bin").write_bytes(b"x")
        (tmp_path / f"{name}.type").write_text("image/png")
        (tmp_path / f"{name}.owner").write_text(owner)
    assert ip.purge_owner("SomeOne") == 6
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.bin", "c.owner", "c.type"]

    r = TestClient(app).get("/img", params={"u": "https://evil.example/a.png"})
    assert r.status_code == 404


def test_real_emails_are_not_written_to_disk(tmp_path, monkeypatch):
    from letsvibe.core import mailer

    monkeypatch.setattr(mailer, "LOG_DIR", tmp_path)
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "1")
    monkeypatch.setenv("SMTP_USER", "u")
    mailer._dispatch_email("person@example.com", "Subject", "Body")
    assert not (tmp_path / "sent_emails.log").exists()


def test_no_email_addresses_are_collected_or_kept(client):
    from letsvibe.storage.db import get_db_connection, init_db, upsert_user

    # Data left by older versions is erased on startup
    upsert_user(github_id="1234", username="legacy-user", email="old@example.com", scope_level="email")
    create_appeal(target="https://github.com/legacy-user/x", submitter_email="old@example.com", message="m")
    init_db()
    conn = get_db_connection()
    assert conn.execute("SELECT COUNT(*) c FROM users WHERE email IS NOT NULL").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM appeals WHERE submitter_email != ''").fetchone()["c"] == 0
    conn.close()

    # A new appeal stores the GitHub username, never an email, even if one is sent
    sign_in(client, OWNER, OWNER_ID)
    r = client.post("/api/appeal/submit", json={"target": f"https://github.com/{OWNER}/app", "submitter_email": "x@y.z", "message": "Built with Cursor."})
    assert r.status_code == 200
    conn = get_db_connection()
    row = conn.execute("SELECT submitter_email, submitted_by FROM appeals WHERE id = ?", (r.json()["appeal_id"],)).fetchone()
    conn.close()
    assert row["submitter_email"] == "" and row["submitted_by"] == OWNER


def test_admin_is_also_limited_to_own_repositories(client):
    from conftest import sign_in
    from letsvibe.core.auth import ADMIN_GITHUB_USER

    sign_in(client, ADMIN_GITHUB_USER, "1", is_admin=True)
    r = client.post("/api/queue/enqueue", json={"target": "https://github.com/torvalds/linux"})
    assert r.status_code == 403
    assert client.post("/api/queue/enqueue", json={"target": "/etc"}).status_code == 400
    assert client.post("/api/project/reanalyze", json={"full_slug": "torvalds/linux"}).status_code == 403


def test_oauth_callback_requires_exact_signed_state(client, monkeypatch):
    import time as _time

    from letsvibe.core.auth import CSRF_COOKIE_NAME, generate_oauth_state

    async def fake_exchange(code, scope_level="minimal"):
        return {"github_id": "777001", "username": "oauth-tester", "avatar_url": None, "email": None, "scope_level": "minimal"}

    monkeypatch.setattr(server, "exchange_github_code", fake_exchange)
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")

    start = client.get("/api/auth/github/login", params={"accept_terms": TERMS_VERSION}, follow_redirects=False)
    state = client.cookies.get(CSRF_COOKIE_NAME)
    assert state and f"state={state}" in start.headers["location"]

    def callback(st, cookie):
        client.cookies.clear()
        if cookie is not None:
            client.cookies.set(CSRF_COOKIE_NAME, cookie)
        return client.get("/api/auth/github/callback", params={"code": "c", "state": st}, follow_redirects=False)

    assert callback(state, None).status_code == 400                     # no cookie (login CSRF)
    assert callback(state, state.split(":")[0]).status_code == 400      # partial match used to pass
    assert callback(state + "x", state + "x").status_code == 400        # tampered signature
    other = generate_oauth_state()
    assert callback(other, state).status_code == 400                    # cookie from another sign-in
    old = state.split(":")
    stale = f"{int(_time.time()) - 3600}:{old[1]}:{old[2]}:{old[3]}"
    assert callback(stale, stale).status_code == 400                    # expired / re-signed parts

    ok = callback(state, state)
    assert ok.status_code == 302 and server.SESSION_COOKIE_NAME in ok.headers.get("set-cookie", "")



def test_session_token_never_exposed_and_renews_while_active(client):
    import time as _time

    from letsvibe.core.auth import SESSION_DURATION_SECONDS, create_session_token
    from letsvibe.storage.db import upsert_user

    sign_in(client, OWNER, OWNER_ID)
    token = client.cookies.get(server.SESSION_COOKIE_NAME)
    r = client.get("/api/auth/me")
    assert r.json()["authenticated"] is True
    assert token not in r.text and "token" not in r.json()
    assert "set-cookie" not in r.headers  # fresh session: no renewal needed

    # A session issued 2 days ago is renewed for another full 30 days, still HttpOnly
    upsert_user(github_id=OWNER_ID, username=OWNER)
    old = create_session_token({"github_id": OWNER_ID, "username": OWNER, "is_admin": False,
                                "exp": int(_time.time()) + SESSION_DURATION_SECONDS - 2 * 86400})
    client.cookies.set(server.SESSION_COOKIE_NAME, old)
    r = client.get("/api/auth/me")
    cookie = r.headers.get("set-cookie", "")
    assert server.SESSION_COOKIE_NAME in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert old not in r.text


def test_admin_is_recognised_by_github_id_when_configured(monkeypatch):
    from letsvibe.core import auth

    monkeypatch.setattr(auth, "ADMIN_GITHUB_ID", "424242")
    assert auth.is_admin_user("Todd0042", "424242")
    assert auth.is_admin_user("renamed-later", "424242")
    # Someone who later claims the admin's username is NOT admin
    assert not auth.is_admin_user("Todd0042", "999")
    monkeypatch.setattr(auth, "ADMIN_GITHUB_ID", "")
    assert auth.is_admin_user("todd0042", "999")  # username fallback only when no ID is set


def test_stale_admin_session_is_rejected(client, monkeypatch):
    from letsvibe.core import auth
    from letsvibe.core.auth import create_session_token
    from letsvibe.storage.db import upsert_user

    monkeypatch.setattr(auth, "ADMIN_GITHUB_ID", "424242")
    upsert_user(github_id="999", username="Todd0042")
    client.cookies.set(server.SESSION_COOKIE_NAME,
                       create_session_token({"github_id": "999", "username": "Todd0042", "is_admin": True}))
    assert client.get("/api/admin/reviews/count").status_code == 403


def test_signups_closed_keeps_admin_and_existing_users(client, monkeypatch):
    from letsvibe.core.auth import CSRF_COOKIE_NAME
    from letsvibe.storage.db import upsert_user

    monkeypatch.setenv("MAX_REGISTERED_USERS", "0")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "id")
    assert client.get("/api/auth/registration").json() == {"open": False}

    who = {}

    async def fake_exchange(code, scope_level="minimal"):
        return {"github_id": who["id"], "username": who["name"], "avatar_url": None, "email": None, "scope_level": "minimal"}

    monkeypatch.setattr(server, "exchange_github_code", fake_exchange)

    def sign_in_as(name, gid):
        who.update(name=name, id=gid)
        client.cookies.clear()
        client.get("/api/auth/github/login", params={"accept_terms": TERMS_VERSION}, follow_redirects=False)
        state = client.cookies.get(CSRF_COOKIE_NAME)
        return client.get("/api/auth/github/callback", params={"code": "c", "state": state}, follow_redirects=False)

    newcomer = sign_in_as("brand-new-user", "31337")
    assert newcomer.status_code == 302 and "registration=closed" in newcomer.headers["location"]
    assert server.get_user_by_github_id("31337") is None
    assert sign_in_as("Todd0042", "1").headers["location"] == "/admin"   # admin always allowed
    upsert_user(github_id="2020", username="early-tester")
    assert sign_in_as("early-tester", "2020").headers["location"] == "/"  # existing accounts still sign in
