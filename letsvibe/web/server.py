"""
FastAPI web server for LetsVibe.
Provides modern, polished web interfaces for both users (leaderboard, podium,
hall of fame, project scorecards) and admins (queue management, pause/resume,
manual run-now triggers, and Ollama engine diagnostics).
"""

import glob
import hashlib
import hmac
import json
import os
import shutil
import subprocess  # nosec B404 - git/nvidia-smi run with fixed list arguments, never a shell
import tempfile
import threading
import time
import urllib.parse
import uuid

import httpx
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core.legal import TERMS_STATE_PREFIX, TERMS_VERSION
from ..core.limits import max_registered_users
from ..core.platforms import GENERAL_CATEGORIES, PLATFORM_GROUPS, PLATFORM_LABELS
from ..core.auth import (
    ADMIN_GITHUB_USER,
    ADMIN_GITHUB_ID,
    CSRF_COOKIE_NAME,
    GITHUB_CLIENT_ID,
    SESSION_COOKIE_NAME,
    SESSION_DURATION_SECONDS,
    build_github_authorize_url,
    create_session_token,
    exchange_github_code,
    is_admin_user,
    verify_oauth_state,
    generate_oauth_state,
    verify_session_token,
)
from ..evaluator.ollama import OllamaClient, find_ollama_binary, get_free_vram_mb
from ..storage.db import (
    clear_finished_tasks,
    clear_pending_tasks,
    export_user_data,
    list_appeals_for_user,
    list_projects_owned_by,
    DEFAULT_DB_PATH,
    check_user_submission_quota,
    count_registered_users,
    create_appeal,
    decline_all_flagged_appeals,
    enqueue_task,
    force_override_task,
    get_appeals,
    get_creators_leaderboard,
    get_db_connection,
    get_hall_of_fame,
    get_leaderboard,
    get_project_detail,
    get_projects_tree,
    get_queue_items,
    get_queue_status,
    get_task_status_with_position,
    get_latest_report_for_slug,
    get_user_by_github_id,
    get_user_by_username,
    init_db,
    review_appeal,
    update_queue_settings,
    upsert_user,
    get_all_project_targets,
    create_content_report,
    list_content_reports,
    resolve_content_report,
    ReportRateLimitError,
    toggle_project_upvote,
    get_upvote_map,
    get_user_chat_unread_count,
    get_user_chat_thread,
    send_user_chat_message,
    get_admin_chat_unread_count,
    list_admin_chat_threads,
    get_admin_chat_thread,
    reply_admin_chat_thread,
    add_chat_message_tag,
    remove_chat_message_tag,
    list_admin_chat_tags,
    list_admin_chat_messages_by_tag,
    get_page_views,
    record_page_view,
)
from ..storage.identity import parse_repo_identity
from ..core.moderation import analyze_appeal_text
from ..core.mailer import send_appeal_to_admin
from . import image_proxy
from .image_proxy import fetch_image
from .privacy_jobs import DeletionJobRunner
from ..storage.backups import (
    BackupScheduler,
    backup_dir as get_backup_dir,
    clear_database_everywhere,
    correct_statement_everywhere,
    create_backup,
    delete_held_everywhere,
    delete_project_everywhere,
    list_backups,
    prune_backups,
)
from ..storage.integrity import (
    MAX_OPEN_ERROR_REPORTS_PER_OWNER,
    count_open_error_reports,
    count_pending_reviews,
    create_error_report,
    get_error_report,
    get_held,
    get_statement,
    list_error_reports,
    list_held_for_admin,
    list_held_for_owner,
    list_statements,
    submit_hold_response,
    update_error_report,
)
from ..storage.manager import ReportStorageManager
from ..core.models import VibeReport
from .worker import AuditQueueWorker


worker: Optional[AuditQueueWorker] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker
    # 1. Clean up orphaned temporary clone directories from previous crashes
    temp_pattern = os.path.join(tempfile.gettempdir(), "letsvibe_clone_*")
    for orphan_dir in glob.glob(temp_pattern):
        try:
            shutil.rmtree(orphan_dir, ignore_errors=True)
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass

    # 2. Initialize DB and recover any zombie tasks left running
    init_db()
    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                UPDATE audit_queue
                SET status = 'queued', progress_message = 'Recovered after server restart'
                WHERE status = 'running'
                """
            )
    except Exception:  # nosec B110 - deliberate best-effort fallback
        pass
    finally:
        conn.close()

    # 3. Check and start Ollama in background if needed
    client = OllamaClient()
    if not client.is_running():
        client.ensure_server_running(timeout_sec=5.0)

    # 4. Start background queue worker
    worker = AuditQueueWorker()
    worker.start()

    # 5. Weekly rolling backups (2 kept); deletions are applied to them too
    backup_scheduler.start()

    yield

    # Shutdown
    backup_scheduler.stop()
    if worker:
        worker.stop()


privacy_jobs = DeletionJobRunner()
backup_scheduler = BackupScheduler()

app = FastAPI(
    title="LetsVibe Leaderboard & Audit Hub",
    description="100% Offline AI Repository Health & Vibe Leaderboard",
    version="0.1.0",
    lifespan=lifespan,
)

_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "LETSVIBE_ALLOWED_ORIGINS",
        "https://letsvibe.todd0042.dev,https://home.todd0042.dev,https://todd0042.dev,http://localhost:8000,http://127.0.0.1:8000,http://localhost:8088,http://127.0.0.1:8088",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


def _host_matches_request(header_url: str, request: Request) -> bool:
    """True when an Origin or Referer URL shares the target host (or is a local/test host)."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(header_url)
    except Exception:
        return False
    origin_host = (parsed.netloc or "").strip().lower()
    if not origin_host:
        return False
    req_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip().lower()
    if origin_host == req_host:
        return True
    allowed_hosts = {urlparse(o).netloc.lower() for o in _ALLOWED_ORIGINS if urlparse(o).netloc}
    allowed_hosts.update({"testserver", "localhost", "127.0.0.1"})
    return origin_host in allowed_hosts


_ACTIVE_VIEWER_WINDOW_SECONDS = 120
_ACTIVE_VIEWER_SKIP_PREFIXES = (
    "/static/",
    "/img",
    "/badge/",
    "/icons/",
    "/favicon.ico",
    "/api/health",
    "/api/queue",
    "/api/engines",
    "/api/taxonomy",
    "/api/me/",
    "/api/admin/",
    "/api/auth/",
)
_ACTIVE_VIEWERS: Dict[str, Dict[str, Any]] = {}
_ACTIVE_VIEWERS_LOCK = threading.Lock()


def _normalize_viewer_path(path: str) -> str:
    """Map SPA data endpoints to their user-facing page path for Live View."""
    if path == "/api/leaderboard":
        return "/"
    if path == "/api/creators":
        return "/?view=creators"
    if path == "/api/hall-of-fame":
        return "/?view=hof"
    if path.startswith("/api/project/") and not path.endswith("/statements"):
        return "/project/" + path[len("/api/project/"):]
    return path


def _record_active_viewer(request: Request) -> None:
    if request.method != "GET":
        return
    path = request.url.path or "/"
    if path.startswith(_ACTIVE_VIEWER_SKIP_PREFIXES):
        return
    path = _normalize_viewer_path(path)
    now = time.monotonic()
    user = get_current_user(request)
    if user and user.get("username"):
        key = f"u:{user['username']}"
        label = f"@{user['username']}"
    else:
        ip = (
            request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "")
            or "unknown"
        )
        ua = request.headers.get("user-agent", "")
        digest = hashlib.sha256(f"{ip}|{ua}".encode("utf-8", errors="ignore")).hexdigest()[:6]
        key = f"v:{digest}"
        label = f"visitor-{digest}"
    country = (request.headers.get("cf-ipcountry") or "").strip().upper()
    if country in ("", "XX", "T1"):
        country = ""
    with _ACTIVE_VIEWERS_LOCK:
        cutoff = now - _ACTIVE_VIEWER_WINDOW_SECONDS
        stale = [k for k, v in _ACTIVE_VIEWERS.items() if v["last_seen"] < cutoff]
        for k in stale:
            _ACTIVE_VIEWERS.pop(k, None)
        prev = _ACTIVE_VIEWERS.get(key)
        _ACTIVE_VIEWERS[key] = {
            "label": label,
            "country": country or (prev["country"] if prev else ""),
            "path": path,
            "last_seen": now,
            "hits": (prev["hits"] + 1) if prev else 1,
        }


def _get_active_viewers(exclude_username: Optional[str] = None) -> list[Dict[str, Any]]:
    now = time.monotonic()
    cutoff = now - _ACTIVE_VIEWER_WINDOW_SECONDS
    exclude_key = f"u:{exclude_username}" if exclude_username else None
    with _ACTIVE_VIEWERS_LOCK:
        stale = [k for k, v in _ACTIVE_VIEWERS.items() if v["last_seen"] < cutoff]
        for k in stale:
            _ACTIVE_VIEWERS.pop(k, None)
        items = [
            {
                "label": v["label"],
                "country": v["country"],
                "path": v["path"],
                "seconds_ago": max(0, int(round(now - v["last_seen"]))),
                "hits": v["hits"],
            }
            for k, v in _ACTIVE_VIEWERS.items()
            if k != exclude_key
        ]
    items.sort(key=lambda x: x["seconds_ago"])
    return items


def _record_non_admin_page_view(request: Request, path: str) -> None:
    """Increment persistent page view count unless the requester is an admin."""
    if is_admin_request(request):
        return
    try:
        record_page_view(path)
    except Exception:  # nosec B110 - never break page serving on analytics write
        pass


