"""
Held reports (owner explanation required before publishing) and owner error reports.

Held reports
  When the internal safety check fails, the report is NOT published. It is stored in `held_reports`
  with the exact lines that need explaining. The owner sees a neutral "please explain these lines"
  request on My Projects; nothing anywhere says why. The admin reviews submitted explanations and
  either publishes or removes the report. Unanswered requests are deleted (live + every backup)
  after HOLD_RESPONSE_DAYS.

Error reports
  The owner of a published report can dispute one specific statement in it. An AI review checks the
  statement against the code once; if it agrees with the owner the statement is corrected in the live
  database and every backup, otherwise the dispute goes to the admin.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .db import DEFAULT_DB_PATH, get_db_connection, init_db, strip_safety_details

HOLD_RESPONSE_DAYS = 7
MAX_FLAGGED_LINES_PER_ITEM = 12
MAX_OPEN_ERROR_REPORTS_PER_OWNER = 5
CORRECTION_NOTE = "Corrected after review"


def _now() -> datetime:
    return datetime.now()


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat(sep=" ")


# ---------------------------------------------------------------------------------------------------
# Locating flagged lines in the real files
# ---------------------------------------------------------------------------------------------------

def locate_flagged_lines(source_path: Path, files: Dict[str, str], concerns: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """
    Turns model-quoted code into exact file/line ranges by searching the ORIGINAL files (the model only
    saw a condensed copy). Falls back to the file alone when the quote cannot be found.
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for concern in concerns:
        quote_lines = [ln.strip() for ln in str(concern.get("code", "")).splitlines() if ln.strip()]
        if not quote_lines:
            continue
        wanted = (concern.get("file") or "").strip().lstrip("./")
        candidates = [p for p in files if wanted and (p == wanted or p.endswith("/" + wanted) or wanted.endswith(p))]
        candidates += [p for p in files if p not in candidates]
        found = None
        for rel in candidates:
            try:
                text = (Path(source_path) / rel).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = files.get(rel, "")
            lines = text.splitlines()
            first = quote_lines[0]
            for idx, line in enumerate(lines):
                if first and first in line.strip():
                    end = min(len(lines), idx + max(1, len(quote_lines)))
                    end = min(end, idx + MAX_FLAGGED_LINES_PER_ITEM)
                    found = {
                        "file": rel,
                        "start_line": idx + 1,
                        "end_line": end,
                        "code": "\n".join(lines[idx:end])[:2000],
                    }
                    break
            if found:
                break
        if not found:
            found = {"file": wanted or "(unknown file)", "start_line": None, "end_line": None,
                     "code": "\n".join(quote_lines)[:2000]}
        key = (found["file"], found["start_line"])
        if key not in seen:
            seen.add(key)
            out.append(found)
    return out


# ---------------------------------------------------------------------------------------------------
# Held reports
# ---------------------------------------------------------------------------------------------------

