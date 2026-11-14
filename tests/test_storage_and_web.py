"""
Tests for repository identity parsing, SQLite database persistence,
historical rankings preservation, and web API endpoints.
"""

import json
from pathlib import Path
import urllib.parse
import pytest
from starlette.testclient import TestClient

from letsvibe.core.icons import discover_launcher_icon_relpath
from letsvibe.storage.identity import parse_repo_identity
from letsvibe.storage.db import (
    init_db,
    get_db_connection,
    save_report_to_db,
    get_leaderboard,
    get_hall_of_fame,
    get_project_detail,
    get_projects_tree,
    get_creators_leaderboard,
    delete_project,
    clear_pending_tasks,
    enqueue_task,
    get_queue_status,
    update_queue_settings,
    recalculate_historical_ranks,
)
from letsvibe.core.auth import (
    ADMIN_GITHUB_USER,
    SESSION_COOKIE_NAME,
    create_session_token,
)
from letsvibe.web.server import app
from tests.conftest import accepted_token


def test_parse_repo_identity_remote():
    gh = parse_repo_identity("https://github.com/torvalds/linux.git")
    assert gh.provider == "github"
    assert gh.owner == "torvalds"
    assert gh.repo_name == "linux"
    assert gh.full_slug == "torvalds/linux"
    assert gh.avatar_url == "https://github.com/torvalds.png"
    assert gh.is_remote is True

    slug = parse_repo_identity("pallets/flask")
    assert slug.owner == "pallets"
    assert slug.repo_name == "flask"
    assert slug.full_slug == "pallets/flask"


def test_parse_repo_identity_local():
    local_ident = parse_repo_identity(".")
    assert local_ident.repo_name == "letsvibe"
    assert local_ident.is_remote is False
    assert local_ident.owner != ""


def test_db_persistence_and_historical_badges(tmp_path):
    test_db = tmp_path / "test_vibe.db"
    init_db(test_db)

    # Project 1: Initial Champion (Score 88.0)
    rep1 = {
        "final_vibe_score": 88.0,
        "letter_grade": "B+",
        "archetype": {"name": "CLI Tool", "primary_language": "Python"},
        "deterministic_metrics": {"total_loc": 1000, "total_files": 10},
        "factor_evaluations": {
            "security": {"score": 90.0},
            "complexity": {"score": 85.0},
        },
        "verdict_title": "Good CLI",
        "verdict_summary": "Solid CLI implementation",
    }
    ident1 = parse_repo_identity("https://github.com/user1/project-alpha")
    save_report_to_db(rep1, ident1, db_path=test_db)

    board1 = get_leaderboard(db_path=test_db)
    assert len(board1) == 1
    assert board1[0]["full_slug"] == "user1/project-alpha"
    assert board1[0]["rank"] == 1
    badges1 = [b["badge_title"] for b in board1[0]["badges"]]
    assert "#1 Overall Champion" in badges1

    # Project 2: Higher score (95.0) overtakes Project 1
    rep2 = {
        "final_vibe_score": 95.0,
        "letter_grade": "A",
        "archetype": {"name": "Web Framework", "primary_language": "Rust"},
        "deterministic_metrics": {"total_loc": 5000, "total_files": 40},
        "factor_evaluations": {
            "security": {"score": 98.0},
            "complexity": {"score": 92.0},
        },
        "verdict_title": "Top Tier",
        "verdict_summary": "Exceptional code quality",
    }
    ident2 = parse_repo_identity("https://github.com/user2/project-beta")
    save_report_to_db(rep2, ident2, db_path=test_db)

    board2 = get_leaderboard(db_path=test_db)
    assert len(board2) == 2
    assert board2[0]["full_slug"] == "user2/project-beta"
    assert board2[0]["rank"] == 1
    assert board2[1]["full_slug"] == "user1/project-alpha"
    assert board2[1]["rank"] == 2

    # Verify historical merit retention! The project page shows only current #1 badges, so project 1's
    # overtaken "#1 Overall Champion" is kept as a former #1 in the Hall of Fame (and in ranking_history).
    p1_detail = get_project_detail("user1/project-alpha", db_path=test_db)
    assert p1_detail is not None
    assert not any(b["category"] == "overall" for b in p1_detail["badges"])

    # Hall of fame includes both: the reigning #1 and the former #1
    hof = get_hall_of_fame(db_path=test_db)
    assert len(hof) >= 2
    assert any(r["full_slug"] == "user1/project-alpha" and r["category"] == "overall" and r["superseded_at"] for r in hof)

    # Test developer tree
    tree = get_projects_tree(db_path=test_db)
    assert len(tree) == 2
    dev_names = [d["developer"] for d in tree]
    assert "user1" in dev_names and "user2" in dev_names

    # Test delete project
    from letsvibe.storage.db import delete_project
    deleted = delete_project("user1/project-alpha", db_path=test_db)
    assert deleted is True
    assert get_project_detail("user1/project-alpha", db_path=test_db) is None
    tree_after = get_projects_tree(db_path=test_db)
    assert len(tree_after) == 1
    assert tree_after[0]["developer"] == "user2"