@app.middleware("http")
async def security_headers_and_csrf_middleware(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        if origin:
            if origin.strip().lower() == "null" or not _host_matches_request(origin, request):
                return JSONResponse({"detail": "Cross-origin state-changing request blocked."}, status_code=403)
        elif referer:
            if not _host_matches_request(referer, request):
                return JSONResponse({"detail": "Cross-origin state-changing request blocked."}, status_code=403)

    _record_active_viewer(request)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serves the official LetsVibe favicon."""
    ico_path = STATIC_DIR / "favicon.ico"
    if ico_path.exists():
        return FileResponse(ico_path, media_type="image/x-icon")
    return Response(status_code=404)


class EnqueueRequest(BaseModel):
    target: str
    mode: str = "blend"
    requested_by: str = "user"
    project_profile: str = "auto"
    provenance: str = "vibe_coded"
    guest_access: str = "auto"
    user_hook: str = ""
    accepted_terms_and_ownership: bool = False
    confirmed_no_explicit_content: bool = False


class ConsistencyTestRequest(BaseModel):
    target: str
    mode: str = "blend"
    project_profile: str = "auto"
    provenance: str = "vibe_coded"
    guest_access: str = "auto"
    user_hook: str = ""
    count: int = 5


class BatchOwnReposRequest(BaseModel):
    targets: list[str]
    mode: str = "blend"


class UpvoteRequest(BaseModel):
    full_slug: str


class QueueControlRequest(BaseModel):
    is_paused: Optional[bool] = None
    auto_process: Optional[bool] = None
    pause_reason: Optional[str] = None


# ==========================================
# Authentication & Session Dependencies
# ==========================================

def _session_still_valid(user: Dict[str, Any]) -> bool:
    """
    Signed tokens are stateless, so each request checks the account still exists (deleted accounts are
    revoked) and has accepted the current Terms of Use (core/legal.py). Otherwise the visitor is treated as
    signed out and must sign in again, accepting the terms.
    """
    gid = user.get("github_id")
    row = get_user_by_github_id(str(gid)) if gid else None
    return row is not None and row.get("terms_version") == TERMS_VERSION


def owns_repo(user: Dict[str, Any], owner: str) -> bool:
    """Everyone, including the site admin, may only submit or manage repositories under their own GitHub account."""
    return bool(owner) and owner.strip().lower() == str(user.get("username", "")).strip().lower()


def is_admin_request(request: Request) -> bool:
    """True when the visitor is signed in as the configured admin (re-checked, never just trusted from the session)."""
    user = get_current_user(request)
    return bool(user and user.get("is_admin") and is_admin_user(user.get("username", ""), user.get("github_id")))


# ------------------------------------------------------------------------------------------------------------------
# TEST MODE (remove before launch). With LETSVIBE_ADMIN_TEST_SUBMISSIONS=1 in .env, the admin may submit repositories
# owned by other GitHub accounts to build a larger evaluation dataset. Those projects are flagged is_test: only the
# admin sees them, and they never affect public grades, curves, ranks or badges (storage/db.py, peer_grades.py).
# Everyone else, the admin's own submissions included, still follows owns_repo().
# ------------------------------------------------------------------------------------------------------------------
def admin_test_submissions_enabled() -> bool:
    return os.getenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", "").strip().lower() in ("1", "true", "yes", "on")


_TEST_TASK_FIELDS = (
    "target", "owner", "repo_name", "full_slug", "progress_message", "error_message",
    "requested_by_user", "rejection_reason", "report_id", "submission_meta_json",
    "mode", "engine_version", "engine_label", "engine_is_testing", "force_override",
)
_PUBLIC_INTERNAL_TASK_FIELDS = ("submission_meta_json", "requested_by_user", "force_override")


def _hide_test_task(task: Optional[Dict[str, Any]], viewer_username: str = "") -> Optional[Dict[str, Any]]:
    """Public view of a queue entry: an admin test grading keeps its place in line but shows no repository details."""
    if not task:
        return task
    if task.get("is_test"):
        hidden = {k: v for k, v in task.items() if k not in _TEST_TASK_FIELDS}
        hidden.update(target="Admin test grading", repo_name="Admin test grading", progress_message="Grading...")
        return hidden
    is_owner = bool(
        viewer_username
        and (
            str(task.get("owner") or "").lower() == viewer_username.lower()
            or str(task.get("requested_by_user") or "").lower() == viewer_username.lower()
        )
    )
    if is_owner:
        return task
    return {k: v for k, v in task.items() if k not in _PUBLIC_INTERNAL_TASK_FIELDS}


def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """Inspects signed session cookie to authenticate the user."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        user = verify_session_token(cookie)
        if user and _session_still_valid(user):
            return user
    # No header- or env-based test bypass: tests sign real session tokens instead.
    return None


def require_admin(request: Request) -> Dict[str, Any]:
    """Strict authorization barrier for administrative actions."""
    user = get_current_user(request)
    # Re-checked against the configured admin on every request, not just trusted from the session
    if not user or not user.get("is_admin") or not is_admin_user(user.get("username", ""), user.get("github_id")):
        raise HTTPException(status_code=403, detail="Admin authorization required.")
    return user


# ==========================================
# Authentication Endpoints
# ==========================================

# Renew the session cookie once it is older than this, so active users stay signed in (sliding expiry)
SESSION_RENEW_AFTER_SECONDS = 24 * 3600


def _set_session_cookie(res: Response, request: Request, token: str) -> None:
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    res.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_DURATION_SECONDS,
        expires=datetime.now(timezone.utc) + timedelta(seconds=SESSION_DURATION_SECONDS),
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )


@app.get("/api/auth/me")
async def api_auth_me(request: Request):
    """
    Returns the signed-in user's profile, role and quota. The session token itself is never returned:
    it stays in the HttpOnly cookie. Sessions are renewed here while the user keeps visiting.
    """
    user = get_current_user(request)
    if not user:
        return {"logged_in": False, "authenticated": False, "user": None}

    username = user.get("username", "")
    is_admin = bool(user.get("is_admin", False))
    quota = check_user_submission_quota(username=username, is_admin=is_admin)

    res = JSONResponse({
        "logged_in": True,
        "authenticated": True,
        "username": username,
        "is_admin": is_admin,
        "quota": quota,
        "user": {
            "github_id": user.get("github_id"),
            "username": username,
            "avatar_url": user.get("avatar_url"),
            "is_admin": is_admin,
            "is_beta_pioneer": user.get("is_beta_pioneer", True),
            "quota": quota,
        },
    })
    expires_at = int(user.get("exp") or 0)
    if expires_at and expires_at - time.time() < SESSION_DURATION_SECONDS - SESSION_RENEW_AFTER_SECONDS:
        payload = {k: v for k, v in user.items() if k != "exp"}
        payload["is_admin"] = is_admin_user(username, user.get("github_id"))
        _set_session_cookie(res, request, create_session_token(payload))
    return res


@app.get("/api/auth/registration")
async def api_registration_status():
    """Whether new accounts can currently be created (existing accounts can always sign in)."""
    return {"open": count_registered_users(ADMIN_GITHUB_USER, ADMIN_GITHUB_ID) < max_registered_users()}


@app.get("/api/auth/github/login")
async def api_github_login(request: Request, accept_terms: str = Query("", max_length=40)):
    """
    Starts GitHub sign-in (read:user only) with CSRF protection. Signing in creates an account, so the
    current Terms of Use (and the 18+ age requirement) must be accepted first; the acceptance travels through
    GitHub inside the signed state and is stored on the account at the callback.
    """
    if accept_terms != TERMS_VERSION:
        return RedirectResponse(url="/sign-in", status_code=302)
    url, state_token = build_github_authorize_url(state=generate_oauth_state(scope_level=TERMS_STATE_PREFIX + TERMS_VERSION))
    res = RedirectResponse(url=url, status_code=302)
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    res.set_cookie(
        CSRF_COOKIE_NAME,
        state_token,  # exact value; compared in full on callback
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )
    return res


@app.get("/api/auth/github/callback")
async def api_github_callback(code: str, state: str, request: Request):
    """Handles OAuth callback, exchanges code, persists Beta Pioneer user, and issues signed session."""
    # Login CSRF protection: the state must carry our valid, unexpired signature AND exactly match the
    # cookie set on this browser when sign-in started (constant-time comparison, no partial matches).
    is_valid_state, carried = verify_oauth_state(state)
    cookie_state = request.cookies.get(CSRF_COOKIE_NAME, "")
    if not (is_valid_state and cookie_state and hmac.compare_digest(cookie_state.encode(), state.encode())):
        raise HTTPException(status_code=400, detail="Sign-in could not be verified. Please start the GitHub sign-in again.")
    if carried != TERMS_STATE_PREFIX + TERMS_VERSION:  # terms not accepted (or an older version): accept first
        return RedirectResponse(url="/sign-in", status_code=302)

    try:
        profile = await exchange_github_code(code)
    except Exception:
        raise HTTPException(status_code=400, detail="GitHub sign-in failed. Please try again.")

    # Determine Admin status
    is_admin = is_admin_user(profile["username"], profile["github_id"])

    # Registration cap: block NEW non-admin sign-ups once MAX_REGISTERED_USERS is reached
    if not is_admin and not get_user_by_github_id(profile["github_id"]):
        if count_registered_users(ADMIN_GITHUB_USER, ADMIN_GITHUB_ID) >= max_registered_users():
            return RedirectResponse(url="/?registration=closed", status_code=302)

    # Persist user with Beta Pioneer flair
    upsert_user(
        github_id=profile["github_id"],
        username=profile["username"],
        avatar_url=profile["avatar_url"],
        email=None,
        scope_level=profile["scope_level"],
        is_beta_pioneer=False,
        bonus_quota=0,
        terms_version=TERMS_VERSION,
    )

    session_payload = {
        "github_id": profile["github_id"],
        "username": profile["username"],
        "avatar_url": profile["avatar_url"],
        "is_admin": is_admin,
        "is_beta_pioneer": False,
    }
    session_token = create_session_token(session_payload)

    target_redirect = "/admin" if is_admin else "/"
    res = RedirectResponse(url=target_redirect, status_code=302)
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    expires_dt = datetime.now(timezone.utc) + timedelta(seconds=SESSION_DURATION_SECONDS)
    res.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=SESSION_DURATION_SECONDS,
        expires=expires_dt,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )
    res.delete_cookie(CSRF_COOKIE_NAME, path="/")
    return res


@app.post("/api/auth/logout")
async def api_logout():
    """Logs out user by clearing the session cookie."""
    res = JSONResponse({"status": "ok", "message": "Logged out successfully."})
    res.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return res


class DevUserLoginRequest(BaseModel):
    secret: str


def _dev_test_user_secret() -> str:
    val = os.getenv("LETSVIBE_DEV_TEST_USER_SECRET", "").strip()
    return val if len(val) >= 16 else ""


