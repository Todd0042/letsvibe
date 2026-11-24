"""Where a project ranks within its category, by the score it shows: the analysis plus its Spark bonus, the same
order as the leaderboard. This is a rank only: grades come straight from the score on the fixed letter bands, no curve."""
import sqlite3
from typing import Dict, List, Optional


def latest_reports_by_category(conn: sqlite3.Connection, test: bool = False) -> Dict[str, List[sqlite3.Row]]:
    """Latest non-test report of every public project (or, with `test`, of every admin test project), grouped by category."""
    if not test:
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT r.*, ROW_NUMBER() OVER (
                    PARTITION BY r.project_id
                    ORDER BY COALESCE(r.rubric_rank, 0) DESC, r.id DESC
                ) AS rn
                FROM reports r
                JOIN projects p ON p.id = r.project_id AND p.is_test = 0
                WHERE COALESCE(r.is_test, 0) = 0
            )
            SELECT id, project_id, final_score, letter_grade, curved_score, coalesce(general_category, 'other') AS cat, report_data_json
            FROM latest WHERE rn = 1
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT r.*, ROW_NUMBER() OVER (
                    PARTITION BY r.project_id
                    ORDER BY (COALESCE(r.is_test, 0) = 0) DESC, COALESCE(r.rubric_rank, 0) DESC, r.id DESC
                ) AS rn
                FROM reports r
                JOIN projects p ON p.id = r.project_id AND p.is_test = 1
            )
            SELECT id, project_id, final_score, letter_grade, curved_score, coalesce(general_category, 'other') AS cat, report_data_json
            FROM latest WHERE rn = 1
            """
        ).fetchall()
    groups: Dict[str, List[sqlite3.Row]] = {}
    for r in rows:
        groups.setdefault(r["cat"], []).append(r)
    return groups


def category_standing(conn: sqlite3.Connection, project_id: int) -> Optional[Dict[str, object]]:
    """{rank, count, category} for a project's latest report within its category (public projects, plus the project
    itself when it is an admin test project)."""
    public = latest_reports_by_category(conn)
    for cat, rows in latest_reports_by_category(conn, test=True).items():
        public[cat] = public.get(cat, []) + [r for r in rows if r["project_id"] == project_id]
    from .db import upvote_score_bonus
    sparks = {int(r[0]): int(r[1]) for r in conn.execute("SELECT project_id, COUNT(*) FROM project_upvotes GROUP BY project_id")}

    def shown(r) -> float:  # the score people see: the analysis plus the Spark bonus (as on the leaderboard)
        return round(min(100.0, float(r["final_score"] or 0) + upvote_score_bonus(sparks.get(int(r["project_id"]), 0))), 1)

    for cat, rows in public.items():
        ordered = sorted(rows, key=lambda r: -shown(r))
        for i, r in enumerate(ordered, start=1):
            if r["project_id"] == project_id:
                return {"category": cat, "rank": i, "count": len(ordered)}
    return None