def hold_report(
    report_dict: Dict[str, Any],
    owner: str,
    repo_name: str,
    full_slug: str,
    canonical_url: str,
    flagged_lines: List[Dict[str, Any]],
    commit_sha: Optional[str] = None,
    repo_icon_url: Optional[str] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Stores a report that needs an owner explanation. Any earlier unanswered hold for the repo is replaced."""
    init_db(db_path)
    clean = strip_safety_details(report_dict)
    deadline = _now() + timedelta(days=HOLD_RESPONSE_DAYS)
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM held_reports WHERE lower(full_slug) = lower(?)", (full_slug,))
            cur = conn.execute(
                """
                INSERT INTO held_reports (owner, repo_name, full_slug, canonical_url, repo_icon_url, commit_sha,
                                          report_data_json, flagged_lines_json, deadline_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (owner, repo_name, full_slug, canonical_url, repo_icon_url, commit_sha,
                 json.dumps(clean), json.dumps(flagged_lines), _iso(deadline)),
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def _held_public(row) -> Dict[str, Any]:
    d = dict(row)
    d["flagged_lines"] = json.loads(d.pop("flagged_lines_json") or "[]")
    d.pop("report_data_json", None)
    return d


def list_held_for_owner(username: str, db_path: Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, full_slug, repo_name, status, flagged_lines_json, owner_response, created_at, deadline_at, responded_at "
            "FROM held_reports WHERE lower(owner) = lower(?) ORDER BY deadline_at",
            (username,),
        ).fetchall()
        return [_held_public(r) for r in rows]
    finally:
        conn.close()


def list_held_for_admin(db_path: Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, owner, full_slug, canonical_url, commit_sha, status, flagged_lines_json, owner_response, "
            "created_at, deadline_at, responded_at FROM held_reports ORDER BY status = 'submitted' DESC, responded_at, deadline_at"
        ).fetchall()
        return [_held_public(r) for r in rows]
    finally:
        conn.close()


def submit_hold_response(held_id: int, username: str, text: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Stores the owner's explanation. Returns False if the hold does not exist or is not theirs."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE held_reports SET owner_response = ?, status = 'submitted', responded_at = ? "
                "WHERE id = ? AND lower(owner) = lower(?)",
                (text, _iso(_now()), held_id, username),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


def get_held(held_id: int, db_path: Path = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM held_reports WHERE id = ?", (held_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_held(held_id: int, db_path: Path = DEFAULT_DB_PATH) -> bool:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            return conn.execute("DELETE FROM held_reports WHERE id = ?", (held_id,)).rowcount > 0
    finally:
        conn.close()


def overdue_held_ids(db_path: Path = DEFAULT_DB_PATH) -> List[int]:
    """Holds whose owner never answered within HOLD_RESPONSE_DAYS."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM held_reports WHERE status = 'awaiting_response' AND deadline_at < ?", (_iso(_now()),)
        ).fetchall()
        return [int(r["id"]) for r in rows]
    finally:
        conn.close()


def count_pending_reviews(db_path: Path = DEFAULT_DB_PATH) -> Dict[str, int]:
    """Items waiting for the admin: submitted explanations and disputed statements."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        held = conn.execute("SELECT COUNT(*) c FROM held_reports WHERE status = 'submitted'").fetchone()["c"]
        errors = conn.execute("SELECT COUNT(*) c FROM error_reports WHERE status = 'needs_review'").fetchone()["c"]
        return {"explanations": int(held), "error_reports": int(errors), "total": int(held) + int(errors)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------------
# Report statements (what an owner can dispute)
# ---------------------------------------------------------------------------------------------------

_LIST_KEYS = ("top_solid_features", "top_features_needing_work", "priority_improvements")
_PATH_RE = re.compile(r"^(verdict_summary|(top_solid_features|top_features_needing_work|priority_improvements)\[(\d+)\]|"
                      r"factor_evaluations\.([a-z_]+)\.(reasoning|strengths\[(\d+)\]|weaknesses\[(\d+)\]))$")


def list_statements(report: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """(path, label, text) for every statement in a published report that an owner may dispute."""
    out: List[Tuple[str, str, str]] = []
    if str(report.get("verdict_summary") or "").strip():
        out.append(("verdict_summary", "Reviewer verdict", report["verdict_summary"]))
    labels = {"top_solid_features": "Top solid feature", "top_features_needing_work": "Area needing attention",
              "priority_improvements": "Priority improvement"}
    for key in _LIST_KEYS:
        for i, text in enumerate(report.get(key) or []):
            if str(text).strip():
                out.append((f"{key}[{i}]", labels[key], str(text)))
    for factor, fe in (report.get("factor_evaluations") or {}).items():
        if factor == "malware_risk" or not isinstance(fe, dict):
            continue
        name = factor.replace("_", " ").title()
        if str(fe.get("reasoning") or "").strip():
            out.append((f"factor_evaluations.{factor}.reasoning", f"{name}: observation", fe["reasoning"]))
        for kind in ("strengths", "weaknesses"):
            for i, text in enumerate(fe.get(kind) or []):
                if str(text).strip():
                    out.append((f"factor_evaluations.{factor}.{kind}[{i}]", f"{name}: {kind[:-1]}", str(text)))
    return out


def get_statement(report: Dict[str, Any], path: str) -> Optional[str]:
    for p, _, text in list_statements(report):
        if p == path:
            return text
    return None


def set_statement(report: Dict[str, Any], path: str, new_text: Optional[str]) -> bool:
    """Replaces (or with None/empty, removes) one statement. Returns False for an invalid path."""
    m = _PATH_RE.match(path or "")
    if not m:
        return False
    new_text = (new_text or "").strip() or None
    if path == "verdict_summary":
        report["verdict_summary"] = new_text or ""
        return True
    if m.group(2):
        items = report.get(m.group(2)) or []
        i = int(m.group(3))
        if i >= len(items):
            return False
        if new_text:
            items[i] = new_text
        else:
            items.pop(i)
        report[m.group(2)] = items
        return True
    fe = (report.get("factor_evaluations") or {}).get(m.group(4))
    if not isinstance(fe, dict):
        return False
    if m.group(5) == "reasoning":
        fe["reasoning"] = new_text or ""
        return True
    kind = "strengths" if m.group(6) is not None else "weaknesses"
    i = int(m.group(6) if m.group(6) is not None else m.group(7))
    items = fe.get(kind) or []
    if i >= len(items):
        return False
    if new_text:
        items[i] = new_text
    else:
        items.pop(i)
    fe[kind] = items
    return True


def apply_correction(report: Dict[str, Any], path: str, original: str, new_text: Optional[str]) -> bool:
    """Applies a correction only if the statement at `path` is still the disputed text, and records it."""
    if get_statement(report, path) != original:
        return False
    if not set_statement(report, path, new_text):
        return False
    report.setdefault("corrections", []).append({
        "original": original,
        "corrected": (new_text or "").strip() or None,
        "note": CORRECTION_NOTE,
        "date": _iso(_now())[:10],
    })
    return True


# ---------------------------------------------------------------------------------------------------
# Error reports
# ---------------------------------------------------------------------------------------------------

def create_error_report(full_slug: str, owner: str, report_id: int, path: str, text: str, explanation: str,
                        db_path: Path = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO error_reports (full_slug, owner, report_id, statement_path, statement_text, explanation) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (full_slug, owner, report_id, path, text, explanation),
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def count_open_error_reports(owner: str, db_path: Path = DEFAULT_DB_PATH) -> int:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        return int(conn.execute(
            "SELECT COUNT(*) c FROM error_reports WHERE lower(owner) = lower(?) AND status IN ('queued', 'needs_review')",
            (owner,),
        ).fetchone()["c"])
    finally:
        conn.close()


def get_error_report(error_id: int, db_path: Path = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM error_reports WHERE id = ?", (error_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_error_report_by_task(task_id: int, db_path: Path = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM error_reports WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_error_report(error_id: int, db_path: Path = DEFAULT_DB_PATH, **fields: Any) -> None:
    allowed = {"status", "ai_verdict", "ai_explanation", "ai_corrected_text", "task_id", "resolved_at"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.execute(
                f"UPDATE error_reports SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                (*sets.values(), error_id),
            )
    finally:
        conn.close()


def list_error_reports(owner: Optional[str] = None, db_path: Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        if owner:
            rows = conn.execute(
                "SELECT * FROM error_reports WHERE lower(owner) = lower(?) ORDER BY id DESC", (owner,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM error_reports ORDER BY status = 'needs_review' DESC, id DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def correct_report_in_db(full_slug: str, path: str, original: str, new_text: Optional[str],
                         db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Applies a correction to the latest published report for `full_slug` in ONE database."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            row = conn.execute(
                "SELECT id, report_data_json FROM reports WHERE lower(full_slug) = lower(?) ORDER BY id DESC LIMIT 1",
                (full_slug,),
            ).fetchone()
            if not row or not row["report_data_json"]:
                return False
            data = json.loads(row["report_data_json"])
            if not apply_correction(data, path, original, new_text):
                return False
            conn.execute(
                "UPDATE reports SET report_data_json = ?, verdict_summary = ? WHERE id = ?",
                (json.dumps(data), data.get("verdict_summary", ""), row["id"]),
            )
            return True
    finally:
        conn.close()