@app.get("/dev-user-login", response_class=HTMLResponse, include_in_schema=False)
async def dev_user_login_page():
    """Minimal non-admin test-user login page; returns 404 unless LETSVIBE_DEV_TEST_USER_SECRET is set."""
    if not _dev_test_user_secret():
        raise HTTPException(status_code=404, detail="Not found")
    return HTMLResponse(
        """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dev Test User Login</title></head>
<body style="background:#070c1a;color:#f1f5f9;font-family:system-ui,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;">
  <form id="f" style="background:#0f172a;border:1px solid #334155;border-radius:12px;padding:24px;width:320px;">
    <h2 style="margin:0 0 8px;font-size:1.1rem;">Non-Admin Test User Login</h2>
    <p style="margin:0 0 14px;font-size:0.82rem;color:#94a3b8;">Signs into a standard (non-admin) test account.</p>
    <input id="s" type="password" placeholder="Dev test user secret" required style="width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #334155;background:#020617;color:#fff;margin-bottom:12px;">
    <button type="submit" style="width:100%;padding:10px;border-radius:8px;border:0;background:#38bdf8;color:#020617;font-weight:700;cursor:pointer;">Sign in as Test User</button>
    <p id="err" style="color:#f87171;font-size:0.8rem;margin:10px 0 0;" hidden></p>
  </form>
  <script>
    document.getElementById('f').addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = document.getElementById('err');
      err.hidden = true;
      const res = await fetch('/api/auth/dev-user-login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({secret: document.getElementById('s').value})
      });
      if (res.ok) { window.location.href = '/'; return; }
      const d = await res.json().catch(() => ({}));
      err.textContent = d.detail || 'Login failed';
      err.hidden = false;
    });
  </script>
</body></html>""",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/auth/dev-user-login")
async def api_dev_user_login(req: DevUserLoginRequest, request: Request):
    """
    Opt-in non-admin test account login for local/private-browser QA when GitHub blocks test signups.
    Disabled (404) unless LETSVIBE_DEV_TEST_USER_SECRET (>= 16 chars) is configured in .env.
    Never grants admin privileges.
    """
    expected = _dev_test_user_secret()
    if not expected:
        raise HTTPException(status_code=404, detail="Not found")
    if not hmac.compare_digest((req.secret or "").encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Invalid dev test user secret.")

    username = (os.getenv("LETSVIBE_DEV_TEST_USER_NAME", "letsvibe_test_user") or "letsvibe_test_user").strip()
    github_id = "dev-test-user-01"
    if is_admin_user(username, github_id):
        raise HTTPException(status_code=403, detail="Dev test user cannot share identity with the admin account.")

    upsert_user(
        github_id=github_id,
        username=username,
        avatar_url="https://github.com/identicons/letsvibe.png",
        email=None,
        scope_level="minimal",
        is_beta_pioneer=False,
        bonus_quota=0,
        terms_version=TERMS_VERSION,
    )

    session_token = create_session_token({
        "github_id": github_id,
        "username": username,
        "avatar_url": "https://github.com/identicons/letsvibe.png",
        "is_admin": False,
        "is_beta_pioneer": False,
    })
    res = JSONResponse({"status": "ok", "username": username, "is_admin": False})
    _set_session_cookie(res, request, session_token)
    return res


# ==========================================
# API Endpoints
# ==========================================

@app.get("/img", include_in_schema=False)
async def api_image_proxy(u: str = Query(..., max_length=500), nocache: bool = False):
    """Same-origin proxy for GitHub avatars/icons/screenshots so visitors' browsers never contact GitHub."""
    from fastapi.concurrency import run_in_threadpool

    try:
        result = await run_in_threadpool(fetch_image, u, not nocache)
    except httpx.HTTPError:
        result = None
    if not result:
        raise HTTPException(status_code=404, detail="Image unavailable.")
    body, ctype = result
    headers = {
        "Cache-Control": "public, max-age=86400",
        "X-Content-Type-Options": "nosniff",
        # SVGs can contain script: never let a proxied image run anything
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    }
    return Response(content=body, media_type=ctype, headers=headers)


@app.get("/api/projects/{owner}/{repo}/maintainer-notes")
async def api_maintainer_notes(owner: str, repo: str, request: Request):
    """Code-health notes (never graded), for the project's owner or the admin only."""
    import re
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to see maintainer notes.")
    is_admin = bool(user.get("is_admin")) and is_admin_user(user.get("username", ""), user.get("github_id"))
    if not is_admin and (user.get("username") or "").lower() != owner.lower():
        raise HTTPException(status_code=403, detail="Maintainer notes are only shown to the project's owner.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", owner) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo):
        raise HTTPException(status_code=404, detail="No maintainer notes.")
    f = Path("data/reports") / re.sub(r"[^\w\-.]", "_", owner) / re.sub(r"[^\w\-.]", "_", repo) / "maintainer_notes.json"
    if not f.is_file():
        return {"available": False, "notes": []}
    data = json.loads(f.read_text())
    return {"available": True, "lines_of_code": data.get("lines_of_code", 0), "notes": data.get("notes", [])}


def _render_project_export_markdown(project: Dict[str, Any], notes_data: Optional[Dict[str, Any]]) -> str:
    """Renders a portable Markdown export of a project's report and maintainer notes."""
    rep = project.get("full_report") or {}
    slug = project.get("full_slug") or f"{project.get('owner', '')}/{project.get('repo_name', '')}"
    lines = [
        f"# LetsVibe Report: {slug}",
        "",
        f"- **Score:** {project.get('curved_score', project.get('final_score'))} ({project.get('curved_letter_grade', project.get('letter_grade'))}) | **Raw Rubric:** {project.get('final_score')} ({project.get('raw_letter_grade', project.get('letter_grade'))})",
        f"- **Rubric Version:** {project.get('rubric_version') or rep.get('rubric_version') or 'N/A'}",
        f"- **Commit SHA:** {project.get('commit_sha') or 'N/A'}",
        f"- **Graded At:** {project.get('graded_at') or 'N/A'}",
        "",
    ]
    if rep.get("verdict_title"):
        lines += [f"## Verdict: {rep['verdict_title']}", "", str(rep.get("verdict_summary") or ""), ""]
    if rep.get("top_solid_features"):
        lines += ["## What Works", ""] + [f"- {x}" for x in rep["top_solid_features"]] + [""]
    if rep.get("top_features_needing_work"):
        lines += ["## Needs Work", ""] + [f"- {x}" for x in rep["top_features_needing_work"]] + [""]
    if rep.get("pro_next_steps"):
        lines += ["## What a Pro Team Would Do Next", ""] + [f"- {x}" for x in rep["pro_next_steps"]] + [""]
    fe_map = rep.get("factor_evaluations") or {}
    if fe_map:
        lines += ["## Factor Scores", ""]
        for k, fe in fe_map.items():
            lines.append(f"- **{k}**: {fe.get('score')}")
            for w in fe.get("weaknesses") or []:
                lines.append(f"  - Weakness: {w}")
        lines.append("")
    notes = (notes_data or {}).get("notes") or []
    if notes:
        lines += [f"## Maintainer Notes ({len(notes)})", ""]
        for n in notes:
            loc = f"{n.get('file')}:{n.get('line')}" if n.get("line") else str(n.get("file"))
            lines.append(f"- **[{n.get('kind')}]** `{loc}` — {n.get('detail')}")
        lines.append("")
    return "\n".join(lines)


@app.get("/api/projects/{owner}/{repo}/export")
async def api_project_export(
    owner: str,
    repo: str,
    request: Request,
    format: str = Query("json", pattern="^(json|markdown)$"),
    report: Optional[int] = Query(None, ge=1),
):
    """Exports the complete evaluation report and maintainer notes for the project's owner or admin."""
    import re
    from ..storage.db import strip_safety_details

    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in with GitHub to export your project report.")
    is_admin = bool(user.get("is_admin")) and is_admin_user(user.get("username", ""), user.get("github_id"))
    if not is_admin and not owns_repo(user, owner):
        raise HTTPException(status_code=403, detail="Only the repository's owner can export its full report.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", owner) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo):
        raise HTTPException(status_code=404, detail="Project report not found.")

    data = get_project_detail(f"{owner}/{repo}", report_id=report, include_test=is_admin)
    if not data:
        raise HTTPException(status_code=404, detail="Project report not found.")

    for k in _INTERNAL_PROJECT_FIELDS:
        data.pop(k, None)
    if isinstance(data.get("full_report"), dict):
        data["full_report"] = strip_safety_details(data["full_report"])

    safe_owner = re.sub(r"[^\w\-.]", "_", owner)
    safe_repo = re.sub(r"[^\w\-.]", "_", repo)
    notes_file = Path("data/reports") / safe_owner / safe_repo / "maintainer_notes.json"
    notes_data = None
    if notes_file.is_file():
        try:
            notes_data = json.loads(notes_file.read_text(encoding="utf-8"))
        except Exception:
            notes_data = None

    if format == "markdown":
        md_text = _render_project_export_markdown(data, notes_data)
        filename = f"letsvibe-{safe_owner}-{safe_repo}-report.md"
        return Response(
            content=md_text,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
        )

    payload = {
        "exported_at": datetime.now().isoformat(),
        "project": data,
        "maintainer_notes": notes_data,
    }
    filename = f"letsvibe-{safe_owner}-{safe_repo}-report.json"
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@app.get("/badge/{owner}/{repo_file}", include_in_schema=False)
async def api_badge(owner: str, repo_file: str, style: str = Query("classic", max_length=20)):
    """
    "Badge of honor" image (web/badges.py) for READMEs and websites: the project's latest public grade, drawn as SVG.
    Public like the project page; admin test gradings and unknown projects get a neutral "not graded" badge. It sets no
    cookies and reads nothing about the viewer. GitHub fetches README images through its own proxy and caches them,
    so a new grade can take a while to show there.
    """
    import re
    from .badges import STYLES, render

    repo = repo_file[:-4] if repo_file.lower().endswith(".svg") else repo_file
    project = None
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", owner) and re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo):
        project = get_project_detail(f"{owner}/{repo}")  # public projects only (never admin test gradings)
    svg = render(style if style in STYLES else "classic", project)
    headers = {
        "Cache-Control": "public, max-age=1800",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    }
    return Response(content=svg, media_type="image/svg+xml", headers=headers)


@app.get("/icons/{owner}/{repo}", include_in_schema=False)
async def api_saved_icon(owner: str, repo: str):
    """Icon saved for a private repository (core/icons.py resolve_icon_url), served only while the project is listed."""
    import re
    from ..core.icons import SAVED_ICON_EXTS

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", owner) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", repo):
        raise HTTPException(status_code=404, detail="Icon unavailable.")
    conn = get_db_connection()
    try:
        listed = conn.execute("SELECT 1 FROM projects WHERE owner = ? AND repo_name = ?", (owner, repo)).fetchone()
    finally:
        conn.close()
    if not listed:
        raise HTTPException(status_code=404, detail="Icon unavailable.")
    # Same folder as RepoIdentity.get_report_dir() (without creating it)
    folder = Path("data/reports") / re.sub(r"[^\w\-.]", "_", owner) / re.sub(r"[^\w\-.]", "_", repo)
    found = next((p for p in sorted(folder.glob("icon.*")) if p.suffix.lower() in SAVED_ICON_EXTS), None) if folder.is_dir() else None
    if not found:
        raise HTTPException(status_code=404, detail="Icon unavailable.")
    headers = {"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "default-src 'none'; sandbox"}
    return FileResponse(found, media_type=SAVED_ICON_EXTS[found.suffix.lower()], headers=headers)


# ==========================================
# Self-service privacy controls ("My Data")
# ==========================================

def require_user(request: Request) -> Dict[str, Any]:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in with GitHub to manage your data.")
    return user


class DeleteAccountRequest(BaseModel):
    confirm_username: str


@app.get("/api/me/projects")
async def api_my_projects(user: Dict[str, Any] = Depends(require_user)):
    """Projects on LetsVibe that belong to the signed-in GitHub account."""
    return {"status": "ok", "username": user.get("username"), "items": list_projects_owned_by(user.get("username", ""))}


@app.get("/api/me/appeals")
async def api_my_appeals(user: Dict[str, Any] = Depends(require_user)):
    """Your appeals and their decisions (decisions are shown here instead of being emailed)."""
    return {"status": "ok", "items": list_appeals_for_user(user.get("username", ""))}


@app.delete("/api/me/projects/{full_slug:path}")
async def api_delete_my_project(full_slug: str, user: Dict[str, Any] = Depends(require_user)):
    """Removes one of your projects (reports, badges, files, cached images) immediately."""
    from fastapi.concurrency import run_in_threadpool

    owned = {p["full_slug"].lower(): p["full_slug"] for p in list_projects_owned_by(user.get("username", ""))}
    slug = owned.get(full_slug.strip().lower())
    if not slug:
        # Same answer whether it does not exist or is not yours: no probing other people's data
        raise HTTPException(status_code=404, detail="No project with that name belongs to your account.")
    await run_in_threadpool(delete_project_everywhere, slug)
    if not owned.keys() - {slug.lower()}:
        image_proxy.purge_owner(user.get("username", ""))
    return {"status": "ok", "message": f"{slug} and all of its data were removed from LetsVibe."}


@app.post("/api/me/projects/delete-all")
async def api_delete_all_my_projects(user: Dict[str, Any] = Depends(require_user)):
    """Starts a batched background deletion of every project on your account; poll the job for progress."""
    job = privacy_jobs.submit("projects", user.get("username", ""), str(user.get("github_id", "")))
    return {"status": "ok", **job.public()}


@app.post("/api/me/account/delete")
async def api_delete_my_account(req: DeleteAccountRequest, user: Dict[str, Any] = Depends(require_user)):
    """Deletes every project and the account itself, then signs you out everywhere."""
    username = user.get("username", "")
    if user.get("is_admin"):
        raise HTTPException(status_code=400, detail="The site administrator account cannot be deleted from here.")
    if req.confirm_username.strip().lower() != username.lower():
        raise HTTPException(status_code=400, detail="Type your GitHub username exactly to confirm.")
    db_user = get_user_by_github_id(str(user.get("github_id", ""))) or {}
    job = privacy_jobs.submit(
        "account", username, str(user.get("github_id", "")), avatar_url=db_user.get("github_avatar_url") or "",
    )
    res = JSONResponse({"status": "ok", **job.public()})
    res.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return res


@app.get("/api/me/jobs/{job_id}")
async def api_privacy_job_status(job_id: str):
    """Progress of a deletion job. The id is a long random token, so no login is needed to read it."""
    job = privacy_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown or expired job.")
    return job.public()


@app.get("/api/me/export")
async def api_export_my_data(user: Dict[str, Any] = Depends(require_user)):
    """Downloads everything LetsVibe stores about your account as JSON."""
    data = export_user_data(str(user.get("github_id", "")), user.get("username", ""))
    filename = f"letsvibe-data-{user.get('username', 'export')}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


# ==========================================
# My Projects: explanations requested before publishing, and disputed statements
# ==========================================

class HoldResponseRequest(BaseModel):
    explanation: str


class ErrorReportRequest(BaseModel):
    full_slug: str
    statement_path: str
    explanation: str


class AdminCorrectionRequest(BaseModel):
    corrected_text: Optional[str] = None  # None/empty removes the statement


@app.get("/api/me/summary")
async def api_my_summary(user: Dict[str, Any] = Depends(require_user)):
    """Small counts for the header badge."""
    held = list_held_for_owner(user.get("username", ""))
    return {"status": "ok", "action_required": sum(1 for h in held if h["status"] == "awaiting_response")}


@app.get("/api/me/held")
async def api_my_held(user: Dict[str, Any] = Depends(require_user)):
    return {"status": "ok", "items": list_held_for_owner(user.get("username", ""))}


@app.post("/api/me/held/{held_id}/respond")
async def api_my_held_respond(held_id: int, req: HoldResponseRequest, user: Dict[str, Any] = Depends(require_user)):
    text = req.explanation.strip()
    if len(text) < 20:
        raise HTTPException(status_code=400, detail="Please explain in at least a sentence or two (20 characters minimum).")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="Please keep the explanation under 4,000 characters.")
    if not submit_hold_response(held_id, user.get("username", ""), text):
        raise HTTPException(status_code=404, detail="That request was not found on your account.")
    return {"status": "ok", "message": "Thank you. Your explanation was submitted for review."}


