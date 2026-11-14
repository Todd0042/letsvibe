"""
SQLite persistence engine for LetsVibe.
Manages repository records, evaluation reports, historical podium badges,
and the audit task queue.
"""

import json
import logging
import math
import os
import re
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.platforms import normalize_category, normalize_platforms
from .identity import RepoIdentity, parse_repo_identity

logger = logging.getLogger("letsvibe.storage")


DEFAULT_DB_PATH = Path("data/letsvibe.db")
# Rubrics that grade the five experience factors only (code health and safety stored empty, never 0 or 100)
EXPERIENCE_RUBRICS = ("v8", "v9")   # and their point releases (v9.1, ...)
LEGACY_V5_RUBRICS = ("v1", "v2", "v3", "v4", "v5")


def rubric_version_rank(version: Optional[str]) -> int:
    """Converts a rubric version string ('v9.3', 'v9', 'v8', 'v1') into an integer rank (903, 900, 800, 100)."""
    if not version:
        return 0
    import re
    m = re.match(r"^v?(\d+)(?:\.(\d+))?", str(version).strip().lower())
    if not m:
        return 0
    return int(m.group(1)) * 100 + int(m.group(2) or 0)

# Factors shown on the site (rubric v8 grades value, creativity and the user journey). Older badges for other
# factors stay in ranking_history but are not displayed.
SHOWN_BADGE_CATEGORIES = ("overall", "resonance", "creativity", "user_journey", "functionality", "ui_ux")
# Only #1 badges are awarded while few projects are graded; the leaderboard and project pages show only the current
# #1s, the Hall of Fame also shows former #1s (#2/#3 and all history stay in ranking_history).
# Passed as a query parameter (json_each), so no SQL is built from strings
_SHOWN_JSON = json.dumps(list(SHOWN_BADGE_CATEGORIES))


def resolve_db_path(db_path: Optional[Path] = None) -> Path:
    """Returns the effective database path, honoring LETSVIBE_DB_PATH environment override if present."""
    if db_path is not None and db_path != DEFAULT_DB_PATH:
        return db_path
    env_override = os.getenv("LETSVIBE_DB_PATH")
    if env_override:
        return Path(env_override)
    return DEFAULT_DB_PATH