def test_web_api_endpoints():
    client = TestClient(app)
    with client:
        # GET Leaderboard (public)
        r = client.get("/api/leaderboard")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

        # GET Admin system without admin credentials -> 403 Forbidden
        r_sys_unauth = client.get("/api/admin/system")
        assert r_sys_unauth.status_code == 403

        # Authenticate as Admin
        admin_token = accepted_token("123456", ADMIN_GITHUB_USER, True)
        client.cookies.set(SESSION_COOKIE_NAME, admin_token)

        # GET Admin system with admin session
        r_sys = client.get("/api/admin/system")
        assert r_sys.status_code == 200
        sys_data = r_sys.json()
        assert "ollama_live" in sys_data
        assert isinstance(sys_data["registered_users"], int)
        assert isinstance(sys_data["max_registered_users"], int)

        # GET Developer tree
        r_tree = client.get("/api/admin/projects-tree")
        assert r_tree.status_code == 200
        assert "tree" in r_tree.json()

        # GET Dedicated Project Page
        r_proj = client.get("/project/Todd0042/deciflux")
        assert r_proj.status_code == 200
        assert "Multi-Factor Balance Radar" in r_proj.text

        # Enqueue new task with authenticated session
        r_enq = client.post(
            "/api/queue/enqueue",
            json={
                "target": f"https://github.com/{ADMIN_GITHUB_USER}/tiki-karaoke",
                "mode": "fast",
                "accepted_terms_and_ownership": True,
                "confirmed_no_explicit_content": True,
            },
        )
        assert r_enq.status_code == 200
        task_id = r_enq.json()["task_id"]

        # GET Queue
        r_q = client.get("/api/queue")
        assert r_q.status_code == 200
        items = r_q.json()["items"]
        assert any(item["id"] == task_id for item in items)

        # GET Creators Leaderboard (peak & avg)
        r_creators_peak = client.get("/api/creators?sort_by=peak")
        assert r_creators_peak.status_code == 200
        creators_data = r_creators_peak.json()
        assert creators_data["status"] == "ok"
        assert "items" in creators_data

        r_creators_avg = client.get("/api/creators?sort_by=avg")
        assert r_creators_avg.status_code == 200
        assert r_creators_avg.json()["status"] == "ok"


def test_discover_launcher_icon_logic():
    # Android launcher icon
    android_files = [
        "app/build.gradle",
        "app/src/main/res/drawable/button_submit.png",
        "app/src/main/res/mipmap-xxxhdpi/ic_launcher_round.webp",
        "app/src/main/res/mipmap-hdpi/ic_launcher.png",
    ]
    icon = discover_launcher_icon_relpath(android_files)
    assert icon == "app/src/main/res/mipmap-xxxhdpi/ic_launcher_round.webp"

    # Static/desktop icon
    web_files = [
        "README.md",
        "static/button_prev.png",
        "static/tiki-icon.png",
        "docs/screenshot.png",
    ]
    assert discover_launcher_icon_relpath(web_files) == "static/tiki-icon.png"

    # In-app UI widget buttons only (should be excluded)
    widgets_only = [
        "assets/ui/play_button.png",
        "assets/ui/pause_button.png",
        "assets/ui/sprite_arrow.png",
    ]
    assert discover_launcher_icon_relpath(widgets_only) is None