@app.delete("/api/me/held/{held_id}")
async def api_my_held_withdraw(held_id: int, user: Dict[str, Any] = Depends(require_user)):
    """Withdraw a held report: it is deleted from live data and every backup."""
    held = get_held(held_id)
    if not held or held["owner"].lower() != user.get("username", "").lower():
        raise HTTPException(status_code=404, detail="That request was not found on your account.")
    delete_held_everywhere(held_id)
    return {"status": "ok", "message": "The report was withdrawn and deleted."}


@app.get("/api/me/error-reports")
async def api_my_error_reports(user: Dict[str, Any] = Depends(require_user)):
    items = list_error_reports(owner=user.get("username", ""))
    for it in items:
        it.pop("task_id", None)
    return {"status": "ok", "items": items}


@app.get("/api/project/{full_slug:path}/statements")
async def api_project_statements(full_slug: str, user: Dict[str, Any] = Depends(require_user)):
    """The statements in your latest published report that you can dispute (owner only)."""
    if not owns_repo(user, full_slug.split("/", 1)[0]):
        raise HTTPException(status_code=403, detail="Only the repository's owner can report an error in its review.")
    detail = get_project_detail(full_slug)
    report = (detail or {}).get("full_report")
    if not report:
        raise HTTPException(status_code=404, detail="No published report found.")
    return {"status": "ok", "items": [{"path": p, "label": l, "text": t} for p, l, t in list_statements(report)]}


@app.post("/api/me/error-reports")
async def api_create_error_report(req: ErrorReportRequest, user: Dict[str, Any] = Depends(require_user)):
    """Dispute one statement in your own published report. It is checked by AI once, then by the admin if needed."""
    username = user.get("username", "")
    slug = req.full_slug.strip()
    if not owns_repo(user, slug.split("/", 1)[0]):
        raise HTTPException(status_code=403, detail="Only the repository's owner can report an error in its review.")
    explanation = req.explanation.strip()
    if not 20 <= len(explanation) <= 4000:
        raise HTTPException(status_code=400, detail="Please explain what is wrong in 20 to 4,000 characters.")
    detail = get_project_detail(slug)
    report = (detail or {}).get("full_report")
    if not report:
        raise HTTPException(status_code=404, detail="No published report found.")
    text = get_statement(report, req.statement_path)
    if text is None:
        raise HTTPException(status_code=400, detail="That statement is no longer in the report. Please reload the page.")
    open_items = list_error_reports(owner=username)
    if any(e["statement_path"] == req.statement_path and e["full_slug"].lower() == slug.lower()
           and e["status"] in ("queued", "needs_review") for e in open_items):
        raise HTTPException(status_code=400, detail="You already reported this statement; it is being reviewed.")
    if count_open_error_reports(username) >= MAX_OPEN_ERROR_REPORTS_PER_OWNER:
        raise HTTPException(status_code=429, detail="You have several error reports in review already. Please wait for them to finish.")
    error_id = create_error_report(detail.get("full_slug") or slug, username, int(detail.get("report_id") or 0),
                                   req.statement_path, text, explanation)
    task_id = enqueue_task(target=f"https://github.com/{detail.get('full_slug') or slug}", mode="error_review",
                           requested_by=username)
    update_error_report(error_id, task_id=task_id)
    return {"status": "ok", "error_report_id": error_id,
            "message": "Thank you. The statement will be checked against your code shortly."}


class ContentReportRequest(BaseModel):
    full_slug: str
    reason: str
    details: str = ""


@app.post("/api/content-reports")
async def api_submit_content_report(req: ContentReportRequest, user: Dict[str, Any] = Depends(require_user)):
    """Visitor/user report for content-guideline violations or stolen work; immediately hides project pending admin review."""
    reason = (req.reason or "").strip()
    if reason not in ("content_violation", "stolen_work"):
        raise HTTPException(status_code=400, detail="Please choose either 'content_violation' or 'stolen_work'.")
    slug = (req.full_slug or "").strip()
    if not slug:
        raise HTTPException(status_code=400, detail="Project slug is required.")
    try:
        report_id = create_content_report(
            full_slug=slug,
            reported_by=user.get("username", ""),
            reason=reason,
            details=req.details,
        )
    except ReportRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    if not report_id:
        raise HTTPException(status_code=404, detail="Project not found.")
    return {
        "status": "ok",
        "report_id": report_id,
        "message": "Thank you. This listing has been hidden from public view and sent to the admin review inbox.",
    }


@app.get("/api/admin/reviews")
async def api_admin_reviews(_admin: Dict[str, Any] = Depends(require_admin)):
    return {
        "status": "ok",
        "counts": count_pending_reviews(),
        "held": list_held_for_admin(),
        "errors": list_error_reports(),
        "content_reports": list_content_reports("open"),
    }


@app.get("/api/admin/reviews/count")
async def api_admin_reviews_count(_admin: Dict[str, Any] = Depends(require_admin)):
    return {"status": "ok", **count_pending_reviews(), "chat_unread": get_admin_chat_unread_count()}


# ==========================================
# Direct Chat & Admin Tagging Endpoints
# ==========================================

class ChatMessageRequest(BaseModel):
    body: str


class ChatTagRequest(BaseModel):
    tag: str


@app.get("/api/me/chat/unread")
async def api_me_chat_unread(request: Request):
    user = get_current_user(request)
    if not user:
        return {"unread": 0}
    return {"unread": get_user_chat_unread_count(str(user.get("github_id", "")))}


@app.get("/api/me/chat")
async def api_me_chat(request: Request, mark_read: bool = True):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="GitHub authentication required.")
    return get_user_chat_thread(
        github_id=str(user.get("github_id", "")),
        username=user.get("username", ""),
        avatar_url=user.get("avatar_url"),
        mark_read=mark_read,
    )


@app.post("/api/me/chat/messages")
async def api_me_chat_send(req: ChatMessageRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="GitHub authentication required.")
    try:
        msg = send_user_chat_message(
            github_id=str(user.get("github_id", "")),
            username=user.get("username", ""),
            avatar_url=user.get("avatar_url"),
            body=req.body,
        )
        return {"status": "ok", "message": msg}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/admin/chat/unread")
async def api_admin_chat_unread(_admin: Dict[str, Any] = Depends(require_admin)):
    return {"unread": get_admin_chat_unread_count()}


@app.get("/api/admin/chat/threads")
async def api_admin_chat_threads(
    q: str = "",
    sort: str = "unread",
    _admin: Dict[str, Any] = Depends(require_admin),
):
    return {"threads": list_admin_chat_threads(search=q, sort=sort)}


@app.get("/api/admin/chat/threads/{thread_id}")
async def api_admin_chat_thread_detail(
    thread_id: int,
    mark_read: bool = True,
    _admin: Dict[str, Any] = Depends(require_admin),
):
    thread = get_admin_chat_thread(thread_id, mark_read=mark_read)
    if not thread:
        raise HTTPException(status_code=404, detail="Chat thread not found.")
    return thread


