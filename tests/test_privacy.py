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


def test_symlinks_outside_repo_never_read_during_ingest_or_holds(tmp_path):
    from letsvibe.core.icons import resolve_icon_url
    from letsvibe.core.ingest import RepositoryIngestor, is_safe_repo_file
    from letsvibe.storage.integrity import locate_flagged_lines

    secret_file = tmp_path / "host_secret.txt"
    secret_file.write_text("TOP_SECRET_ENV_TOKEN=xyz987", encoding="utf-8")

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    symlink_code = repo_dir / "leaked.py"
    symlink_code.symlink_to(secret_file)
    symlink_icon = repo_dir / "icon.png"
    symlink_icon.symlink_to(secret_file)

    assert not is_safe_repo_file(repo_dir, symlink_code)
    packed = RepositoryIngestor.ingest(str(repo_dir))
    assert "leaked.py" not in packed.files
    assert "TOP_SECRET_ENV_TOKEN" not in str(packed.files)

    flagged = locate_flagged_lines(repo_dir, packed.files, [{"file": "leaked.py", "code": "TOP_SECRET_ENV_TOKEN"}])
    assert all("xyz987" not in (item.get("code") or "") for item in flagged)

    report_dir = tmp_path / "report_out"
    assert resolve_icon_url("attacker", "repo", repo_dir, report_dir) is None


def test_url_parser_and_appeal_target_spoofing_blocked(client):
    sign_in(client, OWNER, OWNER_ID)

    # Crafted URL with @host trick must be rejected
    spoofed = f"https://github.com/@evil.example/github.com/{OWNER}/repo"
    r_enq = client.post("/api/queue/enqueue", json={"target": spoofed})
    assert r_enq.status_code == 400

    # Appeal with mismatched full_slug and local target must be rejected
    r_app = client.post(
        "/api/appeal/submit",
        json={"full_slug": f"{OWNER}/repo", "target": "/etc/passwd", "message": "Please review my repo"},
    )
    assert r_app.status_code == 400


def test_admin_test_scans_and_hard_stop_tickets_hidden_from_repo_owner(client):
    from letsvibe.storage.db import get_latest_report_for_slug

    # Admin runs a secret test scan on OWNER's repo
    rep = {"final_vibe_score": 88.0, "letter_grade": "A", "archetype": {"name": "secret"}, "factor_evaluations": {}}
    save_report_to_db(rep, parse_repo_identity(f"https://github.com/{OWNER}/secret-test"), is_test=True, commit_sha="deadbeef")

    # Auto-diagnostic hard-stop ticket created by worker
    create_appeal(
        target=f"https://github.com/{OWNER}/secret-test",
        submitter_email="system-watchdog@letsvibe.local",
        submitted_by="Todd0042",
        message="🛑 [AUTO-DIAGNOSTIC HARD STOP] internal diagnostics",
        full_slug=f"{OWNER}/secret-test",
    )

    sign_in(client, OWNER, OWNER_ID)
    assert client.get("/api/me/projects").json()["items"] == []
    assert client.get("/api/me/appeals").json()["items"] == []
    exported = client.get("/api/me/export").json()
    assert exported["graded_reports"] == []
    assert exported["appeals"] == []
    assert get_latest_report_for_slug(f"{OWNER}/secret-test") is None


def test_public_project_api_strips_internal_columns_and_csrf_blocks_evil_origin(client):
    _add(f"{OWNER}/public-app", 77.0)
    r = client.get(f"/api/project/{OWNER}/public-app")
    assert r.status_code == 200
    proj = r.json()["project"]
    for forbidden in ("report_data_json", "raw_json_path", "html_report_path", "markdown_report_path", "malware_risk_score"):
        assert forbidden not in proj
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"

    # Cross-origin POST with forged Origin header is blocked
    sign_in(client, OWNER, OWNER_ID)
    r_csrf = client.post(
        "/api/me/projects/delete-all",
        headers={"Origin": "https://evil.attacker.example"},
    )
    assert r_csrf.status_code == 403