def test_creators_leaderboard_sorting(tmp_path):
    test_db = tmp_path / "test_creators.db"
    init_db(test_db)

    # Creator A: one high project (95.0), one low project (60.0) -> peak 95.0, avg 77.5
    # Creator B: two steady projects (85.0, 85.0) -> peak 85.0, avg 85.0
    def make_report(score):
        return {
            "final_vibe_score": score,
            "letter_grade": "A" if score >= 90 else "B",
            "archetype": {"name": "Test Tool", "primary_language": "Python"},
            "deterministic_metrics": {"total_loc": 500, "total_files": 5},
            "factor_evaluations": {
                "security": {"score": score},
                "complexity": {"score": score},
                "functionality": {"score": score},
                "resonance": {"score": score},
                "creativity": {"score": score},
                "ui_ux": {"score": score},
                "project_flow": {"score": score},
            },
        }

    save_report_to_db(make_report(95.0), parse_repo_identity("https://github.com/alice/project1"), db_path=test_db)
    save_report_to_db(make_report(60.0), parse_repo_identity("https://github.com/alice/project2"), db_path=test_db)
    save_report_to_db(make_report(85.0), parse_repo_identity("https://github.com/bob/project1"), db_path=test_db)
    save_report_to_db(make_report(85.0), parse_repo_identity("https://github.com/bob/project2"), db_path=test_db)

    # In peak mode, Alice (95.0) should be #1, Bob (85.0) #2
    peak_list = get_creators_leaderboard(sort_by="peak", db_path=test_db)
    assert len(peak_list) == 2
    assert peak_list[0]["owner"] == "alice"
    assert peak_list[0]["top_score"] == 95.0
    assert peak_list[1]["owner"] == "bob"

    # In avg mode, Bob (85.0) should be #1, Alice (77.5) #2
    avg_list = get_creators_leaderboard(sort_by="avg", db_path=test_db)
    assert len(avg_list) == 2
    assert avg_list[0]["owner"] == "bob"
    assert avg_list[0]["average_score"] == 85.0
    assert avg_list[1]["owner"] == "alice"
    assert avg_list[1]["average_score"] == 77.5


def test_moderation_engine():
    from letsvibe.core.moderation import analyze_appeal_text

    # Clean appeal
    clean = analyze_appeal_text("I used Cursor with Sonnet 3.5 to scaffold the whole project in 2 hours.")
    assert clean.is_clean is True
    assert clean.is_profane is False
    assert clean.is_violent is False

    # Profane appeal
    profane = analyze_appeal_text("This grading is pure bullshit and you are an asshole.")
    assert profane.is_clean is False
    assert profane.is_profane is True
    assert profane.is_violent is False

    # Violent appeal
    violent = analyze_appeal_text("Approve my project or I will hunt you down and break your neck.")
    assert violent.is_clean is False
    assert violent.is_violent is True

    # Both violent & profane
    both = analyze_appeal_text("Fuck you I will kill you.")
    assert both.is_clean is False
    assert both.is_profane is True
    assert both.is_violent is True


def test_vibe_detector():
    from letsvibe.core.vibe_detector import VibeDetector

    # Repo with .cursorrules
    files_with_cursor = {
        ".cursorrules": "Prefer Python 3.12, use FastAPI",
        "main.py": "print('hello')",
    }
    res = VibeDetector.evaluate(source_path=Path("."), files=files_with_cursor)
    assert res.is_vibe is True
    assert res.confidence >= 0.8

    # Repo with AI commit message
    git_log_vibe = "feat: scaffold entire UI with Claude Code\nCo-authored-by: Cursor <cursor@cursor.sh>"
    res_git = VibeDetector.evaluate(source_path=Path("."), files={"src/index.ts": "console.log('hi')"}, git_log=git_log_vibe)
    assert res_git.is_vibe is True

    # Repo without any vibe artifacts
    clean_files = {
        "src/util.py": "def add(a, b):\n    return a + b\n",
        "README.md": "# Simple Math\nUtility library",
    }
    res_plain = VibeDetector.evaluate(source_path=Path("."), files=clean_files, git_log="standard human commits")
    assert res_plain.is_vibe is False
    assert res_plain.verdict == "unclear" and res_plain.needs_review     # graded, then held for the admin


def test_cli_ux_analyzer():
    from letsvibe.analyzers.functionality import FunctionalityAnalyzer

    cli_files = {
        "cli.py": "import click\nimport rich\nfrom textual.app import App\n",
        "utils.py": "from colorama import Fore\n",
    }
    has_cli, has_tui, has_rich, details = FunctionalityAnalyzer.evaluate_cli_ux(cli_files)
    assert has_cli is True
    assert has_tui is True
    assert has_rich is True
    assert any("click" in d for d in details)
    assert any("textual" in d for d in details)


