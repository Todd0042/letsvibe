"""
Isolated SQLite persistence for the Anna Edition leaderboard & reports.
Uses a dedicated database (`data/anna_letsvibe.db`) so Anna runs never
pollute the canonical PC leaderboard (`data/letsvibe.db`).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.platforms import normalize_category
from ..evaluator.rubric import calculate_letter_grade
from .db import rubric_version_rank
from .identity import parse_repo_identity
from .peer_grades import global_curve_details

DEFAULT_ANNA_DB_PATH = Path(
    os.environ.get("ANNA_DB_PATH", "data/anna_letsvibe.db")
)
SCHEMA_PATH = Path(__file__).with_name("schema_anna.sql")
RETENTION_DAYS = 15


def get_anna_db_connection(db_path: Path = DEFAULT_ANNA_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_anna_db(db_path: Path = DEFAULT_ANNA_DB_PATH) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_anna_db_connection(db_path) as conn:
        for ddl in (
            "ALTER TABLE projects ADD COLUMN submitted_by TEXT;",
            "ALTER TABLE projects ADD COLUMN deleted_at TEXT;",
            "ALTER TABLE projects ADD COLUMN purge_after TEXT;",
            "ALTER TABLE projects ADD COLUMN deleted_by TEXT;",
            "ALTER TABLE projects ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0;",
            "ALTER TABLE reports ADD COLUMN submitted_by TEXT;",
            "ALTER TABLE reports ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0;",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.executescript(schema_sql)
        conn.execute(
            "DELETE FROM projects WHERE deleted_at IS NOT NULL AND purge_after IS NOT NULL AND purge_after <= ?",
            (_utc_now(),),
        )
        conn.commit()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _primary_language(report: Dict[str, Any]) -> str:
    langs = (report.get("deterministic_metrics") or {}).get("languages") or {}
    if isinstance(langs, dict) and langs:
        try:
            return str(max(langs.items(), key=lambda kv: float(kv[1] or 0))[0])
        except Exception:
            pass
    return ""


def recompute_anna_curve(conn: sqlite3.Connection) -> None:
    """
    Recomputes curved_score and curved_letter_grade across active Anna reports
    using the latest report per non-deleted project as the reference cohort.
    """
    latest_rows = conn.execute(
        """
        SELECT r.id, r.project_id, r.final_score
        FROM reports r
        JOIN projects p ON p.id = r.project_id
        WHERE r.is_latest = 1 AND p.deleted_at IS NULL AND COALESCE(p.is_test, 0) = 0
        """
    ).fetchall()
    cohort = [float(r["final_score"]) for r in latest_rows]
    if not cohort:
        return

    all_reports = conn.execute(
        """
        SELECT r.id, r.project_id, r.final_score, r.is_latest, p.deleted_at, COALESCE(p.is_test, 0) AS is_test
        FROM reports r
        JOIN projects p ON p.id = r.project_id
        """
    ).fetchall()
    for r in all_reports:
        raw = float(r["final_score"])
        is_active_latest = int(r["is_latest"]) == 1 and r["deleted_at"] is None and int(r["is_test"]) == 0
        scores_for_row = cohort if is_active_latest else (cohort + [raw])
        details = global_curve_details(raw, scores_for_row)
        curved = round(float(details["curved_score"]), 1)
        c_grade = calculate_letter_grade(curved)
        conn.execute(
            "UPDATE reports SET curved_score = ?, curved_letter_grade = ? WHERE id = ?",
            (curved, c_grade, int(r["id"])),
        )
        if int(r["is_latest"]) == 1:
            conn.execute(
                "UPDATE projects SET curved_score = ?, curved_letter_grade = ? WHERE id = ?",
                (curved, c_grade, int(r["project_id"])),
            )


def _refresh_project_latest(conn: sqlite3.Connection, project_id: int) -> None:
    rows = conn.execute(
        "SELECT * FROM reports WHERE project_id = ? ORDER BY rubric_rank DESC, id DESC",
        (project_id,),
    ).fetchall()
    if not rows:
        return
    winner = rows[0]
    wid = int(winner["id"])
    conn.execute("UPDATE reports SET is_latest = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE project_id = ?", (wid, project_id))
    conn.execute(
        """
        UPDATE projects
        SET latest_report_id = ?,
            final_score = ?,
            letter_grade = ?,
            rubric_version = ?,
            model_name = ?,
            submitted_by = COALESCE(?, submitted_by),
            graded_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            wid,
            float(winner["final_score"]),
            str(winner["letter_grade"]),
            str(winner["rubric_version"]),
            winner["model_name"],
            winner["submitted_by"],
            str(winner["created_at"]),
            _utc_now(),
            project_id,
        ),
    )