@app.post("/api/admin/chat/threads/{thread_id}/reply")
async def api_admin_chat_reply(
    thread_id: int,
    req: ChatMessageRequest,
    admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        msg = reply_admin_chat_thread(
            thread_id=thread_id,
            admin_username=admin.get("username", ADMIN_GITHUB_USER),
            body=req.body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not msg:
        raise HTTPException(status_code=404, detail="Chat thread not found.")
    return {"status": "ok", "message": msg}


@app.get("/api/admin/chat/tags")
async def api_admin_chat_tags(_admin: Dict[str, Any] = Depends(require_admin)):
    return {"tags": list_admin_chat_tags()}


@app.get("/api/admin/chat/messages")
async def api_admin_chat_messages_by_tag(
    tag: str = "",
    sort: str = "newest",
    _admin: Dict[str, Any] = Depends(require_admin),
):
    return {"tag": tag, "sort": sort, "messages": list_admin_chat_messages_by_tag(tag=tag, sort=sort)}


@app.post("/api/admin/chat/messages/{message_id}/tags")
async def api_admin_add_chat_tag(
    message_id: int,
    req: ChatTagRequest,
    _admin: Dict[str, Any] = Depends(require_admin),
):
    try:
        tags = add_chat_message_tag(message_id=message_id, raw_tag=req.tag)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if tags is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    return {"status": "ok", "tags": tags}


@app.delete("/api/admin/chat/messages/{message_id}/tags/{tag}")
async def api_admin_remove_chat_tag(
    message_id: int,
    tag: str,
    _admin: Dict[str, Any] = Depends(require_admin),
):
    tags = remove_chat_message_tag(message_id=message_id, raw_tag=tag)
    if tags is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    return {"status": "ok", "tags": tags}


@app.post("/api/admin/held/{held_id}/publish")
async def api_admin_publish_held(held_id: int, _admin: Dict[str, Any] = Depends(require_admin)):
    """Publishes a held report after review, then deletes the held copy everywhere."""
    from fastapi.concurrency import run_in_threadpool

    held = get_held(held_id)
    if not held:
        raise HTTPException(status_code=404, detail="Not found.")
    report = VibeReport.model_validate(json.loads(held["report_data_json"]))

    def publish():
        ReportStorageManager.persist_audit(
            report=report, target=held["canonical_url"], commit_sha=held.get("commit_sha"),
            repo_icon_url=held.get("repo_icon_url"),
        )
        delete_held_everywhere(held_id)

    await run_in_threadpool(publish)
    return {"status": "ok", "message": f"{held['full_slug']} was published."}


@app.post("/api/admin/held/{held_id}/remove")
async def api_admin_remove_held(held_id: int, _admin: Dict[str, Any] = Depends(require_admin)):
    if not delete_held_everywhere(held_id):
        raise HTTPException(status_code=404, detail="Not found.")
    return {"status": "ok", "message": "Removed from the database and all backups."}


@app.post("/api/admin/error-reports/{error_id}/apply")
async def api_admin_apply_correction(error_id: int, req: AdminCorrectionRequest, _admin: Dict[str, Any] = Depends(require_admin)):
    er = get_error_report(error_id)
    if not er:
        raise HTTPException(status_code=404, detail="Not found.")
    new_text = (req.corrected_text or "").strip() or None
    if not correct_statement_everywhere(er["full_slug"], er["statement_path"], er["statement_text"], new_text):
        raise HTTPException(status_code=409, detail="The report changed since this was submitted (for example it was re-graded).")
    update_error_report(error_id, status="applied", resolved_at=datetime.now().replace(microsecond=0).isoformat(sep=" "))
    return {"status": "ok", "message": "Correction applied everywhere."}


@app.post("/api/admin/error-reports/{error_id}/dismiss")
async def api_admin_dismiss_error(error_id: int, _admin: Dict[str, Any] = Depends(require_admin)):
    if not get_error_report(error_id):
        raise HTTPException(status_code=404, detail="Not found.")
    update_error_report(error_id, status="dismissed", resolved_at=datetime.now().replace(microsecond=0).isoformat(sep=" "))
    return {"status": "ok", "message": "Dismissed. The review stays as it is."}


@app.post("/api/admin/content-reports/{report_id}/restore")
async def api_admin_restore_content_report(report_id: int, _admin: Dict[str, Any] = Depends(require_admin)):
    item = resolve_content_report(report_id, "restore")
    if not item:
        raise HTTPException(status_code=404, detail="Not found.")
    return {"status": "ok", "message": f"Restored {item['full_slug']} to public view."}


@app.post("/api/admin/content-reports/{report_id}/remove")
async def api_admin_remove_content_report(report_id: int, _admin: Dict[str, Any] = Depends(require_admin)):
    from fastapi.concurrency import run_in_threadpool

    item = resolve_content_report(report_id, "remove")
    if not item:
        raise HTTPException(status_code=404, detail="Not found.")
    await run_in_threadpool(delete_project_everywhere, item["full_slug"])
    return {"status": "ok", "message": f"Removed {item['full_slug']} from the database and backups."}


@app.get("/api/health")
async def api_health():
    """Lightweight public health endpoint reporting online status and total projects rated."""
    items = get_leaderboard(include_test=False, with_facets=False)
    return JSONResponse(
        {"status": "online", "projects_rated": len(items)},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/leaderboard")
async def api_leaderboard(
    request: Request,
    sort_by: str = Query("overall", description="Category to sort by"),
    tier: Optional[str] = Query(None, description="Comma-separated grade tiers (A,B,C,D,F)"),
    category: Optional[str] = Query(None, description="Comma-separated general categories"),
    platform: Optional[str] = Query(None, description="Comma-separated platform ids"),
    search: Optional[str] = Query(None, description="Search keyword"),
    curve: bool = Query(True, description="Whether to apply the bell curve to letter grades"),
    engine: Optional[str] = Query(None, description="Admin-only engine version filter"),
):
    """Ranked repository list. OR within a filter group, AND across groups; `facets` holds option counts."""
    is_admin = is_admin_request(request)
    viewer = get_current_user(request)
    viewer_gid = str((viewer or {}).get("github_id") or "")
    items, facets = get_leaderboard(
        sort_by=sort_by, tier_filter=tier, search=search,
        categories=category, platforms=platform, with_facets=True,
        include_test=is_admin,  # TEST MODE: admin test gradings and engine filter are available for the admin only
        use_curve=curve,
        engine_filter=engine if is_admin else None,
    )
    up_counts, up_mine = get_upvote_map(viewer_gid)
    for it in items:
        pid = int(it.get("project_id") or 0)
        it["upvote_count"] = up_counts.get(pid, 0)
        it["user_upvoted"] = pid in up_mine
    return {"status": "ok", "count": len(items), "items": items, "facets": facets}


@app.post("/api/upvote")
async def api_toggle_upvote(req: UpvoteRequest, user: Dict[str, Any] = Depends(require_user)):
    """Toggles a signed-in user's Spark upvote on a public project."""
    slug = (req.full_slug or "").strip()
    if not slug:
        raise HTTPException(status_code=400, detail="Project slug is required.")
    try:
        result = toggle_project_upvote(
            full_slug=slug,
            github_id=str(user.get("github_id") or ""),
            username=str(user.get("username") or ""),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    if not result:
        raise HTTPException(status_code=404, detail="Project not found.")
    return {"status": "ok", **result}


@app.get("/api/engines")
async def api_engines(request: Request):
    """
    Returns available audit engines and developer intake archetypes.
    Engines marked with "status": "testing" are only returned to the admin (or when LETSVIBE_TESTING_SERVER=1).
    """
    from ..evaluator.pipeline import get_available_engines

    try:
        from ..evaluator.experience.context import load_project_profiles
        raw_profiles = load_project_profiles()
    except ImportError:
        profiles_path = Path(__file__).resolve().parent.parent / "data" / "project_profiles.json"
        raw_profiles = json.loads(profiles_path.read_text(encoding="utf-8")).get("profiles", {}) if profiles_path.exists() else {}

    is_admin = is_admin_request(request)
    testing_server = os.getenv("LETSVIBE_TESTING_SERVER", "").strip().lower() in ("1", "true", "yes", "on")
    engines = get_available_engines(include_testing=is_admin or testing_server)
    profiles = [
        {"id": k, "label": v.get("label", k), "description": v.get("description", "")}
        for k, v in raw_profiles.items()
    ]
    return {
        "status": "ok",
        "is_admin": is_admin,
        "engines": engines,
        "profiles": profiles,
    }


@app.get("/api/taxonomy")
async def api_taxonomy():
    """Category and platform options used to build the leaderboard filters."""
    return {
        "categories": [{"id": k, "label": v} for k, v in GENERAL_CATEGORIES.items()],
        "platform_groups": [
            {"id": gid, "label": g["label"], "children": [{"id": c, "label": PLATFORM_LABELS[c]} for c in g["children"]]}
            for gid, g in PLATFORM_GROUPS.items()
        ],
    }


@app.get("/api/creators")
async def api_creators(request: Request, sort_by: str = Query("peak", description="'peak' or 'avg'")):
    """Retrieves creator leaderboard ranked by peak score or portfolio average score."""
    _record_non_admin_page_view(request, "/?view=creators")
    data = get_creators_leaderboard(sort_by=sort_by)
    return {"status": "ok", "count": len(data), "items": data}


@app.get("/api/hall-of-fame")
async def api_hall_of_fame(request: Request):
    """Retrieves all-time podium champions and historic badge holders."""
    _record_non_admin_page_view(request, "/?view=hof")
    data = get_hall_of_fame()
    return {"status": "ok", "count": len(data), "items": data}


_INTERNAL_PROJECT_FIELDS = (
    "report_data_json",
    "raw_json_path",
    "html_report_path",
    "markdown_report_path",
    "claimed_by_user_id",
    "submitted_by_user_id",
    "malware_risk_score",
)


@app.get("/api/project/{full_slug:path}")
async def api_project(full_slug: str, request: Request, report: Optional[int] = Query(None, ge=1)):
    """Full evaluation report, factor scores and radar metrics: the latest scan, or an earlier one with ?report=<id>."""
    is_admin = is_admin_request(request)
    data = get_project_detail(full_slug, report_id=report, include_test=is_admin)
    if not data:
        raise HTTPException(status_code=404, detail="Project report not found")
    viewer = get_current_user(request)
    viewer_gid = str((viewer or {}).get("github_id") or "")
    _, up_mine = get_upvote_map(viewer_gid)
    data["user_upvoted"] = int(data.get("id") or 0) in up_mine
    if not is_admin:
        for k in _INTERNAL_PROJECT_FIELDS:
            data.pop(k, None)
    referer_path = urllib.parse.urlparse(request.headers.get("referer") or "").path.rstrip("/")
    target_path = f"/project/{full_slug}".rstrip("/")
    if referer_path != target_path:
        _record_non_admin_page_view(request, target_path)
    return {"status": "ok", "project": data}


@app.get("/api/project-html/{full_slug:path}")
async def api_project_html(full_slug: str, request: Request):
    """Serves the standalone HTML report for embedding or direct viewing."""
    data = get_project_detail(full_slug, include_test=is_admin_request(request))
    if not data or not data.get("html_report_path"):
        raise HTTPException(status_code=404, detail="HTML report not found")

    p = Path(data["html_report_path"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="HTML file missing from disk")

    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; sandbox allow-scripts"
        ),
    }
    return HTMLResponse(content=p.read_text(encoding="utf-8"), headers=headers)


@app.get("/api/queue")
async def api_queue(request: Request):
    """Returns queue status, current worker activity, and list of queued items."""
    status = get_queue_status()
    items = get_queue_items(limit=30)
    worker_current = worker.current_task_info if worker else None
    if not is_admin_request(request):  # TEST MODE: the public never sees which repo an admin test grading is
        user = get_current_user(request)
        viewer = (user or {}).get("username", "")
        items = [_hide_test_task(t, viewer) for t in items]
        status["running_task"] = _hide_test_task(status.get("running_task"), viewer)
        worker_current = _hide_test_task(worker_current, viewer)
    return {
        "status": "ok",
        "queue_status": status,
        "worker_running": worker_current,
        "items": items,
    }


@app.post("/api/queue/enqueue")
async def api_enqueue(req: EnqueueRequest, request: Request):
    """Enqueues a repository target for evaluation after pre-flight sanity checks and quota verification."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please connect with GitHub to submit repositories for audit.",
        )

    username = user.get("username", "")
    is_admin = bool(user.get("is_admin", False))

    from ..evaluator.pipeline import get_engine_info

    engine_info = get_engine_info(req.mode)
    # Internal modes such as "reclassify" are admin-only and queued via their own endpoints
    if not engine_info and req.mode not in ("blend", "fast", "deep"):
        raise HTTPException(status_code=400, detail=f"Unknown audit mode '{req.mode}'.")

    is_testing_engine = bool(engine_info and engine_info.get("status") == "testing")
    testing_server = os.getenv("LETSVIBE_TESTING_SERVER", "").strip().lower() in ("1", "true", "yes", "on")
    if is_testing_engine and not (is_admin_request(request) or testing_server):
        raise HTTPException(status_code=403, detail="Testing engines are only available to the site administrator.")

    # Evaluate rolling submission quota
    quota = check_user_submission_quota(username=username, is_admin=is_admin)
    if not quota["allowed"]:
        raise HTTPException(status_code=429, detail=quota["message"])

    target = req.target.strip()
    if not target:
        raise HTTPException(status_code=400, detail="Target repository address is required.")

    is_remote = target.startswith("http://") or target.startswith("https://") or target.startswith("git@")
    if not is_remote:
        # Local paths would read the server's own disk and publish it. Use the CLI for local testing.
        raise HTTPException(status_code=400, detail="Please submit a public GitHub repository URL (https://github.com/you/repo).")
    if is_remote:
        identity = parse_repo_identity(target)
        if (
            not (
                target.startswith("https://github.com/")
                or target.startswith("http://github.com/")
                or target.startswith("git@github.com:")
            )
            or identity.provider != "github"
            or not identity.is_remote
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid repository address. Currently only public GitHub repositories (https://github.com/owner/repo) are supported.",
            )
        target = identity.canonical_url
        is_test = bool(is_testing_engine and not testing_server)
        if not owns_repo(user, identity.owner):
            # TEST MODE (remove before launch): the admin may grade other accounts' repos, shown only to the admin
            if admin_test_submissions_enabled() and is_admin_request(request):
                is_test = True
            else:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"You can only submit repositories owned by your GitHub account (@{username}). "
                        "To rank work from another GitHub account, sign in with that account."
                    ),
                )

        # Pre-flight check via git ls-remote (fast check to verify repo exists and is public)
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            res = subprocess.run(  # nosec B603 B607 - fixed command, list arguments, no shell
                ["git", "ls-remote", "--exit-code", target, "HEAD"],
                capture_output=True,
                text=True,
                timeout=5.0,
                env=env,
            )
            if res.returncode != 0:
                raise HTTPException(
                    status_code=400,
                    detail="Repository not found or private. Ensure the GitHub repository address is typed correctly and is public.",
                )

            # Zero-cost Remote Commit SHA check: prevent burning LLM compute if already evaluated at latest commit
            # (Bypassed when the admin runs a testing engine or re-benchmarks a repository)
            if res.stdout.strip() and not (is_testing_engine or is_admin_request(request)):
                remote_sha = res.stdout.split()[0].strip()
                identity = parse_repo_identity(target)
                latest_report = get_latest_report_for_slug(identity.full_slug)
                if latest_report and latest_report.get("commit_sha") and latest_report["commit_sha"] == remote_sha:
                    short_sha = remote_sha[:7]
                    raise HTTPException(
                        status_code=400,
                        detail=f"Repository '{identity.full_slug}' has already been evaluated at the latest commit ({short_sha}). Push new commits to trigger a new vibe evaluation.",
                    )
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=400,
                detail="Connection to GitHub repository timed out. Please verify the URL and try again.",
            )

    if not req.accepted_terms_and_ownership or not req.confirmed_no_explicit_content:
        raise HTTPException(
            status_code=400,
            detail="Please confirm repository ownership/Terms and that the repository contains no explicit or illegal content.",
        )

    submission_meta = {
        "project_profile": (req.project_profile or "auto").strip(),
        "provenance": (req.provenance or "vibe_coded").strip(),
        "guest_access": (req.guest_access or "auto").strip(),
        "user_hook": (req.user_hook or "").strip()[:240],
    }

    task_id = enqueue_task(
        target=target,
        mode=req.mode,
        requested_by=username,
        is_test=is_test,
        submission_meta=submission_meta,
    )
    message = ("Enqueued as an admin test grading (only you can see it)." if is_test else "Enqueued successfully.")
    return {"status": "ok", "task_id": task_id, "message": message, "quota": quota, "is_test": is_test}


class ReanalyzeRequest(BaseModel):
    full_slug: str


@app.post("/api/project/reanalyze")
async def api_project_reanalyze(req: ReanalyzeRequest, request: Request):
    """
    User endpoint: Re-evaluates a previously graded repository with a zero-cost remote SHA pre-flight.
    If no new commits have been pushed since the last audit, rejects immediately with 0 LLM tokens burned.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please connect with GitHub to request re-evaluation.",
        )

    username = user.get("username", "")
    is_admin = bool(user.get("is_admin", False))

    quota = check_user_submission_quota(username=username, is_admin=is_admin)
    if not quota["allowed"]:
        raise HTTPException(status_code=429, detail=quota["message"])

    slug = req.full_slug.strip()
    if not slug:
        raise HTTPException(status_code=400, detail="Repository slug is required.")
    if not owns_repo(user, slug.split("/", 1)[0]):
        raise HTTPException(status_code=403, detail="Only the repository's owner can request a re-evaluation.")

    target = f"https://github.com/{slug}"
    identity = parse_repo_identity(target)
    latest_report = get_latest_report_for_slug(identity.full_slug)

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        res = subprocess.run(  # nosec B603 B607 - fixed command, list arguments, no shell
            ["git", "ls-remote", "--exit-code", target, "HEAD"],
            capture_output=True,
            text=True,
            timeout=5.0,
            env=env,
        )
        if res.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail=f"Repository '{slug}' could not be reached on GitHub. Verify it is public.",
            )
        remote_sha = res.stdout.split()[0].strip() if res.stdout.strip() else None
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=400, detail="Connection to GitHub timed out. Please try again.")

    if remote_sha and latest_report and latest_report.get("commit_sha") and latest_report["commit_sha"] == remote_sha:
        short_sha = remote_sha[:7]
        raise HTTPException(
            status_code=400,
            detail=f"Repository '{slug}' is already up to date at commit {short_sha}. Zero changes detected since last evaluation.",
        )

    task_id = enqueue_task(target=target, mode="blend", requested_by=username)
    return {
        "status": "ok",
        "task_id": task_id,
        "message": f"New commit detected ({remote_sha[:7] if remote_sha else 'HEAD'}). Re-evaluation enqueued.",
        "quota": quota,
    }