def test_appeals_workflow_and_api():
    from conftest import sign_in

    client = TestClient(app)

    # Appeals require sign-in, and only the repo owner may appeal
    anon = client.post("/api/appeal/submit", json={"target": "https://github.com/coder/vibe-app", "submitter_email": "a@b.c", "message": "hi"})
    assert anon.status_code == 401
    sign_in(client, "coder", "5001")
    not_mine = client.post("/api/appeal/submit", json={"target": "https://github.com/someone-else/app", "submitter_email": "a@b.c", "message": "hi"})
    assert not_mine.status_code == 403

    # 1. Submit clean appeal
    r_clean = client.post(
        "/api/appeal/submit",
        json={
            "target": "https://github.com/coder/vibe-app",
            "submitter_email": "coder@example.com",
            "message": "We built this 100% using Cursor and Claude 3.5 Sonnet. See .cursorrules in repo.",
        },
    )
    assert r_clean.status_code == 200
    data_clean = r_clean.json()
    assert data_clean["status"] == "ok"
    appeal_id_clean = data_clean["appeal_id"]

    # 2. Submit violent/profane appeal
    sign_in(client, "angry", "5002")
    r_bad = client.post(
        "/api/appeal/submit",
        json={
            "target": "https://github.com/angry/hater",
            "submitter_email": "angry@example.com",
            "message": "Fuck you Todd, your app is bullshit I will punch you in the face",
        },
    )
    assert r_bad.status_code == 200
    data_bad = r_bad.json()
    assert data_bad["status"] == "flagged"
    assert "placed at the end of the review queue" in data_bad["message"]
    assert data_bad["is_profane"] is True
    assert data_bad["is_violent"] is True

    # 3. Non-admin access to Admin appeals -> 403 Forbidden
    r_unauth = client.get("/api/admin/appeals")
    assert r_unauth.status_code == 403

    # Authenticate as Admin
    admin_token = accepted_token("123456", ADMIN_GITHUB_USER, True)
    client.cookies.set(SESSION_COOKIE_NAME, admin_token)

    # Admin GET Appeals (FIFO: clean first, flagged at bottom)
    r_appeals = client.get("/api/admin/appeals")
    assert r_appeals.status_code == 200
    items = r_appeals.json()["items"]
    assert len(items) >= 2
    # Verify the clean appeal comes before the violent/profane appeal
    clean_indices = [i for i, a in enumerate(items) if a["id"] == appeal_id_clean]
    bad_indices = [i for i, a in enumerate(items) if a["id"] == data_bad["appeal_id"]]
    assert clean_indices[0] < bad_indices[0]

    # 4. Admin Review: Approve clean appeal with custom reason
    r_review = client.post(
        "/api/admin/appeal/review",
        json={
            "appeal_id": appeal_id_clean,
            "approved": True,
            "admin_response": "Approved by Todd: verified Cursor rules and rapid commit velocity.",
        },
    )
    assert r_review.status_code == 200
    assert r_review.json()["appeal"]["status"] == "approved"

    # 5. Admin Review: Decline flagged appeal with empty custom reason -> defaults to professional boundary message
    r_decline_bad = client.post(
        "/api/admin/appeal/review",
        json={
            "appeal_id": data_bad["appeal_id"],
            "approved": False,
            "admin_response": "",
        },
    )
    assert r_decline_bad.status_code == 200
    assert r_decline_bad.json()["appeal"]["status"] == "declined"
    assert r_decline_bad.json()["appeal"]["admin_response"] == "Resubmit and rephrase appeal professionally if you want me to read it."

    # 6. Bulk decline all flagged appeals
    # (appeals are owner-only for everyone, so the admin appeals about its own repositories here)
    own = f"https://github.com/{ADMIN_GITHUB_USER}"
    client.post("/api/appeal/submit", json={"target": f"{own}/flagged1", "message": "This is total bullshit"})
    client.post("/api/appeal/submit", json={"target": f"{own}/flagged2", "message": "I will murder your server"})
    r_clean_new = client.post("/api/appeal/submit", json={"target": f"{own}/clean-wait", "message": "Used Claude 3.5 Sonnet throughout"})
    clean_new_id = r_clean_new.json()["appeal_id"]

    r_bulk = client.post("/api/admin/appeals/decline-flagged")
    assert r_bulk.status_code == 200
    assert r_bulk.json()["declined_count"] >= 2

    # Clean appeal should still be pending
    all_appeals = client.get("/api/admin/appeals").json()["items"]
    clean_match = [a for a in all_appeals if a["id"] == clean_new_id][0]
    assert clean_match["status"] == "pending"


def test_site_icon_and_static_assets():
    """Verifies that the website icon, favicon, and brand logo are properly served."""
    from letsvibe.web.server import app

    client = TestClient(app)

    # 1. Favicon route
    r_fav = client.get("/favicon.ico")
    assert r_fav.status_code == 200
    assert "image" in r_fav.headers.get("content-type", "")

    # 2. Static WebP and PNG assets
    r_webp = client.get("/static/website-icon.webp")
    assert r_webp.status_code == 200
    assert len(r_webp.content) > 1000

    r_png = client.get("/static/website-icon.png")
    assert r_png.status_code == 200
    assert len(r_png.content) > 1000

    r_touch = client.get("/static/apple-touch-icon.png")
    assert r_touch.status_code == 200

    # 3. Dashboard HTML embeds
    r_dash = client.get("/")
    assert r_dash.status_code == 200
    html = r_dash.text
    assert '<link rel="icon" type="image/webp" href="/static/website-icon.webp?v=2">' in html
    assert '<img src="/static/website-icon.webp?v=2" alt="LetsVibe" class="brand-logo-img">' in html


