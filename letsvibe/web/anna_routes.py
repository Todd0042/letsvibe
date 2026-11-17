"""
Isolated `/api/anna/*` FastAPI router backed by `data/anna_letsvibe.db`.
Shares no rows or curve statistics with the canonical `letsvibe.db`, and matches
the API contract planned for the Cloudflare Workers + D1 mirror.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ..core.auth import SESSION_COOKIE_NAME, is_admin_user, verify_session_token
from ..storage.anna_db import (
    add_anna_ticket_reply,
    ban_anna_user,
    count_open_anna_tickets,
    create_anna_ticket,
    get_active_anna_ban,
    get_anna_admin_overview,
    get_anna_creators,
    get_anna_db_connection,
    get_anna_leaderboard,
    get_anna_project_detail,
    get_anna_stats,
    init_anna_db,
    list_anna_admin_tickets,
    list_anna_bans,
    list_anna_user_projects,
    list_anna_user_tickets,
    purge_anna_project,
    purge_expired_anna_projects,
    recompute_anna_curve,
    restore_anna_project,
    save_anna_report,
    soft_delete_all_anna_user_projects,
    soft_delete_anna_project,
    toggle_anna_upvote,
    unban_anna_user,
    update_anna_ticket,
)
from ..storage.identity import parse_repo_identity
from .badges import STYLES as BADGE_STYLES
from .badges import render as render_badge_svg

router = APIRouter(prefix="/api/anna", tags=["anna"])
_ANNA_SESSION_SECRET = os.getenv("ANNA_SESSION_SECRET", "").encode("utf-8")


class AnnaImportRequest(BaseModel):
    report: Dict[str, Any]


class AnnaTicketCreate(BaseModel):
    subject: str
    message: str
    repo_slug: str = ""


class AnnaTicketUpdate(BaseModel):
    admin_reply: Optional[str] = None
    status: Optional[str] = None


class AnnaTicketReply(BaseModel):
    body: str


class AnnaBanRequest(BaseModel):
    github_username: str
    reason: str
    duration_value: int = 7
    duration_unit: str = "days"


class AnnaUnbanRequest(BaseModel):
    github_username: str


def _verify_any_token(token: str) -> Optional[Dict[str, Any]]:
    user = verify_session_token(token)
    if user:
        return user
    if _ANNA_SESSION_SECRET:
        return verify_session_token(token, secret=_ANNA_SESSION_SECRET)
    return None


def _extract_user(request: Request) -> Optional[Dict[str, Any]]:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        user = _verify_any_token(cookie)
        if user:
            return user
    auth_header = (request.headers.get("authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            user = _verify_any_token(token)
            if user:
                return user
    return None


def _is_admin(user: Optional[Dict[str, Any]]) -> bool:
    if not user:
        return False
    return bool(user.get("is_admin")) or is_admin_user(
        str(user.get("username") or ""), user.get("github_id")
    )


def _require_admin(request: Request) -> Dict[str, Any]:
    user = _extract_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin authorization required.")
    return user  # type: ignore[return-value]


def _require_active_user(request: Request) -> Dict[str, Any]:
    user = _extract_user(request)
    if not user or not user.get("username"):
        raise HTTPException(status_code=401, detail="Sign in with GitHub first.")
    ban = get_active_anna_ban(str(user["username"]))
    if ban:
        exp = f"until {ban['expires_at'][:10]}" if ban.get("expires_at") else "permanently"
        raise HTTPException(
            status_code=403,
            detail=f"Account banned {exp} ({ban['duration_label']}): {ban['reason']}",
        )
    return user


@router.get("/health")
async def anna_health():
    init_anna_db()
    stats = get_anna_stats()
    return {"status": "ok", "backend": "vps", **stats}


@router.get("/auth/me")
async def anna_auth_me(request: Request):
    user = _extract_user(request)
    if not user:
        return {"authenticated": False, "user": None, "is_admin": False, "ban": None}
    adm = _is_admin(user)
    ban = None if adm else get_active_anna_ban(str(user.get("username") or ""))
    return {
        "authenticated": True,
        "user": {**user, "is_admin": adm},
        "is_admin": adm,
        "ban": ban,
    }


@router.get("/me/projects")
async def anna_my_projects(request: Request):
    user = _extract_user(request)
    if not user or not user.get("username"):
        raise HTTPException(status_code=401, detail="Sign in with GitHub first.")
    projects = list_anna_user_projects(str(user["username"]))
    return {"status": "ok", "projects": projects}


@router.post("/me/delete-all")
async def anna_delete_all_my_projects(request: Request):
    user = _require_active_user(request)
    res = soft_delete_all_anna_user_projects(str(user["username"]))
    return {
        "status": "ok",
        "message": f"Hidden {res['hidden_count']} project(s) from public view; permanent purge scheduled on {res['purge_after'][:10]} (15-day legal hold).",
        **res,
    }


@router.get("/leaderboard")
async def anna_leaderboard(
    request: Request,
    scale: str = Query("curve", pattern="^(curve|raw)$"),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    user = _extract_user(request)
    data = get_anna_leaderboard(
        scale=scale,
        category=category,
        search=search,
        limit=limit,
        offset=offset,
        user_login=user.get("username") if user else None,
    )
    return {"status": "ok", **data}


@router.get("/creators")
async def anna_creators():
    return {"status": "ok", "creators": get_anna_creators()}


@router.get("/stats")
async def anna_stats():
    return {"status": "ok", **get_anna_stats()}


@router.get("/project/{owner}/{repo_name}")
async def anna_project_detail(
    owner: str,
    repo_name: str,
    request: Request,
    report: Optional[int] = Query(None),
):
    user = _extract_user(request)
    proj = get_anna_project_detail(
        owner,
        repo_name,
        report_id=report,
        user_login=user.get("username") if user else None,
        is_admin=_is_admin(user),
    )
    if not proj:
        raise HTTPException(status_code=404, detail=f"Anna project '{owner}/{repo_name}' not found.")
    return {"status": "ok", "project": proj}


@router.get("/project/{owner}/{repo_name}/reports")
async def anna_project_reports(owner: str, repo_name: str, request: Request):
    user = _extract_user(request)
    proj = get_anna_project_detail(
        owner,
        repo_name,
        user_login=user.get("username") if user else None,
        is_admin=_is_admin(user),
    )
    if not proj:
        raise HTTPException(status_code=404, detail=f"Anna project '{owner}/{repo_name}' not found.")
    return {"status": "ok", "reports": proj.get("history") or []}


@router.post("/project/{owner}/{repo_name}/delete")
async def anna_request_delete_project(owner: str, repo_name: str, request: Request):
    user = _extract_user(request)
    if not user or not user.get("username"):
        raise HTTPException(status_code=401, detail="Sign in with GitHub to delete your project.")
    try:
        res = soft_delete_anna_project(
            owner,
            repo_name,
            str(user["username"]),
            is_admin=_is_admin(user),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not res:
        raise HTTPException(status_code=404, detail="Project not found.")
    return {
        "status": "ok",
        "message": (
            f"{res['full_slug']} is now hidden from all other users and scheduled for permanent "
            f"deletion on {res['purge_after'][:10]} (15-day legal-hold retention)."
        ),
        **res,
    }


@router.post("/project/{owner}/{repo_name}/upvote")
async def anna_toggle_upvote(owner: str, repo_name: str, request: Request):
    user = _extract_user(request)
    if not user or not user.get("username"):
        raise HTTPException(status_code=401, detail="Sign in with GitHub to upvote.")
    res = toggle_anna_upvote(owner, repo_name, str(user["username"]))
    if not res:
        raise HTTPException(status_code=404, detail="Project not found.")
    return {"status": "ok", **res}


@router.get("/badge/{owner}/{repo_file}")
async def anna_badge(
    owner: str,
    repo_file: str,
    style: str = Query("flat"),
    scale: str = Query("curve"),
):
    repo_name = repo_file[:-4] if repo_file.lower().endswith(".svg") else repo_file
    proj = get_anna_project_detail(owner, repo_name)
    badge_style = style if style in BADGE_STYLES else "flat"
    badge_scale = "curve" if scale == "curve" else "raw"
    svg = render_badge_svg(badge_style, proj, scale=badge_scale)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=60"},
    )


@router.post("/reports/import")
async def anna_import_report(req: AnnaImportRequest, request: Request):
    user = _require_active_user(request)

    rep = dict(req.report or {})
    rv = str(rep.get("rubric_version") or "").strip().lower()
    if rv not in ("v10a", "v10.3a", "v11a", "v12a", "v13", "v13a", "anna-v1"):
        raise HTTPException(status_code=400, detail="Only Anna edition reports (v10a/v10.3a/v11a/v12a/v13/v13a) can be imported here.")

    raw_target = str(rep.get("repo_source") or rep.get("full_slug") or "").strip()
    if not raw_target:
        raise HTTPException(status_code=400, detail="Report is missing repo_source / full_slug.")

    identity = parse_repo_identity(raw_target)
    if identity.provider != "github" or not identity.owner or not identity.repo_name:
        raise HTTPException(status_code=400, detail="Only public GitHub repositories can be imported.")

    username = str(user.get("username") or "")
    adm = _is_admin(user)
    is_other_owner = identity.owner.lower() != username.lower()
    if is_other_owner and not adm:
        raise HTTPException(
            status_code=403,
            detail=f"You can only publish repositories owned by @{username}.",
        )
    is_test_report = bool(is_other_owner and adm)
    commit_sha = str(rep.get("commit_sha") or "").strip().lower()
    if not commit_sha or len(commit_sha) < 7 or not all(c in "0123456789abcdef" for c in commit_sha):
        raise HTTPException(status_code=400, detail="Report is missing a valid commit_sha for repo verification.")

    gh_headers = {"Accept": "application/vnd.github+json", "User-Agent": "LetsVibe-Anna-Verifier"}
    gh_verify_tok = os.getenv("GITHUB_VERIFY_TOKEN", "").strip()
    if gh_verify_tok:
        gh_headers["Authorization"] = f"Bearer {gh_verify_tok}"
    owner_avatar_url = f"https://github.com/{identity.owner}.png?size=96"
    async with httpx.AsyncClient(timeout=12.0, headers=gh_headers) as client:
        repo_res = await client.get(f"https://api.github.com/repos/{identity.owner}/{identity.repo_name}")
        if repo_res.status_code != 200:
            raise HTTPException(status_code=400, detail="Repository not found on GitHub or is private.")
        repo_json = repo_res.json()
        if repo_json.get("private"):
            raise HTTPException(status_code=400, detail="Private repositories cannot be published.")
        owner_avatar_url = str((repo_json.get("owner") or {}).get("avatar_url") or owner_avatar_url)

        commit_res = await client.get(
            f"https://api.github.com/repos/{identity.owner}/{identity.repo_name}/commits/{commit_sha}"
        )
        if commit_res.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Commit '{commit_sha[:7]}' could not be verified on github.com/{identity.full_slug}.",
            )
        verified_sha = str(commit_res.json().get("sha") or commit_sha)

    rep["commit_sha"] = verified_sha
    rep["rubric_version"] = rv
    rep["full_slug"] = identity.full_slug
    rep["owner"] = identity.owner
    rep["repo_name"] = identity.repo_name
    rep["repo_source"] = identity.canonical_url

    saved = save_anna_report(
        rep,
        submitted_by=username,
        is_test=is_test_report,
        owner_avatar_url=owner_avatar_url,
    )
    return {
        "status": "ok",
        "is_test": is_test_report,
        "message": (
            f"Saved {identity.full_slug} ({rv}) as admin-only test report."
            if is_test_report
            else f"Published {identity.full_slug} ({rv}) to Anna DB."
        ),
        **saved,
    }


@router.get("/admin/overview")
async def anna_admin_overview(request: Request):
    _require_admin(request)
    return {"status": "ok", "backend": "vps", **get_anna_admin_overview()}


@router.post("/admin/project/{owner}/{repo_name}/restore")
async def anna_admin_restore(owner: str, repo_name: str, request: Request):
    _require_admin(request)
    res = restore_anna_project(owner, repo_name)
    if not res:
        raise HTTPException(status_code=404, detail="Project not found.")
    return {"status": "ok", **res}


@router.delete("/admin/project/{owner}/{repo_name}")
async def anna_admin_delete(
    owner: str,
    repo_name: str,
    request: Request,
    hard: bool = Query(False),
):
    admin = _require_admin(request)
    if hard:
        ok = purge_anna_project(owner, repo_name)
        if not ok:
            raise HTTPException(status_code=404, detail="Project not found.")
        return {"status": "ok", "purged": True, "full_slug": f"{owner}/{repo_name}"}
    res = soft_delete_anna_project(
        owner,
        repo_name,
        str(admin.get("username") or "admin"),
        is_admin=True,
    )
    if not res:
        raise HTTPException(status_code=404, detail="Project not found.")
    return {"status": "ok", **res}


@router.post("/admin/purge-expired")
async def anna_admin_purge_expired(request: Request):
    _require_admin(request)
    purged = purge_expired_anna_projects()
    return {"status": "ok", "purged_count": purged}


@router.post("/admin/recompute-curve")
async def anna_admin_recompute_curve(request: Request):
    _require_admin(request)
    with get_anna_db_connection() as conn:
        recompute_anna_curve(conn)
        conn.commit()
    return {"status": "ok", "message": "Recomputed Anna cohort bell curve."}


@router.post("/tickets")
async def anna_create_ticket(req: AnnaTicketCreate, request: Request):
    user = _require_active_user(request)
    if not _is_admin(user) and count_open_anna_tickets(str(user["username"])) >= 5:
        raise HTTPException(
            status_code=429,
            detail="You already have 5 open support tickets. Please wait for a resolution before opening another.",
        )
    if not req.subject.strip() or not req.message.strip():
        raise HTTPException(status_code=400, detail="Subject and message are required.")
    ticket = create_anna_ticket(
        str(user["username"]),
        req.subject,
        req.message,
        repo_slug=req.repo_slug,
    )
    return {"status": "ok", "ticket": ticket}


@router.get("/me/tickets")
async def anna_my_tickets(request: Request):
    user = _extract_user(request)
    if not user or not user.get("username"):
        raise HTTPException(status_code=401, detail="Sign in with GitHub to view your tickets.")
    return {"status": "ok", "tickets": list_anna_user_tickets(str(user["username"]))}


@router.post("/tickets/{ticket_id}/reply")
async def anna_ticket_reply(ticket_id: int, req: AnnaTicketReply, request: Request):
    user = _extract_user(request)
    if not user or not user.get("username"):
        raise HTTPException(status_code=401, detail="Sign in with GitHub to reply.")
    if not req.body.strip():
        raise HTTPException(status_code=400, detail="Reply message is required.")
    adm = _is_admin(user)
    try:
        updated = add_anna_ticket_reply(
            ticket_id,
            str(user["username"]),
            "admin" if adm else "user",
            req.body,
            is_admin=adm,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return {"status": "ok", "ticket": updated}


@router.get("/admin/bans")
async def anna_admin_bans(request: Request):
    _require_admin(request)
    return {"status": "ok", "bans": list_anna_bans()}


@router.post("/admin/ban")
async def anna_admin_ban(req: AnnaBanRequest, request: Request):
    admin = _require_admin(request)
    if not req.github_username.strip() or not req.reason.strip():
        raise HTTPException(status_code=400, detail="GitHub username and reason are required.")
    ban = ban_anna_user(
        req.github_username,
        req.reason,
        req.duration_value,
        req.duration_unit,
        str(admin.get("username") or "admin"),
    )
    return {"status": "ok", "ban": ban}


@router.post("/admin/unban")
async def anna_admin_unban(req: AnnaUnbanRequest, request: Request):
    _require_admin(request)
    ok = unban_anna_user(req.github_username)
    return {"status": "ok", "unbanned": ok}


@router.get("/admin/tickets")
async def anna_admin_list_tickets(request: Request):
    _require_admin(request)
    return {"status": "ok", "tickets": list_anna_admin_tickets()}


@router.post("/admin/tickets/{ticket_id}")
async def anna_admin_update_ticket(ticket_id: int, req: AnnaTicketUpdate, request: Request):
    admin = _require_admin(request)
    updated = update_anna_ticket(
        ticket_id,
        admin_reply=req.admin_reply,
        status=req.status,
        sender_login=str(admin.get("username") or "admin"),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return {"status": "ok", "ticket": updated}


@router.get("/admin/bans")
async def anna_admin_bans(request: Request):
    _require_admin(request)
    return {"status": "ok", "bans": list_anna_bans()}


@router.post("/admin/ban")
async def anna_admin_ban(req: AnnaBanRequest, request: Request):
    admin = _require_admin(request)
    if not req.github_username.strip() or not req.reason.strip():
        raise HTTPException(status_code=400, detail="GitHub username and reason are required.")
    ban = ban_anna_user(
        req.github_username,
        req.reason,
        req.duration_value,
        req.duration_unit,
        str(admin.get("username") or "admin"),
    )
    return {"status": "ok", "ban": ban}


@router.post("/admin/unban")
async def anna_admin_unban(req: AnnaUnbanRequest, request: Request):
    _require_admin(request)
    ok = unban_anna_user(req.github_username)
    return {"status": "ok", "unbanned": ok}