@app.post("/api/admin/queue/settings")
async def api_queue_settings(req: QueueControlRequest, _admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Pauses/resumes queue, toggles automatic execution, and sets custom pause reason."""
    update_queue_settings(
        is_paused=req.is_paused,
        auto_process=req.auto_process,
        pause_reason=req.pause_reason,
    )
    return {"status": "ok", "current": get_queue_status()}


@app.get("/api/queue/task/{task_id}")
async def api_task_status(task_id: int, request: Request):
    """Returns live task progress, exact queue position, pause status, and the admin's pause reason."""
    task_info = get_task_status_with_position(task_id)
    if not task_info:
        raise HTTPException(status_code=404, detail="Task not found")
    if not is_admin_request(request):
        user = get_current_user(request)
        task_info = _hide_test_task(task_info, (user or {}).get("username", ""))
    return {"status": "ok", "task": task_info}


@app.post("/api/admin/queue/stop/{task_id}")
async def api_stop_task(task_id: int, _admin: Dict[str, Any] = Depends(require_admin)):
    """Admin: stops the running task. The queue is paused and the task stays first in line for when it resumes."""
    if not worker:
        raise HTTPException(status_code=500, detail="Worker not initialized")
    if not worker.request_stop(task_id):
        raise HTTPException(status_code=400, detail="Only the running task can be stopped.")
    return {"status": "ok", "message": f"Stopping task #{task_id}. The queue is paused; it runs first when you resume."}


@app.post("/api/admin/queue/run-now/{task_id}")
async def api_run_now(task_id: int, _admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Forces execution of a specific task immediately."""
    if not worker:
        raise HTTPException(status_code=500, detail="Worker not initialized")
    success = worker.process_task_now(task_id)
    if not success:
        raise HTTPException(status_code=400, detail="Only a waiting task can be moved to the front of the line.")
    return {"status": "ok", "message": f"Task #{task_id} will run next, as soon as the current grading finishes."}


@app.delete("/api/admin/queue/{task_id}")
async def api_delete_queue_task(task_id: int, _admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Removes or cancels a queued task."""
    conn = get_db_connection()
    with conn:
        conn.execute("DELETE FROM audit_queue WHERE id = ?", (task_id,))
    conn.close()
    return {"status": "ok", "message": f"Task #{task_id} removed."}


@app.get("/api/admin/system")
async def api_admin_system(_admin: Dict[str, Any] = Depends(require_admin)):
    """Engine diagnostics: Ollama liveness, VRAM, and model listings."""
    client = OllamaClient()
    is_live = client.is_running()
    models = client.list_models() if is_live else []
    loaded_models = client.get_loaded_models() if is_live else []
    vram = get_free_vram_mb()
    ollama_bin = find_ollama_binary()

    conn = get_db_connection()
    total_projects = conn.execute("SELECT COUNT(*) as c FROM projects").fetchone()["c"]
    total_reports = conn.execute("SELECT COUNT(*) as c FROM reports").fetchone()["c"]
    conn.close()
    registered_users = count_registered_users(ADMIN_GITHUB_USER, ADMIN_GITHUB_ID)

    backups = list_backups()
    return {
        "status": "ok",
        "ollama_live": is_live,
        "ollama_bin": ollama_bin,
        "models": models,
        "loaded_models": loaded_models,
        "free_vram_mb": vram,
        "total_projects": total_projects,
        "total_reports": total_reports,
        "registered_users": registered_users,
        "max_registered_users": max_registered_users(),
        "backup_dir": str(get_backup_dir()),
        "backup_count": len(backups),
        "latest_backup": backups[0].name if backups else None,
    }


@app.post("/api/admin/backups/test")
async def api_admin_test_backup(_admin: Dict[str, Any] = Depends(require_admin)):
    """Runs the weekly rolling backup + prune cycle on demand to verify backup storage."""
    target = create_backup()
    pruned = prune_backups()
    backups = list_backups()
    return {
        "status": "ok",
        "backup_file": target.name,
        "backup_path": str(target),
        "backup_dir": str(get_backup_dir()),
        "size_bytes": target.stat().st_size if target.exists() else 0,
        "pruned": pruned,
        "backup_count": len(backups),
    }

@app.post("/api/admin/rescan-all")
async def api_admin_rescan_all(_admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Re-queues all database repositories for a fresh prompt evaluation scan."""
    targets = get_all_project_targets()
    
    requeued_count = 0
    for target in targets:
        # Enqueue in 'blend' mode for full dual-model re-evaluation
        enqueue_task(target=target, mode="blend", requested_by="admin_rescan")
        requeued_count += 1
        
    return {
        "status": "ok",
        "requeued_count": requeued_count,
        "message": f"Successfully re-queued {requeued_count} repositories for prompt re-evaluation."
    }

@app.post("/api/admin/queue/find-random")
async def api_admin_find_random(admin: Dict[str, Any] = Depends(require_admin)):
    """
    TEST MODE (remove before launch): finds 5 random vibe-coded GitHub repos (4 small, 1 slightly larger, each from a
    different random project type where possible; web/repo_finder.py) and queues them as admin test gradings,
    submitted by the admin. Skips anything already graded or queued here. Needs LETSVIBE_ADMIN_TEST_SUBMISSIONS=1.
    """
    from fastapi.concurrency import run_in_threadpool
    from .repo_finder import SearchLimitReached, find_random_repos

    if not admin_test_submissions_enabled():
        raise HTTPException(status_code=403, detail="Test mode is off. Set LETSVIBE_ADMIN_TEST_SUBMISSIONS=1 in .env and restart.")
    username = admin.get("username", "")
    conn = get_db_connection()
    try:
        seen = {r[0] for r in conn.execute("SELECT full_slug FROM projects")}
        seen |= {r[0] for r in conn.execute("SELECT full_slug FROM audit_queue WHERE full_slug IS NOT NULL")}
    finally:
        conn.close()
    try:
        found = await run_in_threadpool(find_random_repos, seen)
    except SearchLimitReached as e:
        raise HTTPException(status_code=429, detail=str(e))
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Couldn't reach GitHub search. Try again in a moment.")
    queued = []
    for repo in found["repos"]:
        if repo["full_slug"].split("/", 1)[0].lower() == username.lower():
            continue  # your own repos go through the normal submit form
        repo["task_id"] = enqueue_task(target=repo["url"], mode="blend", requested_by=username, is_test=True)
        queued.append(repo)
    return {"status": "ok", "queued": queued, "notes": found["notes"], "searches": found["queries"],
            "message": f"Queued {len(queued)} random vibe-coded repo{'' if len(queued) == 1 else 's'} as admin test gradings."}


@app.post("/api/admin/queue/find-good")
async def api_admin_find_good(admin: Dict[str, Any] = Depends(require_admin)):
    """
    TEST MODE (remove before launch): finds one vibe-coded GitHub repo that is likely to be good (the best-starred
    apps tagged vibecoded / vibe-coded; web/repo_finder.py find_good_repo) and queues it as an admin test grading,
    submitted by the admin. Skips anything already graded or queued here. Needs LETSVIBE_ADMIN_TEST_SUBMISSIONS=1.
    """
    from fastapi.concurrency import run_in_threadpool
    from .repo_finder import SearchLimitReached, find_good_repo

    if not admin_test_submissions_enabled():
        raise HTTPException(status_code=403, detail="Test mode is off. Set LETSVIBE_ADMIN_TEST_SUBMISSIONS=1 in .env and restart.")
    username = admin.get("username", "")
    conn = get_db_connection()
    try:
        seen = {r[0] for r in conn.execute("SELECT full_slug FROM projects")}
        seen |= {r[0] for r in conn.execute("SELECT full_slug FROM audit_queue WHERE full_slug IS NOT NULL")}
    finally:
        conn.close()
    try:
        found = await run_in_threadpool(find_good_repo, seen)
    except SearchLimitReached as e:
        raise HTTPException(status_code=429, detail=str(e))
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Couldn't reach GitHub search. Try again in a moment.")
    queued = []
    for repo in found["repos"]:
        if repo["full_slug"].split("/", 1)[0].lower() == username.lower():
            continue  # your own repos go through the normal submit form
        repo["task_id"] = enqueue_task(target=repo["url"], mode="blend", requested_by=username, is_test=True)
        queued.append(repo)
    message = ("Queued 1 well-starred vibe-coded repo as an admin test grading." if queued
               else "Nothing new to queue.")
    return {"status": "ok", "queued": queued, "notes": found["notes"], "searches": found["queries"], "message": message}


@app.get("/api/admin/my-repos")
async def api_admin_my_repos(admin: Dict[str, Any] = Depends(require_admin)):
    """Lists all public GitHub repositories owned by the current admin."""
    username = (admin.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Admin username not found.")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "LetsVibe"}
    gh_token = os.getenv("GITHUB_TOKEN", "").strip()
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    repos: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for page in (1, 2):
            resp = await client.get(
                f"https://api.github.com/users/{username}/repos",
                params={"per_page": 100, "page": page, "type": "owner", "sort": "updated"},
                headers=headers,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"GitHub API returned {resp.status_code} while listing repos.")
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            for item in batch:
                if item.get("private") or item.get("archived"):
                    continue
                repos.append({
                    "name": item.get("name", ""),
                    "full_slug": item.get("full_name", ""),
                    "url": item.get("html_url", ""),
                    "language": item.get("language") or "",
                    "fork": bool(item.get("fork")),
                    "stars": int(item.get("stargazers_count") or 0),
                })
            if len(batch) < 100:
                break
    return {"status": "ok", "username": username, "repos": repos}


@app.post("/api/admin/queue/batch-own-repos")
async def api_admin_batch_own_repos(req: BatchOwnReposRequest, admin: Dict[str, Any] = Depends(require_admin)):
    """Enqueues a batch of the admin's own repositories with the selected engine version."""
    from ..evaluator.pipeline import get_engine_info

    mode = (req.mode or "blend").strip()
    engine_info = get_engine_info(mode)
    if not engine_info and mode not in ("blend", "fast", "deep"):
        raise HTTPException(status_code=400, detail=f"Unknown audit mode '{mode}'.")
    is_test = bool(engine_info and engine_info.get("status") == "testing")

    username = (admin.get("username") or "").strip()
    seen: set[str] = set()
    task_ids: List[int] = []
    for raw in req.targets:
        cleaned = (raw or "").strip()
        if not cleaned:
            continue
        identity = parse_repo_identity(cleaned)
        if not identity or identity.owner.lower() != username.lower():
            continue
        key = identity.full_slug.lower()
        if key in seen:
            continue
        seen.add(key)
        tid = enqueue_task(
            target=f"https://github.com/{identity.full_slug}",
            mode=mode,
            requested_by=username,
            is_test=is_test,
        )
        task_ids.append(tid)

    if not task_ids:
        raise HTTPException(status_code=400, detail="No valid repositories selected.")
    return {
        "status": "ok",
        "queued_count": len(task_ids),
        "task_ids": task_ids,
        "message": f"Queued {len(task_ids)} repositories with engine '{mode}'.",
    }


def _validate_github_consistency_target(target: str) -> tuple[str, Any]:
    """Validates a public GitHub URL and verifies it is reachable via git ls-remote."""
    cleaned = (target or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Target repository address is required.")
    if not (
        cleaned.startswith("https://github.com/")
        or cleaned.startswith("http://github.com/")
        or cleaned.startswith("git@github.com:")
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid repository address. Currently only public GitHub repositories (https://github.com/owner/repo) are supported.",
        )
    identity = parse_repo_identity(cleaned)
    if identity.provider != "github" or not identity.is_remote:
        raise HTTPException(
            status_code=400,
            detail="Invalid repository address. Currently only public GitHub repositories (https://github.com/owner/repo) are supported.",
        )
    canonical = identity.canonical_url
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        res = subprocess.run(  # nosec B603 B607 - fixed command, list arguments, no shell
            ["git", "ls-remote", "--exit-code", canonical, "HEAD"],
            capture_output=True,
            text=True,
            timeout=5.0,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=400,
            detail="Connection to GitHub repository timed out. Please verify the URL and try again.",
        )
    if res.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail="Repository not found or private. Ensure the GitHub repository address is typed correctly and is public.",
        )
    return canonical, identity


@app.post("/api/admin/queue/consistency-test")
async def api_admin_consistency_test(
    req: ConsistencyTestRequest,
    admin: Dict[str, Any] = Depends(require_admin),
):
    """
    TEST MODE (Admin only): queues the same GitHub repository multiple times (default 5x) with identical
    engine and intake metadata so the admin can measure scoring consistency across repeated runs.
    """
    if not admin_test_submissions_enabled():
        raise HTTPException(status_code=403, detail="Test mode is off. Set LETSVIBE_ADMIN_TEST_SUBMISSIONS=1 in .env and restart.")

    from ..evaluator.pipeline import get_engine_info

    engine_info = get_engine_info(req.mode)
    if not engine_info and req.mode not in ("blend", "fast", "deep"):
        raise HTTPException(status_code=400, detail=f"Unknown audit mode '{req.mode}'.")

    count = max(2, min(10, int(req.count or 5)))
    target_url, identity = _validate_github_consistency_target(req.target)
    username = admin.get("username", "")
    batch_id = uuid.uuid4().hex[:12]

    base_meta = {
        "project_profile": (req.project_profile or "auto").strip(),
        "provenance": (req.provenance or "vibe_coded").strip(),
        "guest_access": (req.guest_access or "auto").strip(),
        "user_hook": (req.user_hook or "").strip()[:240],
        "consistency_batch": batch_id,
    }

    task_ids = []
    for run_idx in range(1, count + 1):
        meta = {**base_meta, "run_index": run_idx, "batch_size": count}
        task_id = enqueue_task(
            target=target_url,
            mode=req.mode,
            requested_by=username,
            force_override=True,
            is_test=True,
            submission_meta=meta,
        )
        task_ids.append(task_id)

    return {
        "status": "ok",
        "batch_id": batch_id,
        "full_slug": identity.full_slug,
        "mode": req.mode,
        "count": count,
        "task_ids": task_ids,
        "message": f"Queued {count}x consistency test for {identity.full_slug} (engine: {req.mode}, batch: {batch_id}).",
    }


@app.get("/api/admin/consistency-results")
async def api_admin_consistency_results(
    batch_id: Optional[str] = Query(None, description="Optional consistency_batch id; defaults to latest batch"),
    _admin: Dict[str, Any] = Depends(require_admin),
):
    """Admin only: returns run-by-run scores and spread statistics for a consistency test batch."""
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT q.id AS task_id, q.full_slug, q.mode, q.status, q.progress_message, q.created_at,
                   q.submission_meta_json, q.report_id,
                   r.final_score, r.letter_grade,
                   r.user_journey_score, r.functionality_score, r.resonance_score,
                   r.creativity_score, r.ui_ux_score,
                   r.operational_safety_score, r.code_health_score
            FROM audit_queue q
            LEFT JOIN reports r ON q.report_id = r.id
            WHERE q.submission_meta_json LIKE '%"consistency_batch"%'
            ORDER BY q.id DESC
            LIMIT 100
            """
        ).fetchall()
    finally:
        conn.close()

    batches: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        try:
            meta = json.loads(item.get("submission_meta_json") or "{}")
        except (TypeError, ValueError):
            meta = {}
        bid = meta.get("consistency_batch")
        if not bid:
            continue
        item["batch_id"] = bid
        item["run_index"] = int(meta.get("run_index") or 0)
        batches.setdefault(bid, []).append(item)

    if not batches:
        return {"status": "ok", "batch": None, "runs": [], "summary": {}}

    chosen_id = batch_id if (batch_id and batch_id in batches) else next(iter(batches))
    runs = sorted(batches[chosen_id], key=lambda r: (r["run_index"], r["task_id"]))

    metric_keys = [
        "final_score",
        "user_journey_score",
        "functionality_score",
        "resonance_score",
        "creativity_score",
        "ui_ux_score",
        "operational_safety_score",
        "code_health_score",
    ]
    summary: Dict[str, Dict[str, float]] = {}
    for key in metric_keys:
        vals = [float(r[key]) for r in runs if r.get("status") == "completed" and r.get(key) is not None]
        if vals:
            mn, mx = min(vals), max(vals)
            summary[key] = {
                "count": len(vals),
                "min": round(mn, 2),
                "max": round(mx, 2),
                "mean": round(sum(vals) / len(vals), 2),
                "spread": round(mx - mn, 2),
            }

    return {
        "status": "ok",
        "batch": {
            "batch_id": chosen_id,
            "full_slug": runs[0]["full_slug"],
            "mode": runs[0]["mode"],
            "total_runs": len(runs),
            "completed_runs": sum(1 for r in runs if r.get("status") == "completed"),
        },
        "available_batches": [
            {"batch_id": bid, "full_slug": grp[0]["full_slug"], "mode": grp[0]["mode"], "runs": len(grp)}
            for bid, grp in list(batches.items())[:10]
        ],
        "runs": [
            {
                "task_id": r["task_id"],
                "run_index": r["run_index"],
                "status": r["status"],
                "progress_message": r.get("progress_message"),
                "report_id": r.get("report_id"),
                "final_score": r.get("final_score"),
                "letter_grade": r.get("letter_grade"),
                "user_journey_score": r.get("user_journey_score"),
                "functionality_score": r.get("functionality_score"),
                "resonance_score": r.get("resonance_score"),
                "creativity_score": r.get("creativity_score"),
                "ui_ux_score": r.get("ui_ux_score"),
            }
            for r in runs
        ],
        "summary": summary,
    }


@app.post("/api/admin/queue/clear-finished")
async def api_admin_clear_finished(_admin: Dict[str, Any] = Depends(require_admin)):
    """Admin: removes completed/failed tasks from the queue list (reports are not affected)."""
    removed = clear_finished_tasks()
    return {"status": "ok", "removed": removed, "message": f"Cleared {removed} finished task{'s' if removed != 1 else ''}."}


@app.post("/api/admin/queue/clear-pending")
async def api_admin_clear_pending(_admin: Dict[str, Any] = Depends(require_admin)):
    """Admin: cancels/clears all waiting/pending tasks from the queue (running tasks are untouched)."""
    removed = clear_pending_tasks()
    return {"status": "ok", "removed": removed, "message": f"Cleared {removed} pending task{'s' if removed != 1 else ''}."}


@app.post("/api/admin/reclassify-all")
async def api_admin_reclassify_all(_admin: Dict[str, Any] = Depends(require_admin)):
    """Admin: re-derive category, platforms and device profile for every project (no regrade)."""
    targets = get_all_project_targets()
    for target in targets:
        enqueue_task(target=target, mode="reclassify", requested_by="admin_reclassify", force_override=True)
    return {
        "status": "ok",
        "requeued_count": len(targets),
        "message": f"Queued {len(targets)} projects for reclassification (~5-15s each, no regrade).",
    }


@app.get("/api/admin/active-viewers")
async def api_admin_active_viewers(_admin: Dict[str, Any] = Depends(require_admin)):
    """Admin: lists active visitors seen in the last 2 minutes (anonymized for guests), excluding the requesting admin."""
    viewers = _get_active_viewers(exclude_username=_admin.get("username"))
    return {
        "status": "ok",
        "count": len(viewers),
        "window_seconds": _ACTIVE_VIEWER_WINDOW_SECONDS,
        "viewers": viewers,
        "page_views": get_page_views(limit=30),
    }


@app.post("/api/admin/backfill-screenshots")
async def api_admin_backfill_screenshots(_admin: Dict[str, Any] = Depends(require_admin)):
    """Admin: discover screenshot links for already-analyzed GitHub repos without regrading."""
    from fastapi.concurrency import run_in_threadpool
    from ..core.screenshots import backfill_missing_screenshots

    summary = await run_in_threadpool(backfill_missing_screenshots, None, 4, None, True)
    return {
        "status": "ok",
        **summary,
        "message": f"Checked {summary['checked']} repos: updated {summary['updated']}, none found for {summary['skipped']}.",
    }

@app.post("/api/admin/clear-database")
async def api_admin_clear_database(_admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Wipes all database tables for a clean test or launch reset."""
    # Free VRAM / unload models first to release any open file locks on reports
    client = OllamaClient()
    client.unload_all_models()
    
    clear_database_everywhere()
    success = True
    if not success:
        raise HTTPException(status_code=500, detail="Failed to clear database tables.")
        
    return {
        "status": "ok",
        "message": "Database successfully wiped and reset for fresh evaluation."
    }

@app.post("/api/admin/system/free-vram")
async def api_admin_free_vram(_admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Unload all active models from GPU VRAM."""
    client = OllamaClient()
    unloaded = client.unload_all_models()
    return {
        "status": "ok",
        "unloaded_count": unloaded,
        "message": f"Successfully freed GPU memory (unloaded {unloaded} active model(s)).",
    }


@app.post("/api/admin/ollama/start")
async def api_admin_start_ollama(_admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Trigger daemon startup."""
    client = OllamaClient()
    success = client.ensure_server_running(timeout_sec=15.0)
    return {"status": "ok" if success else "error", "running": success}


@app.post("/api/admin/developers/{username}/delete-all")
async def api_admin_delete_developer(username: str, req: DeleteAccountRequest, admin: Dict[str, Any] = Depends(require_admin)):
    """
    Admin: deletes everything LetsVibe holds about one developer (every project, and the account if one exists),
    exactly like the owner's own "Delete my account": from the live site and every backup, with nobody notified.
    For handling "please delete me" requests that arrive by email.
    """
    if req.confirm_username.strip().lower() != username.strip().lower():
        raise HTTPException(status_code=400, detail="Type the developer's username exactly to confirm.")
    if username.strip().lower() == str(admin.get("username", "")).strip().lower():
        raise HTTPException(status_code=400, detail="This is your own admin account; delete your projects one by one instead.")
    db_user = get_user_by_username(username)
    if db_user:
        job = privacy_jobs.submit("account", db_user["github_username"], str(db_user["github_id"]),
                                  avatar_url=db_user.get("github_avatar_url") or "")
    else:  # projects listed without an account row (older data): remove the projects
        job = privacy_jobs.submit("projects", username, f"no-account:{username.lower()}")
    return {"status": "ok", **job.public()}


@app.get("/api/admin/projects-tree")
async def api_admin_projects_tree(search: Optional[str] = Query(None), admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Returns tree of graded projects grouped by developer."""
    tree = get_projects_tree(search=search)
    me = str(admin.get("username", "")).lower()
    for dev in tree:
        dev["is_admin"] = str(dev.get("developer", "")).lower() == me  # no "delete all user data" on your own row
    return {"status": "ok", "tree": tree}


class ForceRescanRequest(BaseModel):
    full_slug: str
    mode: str = "blend"
    project_profile: str = "auto"
    provenance: str = "vibe_coded"
    guest_access: str = "auto"
    user_hook: str = ""


@app.post("/api/admin/project/force-rescan")
async def api_admin_force_rescan(req: ForceRescanRequest, _admin: Dict[str, Any] = Depends(require_admin)):
    """
    Admin: re-grade a project that is already on the leaderboard, even if its latest commit was graded before
    (the "already evaluated at this commit" check lives in the submit endpoints, not in the worker). Used after a
    rubric change or to benchmark historical/testing engines. The vibe-coded check still applies.
    """
    from ..evaluator.pipeline import get_engine_info, is_testing_engine as check_testing_engine

    slug = urllib.parse.unquote(req.full_slug).strip()
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT canonical_url, is_test FROM projects WHERE lower(full_slug) = lower(?)", (slug,)).fetchone()
    finally:
        conn.close()
    if not row or not row["canonical_url"]:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    mode = (req.mode or "blend").strip()
    is_test_engine = check_testing_engine(mode)
    is_test = bool(row["is_test"]) or is_test_engine
    submission_meta = {
        "project_profile": (req.project_profile or "auto").strip(),
        "provenance": (req.provenance or "vibe_coded").strip(),
        "guest_access": (req.guest_access or "auto").strip(),
        "user_hook": (req.user_hook or "").strip()[:240],
    }
    task_id = enqueue_task(
        target=row["canonical_url"],
        mode=mode,
        requested_by="admin_force_rescan",
        is_test=is_test,
        submission_meta=submission_meta,
    )
    info = get_engine_info(mode)
    eng_short = (info or {}).get("short_name") or mode
    return {
        "status": "ok",
        "task_id": task_id,
        "is_test": is_test,
        "message": f"Queued a fresh grade of '{slug}' with {eng_short} (same-commit check skipped).",
    }


class ShareTestProjectRequest(BaseModel):
    full_slug: str
    shared: bool


@app.post("/api/admin/project/share")
async def api_admin_share_test_project(req: ShareTestProjectRequest, _admin: Dict[str, Any] = Depends(require_admin)):
    """
    TEST MODE (remove before launch): shares an admin test grading with its developer, or stops sharing it. A shared
    test project's badge and report page open for anyone with the link; it stays off the leaderboard, creators and
    Hall of Fame, and out of public grades, curves and #1 badges. Only test projects can be shared this way.
    """
    slug = urllib.parse.unquote(req.full_slug).strip()
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT id, is_test FROM projects WHERE lower(full_slug) = lower(?)", (slug,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
        if not row["is_test"]:
            raise HTTPException(status_code=400, detail="Only admin test gradings need sharing; other projects are public.")
        with conn:
            conn.execute("UPDATE projects SET shared = ? WHERE id = ?", (1 if req.shared else 0, row["id"]))
    finally:
        conn.close()
    return {"status": "ok", "shared": req.shared,
            "message": "Shared: its badge and report now open for anyone with the link." if req.shared
            else "No longer shared: only you can see it again."}


@app.delete("/api/admin/project/{full_slug:path}")
async def api_admin_delete_project(full_slug: str, _admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Deletes project, historical badges, and records from database."""
    clean_slug = urllib.parse.unquote(full_slug).strip()
    success = delete_project_everywhere(clean_slug)
    if not success and clean_slug != full_slug:
        success = delete_project_everywhere(full_slug)
    if not success:
        raise HTTPException(status_code=404, detail=f"Project '{clean_slug}' not found")
    return {"status": "ok", "message": f"Project '{clean_slug}' deleted successfully."}


@app.delete("/api/admin/report/{report_id:int}")
async def api_admin_delete_single_report(report_id: int, _admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Deletes a single evaluation report record from live DB and backups."""
    from ..storage.backups import delete_single_report_everywhere
    success = delete_single_report_everywhere(report_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Report #{report_id} not found")
    return {"status": "ok", "message": f"Report #{report_id} deleted successfully."}


# ==========================================
# Appeals & Force Override Endpoints
# ==========================================

class AppealSubmitRequest(BaseModel):
    target: str
    submitter_email: Optional[str] = None  # ignored: LetsVibe does not collect email addresses
    message: str
    task_id: Optional[int] = None
    full_slug: Optional[str] = None


class AppealReviewRequest(BaseModel):
    appeal_id: int
    approved: bool
    admin_response: str = ""


class TaskOverrideRequest(BaseModel):
    task_id: int


@app.post("/api/appeal/submit")
async def api_submit_appeal(req: AppealSubmitRequest, request: Request):
    """
    User endpoint: submits an appeal against a non-vibe rejection. Requires sign-in and ownership
    of the repository. If profane or violent language is detected, it is flagged, placed at the
    bottom of the review queue and no notification email is sent.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in with GitHub to submit an appeal.")
    raw_ref = (req.target or req.full_slug or "").strip()
    identity = parse_repo_identity(raw_ref)
    if req.full_slug and req.target:
        slug_ident = parse_repo_identity(req.full_slug.strip())
        if slug_ident.full_slug.lower() != identity.full_slug.lower():
            raise HTTPException(status_code=400, detail="Repository slug does not match target URL.")
    if identity.provider != "github" or not identity.is_remote:
        raise HTTPException(status_code=400, detail="Appeals are only supported for public GitHub repositories.")
    if not owns_repo(user, identity.owner):
        raise HTTPException(status_code=403, detail="Only the repository's owner can appeal its result.")
    if len(req.message) > 4000:
        raise HTTPException(status_code=400, detail="Appeal message is too long (4,000 characters max).")
    mod_result = analyze_appeal_text(req.message)

    appeal_id = create_appeal(
        target=identity.canonical_url,
        submitter_email="",
        submitted_by=user.get("username", ""),
        message=req.message,
        task_id=req.task_id,
        full_slug=identity.full_slug,
        is_profane=mod_result.is_profane,
        is_violent=mod_result.is_violent,
    )

    if not mod_result.is_clean:
        return {
            "status": "flagged",
            "is_violent": mod_result.is_violent,
            "is_profane": mod_result.is_profane,
            "message": "Your appeal was received but contains language that violates our guidelines, so it has been placed at the end of the review queue.",
            "appeal_id": appeal_id,
        }

    # Clean appeal: notify the site admin
    appeal_dict = {
        "id": appeal_id,
        "target": identity.canonical_url,
        "full_slug": identity.full_slug,
        "submitted_by": user.get("username", ""),
        "message": req.message,
    }
    send_appeal_to_admin(appeal_dict)

    return {
        "status": "ok",
        "message": "Your appeal has been submitted for review.",
        "appeal_id": appeal_id,
    }


@app.get("/api/admin/appeals")
async def api_admin_get_appeals(status: Optional[str] = Query(None), _admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Returns appeals list (clean first oldest-to-newest, flagged at bottom)."""
    appeals = get_appeals(status=status)
    return {"status": "ok", "items": appeals}


@app.post("/api/admin/appeal/review")
async def api_admin_review_appeal(req: AppealReviewRequest, _admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Approves or declines an appeal, emailing the user with the admin's reason."""
    # If response is blank on a flagged appeal, apply default professional boundary notice
    response_text = req.admin_response.strip()
    if not response_text:
        # Check if appeal was flagged
        existing = [a for a in get_appeals() if a["id"] == req.appeal_id]
        if existing and (existing[0]["is_profane"] or existing[0]["is_violent"]):
            response_text = "Resubmit and rephrase appeal professionally if you want me to read it."

    reviewed = review_appeal(req.appeal_id, approved=req.approved, admin_response=response_text)
    if not reviewed:
        raise HTTPException(status_code=404, detail="Appeal not found")

    # No email is sent: the owner sees the decision on their My Data page.

    return {"status": "ok", "appeal": reviewed}


@app.post("/api/admin/appeals/decline-flagged")
async def api_admin_decline_all_flagged(_admin: Dict[str, Any] = Depends(require_admin)):
    """
    Admin endpoint: Automatically declines all violent or profane flagged appeals in bulk,
    emailing each user the standard professional boundary response.
    """
    declined = decline_all_flagged_appeals()

    return {
        "status": "ok",
        "declined_count": len(declined),
        "items": declined,
    }


@app.post("/api/admin/task/override")
async def api_admin_task_override(req: TaskOverrideRequest, _admin: Dict[str, Any] = Depends(require_admin)):
    """Admin endpoint: Forces an evaluation audit of any rejected or failed task."""
    success = force_override_task(req.task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")

    # If worker is active, notify it
    if worker:
        worker.process_task_now(req.task_id)

    return {"status": "ok", "message": f"Task #{req.task_id} force override initiated."}


# ==========================================
# Frontend Dashboard HTML
# ==========================================

from .ui import PAGES, render_admin_messages_html, render_dashboard_html, render_page

@app.get("/", response_class=HTMLResponse)
async def user_dashboard(request: Request):
    """Serves the polished interactive Leaderboard & User UI."""
    view = (request.query_params.get("view") or "").strip().lower()
    if view not in ("creators", "hof", "reviews", "admin"):
        _record_non_admin_page_view(request, "/")
    return HTMLResponse(render_dashboard_html(view="user"))


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Serves the Admin Control & Queue Console UI."""
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(render_dashboard_html(view="admin"))


@app.get("/admin/messages", response_class=HTMLResponse)
async def admin_messages_page(request: Request):
    """Serves the dedicated split-screen Admin Chat & Tag Triage UI."""
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(render_admin_messages_html(), headers={"Cache-Control": "no-cache"})


@app.get("/{page_slug}", response_class=HTMLResponse, include_in_schema=False)
async def content_page(page_slug: str, request: Request):
    """Content pages: how-it-works, faq, privacy, cookies, terms, remove, support, changelog, my-data."""
    if page_slug not in PAGES:
        raise HTTPException(status_code=404, detail="Page not found.")
    _record_non_admin_page_view(request, f"/{page_slug}")
    return HTMLResponse(render_page(page_slug), headers={"Cache-Control": "no-cache"})


@app.get("/project/{full_slug:path}", response_class=HTMLResponse)
async def project_page_view(full_slug: str, request: Request):
    """Serves the dedicated project scorecard & telemetry page."""
    _record_non_admin_page_view(request, f"/project/{full_slug}")
    return HTMLResponse(render_dashboard_html(view="project", project_slug=full_slug))