def test_sqlite_wal_mode_and_concurrency(tmp_path):
    """Verifies SQLite WAL journal mode and busy timeout are configured."""
    test_db = tmp_path / "test_wal.db"
    conn = get_db_connection(test_db)
    journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    conn.close()

    assert journal_mode.lower() == "wal"
    assert busy_timeout >= 5000


def test_ingest_binary_exclusion_and_size_caps(tmp_path):
    """Verifies that release binaries are omitted from ingestion and size ceilings are enforced."""
    from letsvibe.core.ingest import RepositoryIngestor

    # Create dummy repo directory with code and release binaries
    repo_dir = tmp_path / "dummy_repo"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("print('hello world')", encoding="utf-8")
    (repo_dir / "app.exe").write_bytes(b"MZ\x90\x00BinaryExe")
    (repo_dir / "archive.zip").write_bytes(b"PK\x03\x04DummyZip")
    (repo_dir / "weights.pt").write_bytes(b"PK\x03\x04PyTorchWeights")

    packed = RepositoryIngestor.ingest(str(repo_dir))
    assert "main.py" in packed.files
    assert "app.exe" not in packed.files
    assert "archive.zip" not in packed.files
    assert "weights.pt" not in packed.files


def test_preflight_url_validation():
    """Verifies /api/queue/enqueue rejects invalid hosts or non-existent repos before DB write."""
    from letsvibe.web.server import app
    client = TestClient(app)

    # 1. Unauthenticated request -> 401 Unauthorized
    r_unauth = client.post("/api/queue/enqueue", json={"target": "https://gitlab.com/user/project"})
    assert r_unauth.status_code == 401

    # Authenticate as user
    from conftest import sign_in
    sign_in(client, "test_contributor", "777")

    # 2. Non-GitHub URL
    r_bad_host = client.post("/api/queue/enqueue", json={"target": "https://gitlab.com/user/project"})
    assert r_bad_host.status_code == 400
    assert "GitHub" in r_bad_host.json()["detail"]

    # 3. Non-existent GitHub repo (fails fast via git ls-remote)
    r_bad_repo = client.post("/api/queue/enqueue", json={"target": "https://github.com/test_contributor/nonexistent-repo-98765"})
    assert r_bad_repo.status_code == 400
    assert "not found" in r_bad_repo.json()["detail"].lower()

    # 4. Someone else's repository -> 403, without touching GitHub
    r_other = client.post("/api/queue/enqueue", json={"target": "https://github.com/torvalds/linux"})
    assert r_other.status_code == 403
    assert "@test_contributor" in r_other.json()["detail"]

    # 5. Local server paths are never accepted from regular users
    r_local = client.post("/api/queue/enqueue", json={"target": "/etc"})
    assert r_local.status_code == 400