def save_anna_report(
    report: Dict[str, Any],
    *,
    submitted_by: str = "",
    is_test: bool = False,
    repo_icon_url: str = "",
    owner_avatar_url: str = "",
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Dict[str, Any]:
    """Saves an Anna report into anna_letsvibe.db and updates cohort curve."""
    ident = parse_repo_identity(
        str(report.get("repo_source") or report.get("full_slug") or "")
    )
    owner = ident.owner or str(report.get("owner") or "").strip()
    repo_name = ident.repo_name or str(report.get("repo_name") or "").strip()
    full_slug = f"{owner}/{repo_name}" if owner and repo_name else str(report.get("full_slug") or "")
    canonical_url = ident.canonical_url or str(report.get("repo_source") or f"https://github.com/{full_slug}")

    rv = str(report.get("rubric_version") or "v11a").strip()
    rk = rubric_version_rank(rv)
    final_score = round(float(report.get("final_vibe_score") or report.get("final_score") or 0.0), 1)
    letter_grade = str(report.get("letter_grade") or calculate_letter_grade(final_score))
    model_name = str(report.get("model_name") or "").strip() or None
    arch = report.get("archetype") or {}
    arch_cat = str(arch.get("category") or "Other")
    arch_name = str(arch.get("name") or arch_cat)
    gen_cat = normalize_category(arch_cat)
    lang = _primary_language(report)
    now = str(report.get("timestamp") or _utc_now())

    factor_evals = report.get("factor_evaluations") or {}
    factor_scores = {
        k: round(float(v.get("score") or 0.0), 1)
        for k, v in factor_evals.items()
        if isinstance(v, dict) and k != "malware_risk"
    }

    with get_anna_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM projects WHERE lower(full_slug) = lower(?)",
            (full_slug,),
        ).fetchone()
        if row:
            project_id = int(row["id"])
            conn.execute(
                """
                UPDATE projects
                SET repo_icon_url = COALESCE(NULLIF(?, ''), repo_icon_url),
                    owner_avatar_url = COALESCE(NULLIF(?, ''), owner_avatar_url),
                    primary_language = COALESCE(NULLIF(?, ''), primary_language),
                    archetype_category = ?,
                    archetype_name = ?,
                    general_category = ?,
                    submitted_by = COALESCE(NULLIF(?, ''), submitted_by),
                    deleted_at = NULL,
                    purge_after = NULL,
                    deleted_by = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    repo_icon_url,
                    owner_avatar_url or (f"https://github.com/{owner}.png?size=96" if owner else ""),
                    lang,
                    arch_cat,
                    arch_name,
                    gen_cat,
                    submitted_by,
                    _utc_now(),
                    project_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO projects (
                    provider, owner, repo_name, full_slug, canonical_url,
                    repo_icon_url, owner_avatar_url, primary_language,
                    archetype_category, archetype_name, general_category,
                    rubric_version, model_name, submitted_by, is_test, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "github",
                    owner,
                    repo_name,
                    full_slug,
                    canonical_url,
                    repo_icon_url or "",
                    owner_avatar_url or (f"https://github.com/{owner}.png?size=96" if owner else ""),
                    lang,
                    arch_cat,
                    arch_name,
                    gen_cat,
                    rv,
                    model_name,
                    submitted_by or owner,
                    1 if is_test else 0,
                    now,
                    now,
                ),
            )
            project_id = int(cur.lastrowid)

        cur_rep = conn.execute(
            """
            INSERT INTO reports (
                project_id, rubric_version, rubric_rank, model_name, submitted_by, is_test, commit_sha,
                final_score, letter_grade, verdict_title, verdict_summary,
                factor_scores_json, report_data_json, is_latest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                project_id,
                rv,
                rk,
                model_name,
                submitted_by or owner,
                1 if is_test else 0,
                str(report.get("commit_sha") or ""),
                final_score,
                letter_grade,
                str(report.get("verdict_title") or ""),
                str(report.get("verdict_summary") or ""),
                json.dumps(factor_scores),
                json.dumps(report),
                now,
            ),
        )
        report_id = int(cur_rep.lastrowid)
        _refresh_project_latest(conn, project_id)
        recompute_anna_curve(conn)
        conn.commit()

        saved = conn.execute("SELECT curved_score, curved_letter_grade FROM reports WHERE id = ?", (report_id,)).fetchone()
        return {
            "project_id": project_id,
            "report_id": report_id,
            "full_slug": full_slug,
            "final_score": final_score,
            "letter_grade": letter_grade,
            "curved_score": float(saved["curved_score"]) if saved else final_score,
            "curved_letter_grade": str(saved["curved_letter_grade"]) if saved else letter_grade,
            "rubric_version": rv,
        }


