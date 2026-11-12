"""
FastAPI web server for LetsVibe.
Provides modern, polished web interfaces for both users (leaderboard, podium,
hall of fame, project scorecards) and admins (queue management, pause/resume,
manual run-now triggers, and Ollama engine diagnostics).
"""

import glob
import hmac
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse

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
)
from ..storage.identity import parse_repo_identity
from ..core.moderation import analyze_appeal_text
from ..core.mailer import send_appeal_to_admin
from . import image_proxy
from .image_proxy import fetch_image
from .privacy_jobs import DeletionJobRunner
from ..storage.backups import (
    BackupScheduler,
    clear_database_everywhere,
    correct_statement_everywhere,
    delete_held_everywhere,
    delete_project_everywhere,
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
        except Exception:
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
    except Exception:
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        is_beta_pioneer=True,
        bonus_quota=1,
        terms_version=TERMS_VERSION,
    )

    session_payload = {
        "github_id": profile["github_id"],
        "username": profile["username"],
        "avatar_url": profile["avatar_url"],
        "is_admin": is_admin,
        "is_beta_pioneer": True,
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


# ==========================================
# API Endpoints
# ==========================================

@app.get("/img", include_in_schema=False)
async def api_image_proxy(u: str = Query(..., max_length=500)):
    """Same-origin proxy for GitHub avatars/icons so visitors' browsers never contact GitHub."""
    from fastapi.concurrency import run_in_threadpool

    try:
        result = await run_in_threadpool(fetch_image, u)
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


@app.get("/api/admin/reviews")
async def api_admin_reviews(_admin: Dict[str, Any] = Depends(require_admin)):
    return {"status": "ok", "counts": count_pending_reviews(), "held": list_held_for_admin(), "errors": list_error_reports()}


@app.get("/api/admin/reviews/count")
async def api_admin_reviews_count(_admin: Dict[str, Any] = Depends(require_admin)):
    return {"status": "ok", **count_pending_reviews()}


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


@app.get("/api/leaderboard")
async def api_leaderboard(
    sort_by: str = Query("overall", description="Category to sort by"),
    tier: Optional[str] = Query(None, description="Comma-separated grade tiers (A,B,C,D,F)"),
    category: Optional[str] = Query(None, description="Comma-separated general categories"),
    platform: Optional[str] = Query(None, description="Comma-separated platform ids"),
    search: Optional[str] = Query(None, description="Search keyword"),
):
    """Ranked repository list. OR within a filter group, AND across groups; `facets` holds option counts."""
    items, facets = get_leaderboard(
        sort_by=sort_by, tier_filter=tier, search=search,
        categories=category, platforms=platform, with_facets=True,
    )
    return {"status": "ok", "count": len(items), "items": items, "facets": facets}


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
async def api_creators(sort_by: str = Query("peak", description="'peak' or 'avg'")):
    """Retrieves creator leaderboard ranked by peak score or portfolio average score."""
    data = get_creators_leaderboard(sort_by=sort_by)
    return {"status": "ok", "count": len(data), "items": data}


@app.get("/api/hall-of-fame")
async def api_hall_of_fame():
    """Retrieves all-time podium champions and historic badge holders."""
    data = get_hall_of_fame()
    return {"status": "ok", "count": len(data), "items": data}


@app.get("/api/project/{full_slug:path}")
async def api_project(full_slug: str):
    """Retrieves full evaluation report, factor scores, and radar metrics for a repository."""
    data = get_project_detail(full_slug)
    if not data:
        raise HTTPException(status_code=404, detail="Project report not found")
    return {"status": "ok", "project": data}


@app.get("/api/project-html/{full_slug:path}")
async def api_project_html(full_slug: str):
    """Serves the standalone HTML report for embedding or direct viewing."""
    data = get_project_detail(full_slug)
    if not data or not data.get("html_report_path"):
        raise HTTPException(status_code=404, detail="HTML report not found")

    p = Path(data["html_report_path"])
    if not p.exists():
        raise HTTPException(status_code=404, detail="HTML file missing from disk")

    return HTMLResponse(content=p.read_text(encoding="utf-8"))


@app.get("/api/queue")
async def api_queue():
    """Returns queue status, current worker activity, and list of queued items."""
    status = get_queue_status()
    items = get_queue_items(limit=30)
    worker_current = worker.current_task_info if worker else None
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

    # Internal modes such as "reclassify" are admin-only and queued via their own endpoints
    if req.mode not in ("blend", "fast", "deep"):
        raise HTTPException(status_code=400, detail=f"Unknown audit mode '{req.mode}'.")

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
        if not (
            target.startswith("https://github.com/")
            or target.startswith("http://github.com/")
            or target.startswith("git@github.com:")
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid repository address. Currently only public GitHub repositories (https://github.com/owner/repo) are supported.",
            )
        if not owns_repo(user, parse_repo_identity(target).owner):
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
            res = subprocess.run(
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
            if res.stdout.strip():
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

    task_id = enqueue_task(target=target, mode=req.mode, requested_by=username)
    return {"status": "ok", "task_id": task_id, "message": "Enqueued successfully.", "quota": quota}


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
        res = subprocess.run(
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
async def api_task_status(task_id: int):
    """Returns live task progress, exact queue position, pause status, and the admin's pause reason."""
    task_info = get_task_status_with_position(task_id)
    if not task_info:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "ok", "task": task_info}


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

    return {
        "status": "ok",
        "ollama_live": is_live,
        "ollama_bin": ollama_bin,
        "models": models,
        "loaded_models": loaded_models,
        "free_vram_mb": vram,
        "total_projects": total_projects,
        "total_reports": total_reports,
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

@app.post("/api/admin/queue/clear-finished")
async def api_admin_clear_finished(_admin: Dict[str, Any] = Depends(require_admin)):
    """Admin: removes completed/failed tasks from the queue list (reports are not affected)."""
    removed = clear_finished_tasks()
    return {"status": "ok", "removed": removed, "message": f"Cleared {removed} finished task{'s' if removed != 1 else ''}."}


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
    appeal_owner = parse_repo_identity(req.full_slug or req.target).owner
    if not owns_repo(user, appeal_owner):
        raise HTTPException(status_code=403, detail="Only the repository's owner can appeal its result.")
    if len(req.message) > 4000:
        raise HTTPException(status_code=400, detail="Appeal message is too long (4,000 characters max).")
    mod_result = analyze_appeal_text(req.message)

    appeal_id = create_appeal(
        target=req.target,
        submitter_email="",
        submitted_by=user.get("username", ""),
        message=req.message,
        task_id=req.task_id,
        full_slug=req.full_slug,
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
        "target": req.target,
        "full_slug": req.full_slug or req.target,
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

from .ui import PAGES, render_dashboard_html, render_page

@app.get("/", response_class=HTMLResponse)
async def user_dashboard():
    """Serves the polished interactive Leaderboard & User UI."""
    return HTMLResponse(render_dashboard_html(view="user"))


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Serves the Admin Control & Queue Console UI."""
    user = get_current_user(request)
    if not user or not user.get("is_admin"):
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(render_dashboard_html(view="admin"))


@app.get("/{page_slug}", response_class=HTMLResponse, include_in_schema=False)
async def content_page(page_slug: str):
    """Content pages: how-it-works, faq, privacy, cookies, terms, remove, support, changelog, my-data."""
    if page_slug not in PAGES:
        raise HTTPException(status_code=404, detail="Page not found.")
    return HTMLResponse(render_page(page_slug), headers={"Cache-Control": "no-cache"})


@app.get("/project/{full_slug:path}", response_class=HTMLResponse)
async def project_page_view(full_slug: str):
    """Serves the dedicated project scorecard & telemetry page."""
    return HTMLResponse(render_dashboard_html(view="project", project_slug=full_slug))