def test_custom_pause_reason_and_live_task_tracker(tmp_path):
    """Verifies Todd's custom queue pause reason and the live tracker endpoint."""
    from letsvibe.storage.db import get_task_status_with_position
    from letsvibe.web.server import app

    test_db = tmp_path / "test_tracker.db"
    init_db(test_db)

    # 1. Enqueue two tasks
    t1 = enqueue_task("repo/one", db_path=test_db)
    t2 = enqueue_task("repo/two", db_path=test_db)

    # 2. Check queue positions
    s1 = get_task_status_with_position(t1, db_path=test_db)
    s2 = get_task_status_with_position(t2, db_path=test_db)
    assert s1["queue_position"] == 1
    assert s2["queue_position"] == 2
    assert s1["is_queue_paused"] is False

    # 3. Admin pauses queue with custom reason
    custom_msg = "Currently using my computer for video editing"
    update_queue_settings(is_paused=True, pause_reason=custom_msg, db_path=test_db)

    s1_paused = get_task_status_with_position(t1, db_path=test_db)
    assert s1_paused["is_queue_paused"] is True
    assert s1_paused["queue_pause_reason"] == custom_msg

    status = get_queue_status(db_path=test_db)
    assert status["is_paused"] is True
    assert status["pause_reason"] == custom_msg

    # 4. Test Web API endpoints for pause reason and task tracking
    client = TestClient(app)
    admin_token = accepted_token("123456", ADMIN_GITHUB_USER, True)
    client.cookies.set(SESSION_COOKIE_NAME, admin_token)

    # Pause queue with custom reason via API
    r_pause = client.post(
        "/api/admin/queue/settings",
        json={"is_paused": True, "pause_reason": "Mentally drained - taking a rest"},
    )
    assert r_pause.status_code == 200
    assert r_pause.json()["current"]["is_paused"] is True
    assert r_pause.json()["current"]["pause_reason"] == "Mentally drained - taking a rest"

    # Enqueue a valid local directory task to test live tracking endpoint
    repo_dir = tmp_path / "local_repo"
    repo_dir.mkdir()
    (repo_dir / "index.py").write_text("print('test')", encoding="utf-8")

    # The web endpoint never accepts local paths; queue directly to exercise the tracker
    assert client.post("/api/queue/enqueue", json={"target": str(repo_dir)}).status_code == 400
    task_id = enqueue_task(target=str(repo_dir))

    r_track = client.get(f"/api/queue/task/{task_id}")
    assert r_track.status_code == 200
    track_data = r_track.json()["task"]
    assert track_data["id"] == task_id
    assert track_data["is_queue_paused"] is True
    assert track_data["queue_pause_reason"] == "Mentally drained - taking a rest"
    assert track_data["queue_position"] >= 1


def test_auth_endpoints_and_dual_login(monkeypatch):
    """Tests /api/auth/me, the read:user-only OAuth redirect, setup instructions when unconfigured, and logout."""
    client = TestClient(app)

    # 1. Unauthenticated /api/auth/me
    r_me = client.get("/api/auth/me")
    assert r_me.status_code == 200
    assert r_me.json()["authenticated"] is False

    # 2. Configure mock client ID
    monkeypatch.setenv("GITHUB_CLIENT_ID", "mock_oauth_client_id_123")

    # Only read:user is ever requested -- even if an old client asks for the email scope
    from letsvibe.core.legal import TERMS_VERSION
    for url in (f"/api/auth/github/login?accept_terms={TERMS_VERSION}", f"/api/auth/github/login?accept_terms={TERMS_VERSION}&scope=email"):
        r_login = client.get(url, follow_redirects=False)
        assert r_login.status_code in (302, 307)
        location = r_login.headers["location"]
        assert "github.com/login/oauth/authorize" in location
        assert "client_id=mock_oauth_client_id_123" in location
        assert "read%3Auser" in location or "read:user" in location
        assert "email" not in location.lower()

    # 4. Authenticated /api/auth/me with regular user cookie
    from conftest import sign_in
    sign_in(client, "pioneer_coder", "445566")
    r_me_auth = client.get("/api/auth/me")
    assert r_me_auth.status_code == 200
    me_data = r_me_auth.json()
    assert me_data["authenticated"] is True
    assert me_data["username"] == "pioneer_coder"
    assert me_data["is_admin"] is False
    assert "quota" in me_data

    # 5. Logout clears session cookie
    r_logout = client.post("/api/auth/logout")
    assert r_logout.status_code == 200
    assert "letsvibe_session" in r_logout.headers.get("set-cookie", "")
    assert "Max-Age=0" in r_logout.headers.get("set-cookie", "")

    # Clean client test jar and verify following request is unauthenticated
    client.cookies.delete(SESSION_COOKIE_NAME)
    r_me_post_logout = client.get("/api/auth/me")
    assert r_me_post_logout.json()["authenticated"] is False


def test_reanalysis_zero_cost_sha_check(tmp_path):
    """Verifies that submitting or re-analyzing an unchanged commit burns 0 LLM compute."""
    from letsvibe.storage.db import save_report_to_db, get_latest_report_for_slug
    from letsvibe.storage.identity import parse_repo_identity

    test_db = tmp_path / "test_sha.db"
    init_db(test_db)

    # Fake report saved with commit SHA
    dummy_sha = "abc1234567890def"
    identity = parse_repo_identity("https://github.com/pallets/click")
    report_dict = {
        "final_vibe_score": 85.0,
        "letter_grade": "B",
        "archetype": {"name": "CLI Library", "category": "CLI"},
        "deterministic_metrics": {"total_loc": 5000, "total_files": 20},
        "factor_evaluations": {},
        "verdict_title": "Clean CLI",
        "verdict_summary": "Solid architecture",
    }
    save_report_to_db(
        report_dict=report_dict,
        identity=identity,
        commit_sha=dummy_sha,
        db_path=test_db,
    )

    latest = get_latest_report_for_slug(identity.full_slug, db_path=test_db)
    assert latest is not None
    assert latest["commit_sha"] == dummy_sha