def get_anna_leaderboard(
    *,
    scale: str = "curve",
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    user_login: Optional[str] = None,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Dict[str, Any]:
    with get_anna_db_connection(db_path) as conn:
        clauses = ["latest_report_id IS NOT NULL", "deleted_at IS NULL", "COALESCE(is_test, 0) = 0"]
        params: List[Any] = []
        if category and category.lower() != "all":
            clauses.append("lower(general_category) = lower(?)")
            params.append(category)
        if search:
            clauses.append("(lower(full_slug) LIKE lower(?) OR lower(archetype_category) LIKE lower(?))")
            params.extend([f"%{search}%", f"%{search}%"])
        where_sql = " AND ".join(clauses)
        rows = conn.execute(
            f"""
            SELECT * FROM projects
            WHERE {where_sql}
            ORDER BY final_score DESC, upvote_count DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()

        total = int(
            conn.execute(f"SELECT COUNT(*) FROM projects WHERE {where_sql}", params).fetchone()[0]
        )

        upvoted_ids = set()
        if user_login:
            u_rows = conn.execute(
                "SELECT project_id FROM upvotes WHERE lower(user_login) = lower(?)",
                (user_login,),
            ).fetchall()
            upvoted_ids = {int(u["project_id"]) for u in u_rows}

        items: List[Dict[str, Any]] = []
        from .db import upvote_score_bonus
        from ..evaluator.rubric import calculate_letter_grade
        for r in rows:
            d = dict(r)
            base_score = round(float(d.get("final_score") or 0.0), 1)
            up_cnt = int(d.get("upvote_count") or 0)
            up_bonus = upvote_score_bonus(up_cnt)
            boosted = round(min(100.0, base_score + up_bonus), 1)
            grade = calculate_letter_grade(boosted)
            d["rubric_score"] = base_score
            d["final_score"] = boosted
            d["curved_score"] = boosted
            d["letter_grade"] = grade
            d["curved_letter_grade"] = grade
            d["upvote_bonus"] = up_bonus
            d["user_upvoted"] = int(r["id"]) in upvoted_ids
            items.append(d)
        items.sort(key=lambda x: (-float(x["final_score"]), -int(x.get("upvote_count") or 0)))
        for idx, d in enumerate(items, start=offset + 1):
            d["rank"] = idx
        return {"projects": items, "total": total, "scale": "raw"}


def get_anna_creators(db_path: Path = DEFAULT_ANNA_DB_PATH) -> List[Dict[str, Any]]:
    with get_anna_db_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT owner, owner_avatar_url, full_slug, primary_language,
                   final_score, curved_score, upvote_count
            FROM projects
            WHERE latest_report_id IS NOT NULL AND deleted_at IS NULL AND COALESCE(is_test, 0) = 0
            ORDER BY final_score DESC
            """
        ).fetchall()
    by_owner: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        owner = str(r["owner"] or "")
        if not owner:
            continue
        entry = by_owner.setdefault(
            owner,
            {
                "owner": owner,
                "owner_avatar_url": r["owner_avatar_url"] or f"https://github.com/{owner}.png?size=96",
                "total_repos": 0,
                "scores": [],
                "raw_scores": [],
                "languages": [],
                "total_upvotes": 0,
                "top_repo_slug": r["full_slug"],
                "_best_score": -1.0,
            },
        )
        entry["total_repos"] += 1
        rs = float(r["final_score"] or 0.0)
        up_cnt = int(r["upvote_count"] or 0)
        boosted = min(100.0, rs + upvote_score_bonus(up_cnt))
        entry["scores"].append(boosted)
        entry["raw_scores"].append(rs)
        entry["total_upvotes"] += up_cnt
        lang = str(r["primary_language"] or "").strip()
        if lang and lang not in entry["languages"]:
            entry["languages"].append(lang)
        if boosted > entry["_best_score"]:
            entry["_best_score"] = boosted
            entry["top_repo_slug"] = r["full_slug"]

    creators = []
    for c in by_owner.values():
        c["avg_curved_score"] = round(sum(c["scores"]) / len(c["scores"]), 1)
        c["avg_raw_score"] = round(sum(c["raw_scores"]) / len(c["raw_scores"]), 1)
        del c["_best_score"]
        creators.append(c)
    creators.sort(key=lambda x: (-x["avg_curved_score"], -x["total_repos"]))
    return creators


def get_anna_stats(db_path: Path = DEFAULT_ANNA_DB_PATH) -> Dict[str, Any]:
    with get_anna_db_connection(db_path) as conn:
        total_projects = int(
            conn.execute("SELECT COUNT(*) FROM projects WHERE latest_report_id IS NOT NULL AND deleted_at IS NULL AND COALESCE(is_test, 0) = 0").fetchone()[0]
        )
        total_reports = int(
            conn.execute("SELECT COUNT(*) FROM reports r JOIN projects p ON p.id = r.project_id WHERE p.deleted_at IS NULL AND COALESCE(p.is_test, 0) = 0").fetchone()[0]
        )
        total_creators = int(
            conn.execute("SELECT COUNT(DISTINCT lower(owner)) FROM projects WHERE latest_report_id IS NOT NULL AND deleted_at IS NULL AND COALESCE(is_test, 0) = 0").fetchone()[0]
        )
        avg_row = conn.execute(
            "SELECT AVG(final_score) AS avg_raw, AVG(curved_score) AS avg_curved FROM projects WHERE latest_report_id IS NOT NULL AND deleted_at IS NULL AND COALESCE(is_test, 0) = 0"
        ).fetchone()
    return {
        "total_projects": total_projects,
        "total_reports": total_reports,
        "total_creators": total_creators,
        "avg_raw_score": round(float(avg_row["avg_raw"] or 0.0), 1),
        "avg_curved_score": round(float(avg_row["avg_curved"] or 0.0), 1),
    }


def _can_view_deleted(proj: sqlite3.Row, user_login: Optional[str], is_admin: bool) -> bool:
    if is_admin:
        return True
    if not user_login:
        return False
    low = user_login.strip().lower()
    allowed = {
        str(proj["owner"] or "").lower(),
        str(proj["submitted_by"] or "").lower(),
        str(proj["deleted_by"] or "").lower(),
    }
    return low in allowed


def get_anna_project_detail(
    owner: str,
    repo_name: str,
    *,
    report_id: Optional[int] = None,
    user_login: Optional[str] = None,
    is_admin: bool = False,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Optional[Dict[str, Any]]:
    slug = f"{owner}/{repo_name}"
    with get_anna_db_connection(db_path) as conn:
        proj = conn.execute(
            "SELECT * FROM projects WHERE lower(full_slug) = lower(?)",
            (slug,),
        ).fetchone()
        if not proj:
            return None
        if int(proj["is_test"] or 0) == 1 and not is_admin:
            return None
        if proj["deleted_at"] is not None and not _can_view_deleted(proj, user_login, is_admin):
            return None

        pid = int(proj["id"])
        if report_id is not None:
            rep = conn.execute(
                "SELECT * FROM reports WHERE id = ? AND project_id = ?",
                (int(report_id), pid),
            ).fetchone()
        else:
            rep = conn.execute(
                "SELECT * FROM reports WHERE project_id = ? ORDER BY is_latest DESC, rubric_rank DESC, id DESC LIMIT 1",
                (pid,),
            ).fetchone()
        if not rep:
            return None

        history_rows = conn.execute(
            """
            SELECT id, rubric_version, model_name, submitted_by, commit_sha, final_score, letter_grade,
                   curved_score, curved_letter_grade, is_latest, created_at
            FROM reports WHERE project_id = ? ORDER BY rubric_rank DESC, id DESC
            """,
            (pid,),
        ).fetchall()

        user_upvoted = False
        if user_login:
            u = conn.execute(
                "SELECT 1 FROM upvotes WHERE project_id = ? AND lower(user_login) = lower(?)",
                (pid, user_login),
            ).fetchone()
            user_upvoted = bool(u)

        from .db import upvote_score_bonus
        from ..evaluator.rubric import calculate_letter_grade
        out = dict(proj)
        base_score = round(float(rep["final_score"] or 0.0), 1)
        up_cnt = int(proj["upvote_count"] or 0)
        up_bonus = upvote_score_bonus(up_cnt)
        boosted = round(min(100.0, base_score + up_bonus), 1)
        grade = calculate_letter_grade(boosted)
        out["report_id"] = int(rep["id"])
        out["rubric_version"] = rep["rubric_version"]
        out["model_name"] = rep["model_name"]
        out["commit_sha"] = rep["commit_sha"]
        out["rubric_score"] = base_score
        out["final_score"] = boosted
        out["letter_grade"] = grade
        out["curved_score"] = boosted
        out["curved_letter_grade"] = grade
        out["upvote_bonus"] = up_bonus
        out["verdict_title"] = rep["verdict_title"]
        out["verdict_summary"] = rep["verdict_summary"]
        out["graded_at"] = rep["created_at"]
        out["is_latest"] = bool(rep["is_latest"])
        out["full_report"] = json.loads(rep["report_data_json"])
        out["user_upvoted"] = user_upvoted
        out["history"] = [dict(h) for h in history_rows]
        return out


def toggle_anna_upvote(
    owner: str,
    repo_name: str,
    user_login: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Optional[Dict[str, Any]]:
    slug = f"{owner}/{repo_name}"
    with get_anna_db_connection(db_path) as conn:
        proj = conn.execute(
            "SELECT id FROM projects WHERE lower(full_slug) = lower(?) AND deleted_at IS NULL",
            (slug,),
        ).fetchone()
        if not proj:
            return None
        pid = int(proj["id"])
        existing = conn.execute(
            "SELECT id FROM upvotes WHERE project_id = ? AND lower(user_login) = lower(?)",
            (pid, user_login),
        ).fetchone()
        if existing:
            conn.execute("DELETE FROM upvotes WHERE id = ?", (int(existing["id"]),))
            upvoted = False
        else:
            conn.execute(
                "INSERT INTO upvotes (project_id, user_login, created_at) VALUES (?, ?, ?)",
                (pid, user_login, _utc_now()),
            )
            upvoted = True
        cnt = int(conn.execute("SELECT COUNT(*) FROM upvotes WHERE project_id = ?", (pid,)).fetchone()[0])
        conn.execute("UPDATE projects SET upvote_count = ? WHERE id = ?", (cnt, pid))
        conn.commit()
        return {"full_slug": slug, "upvote_count": cnt, "user_upvoted": upvoted}


def soft_delete_anna_project(
    owner: str,
    repo_name: str,
    actor_login: str,
    *,
    is_admin: bool = False,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """
    Marks a project as deleted: immediately hidden from everyone except the
    requester/owner (and admin), and scheduled for permanent purge in 15 days.
    """
    slug = f"{owner}/{repo_name}"
    now_dt = datetime.now(timezone.utc)
    deleted_at = now_dt.isoformat()
    purge_after = (now_dt + timedelta(days=RETENTION_DAYS)).isoformat()

    with get_anna_db_connection(db_path) as conn:
        proj = conn.execute(
            "SELECT * FROM projects WHERE lower(full_slug) = lower(?)",
            (slug,),
        ).fetchone()
        if not proj:
            return None
        if not _can_view_deleted(proj, actor_login, is_admin):
            raise PermissionError("You do not have permission to delete this project.")

        conn.execute(
            "UPDATE projects SET deleted_at = ?, purge_after = ?, deleted_by = ?, updated_at = ? WHERE id = ?",
            (deleted_at, purge_after, actor_login, deleted_at, int(proj["id"])),
        )
        recompute_anna_curve(conn)
        conn.commit()
        return {
            "full_slug": slug,
            "deleted_at": deleted_at,
            "purge_after": purge_after,
            "retention_days": RETENTION_DAYS,
        }


def soft_delete_all_anna_user_projects(
    user_login: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Dict[str, Any]:
    now_dt = datetime.now(timezone.utc)
    deleted_at = now_dt.isoformat()
    purge_after = (now_dt + timedelta(days=RETENTION_DAYS)).isoformat()
    low = user_login.strip().lower()
    with get_anna_db_connection(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE projects
            SET deleted_at = COALESCE(deleted_at, ?),
                purge_after = COALESCE(purge_after, ?),
                deleted_by = COALESCE(deleted_by, ?),
                updated_at = ?
            WHERE (lower(owner) = ? OR lower(COALESCE(submitted_by, '')) = ?) AND deleted_at IS NULL
            """,
            (deleted_at, purge_after, user_login.strip(), deleted_at, low, low),
        )
        cnt = int(cur.rowcount or 0)
        if cnt:
            recompute_anna_curve(conn)
        conn.commit()
    return {"hidden_count": cnt, "purge_after": purge_after, "retention_days": RETENTION_DAYS}


def list_anna_user_projects(
    user_login: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> List[Dict[str, Any]]:
    low = user_login.strip().lower()
    with get_anna_db_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM projects
            WHERE lower(owner) = ? OR lower(COALESCE(submitted_by, '')) = ? OR lower(COALESCE(deleted_by, '')) = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (low, low, low),
        ).fetchall()
    return [dict(r) for r in rows]


def restore_anna_project(
    owner: str,
    repo_name: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Optional[Dict[str, Any]]:
    slug = f"{owner}/{repo_name}"
    with get_anna_db_connection(db_path) as conn:
        proj = conn.execute("SELECT id FROM projects WHERE lower(full_slug) = lower(?)", (slug,)).fetchone()
        if not proj:
            return None
        conn.execute(
            "UPDATE projects SET deleted_at = NULL, purge_after = NULL, deleted_by = NULL, updated_at = ? WHERE id = ?",
            (_utc_now(), int(proj["id"])),
        )
        recompute_anna_curve(conn)
        conn.commit()
        return {"full_slug": slug, "restored": True}


def purge_anna_project(
    owner: str,
    repo_name: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> bool:
    slug = f"{owner}/{repo_name}"
    with get_anna_db_connection(db_path) as conn:
        proj = conn.execute("SELECT id FROM projects WHERE lower(full_slug) = lower(?)", (slug,)).fetchone()
        if not proj:
            return False
        conn.execute("DELETE FROM projects WHERE id = ?", (int(proj["id"]),))
        recompute_anna_curve(conn)
        conn.commit()
        return True


def purge_expired_anna_projects(db_path: Path = DEFAULT_ANNA_DB_PATH) -> int:
    with get_anna_db_connection(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM projects WHERE deleted_at IS NOT NULL AND purge_after IS NOT NULL AND purge_after <= ?",
            (_utc_now(),),
        )
        purged = int(cur.rowcount or 0)
        if purged:
            recompute_anna_curve(conn)
        conn.commit()
        return purged


def get_anna_admin_overview(db_path: Path = DEFAULT_ANNA_DB_PATH) -> Dict[str, Any]:
    with get_anna_db_connection(db_path) as conn:
        stats = get_anna_stats(db_path)
        soft_deleted_count = int(
            conn.execute("SELECT COUNT(*) FROM projects WHERE deleted_at IS NOT NULL").fetchone()[0]
        )
        upvotes_count = int(conn.execute("SELECT COUNT(*) FROM upvotes").fetchone()[0])
        projects = [
            dict(r)
            for r in conn.execute("SELECT * FROM projects ORDER BY updated_at DESC, id DESC LIMIT 300").fetchall()
        ]
        reports = [
            dict(r)
            for r in conn.execute(
                """
                SELECT r.id, r.project_id, p.full_slug, r.rubric_version, r.model_name,
                       r.submitted_by, r.final_score, r.letter_grade, r.curved_score,
                       r.curved_letter_grade, r.is_latest, r.created_at
                FROM reports r
                JOIN projects p ON p.id = r.project_id
                ORDER BY r.id DESC
                LIMIT 200
                """
            ).fetchall()
        ]
        tickets = [dict(t) for t in conn.execute("SELECT user_login FROM support_tickets").fetchall()]
        bans = [dict(b) for b in conn.execute("SELECT * FROM bans").fetchall()]

    ban_by_user = {str(b["github_username"]).lower(): b for b in bans}
    users_map: Dict[str, Dict[str, Any]] = {}
    def _ensure_u(name: str) -> Optional[Dict[str, Any]]:
        clean = str(name or "").strip()
        if not clean:
            return None
        low = clean.lower()
        return users_map.setdefault(low, {
            "username": clean,
            "repo_count": 0,
            "ticket_count": 0,
            "ban": ban_by_user.get(low),
        })

    for p in projects:
        u = _ensure_u(str(p.get("submitted_by") or p.get("owner") or ""))
        if u:
            u["repo_count"] += 1
    for t in tickets:
        u = _ensure_u(str(t.get("user_login") or ""))
        if u:
            u["ticket_count"] += 1
    for b in bans:
        _ensure_u(str(b.get("github_username") or ""))
    users = sorted(users_map.values(), key=lambda x: (0 if x["ban"] else 1, -x["repo_count"], -x["ticket_count"], x["username"].lower()))

    return {
        "stats": {
            **stats,
            "soft_deleted_projects": soft_deleted_count,
            "total_upvotes": upvotes_count,
        },
        "projects": projects,
        "reports": reports,
        "users": users,
    }


def _attach_ticket_messages(conn: sqlite3.Connection, tickets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not tickets:
        return tickets
    ids = [int(t["id"]) for t in tickets]
    placeholders = ",".join("?" for _ in ids)
    msg_rows = conn.execute(
        f"SELECT * FROM support_ticket_messages WHERE ticket_id IN ({placeholders}) ORDER BY id ASC",
        ids,
    ).fetchall()
    by_tid: Dict[int, List[Dict[str, Any]]] = {}
    for m in msg_rows:
        by_tid.setdefault(int(m["ticket_id"]), []).append(dict(m))
    for t in tickets:
        tid = int(t["id"])
        msgs = by_tid.get(tid, [])
        if not msgs:
            msgs = [{
                "id": 0,
                "ticket_id": tid,
                "sender_login": t["user_login"],
                "sender_role": "user",
                "body": t["message"],
                "created_at": t["created_at"],
            }]
            if t.get("admin_reply"):
                msgs.append({
                    "id": 0,
                    "ticket_id": tid,
                    "sender_login": "admin",
                    "sender_role": "admin",
                    "body": t["admin_reply"],
                    "created_at": t["updated_at"],
                })
        t["messages"] = msgs
    return tickets


def create_anna_ticket(
    user_login: str,
    subject: str,
    message: str,
    repo_slug: str = "",
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Dict[str, Any]:
    now = _utc_now()
    body_txt = message.strip()[:4000]
    with get_anna_db_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO support_tickets (user_login, subject, repo_slug, message, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'open', ?, ?)
            """,
            (user_login.strip(), subject.strip()[:160], repo_slug.strip()[:160], body_txt, now, now),
        )
        tid = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO support_ticket_messages (ticket_id, sender_login, sender_role, body, created_at)
            VALUES (?, ?, 'user', ?, ?)
            """,
            (tid, user_login.strip(), body_txt, now),
        )
        conn.commit()
        row = dict(conn.execute("SELECT * FROM support_tickets WHERE id = ?", (tid,)).fetchone())
        return _attach_ticket_messages(conn, [row])[0]


def list_anna_user_tickets(
    user_login: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> List[Dict[str, Any]]:
    with get_anna_db_connection(db_path) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM support_tickets WHERE lower(user_login) = lower(?) ORDER BY id DESC LIMIT 100",
                (user_login.strip(),),
            ).fetchall()
        ]
        return _attach_ticket_messages(conn, rows)


def list_anna_admin_tickets(db_path: Path = DEFAULT_ANNA_DB_PATH) -> List[Dict[str, Any]]:
    with get_anna_db_connection(db_path) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM support_tickets ORDER BY CASE WHEN status = 'open' THEN 0 ELSE 1 END, id DESC LIMIT 200"
            ).fetchall()
        ]
        return _attach_ticket_messages(conn, rows)


def add_anna_ticket_reply(
    ticket_id: int,
    sender_login: str,
    sender_role: str,
    body: str,
    *,
    is_admin: bool = False,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Optional[Dict[str, Any]]:
    now = _utc_now()
    with get_anna_db_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (int(ticket_id),)).fetchone()
        if not row:
            return None
        if not is_admin and str(row["user_login"]).lower() != sender_login.strip().lower():
            raise PermissionError("You do not have permission to reply to this ticket.")
        # Backfill initial message if ticket was created before support_ticket_messages existed
        existing_cnt = int(
            conn.execute("SELECT COUNT(*) FROM support_ticket_messages WHERE ticket_id = ?", (int(ticket_id),)).fetchone()[0]
        )
        if existing_cnt == 0:
            conn.execute(
                "INSERT INTO support_ticket_messages (ticket_id, sender_login, sender_role, body, created_at) VALUES (?, ?, 'user', ?, ?)",
                (int(ticket_id), row["user_login"], row["message"], row["created_at"]),
            )
            if row["admin_reply"]:
                conn.execute(
                    "INSERT INTO support_ticket_messages (ticket_id, sender_login, sender_role, body, created_at) VALUES (?, 'admin', 'admin', ?, ?)",
                    (int(ticket_id), row["admin_reply"], row["updated_at"]),
                )
        conn.execute(
            "INSERT INTO support_ticket_messages (ticket_id, sender_login, sender_role, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (int(ticket_id), sender_login.strip(), sender_role, body.strip()[:4000], now),
        )
        next_status = "open" if sender_role == "user" else row["status"]
        conn.execute(
            "UPDATE support_tickets SET admin_reply = CASE WHEN ? = 'admin' THEN ? ELSE admin_reply END, status = ?, updated_at = ? WHERE id = ?",
            (sender_role, body.strip()[:4000], next_status, now, int(ticket_id)),
        )
        conn.commit()
        updated = dict(conn.execute("SELECT * FROM support_tickets WHERE id = ?", (int(ticket_id),)).fetchone())
        return _attach_ticket_messages(conn, [updated])[0]


def count_open_anna_tickets(
    user_login: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> int:
    with get_anna_db_connection(db_path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM support_tickets WHERE lower(user_login) = lower(?) AND status = 'open'",
                (user_login.strip(),),
            ).fetchone()[0]
        )


def get_active_anna_ban(
    username: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Optional[Dict[str, Any]]:
    if not username:
        return None
    now = _utc_now()
    with get_anna_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM bans WHERE lower(github_username) = lower(?)",
            (username.strip(),),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("expires_at") and str(d["expires_at"]) <= now:
            conn.execute("DELETE FROM bans WHERE id = ?", (int(d["id"]),))
            conn.commit()
            return None
        return d


def ban_anna_user(
    github_username: str,
    reason: str,
    duration_value: int,
    duration_unit: str,
    banned_by: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Dict[str, Any]:
    """
    Bans a GitHub user: saves ONLY (github_username, reason, duration_label, expires_at)
    and immediately hides / soft-deletes all of their projects/tickets/upvotes with 15-day hold.
    """
    uname = github_username.strip().lstrip("@")
    unit = duration_unit.strip().lower()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    purge_after = (now_dt + timedelta(days=RETENTION_DAYS)).isoformat()

    if unit == "permanent":
        duration_label = "permanent"
        expires_at = None
    else:
        val = max(1, int(duration_value or 1))
        days = val * 30 if unit.startswith("month") else val * 7 if unit.startswith("week") else val
        duration_label = f"{val} {unit.rstrip('s')}{'s' if val != 1 else ''}"
        expires_at = (now_dt + timedelta(days=days)).isoformat()

    with get_anna_db_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO bans (github_username, reason, duration_label, expires_at, banned_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(github_username) DO UPDATE SET
                reason = excluded.reason,
                duration_label = excluded.duration_label,
                expires_at = excluded.expires_at,
                banned_by = excluded.banned_by,
                created_at = excluded.created_at
            """,
            (uname, reason.strip()[:500], duration_label, expires_at, banned_by, now),
        )
        low = uname.lower()
        conn.execute(
            """
            UPDATE projects
            SET deleted_at = COALESCE(deleted_at, ?),
                purge_after = COALESCE(purge_after, ?),
                deleted_by = ?
            WHERE lower(owner) = ? OR lower(COALESCE(submitted_by, '')) = ?
            """,
            (now, purge_after, f"ban:{banned_by}", low, low),
        )
        conn.execute("DELETE FROM upvotes WHERE lower(user_login) = ?", (low,))
        conn.execute(
            "UPDATE support_tickets SET status = 'resolved', updated_at = ? WHERE lower(user_login) = ? AND status = 'open'",
            (now, low),
        )
        recompute_anna_curve(conn)
        conn.commit()
        row = dict(conn.execute("SELECT * FROM bans WHERE lower(github_username) = ?", (low,)).fetchone())
    return row


def unban_anna_user(github_username: str, db_path: Path = DEFAULT_ANNA_DB_PATH) -> bool:
    uname = github_username.strip().lstrip("@").lower()
    with get_anna_db_connection(db_path) as conn:
        cur = conn.execute("DELETE FROM bans WHERE lower(github_username) = ?", (uname,))
        conn.commit()
        return int(cur.rowcount or 0) > 0


def list_anna_bans(db_path: Path = DEFAULT_ANNA_DB_PATH) -> List[Dict[str, Any]]:
    now = _utc_now()
    with get_anna_db_connection(db_path) as conn:
        conn.execute("DELETE FROM bans WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))
        conn.commit()
        rows = conn.execute("SELECT * FROM bans ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def update_anna_ticket(
    ticket_id: int,
    *,
    admin_reply: Optional[str] = None,
    status: Optional[str] = None,
    sender_login: str = "admin",
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Optional[Dict[str, Any]]:
    if admin_reply and admin_reply.strip():
        add_anna_ticket_reply(
            ticket_id,
            sender_login,
            "admin",
            admin_reply,
            is_admin=True,
            db_path=db_path,
        )
    with get_anna_db_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (int(ticket_id),)).fetchone()
        if not row:
            return None
        next_status = status.strip().lower() if status in ("open", "resolved") else row["status"]
        conn.execute(
            "UPDATE support_tickets SET status = ?, updated_at = ? WHERE id = ?",
            (next_status, _utc_now(), int(ticket_id)),
        )
        conn.commit()
        updated = dict(conn.execute("SELECT * FROM support_tickets WHERE id = ?", (int(ticket_id),)).fetchone())
        return _attach_ticket_messages(conn, [updated])[0]


def count_open_anna_tickets(
    user_login: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> int:
    with get_anna_db_connection(db_path) as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM support_tickets WHERE lower(user_login) = lower(?) AND status = 'open'",
                (user_login.strip(),),
            ).fetchone()[0]
        )


def get_active_anna_ban(
    username: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Optional[Dict[str, Any]]:
    if not username:
        return None
    now = _utc_now()
    with get_anna_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM bans WHERE lower(github_username) = lower(?)",
            (username.strip(),),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("expires_at") and str(d["expires_at"]) <= now:
            conn.execute("DELETE FROM bans WHERE id = ?", (int(d["id"]),))
            conn.commit()
            return None
        return d


def ban_anna_user(
    github_username: str,
    reason: str,
    duration_value: int,
    duration_unit: str,
    banned_by: str,
    db_path: Path = DEFAULT_ANNA_DB_PATH,
) -> Dict[str, Any]:
    """
    Bans a GitHub user: saves ONLY (github_username, reason, duration_label, expires_at)
    and immediately hides / soft-deletes all of their projects/tickets/upvotes with 15-day hold.
    """
    uname = github_username.strip().lstrip("@")
    unit = duration_unit.strip().lower()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    purge_after = (now_dt + timedelta(days=RETENTION_DAYS)).isoformat()

    if unit == "permanent":
        duration_label = "permanent"
        expires_at = None
    else:
        val = max(1, int(duration_value or 1))
        days = val * 30 if unit.startswith("month") else val * 7 if unit.startswith("week") else val
        duration_label = f"{val} {unit.rstrip('s')}{'s' if val != 1 else ''}"
        expires_at = (now_dt + timedelta(days=days)).isoformat()

    with get_anna_db_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO bans (github_username, reason, duration_label, expires_at, banned_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(github_username) DO UPDATE SET
                reason = excluded.reason,
                duration_label = excluded.duration_label,
                expires_at = excluded.expires_at,
                banned_by = excluded.banned_by,
                created_at = excluded.created_at
            """,
            (uname, reason.strip()[:500], duration_label, expires_at, banned_by, now),
        )
        low = uname.lower()
        conn.execute(
            """
            UPDATE projects
            SET deleted_at = COALESCE(deleted_at, ?),
                purge_after = COALESCE(purge_after, ?),
                deleted_by = ?
            WHERE lower(owner) = ? OR lower(COALESCE(submitted_by, '')) = ?
            """,
            (now, purge_after, f"ban:{banned_by}", low, low),
        )
        conn.execute("DELETE FROM upvotes WHERE lower(user_login) = ?", (low,))
        conn.execute(
            "UPDATE support_tickets SET status = 'resolved', updated_at = ? WHERE lower(user_login) = ? AND status = 'open'",
            (now, low),
        )
        recompute_anna_curve(conn)
        conn.commit()
        row = dict(conn.execute("SELECT * FROM bans WHERE lower(github_username) = ?", (low,)).fetchone())
    return row


def unban_anna_user(github_username: str, db_path: Path = DEFAULT_ANNA_DB_PATH) -> bool:
    uname = github_username.strip().lstrip("@").lower()
    with get_anna_db_connection(db_path) as conn:
        cur = conn.execute("DELETE FROM bans WHERE lower(github_username) = ?", (uname,))
        conn.commit()
        return int(cur.rowcount or 0) > 0


def list_anna_bans(db_path: Path = DEFAULT_ANNA_DB_PATH) -> List[Dict[str, Any]]:
    now = _utc_now()
    with get_anna_db_connection(db_path) as conn:
        conn.execute("DELETE FROM bans WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,))
        conn.commit()
        rows = conn.execute("SELECT * FROM bans ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]