def test_me_export_includes_full_user_data_while_stripping_secrets_and_internal_fields(client, tmp_path, monkeypatch):
    from pathlib import Path
    from letsvibe.storage.db import enqueue_task, get_db_connection

    monkeypatch.chdir(tmp_path)
    slug = f"{OWNER}/full-export-app"
    rep = {
        "final_vibe_score": 84.5,
        "letter_grade": "B+",
        "verdict_title": "Solid app",
        "top_features_needing_work": ["Handle edge case in parser"],
        "pro_next_steps": ["Add integration tests"],
        "factor_evaluations": {
            "functionality": {"score": 8.5, "reasoning": "Works well", "weaknesses": ["Missing retry"]},
            "malware_risk": {"score": 9.0, "concerns": ["Internal malware note should be stripped"]},
        },
        "malware_risk_score": 0.05,
        "integrity_hold": False,
        "integrity_concerns": ["Should not leak"],
    }
    report_dir = Path("data/reports") / OWNER / "full-export-app"
    report_dir.mkdir(parents=True)
    raw_json = report_dir / "report.json"
    raw_json.write_text("{}", encoding="utf-8")
    notes_file = report_dir / "maintainer_notes.json"
    notes_file.write_text('{"notes": [{"kind": "swallowed error", "file": "app.py", "line": 42, "detail": "except Exception: pass"}]}', encoding="utf-8")

    rid = save_report_to_db(
        rep,
        parse_repo_identity(f"https://github.com/{slug}"),
        raw_json_path=str(raw_json),
        commit_sha="abc1234",
    )
    enqueue_task(
        target=f"https://github.com/{slug}",
        requested_by=OWNER,
        submission_meta={"source": "web", "notes": "check parser"},
    )
    # Also ensure an unrelated private test scan + hard-stop ticket stay excluded
    save_report_to_db(rep, parse_repo_identity(f"https://github.com/{OWNER}/hidden-test"), is_test=True, commit_sha="deadbeef")
    create_appeal(
        target=f"https://github.com/{slug}",
        submitter_email="system-watchdog@letsvibe.local",
        submitted_by="Todd0042",
        message="🛑 [AUTO-DIAGNOSTIC HARD STOP] internal",
        full_slug=slug,
    )

    sign_in(client, OWNER, OWNER_ID)
    res = client.get("/api/me/export")
    assert res.status_code == 200
    assert "no-store" in (res.headers.get("cache-control") or "")
    data = res.json()

    assert len(data["graded_reports"]) == 1
    exported_rep = data["graded_reports"][0]
    assert exported_rep["full_slug"] == slug
    assert exported_rep["full_report"]["verdict_title"] == "Solid app"
    assert exported_rep["full_report"]["top_features_needing_work"] == ["Handle edge case in parser"]
    assert data["maintainer_notes"][slug]["notes"] == [{"kind": "swallowed error", "file": "app.py", "line": 42, "detail": "except Exception: pass"}]
    # Internal server paths and safety/integrity internals must never be exported
    for forbidden in ("raw_json_path", "html_report_path", "markdown_report_path", "malware_risk_score", "integrity_hold", "integrity_concerns"):
        assert forbidden not in exported_rep
        assert forbidden not in exported_rep["full_report"]
    assert "malware_risk" not in exported_rep["full_report"]["factor_evaluations"]

    assert len(data["badges"]) >= 1
    assert any(b["full_slug"] == slug for b in data["badges"])

    assert len(data["queue_history"]) == 1
    assert '"notes": "check parser"' in (data["queue_history"][0]["submission_meta_json"] or "")
    assert data["appeals"] == []


def test_project_export_owner_and_admin_only(client, tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    slug = f"{OWNER}/exportable-repo"
    rep = {
        "final_vibe_score": 81.0,
        "letter_grade": "B",
        "verdict_title": "Good structure",
        "verdict_summary": "Clean layout.",
        "top_features_needing_work": ["Tighten error handling"],
        "pro_next_steps": ["Add CI"],
        "factor_evaluations": {
            "functionality": {"score": 8.0, "reasoning": "Runs cleanly", "strengths": ["Fast"], "weaknesses": ["No retry"]},
        },
        "malware_risk_score": 0.0,
    }
    report_dir = Path("data/reports") / OWNER / "exportable-repo"
    report_dir.mkdir(parents=True)
    raw_json = report_dir / "report.json"
    raw_json.write_text("{}", encoding="utf-8")
    (report_dir / "maintainer_notes.json").write_text(
        '{"notes": [{"kind": "unused route", "file": "api.py", "line": 10, "detail": "/old-endpoint"}]}',
        encoding="utf-8",
    )
    save_report_to_db(rep, parse_repo_identity(f"https://github.com/{slug}"), raw_json_path=str(raw_json), commit_sha="1122334")

    # Unshared admin test scan on owner's repo must stay hidden from owner
    test_slug = f"{OWNER}/secret-test-export"
    save_report_to_db(rep, parse_repo_identity(f"https://github.com/{test_slug}"), is_test=True, commit_sha="9999999")

    # Unauthenticated -> 401
    assert client.get(f"/api/projects/{OWNER}/exportable-repo/export").status_code == 401

    # Other signed-in user -> 403
    sign_in(client, OTHER, "910002")
    assert client.get(f"/api/projects/{OWNER}/exportable-repo/export").status_code == 403

    # Repo owner -> 200 JSON and Markdown
    sign_in(client, OWNER, OWNER_ID)
    r_json = client.get(f"/api/projects/{OWNER}/exportable-repo/export?format=json")
    assert r_json.status_code == 200
    assert "attachment;" in (r_json.headers.get("content-disposition") or "")
    payload = r_json.json()
    assert payload["project"]["full_slug"] == slug
    assert payload["project"]["full_report"]["verdict_title"] == "Good structure"
    assert payload["maintainer_notes"]["notes"][0]["kind"] == "unused route"
    assert "raw_json_path" not in payload["project"]
    assert "malware_risk_score" not in payload["project"]["full_report"]

    r_md = client.get(f"/api/projects/{OWNER}/exportable-repo/export?format=markdown")
    assert r_md.status_code == 200
    assert "attachment;" in (r_md.headers.get("content-disposition") or "")
    assert "# LetsVibe Report:" in r_md.text
    assert "## Maintainer Notes" in r_md.text
    assert "unused route" in r_md.text

    # Owner cannot export unshared admin test scan
    assert client.get(f"/api/projects/{OWNER}/secret-test-export/export").status_code == 404

    # Admin can export both normal and test scans
    sign_in(client, "Todd0042", "1", is_admin=True)
    assert client.get(f"/api/projects/{OWNER}/exportable-repo/export").status_code == 200
    assert client.get(f"/api/projects/{OWNER}/secret-test-export/export").status_code == 200