def test_worker_recover_orphaned_tasks(tmp_path):
    """Verifies that the worker resets interrupted/running tasks back to queued upon startup."""
    from letsvibe.web.worker import AuditQueueWorker

    test_db = tmp_path / "test_worker.db"
    init_db(test_db)
    conn = get_db_connection(test_db)
    with conn:
        conn.execute(
            """
            INSERT INTO audit_queue (target, mode, status, progress_message)
            VALUES ('https://github.com/foo/bar', 'blend', 'running', 'Interrupted during power loss')
            """
        )
    conn.close()

    worker = AuditQueueWorker(db_path=test_db)
    worker._recover_orphaned_tasks()

    conn = get_db_connection(test_db)
    row = conn.execute("SELECT status, progress_message FROM audit_queue LIMIT 1").fetchone()
    conn.close()

    assert row["status"] == "queued"
    assert "Recovered from restart" in row["progress_message"]


def test_admin_delete_project_cascades_reports_and_queue(tmp_path):
    """Verifies that deleting an analyzed project cleanly removes reports, unlinks/cleans queue rows, and leaves no FK errors."""
    import urllib.parse
    from letsvibe.storage.db import (
        init_db,
        save_report_to_db,
        get_db_connection,
        delete_project,
    clear_pending_tasks,
        recalculate_historical_ranks,
    )
    from letsvibe.storage.identity import parse_repo_identity

    test_db = tmp_path / "test_del.db"
    init_db(test_db)

    slug = "testauthor/cool-repo"
    identity = parse_repo_identity(f"https://github.com/{slug}")
    rep_dict = {
        "final_vibe_score": 91.5,
        "letter_grade": "A",
        "archetype": {"name": "Test Tool", "category": "CLI"},
        "deterministic_metrics": {"total_loc": 2500, "total_files": 12},
        "factor_evaluations": {},
        "verdict_title": "Top Tier",
        "verdict_summary": "Great vibes",
    }
    rep_id = save_report_to_db(rep_dict, identity, db_path=test_db)
    recalculate_historical_ranks(test_db)

    conn = get_db_connection(test_db)
    pid = conn.execute("SELECT project_id FROM reports WHERE id = ?", (rep_id,)).fetchone()["project_id"]
    conn.close()

    # Simulate an audit queue entry pointing to rep_id
    conn = get_db_connection(test_db)
    with conn:
        cur = conn.execute(
            """
            INSERT INTO audit_queue (target, owner, repo_name, full_slug, mode, status, report_id)
            VALUES (?, 'testauthor', 'cool-repo', ?, 'blend', 'completed', ?)
            """,
            (identity.canonical_url, slug, rep_id),
        )
        task_id = cur.lastrowid
        # Also simulate an appeal
        conn.execute(
            """
            INSERT INTO appeals (task_id, target, full_slug, submitter_email, message)
            VALUES (?, ?, ?, 'author@test.com', 'Please review')
            """,
            (task_id, identity.canonical_url, slug),
        )
    conn.close()

    # Now invoke delete_project
    success = delete_project(slug, db_path=test_db)
    assert success is True

    # Verify project, reports, and rankings are gone
    conn = get_db_connection(test_db)
    proj = conn.execute("SELECT * FROM projects WHERE full_slug = ?", (slug,)).fetchone()
    assert proj is None

    reports = conn.execute("SELECT * FROM reports WHERE full_slug = ?", (slug,)).fetchall()
    assert len(reports) == 0

    ranks = conn.execute("SELECT * FROM ranking_history WHERE project_id = ?", (pid,)).fetchall()
    assert len(ranks) == 0

    # Verify queue rows for that slug are cleaned
    q_rows = conn.execute("SELECT * FROM audit_queue WHERE full_slug = ?", (slug,)).fetchall()
    assert len(q_rows) == 0

    conn.close()

    # Verify deleting non-existent project returns False
    assert delete_project("non/existent", db_path=test_db) is False


