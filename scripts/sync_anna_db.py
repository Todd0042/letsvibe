#!/usr/bin/env python3
"""
Zero-loss two-way sync between VPS SQLite (`data/anna_letsvibe.db`) and Cloudflare D1.
Usage:
  python3 scripts/sync_anna_db.py --from vps --to cloudflare [--dry-run]
  python3 scripts/sync_anna_db.py --from cloudflare --to vps
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_TOKEN_PATH = Path.home() / ".config/cloudflare/api_token"
DEFAULT_VPS_DB = Path("/home/todd/storage/letsvibe-docker/app/data/anna_letsvibe.db")
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "letsvibe/storage/schema_anna.sql"


def _load_cf_token(token_arg: str | None) -> str:
    if token_arg:
        return token_arg.strip()
    env_tok = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if env_tok:
        return env_tok
    if DEFAULT_TOKEN_PATH.exists():
        return DEFAULT_TOKEN_PATH.read_text(encoding="utf-8").strip()
    raise RuntimeError("Cloudflare API token not found. Set CLOUDFLARE_API_TOKEN or ~/.config/cloudflare/api_token.")


def _cf_request(
    method: str,
    url: str,
    token: str,
    body: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "LetsVibe-Anna-Sync/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloudflare API HTTP {exc.code}: {detail}") from exc
    if not payload.get("success"):
        raise RuntimeError(f"Cloudflare API error: {payload.get('errors')}")
    return payload


def _d1_query(
    account_id: str,
    db_id: str,
    token: str,
    sql: str,
    params: List[Any] | None = None,
) -> List[Dict[str, Any]]:
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{db_id}/query"
    res = _cf_request("POST", url, token, {"sql": sql, "params": params or []})
    batches = res.get("result") or []
    if not batches:
        return []
    return list(batches[0].get("results") or [])


def _connect_sqlite(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if SCHEMA_PATH.exists():
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def sync_vps_to_cloudflare(
    vps_db: Path,
    account_id: str,
    d1_db_id: str,
    token: str,
    *,
    dry_run: bool = False,
) -> None:
    if not vps_db.exists():
        raise FileNotFoundError(f"VPS DB not found: {vps_db}")

    with _connect_sqlite(vps_db) as conn:
        projects = [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY id ASC").fetchall()]
        reports = [dict(r) for r in conn.execute("SELECT * FROM reports ORDER BY id ASC").fetchall()]
        upvotes = [dict(r) for r in conn.execute("SELECT * FROM upvotes ORDER BY id ASC").fetchall()]
        tickets = [dict(r) for r in conn.execute("SELECT * FROM support_tickets ORDER BY id ASC").fetchall()]
        ticket_msgs = [dict(r) for r in conn.execute("SELECT * FROM support_ticket_messages ORDER BY id ASC").fetchall()]
        bans = [dict(r) for r in conn.execute("SELECT * FROM bans ORDER BY id ASC").fetchall()]

    print(f"VPS snapshot: {len(projects)} projects, {len(reports)} reports, {len(tickets)} tickets, {len(bans)} bans")
    if dry_run:
        print("[dry-run] No writes sent to Cloudflare D1.")
        return

    # Ensure schema exists on D1
    for stmt in [s.strip() for s in SCHEMA_PATH.read_text(encoding="utf-8").split(";") if s.strip()]:
        _d1_query(account_id, d1_db_id, token, stmt + ";")

    proj_slug_by_id = {int(p["id"]): p["full_slug"] for p in projects}

    for p in projects:
        _d1_query(
            account_id,
            d1_db_id,
            token,
            """
            INSERT INTO projects (
                provider, owner, repo_name, full_slug, canonical_url,
                repo_icon_url, owner_avatar_url, primary_language,
                archetype_category, archetype_name, general_category,
                latest_report_id, final_score, letter_grade, curved_score,
                curved_letter_grade, rubric_version, model_name, submitted_by,
                upvote_count, is_test, deleted_at, purge_after, deleted_by,
                graded_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(full_slug) DO UPDATE SET
                repo_icon_url=excluded.repo_icon_url,
                owner_avatar_url=excluded.owner_avatar_url,
                primary_language=excluded.primary_language,
                archetype_category=excluded.archetype_category,
                archetype_name=excluded.archetype_name,
                general_category=excluded.general_category,
                final_score=excluded.final_score,
                letter_grade=excluded.letter_grade,
                curved_score=excluded.curved_score,
                curved_letter_grade=excluded.curved_letter_grade,
                rubric_version=excluded.rubric_version,
                model_name=excluded.model_name,
                submitted_by=excluded.submitted_by,
                upvote_count=excluded.upvote_count,
                is_test=excluded.is_test,
                deleted_at=excluded.deleted_at,
                purge_after=excluded.purge_after,
                deleted_by=excluded.deleted_by,
                graded_at=excluded.graded_at,
                updated_at=excluded.updated_at
            """,
            [
                p["provider"], p["owner"], p["repo_name"], p["full_slug"], p["canonical_url"],
                p["repo_icon_url"], p["owner_avatar_url"], p["primary_language"],
                p["archetype_category"], p["archetype_name"], p["general_category"],
                p["latest_report_id"], p["final_score"], p["letter_grade"], p["curved_score"],
                p["curved_letter_grade"], p["rubric_version"], p["model_name"], p.get("submitted_by"),
                p["upvote_count"], p.get("is_test", 0), p.get("deleted_at"), p.get("purge_after"), p.get("deleted_by"),
                p["graded_at"], p["created_at"], p["updated_at"],
            ],
        )

    d1_projs = _d1_query(account_id, d1_db_id, token, "SELECT id, full_slug FROM projects")
    d1_id_by_slug = {r["full_slug"].lower(): int(r["id"]) for r in d1_projs}

    existing_reps = _d1_query(account_id, d1_db_id, token, "SELECT project_id, rubric_version, created_at FROM reports")
    rep_keys = {(int(r["project_id"]), r["rubric_version"], r["created_at"]) for r in existing_reps}

    inserted_reps = 0
    for r in reports:
        slug = proj_slug_by_id.get(int(r["project_id"]), "").lower()
        dest_pid = d1_id_by_slug.get(slug)
        if not dest_pid:
            continue
        key = (dest_pid, r["rubric_version"], r["created_at"])
        if key in rep_keys:
            continue
        _d1_query(
            account_id,
            d1_db_id,
            token,
            """
            INSERT INTO reports (
                project_id, rubric_version, rubric_rank, model_name, commit_sha,
                final_score, letter_grade, curved_score, curved_letter_grade,
                verdict_title, verdict_summary, factor_scores_json,
                report_data_json, is_latest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                dest_pid, r["rubric_version"], r["rubric_rank"], r["model_name"], r["commit_sha"],
                r["final_score"], r["letter_grade"], r["curved_score"], r["curved_letter_grade"],
                r["verdict_title"], r["verdict_summary"], r["factor_scores_json"],
                r["report_data_json"], r["is_latest"], r["created_at"],
            ],
        )
        inserted_reps += 1

    for u in upvotes:
        slug = proj_slug_by_id.get(int(u["project_id"]), "").lower()
        dest_pid = d1_id_by_slug.get(slug)
        if not dest_pid:
            continue
        _d1_query(
            account_id,
            d1_db_id,
            token,
            "INSERT OR IGNORE INTO upvotes (project_id, user_login, created_at) VALUES (?, ?, ?)",
            [dest_pid, u["user_login"], u["created_at"]],
        )

    # Ensure latest_report_id on D1 projects points to a valid D1 report row
    for slug_low, dest_pid in d1_id_by_slug.items():
        _d1_query(
            account_id,
            d1_db_id,
            token,
            "UPDATE projects SET latest_report_id = (SELECT id FROM reports WHERE project_id = ? ORDER BY is_latest DESC, rubric_rank DESC, id DESC LIMIT 1) WHERE id = ?",
            [dest_pid, dest_pid],
        )

    existing_t = _d1_query(account_id, d1_db_id, token, "SELECT user_login, subject, created_at FROM support_tickets")
    t_keys = {(r["user_login"], r["subject"], r["created_at"]) for r in existing_t}
    for t in tickets:
        if (t["user_login"], t["subject"], t["created_at"]) not in t_keys:
            _d1_query(
                account_id,
                d1_db_id,
                token,
                "INSERT INTO support_tickets (user_login, subject, repo_slug, message, status, admin_reply, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [t["user_login"], t["subject"], t["repo_slug"], t["message"], t["status"], t["admin_reply"], t["created_at"], t["updated_at"]],
            )

    d1_tickets = _d1_query(account_id, d1_db_id, token, "SELECT id, user_login, subject, created_at FROM support_tickets")
    d1_tid_by_key = {(r["user_login"], r["subject"], r["created_at"]): int(r["id"]) for r in d1_tickets}
    vps_t_by_id = {int(t["id"]): (t["user_login"], t["subject"], t["created_at"]) for t in tickets}
    existing_msgs = _d1_query(account_id, d1_db_id, token, "SELECT ticket_id, sender_login, created_at FROM support_ticket_messages")
    msg_keys = {(int(m["ticket_id"]), m["sender_login"], m["created_at"]) for m in existing_msgs}
    for m in ticket_msgs:
        tk = vps_t_by_id.get(int(m["ticket_id"]))
        dest_tid = d1_tid_by_key.get(tk) if tk else None
        if dest_tid and (dest_tid, m["sender_login"], m["created_at"]) not in msg_keys:
            _d1_query(
                account_id,
                d1_db_id,
                token,
                "INSERT INTO support_ticket_messages (ticket_id, sender_login, sender_role, body, created_at) VALUES (?, ?, ?, ?, ?)",
                [dest_tid, m["sender_login"], m["sender_role"], m["body"], m["created_at"]],
            )

    for b in bans:
        _d1_query(
            account_id,
            d1_db_id,
            token,
            "INSERT INTO bans (github_username, reason, duration_label, expires_at, banned_by, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(github_username) DO UPDATE SET reason=excluded.reason, duration_label=excluded.duration_label, expires_at=excluded.expires_at",
            [b["github_username"], b["reason"], b["duration_label"], b["expires_at"], b["banned_by"], b["created_at"]],
        )

    print(f"Synced to Cloudflare D1: {len(projects)} projects, {inserted_reps} new reports, {len(tickets)} tickets, {len(bans)} bans.")