def get_db_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Opens a SQLite connection with row factories, foreign keys, and WAL mode enabled."""
    effective_path = resolve_db_path(db_path)
    effective_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(effective_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    # Overwrite deleted content with zeros instead of leaving it in free pages (privacy)
    conn.execute("PRAGMA secure_delete = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Initializes tables for projects, reports, ranking history, and audit queue."""
    conn = get_db_connection(db_path)
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            full_slug TEXT NOT NULL UNIQUE,
            canonical_url TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'github',
            owner_avatar_url TEXT,
            repo_icon_url TEXT,
            claimed_by_user_id TEXT,
            submitted_by_user_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            owner TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            full_slug TEXT NOT NULL,
            final_score REAL NOT NULL,
            letter_grade TEXT NOT NULL,
            archetype_name TEXT NOT NULL,
            archetype_category TEXT,
            primary_language TEXT,
            verdict_title TEXT,
            verdict_summary TEXT,
            total_loc INTEGER DEFAULT 0,
            total_files INTEGER DEFAULT 0,
            malware_risk_score REAL DEFAULT 0,
            operational_safety_score REAL DEFAULT 0,
            maintainability_score REAL DEFAULT 0,
            functionality_score REAL DEFAULT 0,
            resonance_score REAL DEFAULT 0,
            creativity_score REAL DEFAULT 0,
            ui_ux_score REAL DEFAULT 0,
            architecture_score REAL DEFAULT 0,
            raw_json_path TEXT,
            html_report_path TEXT,
            markdown_report_path TEXT,
            report_data_json TEXT,
            commit_sha TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ranking_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
            category TEXT NOT NULL,           -- 'overall', 'malware_risk', 'maintainability', etc.
            rank INTEGER NOT NULL,            -- 1, 2, or 3
            badge_title TEXT NOT NULL,        -- e.g. "Former #1 Overall Champion"
            achieved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            superseded_at TIMESTAMP,         -- NULL if currently active
            peak_score REAL NOT NULL,
            UNIQUE(project_id, category, rank, achieved_at)
        );

        CREATE TABLE IF NOT EXISTS audit_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            owner TEXT,
            repo_name TEXT,
            full_slug TEXT,
            mode TEXT NOT NULL DEFAULT 'blend',
            status TEXT NOT NULL DEFAULT 'queued', -- queued, running, completed, failed, cancelled
            progress_message TEXT DEFAULT 'Waiting in queue...',
            requested_by_user TEXT DEFAULT 'anonymous',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            error_message TEXT,
            report_id INTEGER REFERENCES reports(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS queue_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            is_paused INTEGER NOT NULL DEFAULT 0,
            auto_process INTEGER NOT NULL DEFAULT 1,
            pause_reason TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        INSERT OR IGNORE INTO queue_settings (id, is_paused, auto_process) VALUES (1, 0, 1);

        CREATE TABLE IF NOT EXISTS appeals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER REFERENCES audit_queue(id) ON DELETE CASCADE,
            target TEXT NOT NULL,
            full_slug TEXT,
            submitter_email TEXT NOT NULL,
            message TEXT NOT NULL,
            is_profane INTEGER DEFAULT 0,
            is_violent INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending', -- pending, approved, declined
            admin_response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TIMESTAMP
        );

        -- Reports held for an owner explanation before publishing (never shown publicly)
        CREATE TABLE IF NOT EXISTS held_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT NOT NULL,
            repo_name TEXT NOT NULL,
            full_slug TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            repo_icon_url TEXT,
            commit_sha TEXT,
            report_data_json TEXT NOT NULL,
            flagged_lines_json TEXT NOT NULL DEFAULT '[]',
            owner_response TEXT,
            status TEXT NOT NULL DEFAULT 'awaiting_response', -- awaiting_response, submitted, awaiting_admin
            kind TEXT NOT NULL DEFAULT 'explain', -- explain (owner explains lines), vibe_review (admin confirms vibe-coded)
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deadline_at TIMESTAMP NOT NULL,
            responded_at TIMESTAMP
        );
        -- Owner-reported errors in a published review
        CREATE TABLE IF NOT EXISTS error_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_slug TEXT NOT NULL,
            owner TEXT NOT NULL,
            report_id INTEGER,
            statement_path TEXT NOT NULL,
            statement_text TEXT NOT NULL,
            explanation TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', -- queued, corrected, needs_review, applied, dismissed
            ai_verdict TEXT,
            ai_explanation TEXT,
            ai_corrected_text TEXT,
            task_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        );
        -- Community/visitor reports of content-guideline violations or stolen work
        CREATE TABLE IF NOT EXISTS content_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            full_slug TEXT NOT NULL,
            reported_by TEXT NOT NULL,
            reason TEXT NOT NULL,
            details TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users (
            github_id TEXT PRIMARY KEY,
            github_username TEXT NOT NULL,
            github_avatar_url TEXT,
            email TEXT,
            scope_level TEXT NOT NULL DEFAULT 'minimal', -- 'email' or 'minimal'
            is_beta_pioneer INTEGER NOT NULL DEFAULT 1,
            bonus_quota INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS project_upvotes (
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            github_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (project_id, github_id)
        );
        """)
        # Safe migrations for existing databases
        try:  # number shown with the letter, from the same bell curve (storage/peer_grades.py)
            conn.execute("ALTER TABLE reports ADD COLUMN curved_score REAL;")
        except sqlite3.OperationalError:
            pass
        try:  # a task stopped by the admin runs first when the queue resumes (web/worker.py)
            conn.execute("ALTER TABLE queue_settings ADD COLUMN front_task_id INTEGER;")
        except sqlite3.OperationalError:
            pass
        for col in ("terms_version TEXT", "terms_accepted_at TEXT"):  # Terms of Use acceptance (core/legal.py)
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col};")
            except sqlite3.OperationalError:
                pass
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN repo_icon_url TEXT;")
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass
        # TEST MODE (remove before launch): projects the admin graded from someone else's GitHub account to build a
        # larger evaluation dataset. They are shown only to the admin and never affect public grades, curves or badges.
        for table in ("projects", "audit_queue"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0;")
            except sqlite3.OperationalError:
                pass
        # A test project the admin chose to share with its developer: its badge and report open by link, but it stays
        # off every public list and out of public grades, curves and #1 badges.
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN shared INTEGER NOT NULL DEFAULT 0;")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN moderation_hidden INTEGER NOT NULL DEFAULT 0;")
        except sqlite3.OperationalError:
            pass
        try:  # vibe-coded check: reports held for the admin to confirm the project was vibe-coded (core/vibe_detector.py)
            conn.execute("ALTER TABLE held_reports ADD COLUMN kind TEXT NOT NULL DEFAULT 'explain';")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE audit_queue ADD COLUMN force_override INTEGER DEFAULT 0;")
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass
        try:
            conn.execute("ALTER TABLE audit_queue ADD COLUMN rejection_reason TEXT;")
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass
        try:
            conn.execute("ALTER TABLE queue_settings ADD COLUMN pause_reason TEXT DEFAULT '';")
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN is_beta_pioneer INTEGER NOT NULL DEFAULT 1;")
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN bonus_quota INTEGER NOT NULL DEFAULT 1;")
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass
        try:
            conn.execute("ALTER TABLE reports ADD COLUMN commit_sha TEXT;")
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass
        # Rubric v6 retired the maintainability/architecture badge categories
        try:
            conn.execute("DELETE FROM ranking_history WHERE category IN ('maintainability', 'architecture')")
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass
        # The safety check is never shown anywhere: strip it from reports stored by earlier versions
        try:
            conn.execute("DELETE FROM ranking_history WHERE category = 'malware_risk'")
            for row in conn.execute(
                "SELECT id, report_data_json FROM reports WHERE report_data_json LIKE '%malware_risk%'"
            ).fetchall():
                data = json.loads(row["report_data_json"])
                (data.get("factor_evaluations") or {}).pop("malware_risk", None)
                for key in ("malware_risk_score", "integrity_hold", "integrity_concerns"):
                    data.pop(key, None)
                conn.execute("UPDATE reports SET report_data_json = ?, malware_risk_score = 0 WHERE id = ?", (json.dumps(data), row["id"]))
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass
        # LetsVibe no longer collects email addresses: erase any stored by earlier versions
        try:
            conn.execute("UPDATE users SET email = NULL WHERE email IS NOT NULL")
            conn.execute("UPDATE users SET scope_level = 'minimal' WHERE scope_level != 'minimal'")
            conn.execute("UPDATE appeals SET submitter_email = '' WHERE submitter_email != ''")
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass
        for ddl in (
            "ALTER TABLE reports ADD COLUMN general_category TEXT DEFAULT 'other';",
            # Stored as ",windows,linux," so a platform id can be matched with LIKE '%,id,%'
            "ALTER TABLE reports ADD COLUMN platforms TEXT DEFAULT '';",
            "ALTER TABLE reports ADD COLUMN device_profile TEXT DEFAULT '';",
            "ALTER TABLE appeals ADD COLUMN submitted_by TEXT DEFAULT '';",
            # Rubric v6 factors
            "ALTER TABLE reports ADD COLUMN code_health_score REAL DEFAULT 0;",
            "ALTER TABLE reports ADD COLUMN user_journey_score REAL DEFAULT 0;",
            # Developer intake metadata (category, provenance, guest_access, user_hook)
            "ALTER TABLE audit_queue ADD COLUMN submission_meta_json TEXT DEFAULT '{}';",
            # Report-level test flag & rubric version ordering
            "ALTER TABLE reports ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0;",
            "ALTER TABLE reports ADD COLUMN rubric_version TEXT DEFAULT '';",
            "ALTER TABLE reports ADD COLUMN rubric_rank INTEGER NOT NULL DEFAULT 0;",
        ):
            try:
                conn.execute(ddl)
            except Exception:  # nosec B110 - deliberate best-effort fallback
                pass
        # Backfill rubric_version and rubric_rank on existing reports
        try:
            for row in conn.execute(
                "SELECT id, rubric_version, rubric_rank, json_extract(report_data_json, '$.rubric_version') AS jv FROM reports"
            ).fetchall():
                rv = str(row["rubric_version"] or row["jv"] or "")
                rk = rubric_version_rank(rv)
                if rv != (row["rubric_version"] or "") or rk != int(row["rubric_rank"] or 0):
                    conn.execute("UPDATE reports SET rubric_version = ?, rubric_rank = ? WHERE id = ?", (rv, rk, row["id"]))
        except Exception as exc:
            logger.warning(f"Rubric rank backfill skipped: {exc}")
        # Ensure stored letter_grade in reports is always the uncurved raw rubric grade
        # (bell curve is applied dynamically at presentation time via peer_grades.compute_peer_overlays)
        try:
            from ..evaluator.rubric import calculate_letter_grade
            for row in conn.execute("SELECT id, final_score, letter_grade, curved_score, report_data_json FROM reports").fetchall():
                raw_grade = calculate_letter_grade(float(row["final_score"]))
                if row["letter_grade"] != raw_grade or row["curved_score"] is not None:
                    blob = row["report_data_json"]
                    if blob:
                        try:
                            data = json.loads(blob)
                            data["letter_grade"] = raw_grade
                            data.pop("curve", None)
                            data["grade_basis"] = "rubric"
                            blob = json.dumps(data)
                        except (ValueError, TypeError):
                            pass
                    conn.execute(
                        "UPDATE reports SET letter_grade = ?, curved_score = NULL, report_data_json = ? WHERE id = ?",
                        (raw_grade, blob, row["id"]),
                    )
        except Exception as exc:
            logger.warning(f"Raw grade normalization skipped: {exc}")
        # One-time repair: older builds saved malware/ops qualifier scores as 0 (wrong JSON key).
        try:
            stale = conn.execute(
                "SELECT id, report_data_json FROM reports WHERE malware_risk_score = 0 AND operational_safety_score = 0 AND report_data_json IS NOT NULL"
            ).fetchall()
            for row in stale:
                data = json.loads(row["report_data_json"])
                fe = data.get("factor_evaluations", {})
                mal = data.get("malware_risk_score", (fe.get("malware_risk") or {}).get("score"))
                ops = data.get("operational_safety_score", (fe.get("operational_safety") or {}).get("score"))
                if mal is not None and ops is not None and (float(mal) > 0 or float(ops) > 0):
                    conn.execute(
                        "UPDATE reports SET malware_risk_score = ?, operational_safety_score = ? WHERE id = ?",
                        (float(mal), float(ops), row["id"]),
                    )
        except Exception as exc:
            logger.warning(f"Qualifier score backfill skipped: {exc}")
    conn.close()


def strip_safety_details(report_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns a copy with every trace of the internal safety check removed. LetsVibe never states or
    shows safety-check results anywhere; it only uses them to decide whether a report is held.
    """
    data = json.loads(json.dumps(report_dict, default=str))
    (data.get("factor_evaluations") or {}).pop("malware_risk", None)
    (data.get("factors") or {}).pop("malware_risk", None)
    for key in ("malware_risk_score", "integrity_hold", "integrity_concerns"):
        data.pop(key, None)
    for p in data.get("perspectives") or []:
        (p.get("factor_scores") or {}).pop("malware_risk", None)
    return data


def save_report_to_db(
    report_dict: Dict[str, Any],
    identity: RepoIdentity,
    raw_json_path: Optional[str] = None,
    html_report_path: Optional[str] = None,
    markdown_report_path: Optional[str] = None,
    commit_sha: Optional[str] = None,
    db_path: Path = DEFAULT_DB_PATH,
    is_test: bool = False,
) -> int:
    """
    Saves a report and project entry, recalculating ranks and awarding badges.
    Returns the report_id. `is_test` marks a new project as an admin test grading (set only when the project is
    first created; re-scans keep the project's original flag).
    """
    report_dict = strip_safety_details(report_dict)
    init_db(db_path)
    conn = get_db_connection(db_path)

    from ..evaluator.rubric import calculate_letter_grade

    now_iso = datetime.now().isoformat()
    final_score = float(report_dict.get("final_vibe_score", 0.0))
    # Always store the uncurved raw rubric letter grade in SQLite (bell curve is applied dynamically on read)
    letter_grade = calculate_letter_grade(final_score)
    report_dict["letter_grade"] = letter_grade
    archetype = report_dict.get("archetype", {})
    archetype_name = archetype.get("name", "Unknown Archetype")
    archetype_cat = archetype.get("category", "")
    primary_lang = archetype.get("primary_language", "Unknown")
    general_category = normalize_category(archetype.get("general_category"))
    platforms = normalize_platforms(archetype.get("platforms"))
    platforms_col = f",{','.join(platforms)}," if platforms else ""
    device_profile = str(archetype.get("device_profile") or "")

    d_metrics = report_dict.get("deterministic_metrics", {})
    total_loc = int(d_metrics.get("total_loc", 0))
    total_files = int(d_metrics.get("total_files", 0))

    factors = report_dict.get("factor_evaluations", report_dict.get("factors", {}))

    def _score(name: str, *fallbacks: str) -> float:
        """Reads a score from factor_evaluations (current format), then legacy keys."""
        for key in (name, *fallbacks):
            entry = factors.get(key)
            if isinstance(entry, dict) and entry.get("score") is not None:
                return float(entry["score"])
        return -1.0

    malware_score = report_dict.get("malware_risk_score")
    malware_score = float(malware_score) if malware_score is not None else _score("malware_risk", "security")
    ops_score = report_dict.get("operational_safety_score")
    ops_score = float(ops_score) if ops_score is not None else _score("operational_safety")
    malware_score = max(0.0, malware_score)
    ops_score = max(0.0, ops_score)

    maint_score = float(factors.get("maintainability", {}).get("score", factors.get("complexity", {}).get("score", 0.0)))
    legacy_health = [float(factors[k]["score"]) for k in ("maintainability", "architecture")
                     if isinstance(factors.get(k), dict) and factors[k].get("score") is not None]
    health_score = float((factors.get("code_health") or {}).get("score", sum(legacy_health) / len(legacy_health) if legacy_health else 0.0))
    journey_score = float((factors.get("user_journey") or {}).get("score", 0.0))
    fnc_score = float(factors.get("functionality", {}).get("score", 0.0))
    vib_score = float(report_dict.get("resonance_score") or factors.get("resonance", {}).get("score", 0.0))
    crt_score = float(factors.get("creativity", {}).get("score", 0.0))
    ui_score = float(factors.get("ui_ux", {}).get("score", 0.0))
    arch_score = float(factors.get("architecture", {}).get("score", factors.get("project_flow", {}).get("score", 0.0)))

    verdict_title = report_dict.get("verdict_title", "")
    verdict_summary = report_dict.get("verdict_summary", "")

    rubric_ver = str(report_dict.get("rubric_version") or "")
    rubric_rk = rubric_version_rank(rubric_ver)
    major_ver = rubric_ver.split(".")[0]

    # Experience grading (rubric v8 and later) grades value, creativity, user journey, functionality and UI/UX. Code health and
    # operational safety are stored empty (never 0 or 100), so rankings, badges and averages treat them as "not graded".
    if major_ver in EXPERIENCE_RUBRICS:
        ops_score = maint_score = arch_score = health_score = None
        vib_score = float((factors.get("resonance") or {}).get("score", 0.0))
        fnc_score = float(factors["functionality"]["score"]) if isinstance(factors.get("functionality"), dict) else None
        ui_score = float(factors["ui_ux"]["score"]) if isinstance(factors.get("ui_ux"), dict) else None
    elif major_ver in LEGACY_V5_RUBRICS:
        # Rubric v1-v5 graded maintainability and architecture instead of user_journey and code_health
        journey_score = None
        health_score = None

    with conn:
        # Upsert project
        conn.execute(
            """
            INSERT INTO projects (owner, repo_name, full_slug, canonical_url, provider, owner_avatar_url, repo_icon_url, updated_at, is_test)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(full_slug) DO UPDATE SET
                canonical_url=excluded.canonical_url,
                owner_avatar_url=excluded.owner_avatar_url,
                repo_icon_url=COALESCE(excluded.repo_icon_url, projects.repo_icon_url),
                updated_at=excluded.updated_at
            """,
            (
                identity.owner,
                identity.repo_name,
                identity.full_slug,
                identity.canonical_url,
                identity.provider,
                identity.avatar_url,
                identity.repo_icon_url,
                now_iso,
                1 if is_test else 0,
            ),
        )
        cur = conn.execute("SELECT id FROM projects WHERE full_slug = ?", (identity.full_slug,))
        project_id = cur.fetchone()["id"]

        # Insert new report
        cur = conn.execute(
            """
            INSERT INTO reports (
                project_id, owner, repo_name, full_slug, final_score, letter_grade,
                archetype_name, archetype_category, primary_language,
                verdict_title, verdict_summary, total_loc, total_files,
                malware_risk_score, operational_safety_score, maintainability_score, functionality_score,
                resonance_score, creativity_score, ui_ux_score, architecture_score,
                raw_json_path, html_report_path, markdown_report_path, report_data_json,
                commit_sha, general_category, platforms, device_profile, code_health_score, user_journey_score,
                is_test, rubric_version, rubric_rank, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                identity.owner,
                identity.repo_name,
                identity.full_slug,
                final_score,
                letter_grade,
                archetype_name,
                archetype_cat,
                primary_lang,
                verdict_title,
                verdict_summary,
                total_loc,
                total_files,
                malware_score,
                ops_score,
                maint_score,
                fnc_score,
                vib_score,
                crt_score,
                ui_score,
                arch_score,
                raw_json_path,
                html_report_path,
                markdown_report_path,
                json.dumps(report_dict),
                commit_sha,
                general_category,
                platforms_col,
                device_profile,
                health_score,
                journey_score,
                1 if is_test else 0,
                rubric_ver,
                rubric_rk,
                now_iso,
            ),
        )
        report_id = cur.lastrowid

    conn.close()

    # Recalculate historical ranks
    recalculate_historical_ranks(db_path)
    return report_id


def recalculate_historical_ranks(db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Computes current top 3 for Overall and every factor dimension.
    Maintains historical achievement records for past podium champions.
    """
    conn = get_db_connection(db_path)

    # Rubric v8 grades value, creativity, user journey, functionality and UI/UX; badges follow the graded factors.
    categories = {
        "overall": ("final_score", "Overall Champion"),
        "user_journey": ("user_journey_score", "Smoothest User Journey"),
        "resonance": ("resonance_score", "Real-World Value Pioneer"),
        "creativity": ("creativity_score", "Creativity & Novelty Icon"),
        "functionality": ("functionality_score", "Feature Completeness Champion"),
        "ui_ux": ("ui_ux_score", "UI & UX Intuitiveness Leader"),
    }

    now_iso = datetime.now().isoformat()

    with conn:
        for cat_key, (col_name, title_suffix) in categories.items():
            # Get latest report from the newest production/released engine for each distinct public project
            query = f"""
            WITH latest_reports AS (
                SELECT r.*, ROW_NUMBER() OVER(
                    PARTITION BY r.project_id
                    ORDER BY COALESCE(r.rubric_rank, 0) DESC, r.id DESC
                ) as rn
                FROM reports r
                JOIN projects p ON p.id = r.project_id AND p.is_test = 0 AND COALESCE(p.moderation_hidden, 0) = 0
                WHERE COALESCE(r.is_test, 0) = 0
            )
            SELECT project_id, id as report_id, full_slug, {col_name} as score
            FROM latest_reports
            WHERE rn = 1 AND {col_name} IS NOT NULL
            ORDER BY score DESC, id ASC
            LIMIT 1;
            """  # nosec B608 - only fixed column names are formatted in; values use ? placeholders
            cur = conn.execute(query)
            current_top_3 = cur.fetchall()

            current_pids_at_ranks = {}
            for rank_idx, row in enumerate(current_top_3, start=1):
                pid = row["project_id"]
                rid = row["report_id"]
                score = row["score"]
                current_pids_at_ranks[rank_idx] = pid

                badge_name = f"#{rank_idx} {title_suffix}"

                # Crucial rule: If project currently holds this rank, delete any obsolete 'Former' record for this exact rank
                conn.execute(
                    """
                    DELETE FROM ranking_history
                    WHERE project_id = ? AND category = ? AND rank = ? AND superseded_at IS NOT NULL
                    """,
                    (pid, cat_key, rank_idx),
                )

                # Check if this exact achievement is already active
                existing = conn.execute(
                    """
                    SELECT id FROM ranking_history
                    WHERE project_id = ? AND category = ? AND rank = ? AND superseded_at IS NULL
                    """,
                    (pid, cat_key, rank_idx),
                ).fetchone()

                if not existing:
                    # New or promoted rank achievement!
                    conn.execute(
                        """
                        INSERT INTO ranking_history (project_id, report_id, category, rank, badge_title, peak_score, achieved_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (pid, rid, cat_key, rank_idx, badge_name, score, now_iso),
                    )

            # Mark any active rank 1-3 achievements as superseded if another project has overtaken them
            for rank_idx in (1, 2, 3):
                active_at_rank = conn.execute(
                    """
                    SELECT id, project_id, badge_title, peak_score FROM ranking_history
                    WHERE category = ? AND rank = ? AND superseded_at IS NULL
                    """,
                    (cat_key, rank_idx),
                ).fetchall()

                current_holder = current_pids_at_ranks.get(rank_idx)
                for rec in active_at_rank:
                    if rec["project_id"] != current_holder:
                        former_title = rec["badge_title"].replace(f"#{rank_idx} ", f"Former #{rank_idx} ")
                        # Check if a former record already exists for this project/category/rank
                        existing_former = conn.execute(
                            """
                            SELECT id FROM ranking_history
                            WHERE project_id = ? AND category = ? AND rank = ? AND superseded_at IS NOT NULL
                            """,
                            (rec["project_id"], cat_key, rank_idx),
                        ).fetchone()

                        if existing_former:
                            # Avoid duplicate former entries
                            conn.execute("DELETE FROM ranking_history WHERE id = ?", (rec["id"],))
                        else:
                            conn.execute(
                                """
                                UPDATE ranking_history
                                SET superseded_at = ?, badge_title = ?
                                WHERE id = ?
                                """,
                                (now_iso, former_title, rec["id"]),
                            )

        # Cleanup any duplicate history records across the board
        conn.execute("""
            DELETE FROM ranking_history
            WHERE id NOT IN (
                SELECT MIN(id) FROM ranking_history
                GROUP BY project_id, category, rank, (superseded_at IS NOT NULL)
            )
        """)

    conn.close()


def _as_set(value: Optional[Any]) -> set:
    """Accepts None, 'a,b' or ['a', 'b'] and returns a lower-cased set (empty = no filter)."""
    if not value:
        return set()
    items = value.split(",") if isinstance(value, str) else value
    return {str(v).strip().lower() for v in items if str(v).strip() and str(v).strip().lower() != "all"}


def get_leaderboard(
    sort_by: str = "overall",
    tier_filter: Optional[Any] = None,
    search: Optional[str] = None,
    db_path: Path = DEFAULT_DB_PATH,
    categories: Optional[Any] = None,
    platforms: Optional[Any] = None,
    with_facets: bool = False,
    include_test: bool = False,
    use_curve: bool = True,
    engine_filter: Optional[str] = None,
) -> Any:
    """
    Retrieves ranked project listings with historical and current badges.

    Filters combine the way shoppers expect: options inside one group are OR'd
    (Android or iOS), groups are AND'd together (Game AND Android AND tier B).
    `rank` is always the project's position in the full sorted list, so filtering
    never renumbers a project. With `with_facets`, also returns per-option counts
    that respect every *other* active filter.
    """
    init_db(db_path)
    conn = get_db_connection(db_path)

    col_map = {
        "overall": "r.final_score",
        "operational_safety": "r.operational_safety_score",
        "code_health": "r.code_health_score",
        "user_journey": "r.user_journey_score",
        "functionality": "r.functionality_score",
        "resonance": "r.resonance_score",
        "creativity": "r.creativity_score",
        "ui_ux": "r.ui_ux_score",
        "maintainability": "r.maintainability_score",
        "architecture": "r.architecture_score",
        "date": "r.created_at",
    }
    order_col = col_map.get(sort_by, "r.final_score")

    # Admin-only engine filter: when set (and include_test=True), rank projects by their latest scan from that engine
    eng_rank = rubric_version_rank(engine_filter) if (include_test and engine_filter and engine_filter not in ("all", "default")) else 0

    if eng_rank > 0:
        query = f"""
        WITH latest_reports AS (
            SELECT r.*, ROW_NUMBER() OVER(
                PARTITION BY r.project_id
                ORDER BY r.id DESC
            ) as rn
            FROM reports r
            WHERE COALESCE(r.rubric_rank, 0) = ?
        )
        SELECT
            p.id as project_id,
            p.owner,
            p.repo_name,
            p.full_slug,
            p.canonical_url,
            p.owner_avatar_url,
            p.repo_icon_url,
            r.id as report_id,
            r.final_score,
            COALESCE(r.curved_score, r.final_score) AS curved_score,
            r.letter_grade,
            r.archetype_name,
            r.archetype_category,
            r.primary_language,
            r.verdict_title,
            r.verdict_summary,
            r.general_category,
            r.platforms,
            r.device_profile,
            r.total_loc,
            r.total_files,
            r.operational_safety_score,
            r.code_health_score,
            r.user_journey_score,
            r.functionality_score,
            r.resonance_score,
            r.creativity_score,
            r.ui_ux_score,
            r.maintainability_score,
            r.architecture_score,
            COALESCE(r.rubric_version, '') AS rubric_version,
            COALESCE(r.is_test, 0) AS is_test_report,
            r.created_at as graded_at,
            p.is_test
        FROM projects p
        JOIN latest_reports r ON p.id = r.project_id AND r.rn = 1
        WHERE (p.is_test = 0 OR ?)
          AND (COALESCE(p.moderation_hidden, 0) = 0 OR ?)
        ORDER BY {order_col} DESC, r.final_score DESC
        """  # nosec B608
        cur = conn.execute(query, (eng_rank, 1 if include_test else 0, 1 if include_test else 0))
    else:
        query = f"""
        WITH latest_reports AS (
            SELECT r.*, ROW_NUMBER() OVER(
                PARTITION BY r.project_id
                ORDER BY (COALESCE(r.is_test, 0) = 0) DESC, COALESCE(r.rubric_rank, 0) DESC, r.id DESC
            ) as rn
            FROM reports r
            WHERE (COALESCE(r.is_test, 0) = 0 OR ?)
        )
        SELECT
            p.id as project_id,
            p.owner,
            p.repo_name,
            p.full_slug,
            p.canonical_url,
            p.owner_avatar_url,
            p.repo_icon_url,
            r.id as report_id,
            r.final_score,
            COALESCE(r.curved_score, r.final_score) AS curved_score,
            r.letter_grade,
            r.archetype_name,
            r.archetype_category,
            r.primary_language,
            r.verdict_title,
            r.verdict_summary,
            r.general_category,
            r.platforms,
            r.device_profile,
            r.total_loc,
            r.total_files,
            r.operational_safety_score,
            r.code_health_score,
            r.user_journey_score,
            r.functionality_score,
            r.resonance_score,
            r.creativity_score,
            r.ui_ux_score,
            r.maintainability_score,
            r.architecture_score,
            COALESCE(r.rubric_version, '') AS rubric_version,
            COALESCE(r.is_test, 0) AS is_test_report,
            r.created_at as graded_at,
            p.is_test
        FROM projects p
        JOIN latest_reports r ON p.id = r.project_id AND r.rn = 1
        WHERE (p.is_test = 0 OR ?)
          AND (COALESCE(p.moderation_hidden, 0) = 0 OR ?)
        ORDER BY {order_col} DESC, r.final_score DESC
        """  # nosec B608 - only fixed column names or the fixed badge list are formatted in; values use ? placeholders
        cur = conn.execute(query, (1 if include_test else 0, 1 if include_test else 0, 1 if include_test else 0))
    rows = [dict(row) for row in cur.fetchall()]

    # Compute presentation-layer bell curve overlays without mutating SQLite
    from ..evaluator.rubric import calculate_letter_grade
    from .peer_grades import basis_for, compute_peer_overlays
    overlays = compute_peer_overlays(conn)

    # Collect badges for all projects
    badge_cur = conn.execute("""
        SELECT project_id, category, rank, badge_title, peak_score, achieved_at, superseded_at
        FROM ranking_history
        WHERE rank = 1 AND superseded_at IS NULL AND category IN (SELECT value FROM json_each(?))
        ORDER BY superseded_at IS NULL DESC, rank ASC, achieved_at DESC
    """, (_SHOWN_JSON,))
    badges_by_project: Dict[int, List[Dict[str, Any]]] = {}
    for b in badge_cur.fetchall():
        badges_by_project.setdefault(b["project_id"], []).append(dict(b))

    up_counts: Dict[int, int] = {}
    for ur in conn.execute("SELECT project_id, COUNT(*) c FROM project_upvotes GROUP BY project_id"):
        up_counts[int(ur["project_id"])] = int(ur["c"])

    tiers_wanted = _as_set(tier_filter)
    cats_wanted = _as_set(categories)
    plats_wanted = _as_set(platforms)
    q = (search or "").strip().lower()

    for item in rows:
        item["badges"] = badges_by_project.get(item["project_id"], [])
        ov = overlays.get(int(item["report_id"]))
        raw_grade = calculate_letter_grade(float(item.get("final_score") or 0.0))
        item["raw_letter_grade"] = ov["raw_letter_grade"] if ov else raw_grade
        item["curved_letter_grade"] = ov["curved_letter_grade"] if ov else raw_grade
        item["curved_score"] = ov["curved_score"] if ov else round(float(item.get("final_score") or 0.0), 1)
        up_cnt = up_counts.get(int(item["project_id"]), 0)
        up_bonus = upvote_score_bonus(up_cnt)
        item["upvote_count"] = up_cnt
        item["upvote_bonus"] = up_bonus
        if up_bonus > 0:
            boosted = round(min(100.0, float(item["curved_score"]) + up_bonus), 1)
            item["curved_score"] = boosted
            item["curved_letter_grade"] = calculate_letter_grade(boosted)
        item["curve"] = ov["curve"] if ov else None
        item["raw_tier"] = item["raw_letter_grade"][0] if item["raw_letter_grade"][:1] in ("A", "B", "C", "D") else "F"
        item["curved_tier"] = item["curved_letter_grade"][0] if item["curved_letter_grade"][:1] in ("A", "B", "C", "D") else "F"
        item["letter_grade"] = item["curved_letter_grade"] if use_curve else item["raw_letter_grade"]
        # Tier = letter-grade family, so the filter always agrees with the grade shown
        grade = (item.get("letter_grade") or "F").strip().upper()
        item["tier"] = grade[0] if grade[:1] in ("A", "B", "C", "D") else "F"
        item["general_category"] = normalize_category(item.get("general_category"))
        item["platforms"] = [p for p in (item.get("platforms") or "").split(",") if p]

    if sort_by == "overall" and use_curve:
        rows.sort(key=lambda r: (float(r.get("curved_score") or 0.0), float(r.get("final_score") or 0.0)), reverse=True)

    for rank_idx, item in enumerate(rows, start=1):
        item["rank"] = rank_idx

    # Standing within the project's category (what the peer-relative letter grade is based on)
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for item in rows:
        by_cat.setdefault(item["general_category"], []).append(item)
    for cat_items in by_cat.values():
        for i, item in enumerate(sorted(cat_items, key=lambda x: -float(x["final_score"] or 0)), start=1):
            item["category_rank"] = i
            item["category_count"] = len(cat_items)
            ov = overlays.get(int(item["report_id"]))
            item["grade_basis"] = ov["grade_basis"] if ov else basis_for(len(cat_items))

    def matches(item: Dict[str, Any], skip: str = "") -> bool:
        if skip != "tier" and tiers_wanted and item["tier"].lower() not in tiers_wanted:
            return False
        if skip != "category" and cats_wanted and item["general_category"] not in cats_wanted:
            return False
        if skip != "platform" and plats_wanted and not plats_wanted.intersection(item["platforms"]):
            return False
        if q:
            haystack = " ".join(
                str(item.get(k) or "") for k in (
                    "owner", "repo_name", "full_slug", "primary_language", "archetype_name",
                    "archetype_category", "verdict_title",
                )
            ).lower()
            if q not in haystack:
                return False
        return True

    results = [item for item in rows if matches(item)]
    if not with_facets:
        conn.close()
        return results

    facets: Dict[str, Dict[str, int]] = {"tier": {}, "category": {}, "platform": {}, "engine": {}}
    if include_test:
        try:
            eng_rows = conn.execute(
                "SELECT COALESCE(rubric_rank, 0) AS rk, MAX(COALESCE(rubric_version, '')) AS rv, COUNT(DISTINCT project_id) AS cnt "
                "FROM reports WHERE COALESCE(rubric_rank, 0) > 0 GROUP BY rk"
            ).fetchall()
            for er in eng_rows:
                rk = int(er["rk"] or 0)
                cnt = int(er["cnt"] or 0)
                rv = str(er["rv"] or "")
                if rv:
                    facets["engine"][rv] = cnt
                if rk >= 100:
                    maj, min_v = rk // 100, rk % 100
                    facets["engine"][f"v{maj}.{min_v}"] = cnt
                    if min_v == 0:
                        facets["engine"][f"v{maj}"] = cnt
        except Exception:
            pass
    conn.close()
    for item in rows:
        if matches(item, skip="tier"):
            facets["tier"][item["tier"]] = facets["tier"].get(item["tier"], 0) + 1
        if matches(item, skip="category"):
            c = item["general_category"]
            facets["category"][c] = facets["category"].get(c, 0) + 1
        if matches(item, skip="platform"):
            for pl in item["platforms"]:
                facets["platform"][pl] = facets["platform"].get(pl, 0) + 1
    return results, facets


def get_hall_of_fame(db_path: Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """
    Returns historical podium achievers across categories, ensuring zero duplicates.
    If a project currently holds an active rank in a category, it is never listed as former
    in that same category.
    """
    init_db(db_path)
    conn = get_db_connection(db_path)

    query = """
    SELECT
        h.id,
        h.project_id,
        h.category,
        h.rank,
        h.badge_title,
        h.peak_score,
        h.achieved_at,
        h.superseded_at,
        p.owner,
        p.repo_name,
        p.full_slug,
        p.canonical_url,
        p.owner_avatar_url,
        p.repo_icon_url,
        r.letter_grade,
        r.archetype_name
    FROM ranking_history h
    JOIN projects p ON h.project_id = p.id AND p.is_test = 0 AND COALESCE(p.moderation_hidden, 0) = 0
    JOIN reports r ON h.report_id = r.id
    WHERE h.rank = 1 AND h.category IN (SELECT value FROM json_each(?))
    ORDER BY h.category = 'overall' DESC, h.rank ASC, h.superseded_at IS NULL DESC, h.achieved_at DESC
    """
    cur = conn.execute(query, (_SHOWN_JSON,))
    raw_records = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Track which (category, project_id) pairs currently have an active reigning rank
    active_pairs = set()
    for r in raw_records:
        if r["superseded_at"] is None:
            active_pairs.add((r["category"], r["project_id"]))

    seen_pairs = set()
    deduped = []

    # Sort so active reigning ranks come first, then lowest rank number (best rank), then highest score
    sorted_records = sorted(
        raw_records,
        key=lambda x: (
            x["category"],
            0 if x["superseded_at"] is None else 1,
            x["rank"],
            -x["peak_score"],
        ),
    )

    for r in sorted_records:
        key = (r["category"], r["project_id"])
        if key in seen_pairs:
            continue

        # If this is a superseded record but the project currently has an active rank in this category, skip it!
        if r["superseded_at"] is not None and key in active_pairs:
            continue

        seen_pairs.add(key)
        deduped.append(r)

    # Return overall first, then category, then rank asc
    return sorted(
        deduped,
        key=lambda x: (
            0 if x["category"] == "overall" else 1,
            x["category"],
            x["rank"],
            0 if x["superseded_at"] is None else 1,
        ),
    )


RADAR_FACTOR_COLUMNS = (
    "functionality",
    "user_journey",
    "ui_ux",
    "resonance",
    "creativity",
    "code_health",
    "operational_safety",
    "maintainability",
    "architecture",
)
def factor_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Per-factor average and highest score across every project's latest report, for the report radar: the dashed
    line is the average, and each axis runs to the highest score any project has on that factor.
    """
    cols = ", ".join(f"r.{f}_score AS {f}" for f in RADAR_FACTOR_COLUMNS)
    rows = [dict(x) for x in conn.execute(f"""
        WITH latest AS (
            SELECT r.*, ROW_NUMBER() OVER (
                PARTITION BY r.project_id
                ORDER BY COALESCE(r.rubric_rank, 0) DESC, r.id DESC
            ) AS rn
            FROM reports r
            JOIN projects p ON p.id = r.project_id AND p.is_test = 0 AND COALESCE(p.moderation_hidden, 0) = 0
            WHERE COALESCE(r.is_test, 0) = 0
        )
        SELECT {cols} FROM latest r WHERE r.rn = 1
    """).fetchall()]  # nosec B608 - only fixed column names or the fixed badge list are formatted in; values use ? placeholders
    avg, best = {}, {}
    for f in RADAR_FACTOR_COLUMNS:
        vals = [float(r[f]) for r in rows if r.get(f) is not None]
        if vals:
            avg[f] = round(sum(vals) / len(vals), 1)
            best[f] = round(max(vals), 1)
    return {"count": len(rows), "average": avg, "best": best}


def get_project_detail(
    full_slug: str,
    db_path: Path = DEFAULT_DB_PATH,
    report_id: Optional[int] = None,
    include_test: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Returns project details, a report (the latest production/newest-engine report, or `report_id` when it belongs to
    this project), parsed JSON, current achievements and the project's scan history. Test reports and test projects
    are returned only with `include_test` (the admin).
    """
    init_db(db_path)
    conn = get_db_connection(db_path)

    cur = conn.execute(
        """
        SELECT p.*, r.id as report_id, r.commit_sha, r.final_score, COALESCE(r.curved_score, r.final_score) AS curved_score,
               COALESCE(r.general_category, 'other') AS general_category, r.letter_grade, r.archetype_name,
               r.archetype_category, r.primary_language, r.verdict_title, r.verdict_summary,
               r.total_loc, r.total_files, r.malware_risk_score, r.operational_safety_score, r.code_health_score,
               r.user_journey_score, r.functionality_score, r.resonance_score, r.creativity_score,
               r.ui_ux_score, r.maintainability_score, r.architecture_score,
               COALESCE(r.rubric_version, '') AS rubric_version, COALESCE(r.is_test, 0) AS is_test_report,
               r.raw_json_path, r.html_report_path,
               r.markdown_report_path, r.report_data_json, r.created_at as graded_at
        FROM projects p
        JOIN reports r ON p.id = r.project_id
        WHERE p.full_slug = ?
          AND (p.is_test = 0 OR p.shared = 1 OR ?)
          AND (COALESCE(p.moderation_hidden, 0) = 0 OR ?)
          AND (COALESCE(r.is_test, 0) = 0 OR p.shared = 1 OR ?)
          AND (? IS NULL OR r.id = ?)
        ORDER BY (COALESCE(r.is_test, 0) = 0) DESC, COALESCE(r.rubric_rank, 0) DESC, r.id DESC
        LIMIT 1
        """,
        (full_slug, 1 if include_test else 0, 1 if include_test else 0, 1 if include_test else 0, report_id, report_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    data = dict(row)
    if data.get("report_data_json"):
        try:
            data["full_report"] = json.loads(data["report_data_json"])
        except Exception:
            data["full_report"] = None

    # Every visible scan of this project: production/newest-engine first, then older/test scans (admin only for test scans)
    show_test_reports = 1 if (include_test or data.get("shared")) else 0
    data["history"] = [dict(h) for h in conn.execute(
        """
        SELECT id AS report_id, created_at AS graded_at, final_score,
               COALESCE(curved_score, final_score) AS curved_score, letter_grade, commit_sha,
               COALESCE(NULLIF(rubric_version, ''), json_extract(report_data_json, '$.rubric_version')) AS rubric_version,
               COALESCE(is_test, 0) AS is_test
        FROM reports
        WHERE project_id = ? AND (COALESCE(is_test, 0) = 0 OR ?)
        ORDER BY (COALESCE(is_test, 0) = 0) DESC, COALESCE(rubric_rank, 0) DESC, id DESC
        """,
        (data["id"], show_test_reports),
    ).fetchall()]
    data["is_latest"] = bool(data["history"]) and data["history"][0]["report_id"] == data["report_id"]
    from ..evaluator.rubric import calculate_letter_grade
    from .peer_grades import category_standing, compute_peer_overlays
    overlays = compute_peer_overlays(conn)
    ov = overlays.get(int(data["report_id"]))
    raw_grade = calculate_letter_grade(float(data.get("final_score") or 0.0))
    data["raw_letter_grade"] = ov["raw_letter_grade"] if ov else raw_grade
    data["curved_letter_grade"] = ov["curved_letter_grade"] if ov else raw_grade
    data["curved_score"] = ov["curved_score"] if ov else round(float(data.get("final_score") or 0.0), 1)
    up_cnt = int(conn.execute("SELECT COUNT(*) c FROM project_upvotes WHERE project_id = ?", (data["id"],)).fetchone()["c"])
    up_bonus = upvote_score_bonus(up_cnt)
    data["upvote_count"] = up_cnt
    data["upvote_bonus"] = up_bonus
    if up_bonus > 0:
        boosted = round(min(100.0, float(data["curved_score"]) + up_bonus), 1)
        data["curved_score"] = boosted
        data["curved_letter_grade"] = calculate_letter_grade(boosted)
    data["grade_basis"] = ov["grade_basis"] if ov else "rubric"
    data["curve"] = ov["curve"] if ov else None
    data["letter_grade"] = data["curved_letter_grade"]
    if isinstance(data.get("full_report"), dict):
        data["full_report"]["raw_letter_grade"] = data["raw_letter_grade"]
        data["full_report"]["curved_letter_grade"] = data["curved_letter_grade"]
        data["full_report"]["letter_grade"] = data["curved_letter_grade"]
        data["full_report"]["grade_basis"] = data["grade_basis"]
        data["full_report"]["curve"] = data["curve"]
    for h in data["history"]:
        h_ov = overlays.get(int(h["report_id"]))
        h_raw = calculate_letter_grade(float(h.get("final_score") or 0.0))
        h["raw_letter_grade"] = h_ov["raw_letter_grade"] if h_ov else h_raw
        h["curved_letter_grade"] = h_ov["curved_letter_grade"] if h_ov else h_raw
        h["curved_score"] = h_ov["curved_score"] if h_ov else round(float(h.get("final_score") or 0.0), 1)
        h["letter_grade"] = h["curved_letter_grade"]
    data["standing"] = category_standing(conn, data["id"])
    data["factor_stats"] = factor_stats(conn)

    # Fetch badges
    b_cur = conn.execute(
        """
        SELECT category, rank, badge_title, peak_score, achieved_at, superseded_at
        FROM ranking_history
        WHERE project_id = ? AND rank = 1 AND superseded_at IS NULL AND category IN (SELECT value FROM json_each(?))
        ORDER BY superseded_at IS NULL DESC, rank ASC, achieved_at DESC
        """,
        (data["id"], _SHOWN_JSON),
    )
    data["badges"] = [dict(b) for b in b_cur.fetchall()]

    conn.close()
    return data


def get_latest_report_for_slug(
    full_slug: str,
    db_path: Path = DEFAULT_DB_PATH,
    include_test: bool = False,
) -> Optional[Dict[str, Any]]:
    """Returns the most recent report record for a project slug, including commit SHA."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.execute(
            """
            SELECT r.*
            FROM reports r
            JOIN projects p ON p.id = r.project_id
            WHERE r.full_slug = ?
              AND (COALESCE(p.is_test, 0) = 0 OR ?)
              AND (COALESCE(r.is_test, 0) = 0 OR ?)
            ORDER BY r.id DESC
            LIMIT 1
            """,
            (full_slug, 1 if include_test else 0, 1 if include_test else 0),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# Queue functions
def enqueue_task(
    target: str,
    mode: str = "blend",
    requested_by: str = "user",
    force_override: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    is_test: bool = False,
    submission_meta: Optional[Dict[str, Any]] = None,
) -> int:
    """Adds a repository evaluation task to the queue (`is_test`: an admin test grading, shown only to the admin)."""
    init_db(db_path)
    conn = get_db_connection(db_path)

    identity = parse_repo_identity(target)
    meta_json = json.dumps(submission_meta or {})

    with conn:
        cur = conn.execute(
            """
            INSERT INTO audit_queue (target, owner, repo_name, full_slug, mode, status, requested_by_user, force_override, is_test, submission_meta_json)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (target, identity.owner, identity.repo_name, identity.full_slug, mode, requested_by,
             1 if force_override else 0, 1 if is_test else 0, meta_json),
        )
        task_id = cur.lastrowid
    conn.close()
    return task_id


def clear_finished_tasks(db_path: Path = DEFAULT_DB_PATH) -> int:
    """
    Removes completed and failed tasks from the queue list. Graded reports are separate and untouched.
    Rejected tasks are kept: owners appeal from them, and deleting a task would also delete its appeals.
    """
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            return conn.execute("DELETE FROM audit_queue WHERE status IN ('completed', 'failed', 'cancelled')").rowcount
    finally:
        conn.close()


def clear_pending_tasks(db_path: Path = DEFAULT_DB_PATH) -> int:
    """
    Removes all waiting/pending tasks from the queue list ('queued' status).
    Running tasks, completed reports, and rejected tasks are untouched.
    """
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            return conn.execute("DELETE FROM audit_queue WHERE status = 'queued'").rowcount
    finally:
        conn.close()


def get_queue_status(db_path: Path = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Retrieves queue control state and counts."""
    init_db(db_path)
    conn = get_db_connection(db_path)

    settings = conn.execute("SELECT is_paused, auto_process, pause_reason FROM queue_settings WHERE id = 1").fetchone()
    is_paused = bool(settings["is_paused"]) if settings else False
    auto_process = bool(settings["auto_process"]) if settings else True
    pause_reason = str(settings["pause_reason"] or "") if settings and "pause_reason" in settings.keys() else ""

    pending_count = conn.execute("SELECT COUNT(*) as c FROM audit_queue WHERE status = 'queued'").fetchone()["c"]
    running = conn.execute("SELECT * FROM audit_queue WHERE status = 'running' ORDER BY started_at DESC LIMIT 1").fetchone()

    conn.close()
    return {
        "is_paused": is_paused,
        "auto_process": auto_process,
        "pause_reason": pause_reason,
        "pending_count": pending_count,
        "running_task": dict(running) if running else None,
    }


def update_queue_settings(
    is_paused: Optional[bool] = None,
    auto_process: Optional[bool] = None,
    pause_reason: Optional[str] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    init_db(db_path)
    conn = get_db_connection(db_path)
    with conn:
        if is_paused is not None:
            conn.execute("UPDATE queue_settings SET is_paused = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1", (int(is_paused),))
        if auto_process is not None:
            conn.execute("UPDATE queue_settings SET auto_process = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1", (int(auto_process),))
        if pause_reason is not None:
            conn.execute("UPDATE queue_settings SET pause_reason = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1", (str(pause_reason),))
    conn.close()


def _enrich_queue_task_engine(item: Dict[str, Any]) -> Dict[str, Any]:
    """Attaches human-friendly engine_version and engine_label to a queue task dictionary."""
    mode = str(item.get("mode") or "blend").strip()
    if mode == "reclassify":
        item["engine_version"] = "reclassify"
        item["engine_label"] = "Reclassify"
        return item
    if mode == "error_review":
        item["engine_version"] = "error_review"
        item["engine_label"] = "Error Review"
        return item
    try:
        from ..evaluator.pipeline import get_engine_info
        info = get_engine_info(mode)
    except Exception:
        info = None
    if info:
        rv = str(info.get("rubric_version") or info.get("engine_version") or mode)
        short = str(info.get("short_name") or f"Rubric {rv}")
        item["engine_version"] = rv
        item["engine_label"] = short
        item["engine_is_testing"] = str(info.get("status") or "").lower() == "testing"
    else:
        rv = "v9.4" if mode in ("blend", "fast", "deep") else mode
        item["engine_version"] = rv
        item["engine_label"] = f"Rubric {rv}"
        item["engine_is_testing"] = False
    return item


def get_task_status_with_position(task_id: int, db_path: Path = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieves a single task's live status, queue position, pause status, and the admin's pause reason."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    task_row = conn.execute("SELECT * FROM audit_queue WHERE id = ?", (task_id,)).fetchone()
    if not task_row:
        conn.close()
        return None

    task = _enrich_queue_task_engine(dict(task_row))
    settings = conn.execute("SELECT is_paused, auto_process, pause_reason FROM queue_settings WHERE id = 1").fetchone()
    is_paused = bool(settings["is_paused"]) if settings else False
    pause_reason = str(settings["pause_reason"] or "") if settings and "pause_reason" in settings.keys() else ""

    position = None
    if task["status"] == "queued":
        front = conn.execute("SELECT front_task_id FROM queue_settings WHERE id = 1").fetchone()
        front_id = front["front_task_id"] if front else None
        ahead = 0 if front_id == task_id else conn.execute(
            """
            SELECT COUNT(*) as c FROM audit_queue
            WHERE status = 'queued' AND (id < ? OR id = ?)
            """,
            (task_id, front_id if front_id is not None else -1),
        ).fetchone()["c"]
        position = ahead + 1

    conn.close()
    return {
        **task,
        "queue_position": position,
        "is_queue_paused": is_paused,
        "queue_pause_reason": pause_reason,
    }


def get_queue_items(limit: int = 50, db_path: Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    init_db(db_path)
    conn = get_db_connection(db_path)
    cur = conn.execute(
        """
        SELECT * FROM audit_queue
        ORDER BY
            CASE status
                WHEN 'running' THEN 1
                WHEN 'queued' THEN 2
                ELSE 3
            END,
            -- Waiting tasks are listed in the order they will run: a task stopped by the admin first, then
            -- oldest first (as the worker takes them); finished ones newest first.
            CASE WHEN status = 'queued' AND id = (SELECT front_task_id FROM queue_settings WHERE id = 1) THEN 0 ELSE 1 END,
            CASE WHEN status = 'queued' THEN id END ASC,
            id DESC
        LIMIT ?
        """,
        (limit,),
    )
    items = [_enrich_queue_task_engine(dict(row)) for row in cur.fetchall()]
    conn.close()
    position = 0
    for item in items:
        if item["status"] == "queued":
            position += 1
            item["queue_position"] = position
    return items


def get_projects_tree(search: Optional[str] = None, db_path: Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Returns projects grouped by developer (owner) in a hierarchical tree structure, with all scanned engine versions."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    cur = conn.execute("""
        WITH latest_reports AS (
            SELECT r.*, ROW_NUMBER() OVER(
                PARTITION BY r.project_id
                ORDER BY (COALESCE(r.is_test, 0) = 0) DESC, COALESCE(r.rubric_rank, 0) DESC, r.id DESC
            ) as rn
            FROM reports r
        )
        SELECT
            p.id as project_id,
            p.owner,
            p.repo_name,
            p.full_slug,
            p.canonical_url,
            p.owner_avatar_url,
            p.repo_icon_url,
            r.id as report_id,
            r.final_score,
            r.letter_grade,
            r.archetype_name,
            r.primary_language,
            COALESCE(r.rubric_version, '') as rubric_version,
            r.created_at as graded_at,
            p.is_test
        FROM projects p
        LEFT JOIN latest_reports r ON p.id = r.project_id AND r.rn = 1
        ORDER BY p.owner ASC, r.final_score DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]

    # Collect all scanned versions per project so Admin can see every engine a repository has been graded with
    rep_rows = conn.execute("""
        SELECT
            project_id,
            id AS report_id,
            COALESCE(rubric_version, '') AS rubric_version,
            COALESCE(rubric_rank, 0) AS rubric_rank,
            COALESCE(is_test, 0) AS is_test,
            final_score,
            letter_grade,
            created_at AS graded_at
        FROM reports
        ORDER BY (COALESCE(is_test, 0) = 0) DESC, COALESCE(rubric_rank, 0) DESC, id DESC
    """).fetchall()
    scans_by_pid: Dict[int, List[Dict[str, Any]]] = {}
    for rr in rep_rows:
        scans_by_pid.setdefault(int(rr["project_id"]), []).append(dict(rr))

    # Collect most recent submission_meta per slug so Force Re-Scan pre-populates previous answers
    meta_by_slug: Dict[str, Dict[str, Any]] = {}
    try:
        q_rows = conn.execute("""
            SELECT full_slug, submission_meta_json
            FROM audit_queue
            WHERE full_slug IS NOT NULL AND submission_meta_json IS NOT NULL
              AND submission_meta_json != '' AND submission_meta_json != '{}'
            ORDER BY id DESC
        """).fetchall()
        for qr in q_rows:
            s_key = str(qr["full_slug"]).lower()
            if s_key not in meta_by_slug:
                try:
                    parsed = json.loads(qr["submission_meta_json"])
                    if isinstance(parsed, dict) and parsed:
                        meta_by_slug[s_key] = parsed
                except Exception:
                    pass
    except Exception:
        pass

    conn.close()

    for r in rows:
        pid = int(r["project_id"])
        r["scanned_versions"] = scans_by_pid.get(pid, [])
        r["last_submission_meta"] = meta_by_slug.get(str(r["full_slug"] or "").lower(), {})

    if search:
        q = search.lower().strip()
        rows = [r for r in rows if q in (r["owner"] + " " + r["repo_name"] + " " + r["full_slug"]).lower()]

    grouped: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        owner = r["owner"]
        if owner not in grouped:
            grouped[owner] = {
                "developer": owner,
                "avatar_url": r["owner_avatar_url"] or f"https://github.com/{owner}.png",
                "project_count": 0,
                "projects": [],
            }
        grouped[owner]["projects"].append(r)
        grouped[owner]["project_count"] += 1

    return list(grouped.values())


def delete_project(full_slug: str, db_path: Path = DEFAULT_DB_PATH, recalc: bool = True) -> bool:
    """
    Deletes a project, cascading removal to reports, badges, queue items and appeals, plus its
    on-disk report files, then updates ranks (callers deleting many projects pass recalc=False and
    recalculate once at the end).
    """
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM held_reports WHERE lower(full_slug) = lower(?)", (full_slug,))
            conn.execute("DELETE FROM error_reports WHERE lower(full_slug) = lower(?)", (full_slug,))
            proj = conn.execute("SELECT id FROM projects WHERE full_slug = ?", (full_slug,)).fetchone()
            if not proj:
                return False
            pid = proj["id"]
            # 1. Nullify any audit_queue foreign keys referencing reports of this project
            conn.execute(
                """
                UPDATE audit_queue
                SET report_id = NULL
                WHERE report_id IN (SELECT id FROM reports WHERE project_id = ?)
                """,
                (pid,),
            )
            # 2. Remove audit_queue entries for this project
            conn.execute("DELETE FROM audit_queue WHERE full_slug = ?", (full_slug,))
            # 3. Remove appeals associated with this project
            conn.execute("DELETE FROM appeals WHERE full_slug = ?", (full_slug,))
            # 4. Remove project record (cascades to reports and ranking_history)
            conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
    finally:
        conn.close()

    # 5. Clean up on-disk report directory if it exists (never outside data/reports)
    try:
        parts = full_slug.split("/", 1)
        if len(parts) == 2:
            owner, repo_name = parts
            reports_root = Path("data/reports").resolve()
            report_dir = (reports_root / owner / repo_name).resolve()
            if reports_root in report_dir.parents and report_dir.is_dir():
                shutil.rmtree(report_dir, ignore_errors=True)
                owner_dir = report_dir.parent
                if owner_dir != reports_root and owner_dir.is_dir() and not any(owner_dir.iterdir()):
                    owner_dir.rmdir()
    except Exception as exc:
        logger.warning(f"Error removing disk reports for a deleted project: {exc}")

    if recalc:
        recalculate_historical_ranks(db_path)
    return True


def delete_single_report(report_id: int, db_path: Path = DEFAULT_DB_PATH, recalc: bool = True) -> bool:
    """
    Deletes a single evaluation report by ID.
    - If this was the project's only report, also deletes the parent project.
    - If earlier reports exist, the project remains and reverts to its most recent remaining report.
    - Updates audit_queue foreign keys, ranking history, and recalculates historical ranks.
    """
    init_db(db_path)
    conn = get_db_connection(db_path)
    proj_id = None
    remaining_count = 0
    full_slug = None
    try:
        with conn:
            rep = conn.execute("SELECT id, project_id, full_slug FROM reports WHERE id = ?", (report_id,)).fetchone()
            if not rep:
                return False
            proj_id = rep["project_id"]
            full_slug = rep["full_slug"]

            # Unlink any audit_queue items pointing to this specific report
            conn.execute("UPDATE audit_queue SET report_id = NULL WHERE report_id = ?", (report_id,))

            # Remove from ranking history if this report held badges
            conn.execute("DELETE FROM ranking_history WHERE report_id = ?", (report_id,))

            # Delete the specific report
            conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))

            # Check if any reports remain for this project
            count_row = conn.execute("SELECT COUNT(*) AS c FROM reports WHERE project_id = ?", (proj_id,)).fetchone()
            remaining_count = count_row["c"] if count_row else 0

            if remaining_count == 0:
                # No more reports left for this project: remove project record cascading
                conn.execute("DELETE FROM audit_queue WHERE full_slug = ?", (full_slug,))
                conn.execute("DELETE FROM appeals WHERE full_slug = ?", (full_slug,))
                conn.execute("DELETE FROM projects WHERE id = ?", (proj_id,))
    finally:
        conn.close()

    if remaining_count == 0 and full_slug:
        # Also clean up disk reports if the whole project is now gone
        try:
            parts = full_slug.split("/", 1)
            if len(parts) == 2:
                owner, repo_name = parts
                reports_root = Path("data/reports").resolve()
                report_dir = (reports_root / owner / repo_name).resolve()
                if reports_root in report_dir.parents and report_dir.is_dir():
                    shutil.rmtree(report_dir, ignore_errors=True)
                    owner_dir = report_dir.parent
                    if owner_dir != reports_root and owner_dir.is_dir() and not any(owner_dir.iterdir()):
                        owner_dir.rmdir()
        except Exception as exc:
            logger.warning(f"Error removing disk reports: {exc}")

    if recalc:
        recalculate_historical_ranks(db_path)
    return True


# ==========================================
# Self-service privacy controls
# ==========================================

def list_projects_owned_by(
    username: str,
    db_path: Path = DEFAULT_DB_PATH,
    include_test: bool = False,
) -> List[Dict[str, Any]]:
    """Projects under this GitHub account (case-insensitive owner match), newest grade first."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    inc = 1 if include_test else 0
    try:
        rows = conn.execute(
            """
            SELECT p.full_slug, p.repo_name, p.created_at,
                   (SELECT r.final_score FROM reports r WHERE r.project_id = p.id AND (COALESCE(r.is_test, 0) = 0 OR p.shared = 1 OR ?) ORDER BY (COALESCE(r.is_test, 0) = 0) DESC, COALESCE(r.rubric_rank, 0) DESC, r.id DESC LIMIT 1) AS final_score,
                   (SELECT r.letter_grade FROM reports r WHERE r.project_id = p.id AND (COALESCE(r.is_test, 0) = 0 OR p.shared = 1 OR ?) ORDER BY (COALESCE(r.is_test, 0) = 0) DESC, COALESCE(r.rubric_rank, 0) DESC, r.id DESC LIMIT 1) AS letter_grade,
                   (SELECT r.created_at FROM reports r WHERE r.project_id = p.id AND (COALESCE(r.is_test, 0) = 0 OR p.shared = 1 OR ?) ORDER BY (COALESCE(r.is_test, 0) = 0) DESC, COALESCE(r.rubric_rank, 0) DESC, r.id DESC LIMIT 1) AS graded_at,
                   (SELECT COUNT(*) FROM reports r WHERE r.project_id = p.id AND (COALESCE(r.is_test, 0) = 0 OR p.shared = 1 OR ?)) AS report_count
            FROM projects p
            WHERE lower(p.owner) = lower(?)
              AND (COALESCE(p.is_test, 0) = 0 OR p.shared = 1 OR ?)
            ORDER BY graded_at DESC
            """,
            (inc, inc, inc, inc, username, inc),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_pending_tasks_for_owner(username: str, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Removes queued/running/finished queue rows for this account's repositories or submitted by it."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM held_reports WHERE lower(owner) = lower(?)", (username,))
            conn.execute("DELETE FROM error_reports WHERE lower(owner) = lower(?)", (username,))
            cur = conn.execute(
                "DELETE FROM audit_queue WHERE lower(owner) = lower(?) OR lower(requested_by_user) = lower(?)",
                (username, username),
            )
            return cur.rowcount
    finally:
        conn.close()


def delete_user_account(github_id: str, username: str, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """
    Deletes the account row and personal data tied to it: appeals about the account's repositories
    or sent from its email address, and its queue history. Projects are deleted separately
    (in batches) before this is called. After this, existing sessions for the account stop working.
    """
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            row = conn.execute("SELECT email FROM users WHERE github_id = ?", (github_id,)).fetchone()
            email = (row["email"] or "").strip().lower() if row else ""
            conn.execute(
                "DELETE FROM appeals WHERE lower(substr(full_slug, 1, instr(full_slug, '/') - 1)) = lower(?)"
                " OR lower(target) LIKE ? OR lower(submitted_by) = lower(?) OR (? != '' AND lower(submitter_email) = ?)",
                (username, f"%github.com/{username.lower()}/%", username, email, email),
            )
            conn.execute(
                "DELETE FROM audit_queue WHERE lower(owner) = lower(?) OR lower(requested_by_user) = lower(?)",
                (username, username),
            )
            conn.execute("DELETE FROM held_reports WHERE lower(owner) = lower(?)", (username,))
            conn.execute("DELETE FROM error_reports WHERE lower(owner) = lower(?)", (username,))
            conn.execute("UPDATE projects SET submitted_by_user_id = NULL WHERE submitted_by_user_id = ?", (github_id,))
            conn.execute("UPDATE projects SET claimed_by_user_id = NULL WHERE claimed_by_user_id = ?", (github_id,))
            conn.execute("DELETE FROM project_upvotes WHERE github_id = ?", (github_id,))
            cur = conn.execute("DELETE FROM users WHERE github_id = ?", (github_id,))
            return cur.rowcount > 0
    finally:
        conn.close()


def list_appeals_for_user(username: str, db_path: Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Appeals the account submitted or that concern its repositories, newest first."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, full_slug, target, message, status, admin_response, created_at, reviewed_at FROM appeals "
            "WHERE (lower(submitted_by) = lower(?) OR lower(substr(full_slug, 1, instr(full_slug, '/') - 1)) = lower(?)) "
            "AND COALESCE(message, '') NOT LIKE '🛑 [AUTO-DIAGNOSTIC HARD STOP]%' "
            "ORDER BY id DESC",
            (username, username),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_maintainer_notes_for_slug(full_slug: str) -> Optional[Dict[str, Any]]:
    """Loads maintainer_notes.json for a validated owner/repo slug if present on disk."""
    if "/" not in (full_slug or ""):
        return None
    owner, repo = full_slug.split("/", 1)
    safe_owner = re.sub(r"[^\w\-.]", "_", owner)
    safe_repo = re.sub(r"[^\w\-.]", "_", repo)
    f = Path("data/reports") / safe_owner / safe_repo / "maintainer_notes.json"
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def export_user_data(github_id: str, username: str, db_path: Path = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """Everything LetsVibe stores that is tied to this account, as plain data (right of access)."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        user = conn.execute(
            "SELECT github_id, github_username, github_avatar_url, created_at, last_login_at, terms_version, terms_accepted_at "
            "FROM users WHERE github_id = ?",
            (github_id,),
        ).fetchone()
        project_rows = conn.execute(
            """
            SELECT r.id AS report_id, r.full_slug, r.final_score, r.curved_score, r.letter_grade,
                   r.verdict_title, r.verdict_summary, r.general_category, r.platforms, r.device_profile,
                   r.archetype_name, r.archetype_category, r.primary_language, r.total_loc, r.total_files,
                   r.operational_safety_score, r.code_health_score, r.maintainability_score,
                   r.functionality_score, r.resonance_score, r.creativity_score, r.ui_ux_score,
                   r.architecture_score, r.user_journey_score, r.rubric_version,
                   r.commit_sha, r.report_data_json, r.created_at
            FROM reports r JOIN projects p ON p.id = r.project_id
            WHERE lower(p.owner) = lower(?)
              AND (COALESCE(p.is_test, 0) = 0 OR p.shared = 1)
              AND (COALESCE(r.is_test, 0) = 0 OR p.shared = 1)
            ORDER BY r.id
            """,
            (username,),
        ).fetchall()
        projects = []
        seen_slugs = set()
        for row in project_rows:
            item = dict(row)
            raw_json = item.pop("report_data_json", None)
            if raw_json:
                try:
                    item["full_report"] = strip_safety_details(json.loads(raw_json))
                except Exception:
                    item["full_report"] = None
            seen_slugs.add(item["full_slug"])
            projects.append(item)

        maintainer_notes = {}
        for slug in sorted(seen_slugs):
            notes = _load_maintainer_notes_for_slug(slug)
            if notes is not None:
                maintainer_notes[slug] = notes

        badges = conn.execute(
            """
            SELECT p.full_slug, rh.category, rh.rank, rh.badge_title, rh.peak_score, rh.achieved_at, rh.superseded_at
            FROM ranking_history rh JOIN projects p ON p.id = rh.project_id
            WHERE lower(p.owner) = lower(?)
              AND (COALESCE(p.is_test, 0) = 0 OR p.shared = 1)
            ORDER BY rh.id
            """,
            (username,),
        ).fetchall()
        appeals = conn.execute(
            "SELECT full_slug, target, message, status, admin_response, created_at, reviewed_at FROM appeals "
            "WHERE (lower(substr(full_slug, 1, instr(full_slug, '/') - 1)) = lower(?) OR lower(target) LIKE ? "
            "OR lower(submitted_by) = lower(?)) "
            "AND COALESCE(message, '') NOT LIKE '🛑 [AUTO-DIAGNOSTIC HARD STOP]%'",
            (username, f"%github.com/{username.lower()}/%", username),
        ).fetchall()
        queue = conn.execute(
            "SELECT full_slug, target, mode, status, progress_message, error_message, rejection_reason, "
            "submission_meta_json, created_at, completed_at FROM audit_queue "
            "WHERE (lower(owner) = lower(?) OR lower(requested_by_user) = lower(?)) "
            "AND COALESCE(is_test, 0) = 0 ORDER BY id",
            (username, username),
        ).fetchall()
        held = conn.execute(
            "SELECT full_slug, kind, status, flagged_lines_json, owner_response, created_at, deadline_at, responded_at "
            "FROM held_reports WHERE lower(owner) = lower(?) ORDER BY id",
            (username,),
        ).fetchall()
        errors = conn.execute(
            "SELECT full_slug, statement_path, statement_text, explanation, status, ai_verdict, ai_explanation, "
            "ai_corrected_text, created_at, resolved_at "
            "FROM error_reports WHERE lower(owner) = lower(?) ORDER BY id",
            (username,),
        ).fetchall()
        return {
            "exported_at": datetime.now().isoformat(),
            "account": dict(user) if user else None,
            "reports_awaiting_your_explanation": [dict(r) for r in held],
            "error_reports": [dict(r) for r in errors],
            "graded_reports": projects,
            "maintainer_notes": maintainer_notes,
            "badges": [dict(b) for b in badges],
            "appeals": [dict(r) for r in appeals],
            "queue_history": [dict(r) for r in queue],
        }
    finally:
        conn.close()


def get_creators_leaderboard(sort_by: str = "peak", db_path: Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """
    Returns creator rankings:
    - If sort_by == 'peak': sorted by highest graded repository score desc
    - If sort_by == 'avg': sorted by average score across all graded repositories desc
    """
    init_db(db_path)
    conn = get_db_connection(db_path)
    cur = conn.execute("""
        WITH latest_reports AS (
            SELECT r.*, ROW_NUMBER() OVER(
                PARTITION BY r.project_id
                ORDER BY COALESCE(r.rubric_rank, 0) DESC, r.id DESC
            ) as rn
            FROM reports r
            WHERE COALESCE(r.is_test, 0) = 0
        )
        SELECT
            p.id as project_id,
            p.owner,
            p.repo_name,
            p.full_slug,
            p.canonical_url,
            p.owner_avatar_url,
            p.repo_icon_url,
            r.final_score,
            r.letter_grade,
            r.archetype_name,
            r.primary_language,
            r.total_loc
        FROM projects p
        JOIN latest_reports r ON p.id = r.project_id AND r.rn = 1
        WHERE p.is_test = 0 AND COALESCE(p.moderation_hidden, 0) = 0
    """)
    rows = [dict(r) for r in cur.fetchall()]

    # Fetch beta pioneer usernames
    try:
        cur_p = conn.execute("SELECT github_username FROM users WHERE is_beta_pioneer = 1")
        pioneer_set = {row["github_username"].lower() for row in cur_p.fetchall()}
    except Exception:
        pioneer_set = set()

    conn.close()

    creators_dict: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        owner = r["owner"]
        if owner not in creators_dict:
            creators_dict[owner] = {
                "owner": owner,
                "avatar_url": r["owner_avatar_url"] or f"https://github.com/{owner}.png",
                "is_beta_pioneer": owner.lower() in pioneer_set,
                "total_repos": 0,
                "total_loc": 0,
                "scores": [],
                "languages": set(),
                "top_repo_name": r["repo_name"],
                "top_repo_slug": r["full_slug"],
                "top_score": r["final_score"],
                "top_grade": r["letter_grade"],
                "top_repo_icon": r["repo_icon_url"] or r["owner_avatar_url"] or f"https://github.com/{owner}.png",
                "projects": [],
            }
        c = creators_dict[owner]
        c["total_repos"] += 1
        c["total_loc"] += r["total_loc"] or 0
        c["scores"].append(r["final_score"])
        if r.get("primary_language"):
            c["languages"].add(r["primary_language"])
        c["projects"].append(r)

        if r["final_score"] > c["top_score"]:
            c["top_score"] = r["final_score"]
            c["top_grade"] = r["letter_grade"]
            c["top_repo_name"] = r["repo_name"]
            c["top_repo_slug"] = r["full_slug"]
            c["top_repo_icon"] = r["repo_icon_url"] or r["owner_avatar_url"] or f"https://github.com/{owner}.png"

    from ..evaluator.rubric import calculate_letter_grade

    creators_list = []
    for c in creators_dict.values():
        avg_score = round(sum(c["scores"]) / len(c["scores"]), 1) if c["scores"] else 0.0
        c["average_score"] = avg_score
        c["average_grade"] = calculate_letter_grade(avg_score)
        c["languages"] = list(c["languages"])[:4]
        creators_list.append(c)

    if sort_by == "avg":
        creators_list.sort(key=lambda x: (-x["average_score"], -x["top_score"], x["owner"]))
    else:
        creators_list.sort(key=lambda x: (-x["top_score"], -x["average_score"], x["owner"]))

    for idx, c in enumerate(creators_list, start=1):
        c["rank"] = idx

    return creators_list


# ==========================================
# Appeals & Force Override Functions
# ==========================================

def create_appeal(
    target: str,
    submitter_email: str = "",
    message: str = "",
    task_id: Optional[int] = None,
    full_slug: Optional[str] = None,
    is_profane: bool = False,
    is_violent: bool = False,
    db_path: Path = DEFAULT_DB_PATH,
    submitted_by: str = "",
) -> int:
    """Records an appeal against a non-vibe rejection (by GitHub username; no email is stored)."""
    init_db(db_path)
    conn = get_db_connection(db_path)

    if not full_slug:
        try:
            ident = parse_repo_identity(target)
            full_slug = ident.full_slug
        except Exception:
            full_slug = target

    with conn:
        cur = conn.execute(
            """
            INSERT INTO appeals (task_id, target, full_slug, submitter_email, message, is_profane, is_violent, submitted_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                target,
                full_slug,
                submitter_email,
                message,
                1 if is_profane else 0,
                1 if is_violent else 0,
                submitted_by,
            ),
        )
        appeal_id = cur.lastrowid
    conn.close()
    return appeal_id


def get_appeals(status: Optional[str] = None, db_path: Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """
    Returns appeals ordered fairly:
    - Clean appeals are served from oldest to newest first.
    - Flagged appeals (violent/profane) are sent to the bottom of the queue, ordered oldest to newest.
    """
    init_db(db_path)
    conn = get_db_connection(db_path)

    query = """
        SELECT * FROM appeals
    """
    params = []
    if status:
        query += " WHERE status = ?"
        params.append(status)

    # Clean appeals (0) first, flagged (1) bottom. Both sorted oldest to newest (created_at ASC)
    query += " ORDER BY (is_profane = 1 OR is_violent = 1) ASC, created_at ASC"

    cur = conn.execute(query, tuple(params))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def review_appeal(
    appeal_id: int,
    approved: bool,
    admin_response: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Marks an appeal as approved or declined, and if approved, enqueues the repo with force_override."""
    init_db(db_path)
    conn = get_db_connection(db_path)

    now_iso = datetime.now().isoformat()
    new_status = "approved" if approved else "declined"

    with conn:
        cur = conn.execute("SELECT * FROM appeals WHERE id = ?", (appeal_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return None

        appeal = dict(row)
        conn.execute(
            """
            UPDATE appeals
            SET status = ?, admin_response = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (new_status, admin_response, now_iso, appeal_id),
        )

    conn.close()

    if approved:
        # Re-enqueue repository with force_override=True
        enqueue_task(
            target=appeal["target"],
            mode="blend",
            requested_by=f"appeal_override:{appeal['submitter_email']}",
            force_override=True,
            db_path=db_path,
        )

    appeal["status"] = new_status
    appeal["admin_response"] = admin_response
    appeal["reviewed_at"] = now_iso
    return appeal


def decline_all_flagged_appeals(
    reason: str = "Resubmit and rephrase appeal professionally if you want me to read it.",
    db_path: Path = DEFAULT_DB_PATH,
) -> List[Dict[str, Any]]:
    """Declines all pending violent or profane flagged appeals in bulk, applying the default boundary notice."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    now_iso = datetime.now().isoformat()
    declined_appeals = []

    try:
        with conn:
            cur = conn.execute(
                """
                SELECT * FROM appeals
                WHERE status = 'pending' AND (is_profane = 1 OR is_violent = 1)
                ORDER BY created_at ASC
                """
            )
            rows = [dict(r) for r in cur.fetchall()]
            if not rows:
                return []

            for row in rows:
                conn.execute(
                    """
                    UPDATE appeals
                    SET status = 'declined', admin_response = ?, reviewed_at = ?
                    WHERE id = ?
                    """,
                    (reason, now_iso, row["id"]),
                )
                row["status"] = "declined"
                row["admin_response"] = reason
                row["reviewed_at"] = now_iso
                declined_appeals.append(row)

        return declined_appeals
    finally:
        conn.close()


def force_override_task(task_id: int, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Direct admin override button: forces an audit of any rejected or failed task."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            cur = conn.execute("SELECT * FROM audit_queue WHERE id = ?", (task_id,))
            row = cur.fetchone()
            if not row:
                return False

            conn.execute(
                """
                UPDATE audit_queue
                SET status = 'queued', force_override = 1, progress_message = 'Admin forced audit override enqueued...'
                WHERE id = ?
                """,
                (task_id,),
            )
        return True
    finally:
        conn.close()


# ==========================================
# User & Beta Pioneer Quota Operations
# ==========================================

def upsert_user(
    github_id: str,
    username: str,
    avatar_url: Optional[str] = None,
    email: Optional[str] = None,
    scope_level: str = "minimal",
    is_beta_pioneer: bool = True,
    bonus_quota: int = 1,
    db_path: Path = DEFAULT_DB_PATH,
    terms_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Inserts or updates an authenticated user profile. `terms_version` records the Terms of Use accepted at this sign-in."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    now_iso = datetime.now().isoformat()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO users (github_id, github_username, github_avatar_url, email, scope_level, is_beta_pioneer, bonus_quota,
                                   last_login_at, terms_version, terms_accepted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(github_id) DO UPDATE SET
                    github_username = excluded.github_username,
                    github_avatar_url = excluded.github_avatar_url,
                    email = COALESCE(excluded.email, users.email),
                    scope_level = excluded.scope_level,
                    last_login_at = excluded.last_login_at,
                    terms_version = COALESCE(excluded.terms_version, users.terms_version),
                    terms_accepted_at = COALESCE(excluded.terms_accepted_at, users.terms_accepted_at)
                """,
                (
                    str(github_id),
                    username,
                    avatar_url,
                    email,
                    scope_level,
                    int(is_beta_pioneer),
                    bonus_quota,
                    now_iso,
                    terms_version,
                    now_iso if terms_version else None,
                ),
            )
            row = conn.execute("SELECT * FROM users WHERE github_id = ?", (str(github_id),)).fetchone()
            return dict(row)
    finally:
        conn.close()


def get_user_by_github_id(github_id: str, db_path: Path = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieves user profile by permanent numerical GitHub ID."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM users WHERE github_id = ?", (str(github_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_username(username: str, db_path: Path = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Retrieves user profile by GitHub login username."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM users WHERE LOWER(github_username) = LOWER(?)", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def count_registered_users(exclude_username: str = "", exclude_github_id: str = "", db_path: Path = DEFAULT_DB_PATH) -> int:
    """Counts registered users, excluding the owner/admin account (who is exempt from limits)."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE LOWER(github_username) != LOWER(?) AND github_id != ?",
            (exclude_username or "", str(exclude_github_id or "")),
        ).fetchone()
        return int(row["c"])
    finally:
        conn.close()


def check_user_submission_quota(
    username: str,
    is_admin: bool = False,
    base_quota: Optional[int] = None,
    window_hours: Optional[int] = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    """
    Evaluates the rolling per-user submission quota.
    Limits come from letsvibe/core/limits.py (override in .env); admins are unlimited.
    """
    from ..core import limits

    if base_quota is None:
        base_quota = limits.submissions_per_window()
    if window_hours is None:
        window_hours = limits.window_hours()

    if is_admin:
        return {
            "allowed": True,
            "remaining": 999,
            "total_limit": 999,
            "resets_in_seconds": 0,
            "window_hours": window_hours,
            "is_admin": True,
            "is_beta_pioneer": True,
        }

    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        user_row = conn.execute("SELECT * FROM users WHERE LOWER(github_username) = LOWER(?)", (username,)).fetchone()
        is_pioneer = bool(user_row["is_beta_pioneer"]) if user_row else True
        total_limit = base_quota

        cutoff = datetime.now() - timedelta(hours=window_hours)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        recent_submissions = conn.execute(
            """
            SELECT created_at FROM audit_queue
            WHERE LOWER(requested_by_user) = LOWER(?) AND created_at > ?
              AND (mode IS NULL OR mode NOT IN ('reclassify', 'factor_recheck', 'error_review'))
            ORDER BY created_at ASC
            """,
            (username, cutoff_str),
        ).fetchall()

        used = len(recent_submissions)
        remaining = max(0, total_limit - used)
        window_label = f"{window_hours // 24} day(s)" if window_hours % 24 == 0 else f"{window_hours}h"

        if remaining <= 0 and recent_submissions:
            try:
                oldest_str = recent_submissions[0]["created_at"]
                if "T" in oldest_str:
                    oldest_dt = datetime.fromisoformat(oldest_str)
                else:
                    oldest_dt = datetime.strptime(oldest_str, "%Y-%m-%d %H:%M:%S")
                resets_at = oldest_dt + timedelta(hours=window_hours)
                resets_sec = max(0, int((resets_at - datetime.now()).total_seconds()))
            except Exception:
                resets_sec = 3600

            days = resets_sec // 86400
            hrs = (resets_sec % 86400) // 3600
            mins = (resets_sec % 3600) // 60
            time_label = f"{days}d {hrs}h" if days > 0 else (f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m")

            return {
                "allowed": False,
                "reason": "quota_exhausted",
                "remaining": 0,
                "total_limit": total_limit,
                "window_hours": window_hours,
                "is_beta_pioneer": is_pioneer,
                "resets_in_seconds": resets_sec,
                "reset_in_hours": max(1, round(resets_sec / 3600)),
                "message": f"Submission limit reached ({total_limit} per {window_label}). Next slot opens in {time_label}.",
            }

        return {
            "allowed": True,
            "remaining": remaining,
            "total_limit": total_limit,
            "window_hours": window_hours,
            "is_beta_pioneer": is_pioneer,
            "resets_in_seconds": 0,
            "message": f"{remaining}/{total_limit} submissions remaining (per {window_label}).",
        }
    finally:
        conn.close()


def get_all_project_targets(db_path: Path = DEFAULT_DB_PATH) -> List[str]:
    """Retrieves all canonical repository URLs for bulk re-scanning."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute("SELECT canonical_url FROM projects").fetchall()
        return [row["canonical_url"] for row in rows if row["canonical_url"]]
    finally:
        conn.close()

def clear_entire_database(db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Wipes all evaluation reports, projects, ranking history, and queue tasks for testing or launch reset."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.executescript("""
                DELETE FROM ranking_history;
                DELETE FROM held_reports;
                DELETE FROM error_reports;
                DELETE FROM reports;
                DELETE FROM projects;
                DELETE FROM audit_queue;
                DELETE FROM appeals;
            """)
        return True
    except Exception as e:
        logger.error(f"Failed to clear database: {e}")
        return True
    finally:
        conn.close()

def regrade_all_reports(db_path: Path = DEFAULT_DB_PATH) -> int:
    """Recomputes stored letter grades from scores using the current grade curve. Returns rows changed."""
    from ..evaluator.rubric import calculate_letter_grade

    init_db(db_path)
    conn = get_db_connection(db_path)
    changed = 0
    try:
        with conn:
            for row in conn.execute("SELECT id, final_score, letter_grade, report_data_json FROM reports").fetchall():
                new_grade = calculate_letter_grade(float(row["final_score"]))
                if new_grade == row["letter_grade"]:
                    continue
                blob = row["report_data_json"]
                if blob:
                    try:
                        data = json.loads(blob)
                        data["letter_grade"] = new_grade
                        blob = json.dumps(data)
                    except (ValueError, TypeError):
                        pass
                conn.execute(
                    "UPDATE reports SET letter_grade = ?, report_data_json = ? WHERE id = ?",
                    (new_grade, blob, row["id"]),
                )
                changed += 1
    finally:
        conn.close()
    return changed


def update_report_classification(
    full_slug: str,
    general_category: str,
    platforms: List[str],
    device_profile: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> Optional[int]:
    """
    Reclassify: rewrites category / platforms / device profile on a project's latest report
    (columns and stored JSON) without touching its scores. Returns the report id, or None.
    """
    init_db(db_path)
    conn = get_db_connection(db_path)
    category = normalize_category(general_category)
    plats = normalize_platforms(platforms)
    try:
        with conn:
            row = conn.execute(
                "SELECT id, report_data_json FROM reports WHERE full_slug = ? ORDER BY id DESC LIMIT 1",
                (full_slug,),
            ).fetchone()
            if not row:
                return None
            blob = row["report_data_json"]
            if blob:
                try:
                    data = json.loads(blob)
                    arch = data.setdefault("archetype", {})
                    arch.update(general_category=category, platforms=plats, device_profile=device_profile)
                    blob = json.dumps(data)
                except (ValueError, TypeError, AttributeError):
                    pass
            conn.execute(
                "UPDATE reports SET general_category = ?, platforms = ?, device_profile = ?, report_data_json = ? WHERE id = ?",
                (category, f",{','.join(plats)}," if plats else "", device_profile, blob, row["id"]),
            )
            return row["id"]
    finally:
        conn.close()


MAX_OPEN_CONTENT_REPORTS_PER_USER = 3
MAX_CONTENT_REPORTS_PER_USER_24H = 5


class ReportRateLimitError(Exception):
    """Raised when a user exceeds the content-reporting rate limit."""


def create_content_report(
    full_slug: str,
    reported_by: str,
    reason: str,
    details: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> Optional[int]:
    """
    Files a visitor/community report (content violation or stolen work), immediately hides the project
    from public visibility (`moderation_hidden = 1`), and queues it for admin review.
    Enforces per-user rate limits so one account cannot mass-hide listings.
    """
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            proj = conn.execute("SELECT id FROM projects WHERE full_slug = ?", (full_slug,)).fetchone()
            if not proj:
                return None
            existing = conn.execute(
                "SELECT id FROM content_reports WHERE project_id = ? AND LOWER(reported_by) = LOWER(?) AND status = 'open'",
                (proj["id"], reported_by),
            ).fetchone()
            if existing:
                conn.execute("UPDATE projects SET moderation_hidden = 1 WHERE id = ?", (proj["id"],))
                return int(existing["id"])

            open_count = int(
                conn.execute(
                    "SELECT COUNT(*) c FROM content_reports WHERE LOWER(reported_by) = LOWER(?) AND status = 'open'",
                    (reported_by,),
                ).fetchone()["c"]
            )
            recent_24h = int(
                conn.execute(
                    "SELECT COUNT(*) c FROM content_reports WHERE LOWER(reported_by) = LOWER(?) AND created_at >= datetime('now', '-1 day')",
                    (reported_by,),
                ).fetchone()["c"]
            )
            if open_count >= MAX_OPEN_CONTENT_REPORTS_PER_USER or recent_24h >= MAX_CONTENT_REPORTS_PER_USER_24H:
                raise ReportRateLimitError(
                    "You have reached the content report limit while your previous reports are under review."
                )

            conn.execute("UPDATE projects SET moderation_hidden = 1 WHERE id = ?", (proj["id"],))
            cur = conn.execute(
                """
                INSERT INTO content_reports (project_id, full_slug, reported_by, reason, details, status)
                VALUES (?, ?, ?, ?, ?, 'open')
                """,
                (proj["id"], full_slug, reported_by, reason, (details or "").strip()[:1000]),
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def list_content_reports(status: str = "open", db_path: Path = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    """Lists content/stolen-work reports for the admin review inbox."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, project_id, full_slug, reported_by, reason, details, status, created_at, resolved_at
            FROM content_reports
            WHERE status = ?
            ORDER BY id DESC
            """,
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def resolve_content_report(report_id: int, action: str, db_path: Path = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """Admin action on a content report: 'restore' (unhide project) or 'remove' (delete project)."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    try:
        row = conn.execute("SELECT * FROM content_reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        if action == "restore":
            with conn:
                conn.execute("UPDATE projects SET moderation_hidden = 0 WHERE id = ?", (item["project_id"],))
                conn.execute(
                    "UPDATE content_reports SET status = 'restored', resolved_at = ? WHERE project_id = ? AND status = 'open'",
                    (now, item["project_id"]),
                )
            return item
        if action == "remove":
            with conn:
                conn.execute(
                    "UPDATE content_reports SET status = 'removed', resolved_at = ? WHERE project_id = ? AND status = 'open'",
                    (now, item["project_id"]),
                )
            delete_project(item["full_slug"], db_path=db_path)
            return item
        return None
    finally:
        conn.close()


def upvote_score_bonus(upvote_count: int) -> float:
    """Diminishing-returns community Spark bonus, capped at +3.0 points."""
    n = max(0, int(upvote_count or 0))
    if n <= 0:
        return 0.0
    return round(min(3.0, 0.5 * math.log2(1 + n)), 2)


def toggle_project_upvote(
    full_slug: str,
    github_id: str,
    username: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> Optional[Dict[str, Any]]:
    """Toggles a signed-in user's Spark upvote on a public project (unlimited for others' apps, max 1 of your own)."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        with conn:
            proj = conn.execute(
                "SELECT id, owner, full_slug FROM projects WHERE lower(full_slug) = lower(?) AND COALESCE(moderation_hidden, 0) = 0",
                (full_slug,),
            ).fetchone()
            if not proj:
                return None
            pid = int(proj["id"])
            existing = conn.execute(
                "SELECT 1 FROM project_upvotes WHERE project_id = ? AND github_id = ?",
                (pid, github_id),
            ).fetchone()
            if existing:
                conn.execute(
                    "DELETE FROM project_upvotes WHERE project_id = ? AND github_id = ?",
                    (pid, github_id),
                )
                upvoted = False
            else:
                is_own = bool(username and proj["owner"] and proj["owner"].lower() == username.lower())
                if is_own:
                    other_own = conn.execute(
                        """
                        SELECT p.full_slug FROM project_upvotes u
                        JOIN projects p ON p.id = u.project_id
                        WHERE u.github_id = ? AND lower(p.owner) = lower(?) AND p.id != ?
                        """,
                        (github_id, username, pid),
                    ).fetchone()
                    if other_own:
                        raise PermissionError(
                            f"You can only upvote one of your own repositories (already upvoted {other_own['full_slug']})."
                        )
                conn.execute(
                    "INSERT INTO project_upvotes (project_id, github_id) VALUES (?, ?)",
                    (pid, github_id),
                )
                upvoted = True
            count = int(
                conn.execute("SELECT COUNT(*) c FROM project_upvotes WHERE project_id = ?", (pid,)).fetchone()["c"]
            )
            return {
                "full_slug": proj["full_slug"],
                "upvoted": upvoted,
                "upvote_count": count,
                "upvote_bonus": upvote_score_bonus(count),
            }
    finally:
        conn.close()


def get_upvote_map(
    viewer_github_id: str = "",
    db_path: Path = DEFAULT_DB_PATH,
) -> Tuple[Dict[int, int], set[int]]:
    """Returns (counts_by_project_id, viewer_upvoted_project_ids)."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        counts: Dict[int, int] = {}
        for r in conn.execute("SELECT project_id, COUNT(*) c FROM project_upvotes GROUP BY project_id"):
            counts[int(r["project_id"])] = int(r["c"])
        mine: set[int] = set()
        if viewer_github_id:
            for r in conn.execute("SELECT project_id FROM project_upvotes WHERE github_id = ?", (viewer_github_id,)):
                mine.add(int(r["project_id"]))
        return counts, mine
    finally:
        conn.close()