def test_api_admin_delete_project_endpoint():
    """Verifies the HTTP DELETE /api/admin/project/{full_slug} endpoint with admin auth."""
    client = TestClient(app)
    admin_token = accepted_token("123456", ADMIN_GITHUB_USER, True)
    client.cookies.set(SESSION_COOKIE_NAME, admin_token)

    # First attempt deleting non-existent -> 404
    r_404 = client.delete("/api/admin/project/no_such/project_here")
    assert r_404.status_code == 404

    # Save a sample report in the test DB
    from letsvibe.storage.db import save_report_to_db, resolve_db_path
    from letsvibe.storage.identity import parse_repo_identity
    slug = "webdel/sample-repo"
    identity = parse_repo_identity(f"https://github.com/{slug}")
    rep_dict = {
        "final_vibe_score": 88.0,
        "letter_grade": "B+",
        "archetype": {"name": "Web Tool", "category": "Web"},
        "deterministic_metrics": {"total_loc": 1000, "total_files": 5},
        "factor_evaluations": {},
        "verdict_title": "Clean",
        "verdict_summary": "Passed",
    }
    save_report_to_db(rep_dict, identity, db_path=resolve_db_path())

    # Delete via API with URL encoded slug
    r_del = client.delete(f"/api/admin/project/{urllib.parse.quote(slug, safe='')}")
    assert r_del.status_code == 200
    assert r_del.json()["status"] == "ok"


def test_admin_free_vram_endpoint():
    """Verifies that the /api/admin/system/free-vram endpoint executes and returns status ok."""
    client = TestClient(app)
    admin_token = accepted_token("123456", ADMIN_GITHUB_USER, True)
    client.cookies.set(SESSION_COOKIE_NAME, admin_token)

    r = client.post("/api/admin/system/free-vram")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "unloaded_count" in data











def test_quota_limits_and_admin_exempt(tmp_path, monkeypatch):
    """2 submissions per 168h window for regular users; admin unlimited; limits come from env."""
    from letsvibe.storage.db import check_user_submission_quota, get_db_connection, count_registered_users, upsert_user

    monkeypatch.delenv("SUBMISSIONS_PER_WINDOW", raising=False)
    monkeypatch.delenv("WINDOW_HOURS", raising=False)
    db = tmp_path / "q.db"
    init_db(db)
    q = check_user_submission_quota("alice", db_path=db)
    assert q["total_limit"] == 2 and q["window_hours"] == 168 and q["allowed"]

    conn = get_db_connection(db)
    with conn:
        for i in range(2):
            conn.execute(
                "INSERT INTO audit_queue (target, requested_by_user, status, created_at) VALUES (?, 'alice', 'completed', datetime('now','localtime','-3 days'))",
                (f"https://github.com/a/r{i}",),
            )
    conn.close()
    assert check_user_submission_quota("alice", db_path=db)["allowed"] is False
    assert check_user_submission_quota("alice", is_admin=True, db_path=db)["allowed"] is True
    monkeypatch.setenv("SUBMISSIONS_PER_WINDOW", "3")
    assert check_user_submission_quota("alice", db_path=db)["allowed"] is True

    for i in range(3):
        upsert_user(str(i), f"user{i}", db_path=db)
    upsert_user("99", "Todd0042", db_path=db)
    assert count_registered_users("Todd0042", "", db_path=db) == 3


def test_qualifier_scores_persist(tmp_path):
    from letsvibe.storage.db import save_report_to_db, get_project_detail
    from letsvibe.storage.identity import parse_repo_identity

    db = tmp_path / "s.db"
    init_db(db)
    identity = parse_repo_identity("https://github.com/pallets/click")
    save_report_to_db(
        {
            "final_vibe_score": 70.0, "letter_grade": "C", "malware_risk_score": 100.0,
            "operational_safety_score": 90.0, "resonance_score": 60.0,
            "factor_evaluations": {"malware_risk": {"score": 100.0}, "maintainability": {"score": 80.0}},
        },
        identity, db_path=db,
    )
    d = get_project_detail(identity.full_slug, db_path=db)
    assert d["operational_safety_score"] == 90.0 and d["resonance_score"] == 60.0
    # The internal safety check is never stored with a published report
    assert d["malware_risk_score"] == 0
    assert "malware" not in json.dumps(d["full_report"])


def test_clear_pending_tasks_in_queue(tmp_path):
    db = tmp_path / "queue_test.db"
    init_db(db)
    enqueue_task("https://github.com/a/repo1", requested_by="user1", db_path=db)
    enqueue_task("https://github.com/b/repo2", requested_by="user2", db_path=db)

    conn = get_db_connection(db)
    assert conn.execute("SELECT COUNT(*) as c FROM audit_queue WHERE status = 'queued'").fetchone()["c"] == 2
    conn.close()

    removed = clear_pending_tasks(db_path=db)
    assert removed == 2

    conn = get_db_connection(db)
    assert conn.execute("SELECT COUNT(*) as c FROM audit_queue WHERE status = 'queued'").fetchone()["c"] == 0
    conn.close()