def sync_cloudflare_to_vps(
    vps_db: Path,
    account_id: str,
    d1_db_id: str,
    token: str,
    *,
    dry_run: bool = False,
) -> None:
    projects = _d1_query(account_id, d1_db_id, token, "SELECT * FROM projects ORDER BY id ASC")
    reports = _d1_query(account_id, d1_db_id, token, "SELECT * FROM reports ORDER BY id ASC")
    upvotes = _d1_query(account_id, d1_db_id, token, "SELECT * FROM upvotes ORDER BY id ASC")
    tickets = _d1_query(account_id, d1_db_id, token, "SELECT * FROM support_tickets ORDER BY id ASC")
    ticket_msgs = _d1_query(account_id, d1_db_id, token, "SELECT * FROM support_ticket_messages ORDER BY id ASC")
    bans = _d1_query(account_id, d1_db_id, token, "SELECT * FROM bans ORDER BY id ASC")
    print(f"Cloudflare D1 snapshot: {len(projects)} projects, {len(reports)} reports, {len(tickets)} tickets, {len(bans)} bans")
    if dry_run:
        print("[dry-run] No writes applied to VPS DB.")
        return

    d1_slug_by_id = {int(p["id"]): p["full_slug"] for p in projects}
    with _connect_sqlite(vps_db) as conn:
        for p in projects:
            conn.execute(
                """
                INSERT INTO projects (
                    provider, owner, repo_name, full_slug, canonical_url,
                    repo_icon_url, owner_avatar_url, primary_language,
                    archetype_category, archetype_name, general_category,
                    latest_report_id, final_score, letter_grade, curved_score,
                    curved_letter_grade, rubric_version, model_name, submitted_by,
                    upvote_count, is_test, deleted_at, purge_after, deleted_by,
                    graded_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(full_slug) DO UPDATE SET
                    final_score=excluded.final_score,
                    letter_grade=excluded.letter_grade,
                    curved_score=excluded.curved_score,
                    curved_letter_grade=excluded.curved_letter_grade,
                    rubric_version=excluded.rubric_version,
                    model_name=excluded.model_name,
                    submitted_by=excluded.submitted_by,
                    upvote_count=excluded.upvote_count,
                    is_test=excluded.is_test,
                    deleted_at=excluded.deleted_at,
                    purge_after=excluded.purge_after,
                    deleted_by=excluded.deleted_by,
                    updated_at=excluded.updated_at
                """,
                (
                    p["provider"], p["owner"], p["repo_name"], p["full_slug"], p["canonical_url"],
                    p["repo_icon_url"], p["owner_avatar_url"], p["primary_language"],
                    p["archetype_category"], p["archetype_name"], p["general_category"],
                    p["latest_report_id"], p["final_score"], p["letter_grade"], p["curved_score"],
                    p["curved_letter_grade"], p["rubric_version"], p["model_name"], p.get("submitted_by"),
                    p["upvote_count"], p.get("is_test", 0), p.get("deleted_at"), p.get("purge_after"), p.get("deleted_by"),
                    p["graded_at"], p["created_at"], p["updated_at"],
                ),
            )
        local_id_by_slug = {
            r["full_slug"].lower(): int(r["id"])
            for r in conn.execute("SELECT id, full_slug FROM projects").fetchall()
        }
        existing_keys = {
            (int(r["project_id"]), r["rubric_version"], r["created_at"])
            for r in conn.execute("SELECT project_id, rubric_version, created_at FROM reports").fetchall()
        }
        inserted = 0
        for r in reports:
            slug = d1_slug_by_id.get(int(r["project_id"]), "").lower()
            pid = local_id_by_slug.get(slug)
            if not pid or (pid, r["rubric_version"], r["created_at"]) in existing_keys:
                continue
            conn.execute(
                """
                INSERT INTO reports (
                    project_id, rubric_version, rubric_rank, model_name, submitted_by, is_test, commit_sha,
                    final_score, letter_grade, curved_score, curved_letter_grade,
                    verdict_title, verdict_summary, factor_scores_json,
                    report_data_json, is_latest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid, r["rubric_version"], r["rubric_rank"], r["model_name"], r.get("submitted_by"),
                    r.get("is_test", 0), r["commit_sha"], r["final_score"], r["letter_grade"], r["curved_score"],
                    r["curved_letter_grade"], r["verdict_title"], r["verdict_summary"], r["factor_scores_json"],
                    r["report_data_json"], r["is_latest"], r["created_at"],
                ),
            )
            inserted += 1
        for pid in local_id_by_slug.values():
            conn.execute(
                "UPDATE projects SET latest_report_id = (SELECT id FROM reports WHERE project_id = ? ORDER BY is_latest DESC, rubric_rank DESC, id DESC LIMIT 1) WHERE id = ?",
                (pid, pid),
            )
        for u in upvotes:
            slug = d1_slug_by_id.get(int(u["project_id"]), "").lower()
            pid = local_id_by_slug.get(slug)
            if pid:
                conn.execute(
                    "INSERT OR IGNORE INTO upvotes (project_id, user_login, created_at) VALUES (?, ?, ?)",
                    (pid, u["user_login"], u["created_at"]),
                )
        existing_t = {
            (r["user_login"], r["subject"], r["created_at"])
            for r in conn.execute("SELECT user_login, subject, created_at FROM support_tickets").fetchall()
        }
        for t in tickets:
            if (t["user_login"], t["subject"], t["created_at"]) not in existing_t:
                conn.execute(
                    "INSERT INTO support_tickets (user_login, subject, repo_slug, message, status, admin_reply, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (t["user_login"], t["subject"], t["repo_slug"], t["message"], t["status"], t["admin_reply"], t["created_at"], t["updated_at"]),
                )
        vps_tid_by_key = {
            (r["user_login"], r["subject"], r["created_at"]): int(r["id"])
            for r in conn.execute("SELECT id, user_login, subject, created_at FROM support_tickets").fetchall()
        }
        d1_t_by_id = {int(t["id"]): (t["user_login"], t["subject"], t["created_at"]) for t in tickets}
        existing_m = {
            (int(r["ticket_id"]), r["sender_login"], r["created_at"])
            for r in conn.execute("SELECT ticket_id, sender_login, created_at FROM support_ticket_messages").fetchall()
        }
        for m in ticket_msgs:
            tk = d1_t_by_id.get(int(m["ticket_id"]))
            dest_tid = vps_tid_by_key.get(tk) if tk else None
            if dest_tid and (dest_tid, m["sender_login"], m["created_at"]) not in existing_m:
                conn.execute(
                    "INSERT INTO support_ticket_messages (ticket_id, sender_login, sender_role, body, created_at) VALUES (?, ?, ?, ?, ?)",
                    (dest_tid, m["sender_login"], m["sender_role"], m["body"], m["created_at"]),
                )
        for b in bans:
            conn.execute(
                "INSERT INTO bans (github_username, reason, duration_label, expires_at, banned_by, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(github_username) DO UPDATE SET reason=excluded.reason, duration_label=excluded.duration_label, expires_at=excluded.expires_at",
                (b["github_username"], b["reason"], b["duration_label"], b["expires_at"], b["banned_by"], b["created_at"]),
            )
        conn.commit()
    print(f"Synced to VPS SQLite ({vps_db}): {len(projects)} projects, {inserted} new reports, {len(tickets)} tickets, {len(bans)} bans.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Two-way Anna DB sync (VPS SQLite <-> Cloudflare D1).")
    parser.add_argument("--from", dest="src", required=True, choices=["vps", "cloudflare"])
    parser.add_argument("--to", dest="dst", required=True, choices=["vps", "cloudflare"])
    parser.add_argument("--vps-db", type=Path, default=DEFAULT_VPS_DB)
    parser.add_argument("--account-id", default=os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""))
    parser.add_argument("--d1-id", default=os.environ.get("ANNA_D1_DATABASE_ID", ""))
    parser.add_argument("--token", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.src == args.dst:
        print("Source and destination must differ.", file=sys.stderr)
        return 1
    if not args.account_id or not args.d1_id:
        print("Set CLOUDFLARE_ACCOUNT_ID and ANNA_D1_DATABASE_ID (or pass --account-id / --d1-id).", file=sys.stderr)
        return 1

    token = _load_cf_token(args.token)
    if args.src == "vps":
        sync_vps_to_cloudflare(args.vps_db, args.account_id, args.d1_id, token, dry_run=args.dry_run)
    else:
        sync_cloudflare_to_vps(args.vps_db, args.account_id, args.d1_id, token, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
