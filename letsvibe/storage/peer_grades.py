"""
Peer-relative letter grades (rubric v6).

LetsVibe compares vibe-coded projects with each other, so a project's letter grade reflects where it
stands among published projects in the SAME category:

- Until a category has PEER_MIN_PROJECTS projects, the grade comes from the prior curve
  (`evaluator.consensus.calculate_letter_grade`), which mirrors the rubric's score bands.
- From then on it comes from the project's percentile in its category: roughly the top 10% are A,
  the next 28% B, the middle 40% C and the rest D.

Guardrails keep percentiles honest: a score below FAIL_BELOW is always an F, and A grades also need
an absolute score of at least A_REQUIRES, so the best project in a weak category is not an A merely
for being first. Scores themselves never change here, only the letter.
"""

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---- the grading curve (a bell curve) ------------------------------------------------------------
# AI reviewers hedge toward the middle (in LetsVibe's testing a deliberately bad app and a carefully built
# one averaged about 28 and 68, never near 0 or 100), so letters come from a bell curve:
#
#   z = (score - average) / standard deviation
#
# Before a category has projects, the bell is set by two published reference projects
# (letsvibe/data/curve_calibration.json): the average is the midpoint between them and one standard
# deviation is a quarter of the gap, so the top reference sits at +2 and the bottom at -2. As a category
# grows, its own average and standard deviation take over, fully at PEER_MIN_PROJECTS projects.
#
# Rails tie letters to the references: an A also needs a score at least A_REQUIRES_POSITION of the way from
# the bottom reference to the top one, and a score below F_BELOW_POSITION of that way is an F. If a real
# project ever scores beyond a reference, the scale used by the rails stretches to include it.
# Raw scores are never changed.
CALIBRATION_PATH = Path(__file__).resolve().parent.parent / "data" / "curve_calibration.json"
A_REQUIRES_POSITION = 0.75
F_BELOW_POSITION = 0.10
# (minimum z, grade), best first. On a full bell this gives about 7% A, 24% B, 38% C, 24% D and 7% F.
BELL_GRADES: Tuple[Tuple[float, str], ...] = (
    (2.0, "A+"), (1.75, "A"), (1.5, "A-"),
    (1.17, "B+"), (0.83, "B"), (0.5, "B-"),
    (0.17, "C+"), (-0.17, "C"), (-0.5, "C-"),
    (-1.5, "D"),
)

PEER_MIN_PROJECTS = 30
FAIL_BELOW = 40.0
A_REQUIRES = 75.0

# (minimum percentile, grade), best first. Percentile = share of category projects scoring lower.
PERCENTILE_GRADES: Tuple[Tuple[float, str], ...] = (
    (0.98, "A+"),
    (0.94, "A"),
    (0.90, "A-"),
    (0.82, "B+"),
    (0.72, "B"),
    (0.62, "B-"),
    (0.50, "C+"),
    (0.36, "C"),
    (0.22, "C-"),
    (0.00, "D"),
)


def percentile_grade(percentile: float, score: float) -> str:
    if score < FAIL_BELOW:
        return "F"
    for cut, grade in PERCENTILE_GRADES:
        if percentile >= cut:
            if grade.startswith("A") and score < A_REQUIRES:
                return "B+"
            return grade
    return "D"


def percentile_of(score: float, cohort: List[float]) -> float:
    """Share of the cohort scoring lower, counting ties as half (so a cohort of equals sits mid-table)."""
    if not cohort:
        return 0.5
    below = sum(1 for s in cohort if s < score)
    equal = sum(1 for s in cohort if s == score)
    return (below + 0.5 * max(0, equal - 1)) / max(1, len(cohort) - 1) if len(cohort) > 1 else 0.5


@lru_cache(maxsize=1)
def load_calibration() -> Optional[Dict[str, Any]]:
    """The published anchor calibration, or None if it has not been created (grades fall back to the rubric)."""
    try:
        data = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
        bottom, top = float(data["anchors"]["bottom"]["mean"]), float(data["anchors"]["top"]["mean"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return data if top > bottom else None


def scale_ends(calibration: Dict[str, Any], all_scores: List[float]) -> Tuple[float, float]:
    """(0% raw score, 100% raw score): the anchors, widened by any real project beyond them."""
    low = float(calibration["anchors"]["bottom"]["mean"])
    high = float(calibration["anchors"]["top"]["mean"])
    if all_scores:
        low, high = min(low, min(all_scores)), max(high, max(all_scores))
    return low, high


def anchor_position(score: float, ends: Tuple[float, float]) -> float:
    low, high = ends
    return 0.5 if high <= low else min(1.0, max(0.0, (score - low) / (high - low)))


def peer_weight(category_size: int) -> float:
    return min(1.0, category_size / PEER_MIN_PROJECTS)


def bell_share(z_low: float, z_high: float) -> float:
    """Share of a normal bell curve between two z values (for the published grade table)."""
    from math import erf, inf, sqrt

    cdf = lambda z: 0.0 if z == -inf else 1.0 if z == inf else 0.5 * (1 + erf(z / sqrt(2)))  # noqa: E731
    return cdf(z_high) - cdf(z_low)


def bell_params(calibration: Dict[str, Any], cohort: List[float]) -> Tuple[float, float, float]:
    """(average, standard deviation, category weight): the anchors' bell blended with the category's own."""
    import statistics

    bottom = float(calibration["anchors"]["bottom"]["mean"])
    top = float(calibration["anchors"]["top"]["mean"])
    mean, sd = (top + bottom) / 2, (top - bottom) / 4
    w = peer_weight(len(cohort)) if len(cohort) >= 2 else 0.0
    if w:
        mean = (1 - w) * mean + w * statistics.mean(cohort)
        sd = (1 - w) * sd + w * max(statistics.stdev(cohort), 1.0)
    return mean, sd, w


def bell_grade(z: float, anchor_pos: float) -> str:
    if anchor_pos < F_BELOW_POSITION:
        return "F"
    for cut, grade in BELL_GRADES:
        if z >= cut:
            return "B+" if grade.startswith("A") and anchor_pos < A_REQUIRES_POSITION else grade
    return "F"


def curve_details(score: float, cohort: List[float], calibration: Dict[str, Any], all_scores: List[float]) -> Dict[str, float]:
    mean, sd, w = bell_params(calibration, cohort)
    return {"z": round((score - mean) / sd, 3), "average": round(mean, 2), "sd": round(sd, 2), "peer_weight": round(w, 4),
            "anchor_position": round(anchor_position(score, scale_ends(calibration, all_scores)), 4)}


def grade_for(score: float, cohort: List[float], all_scores: Optional[List[float]] = None) -> Tuple[str, str]:
    """(letter, basis) for one project given all scores in its category (including its own)."""
    letter, basis, _ = grade_with_details(score, cohort, all_scores if all_scores is not None else cohort)
    return letter, basis


def grade_with_details(score: float, cohort: List[float], all_scores: List[float]) -> Tuple[str, str, Optional[Dict[str, float]]]:
    """(letter, basis, curve details). basis: "curve" (bell set by the anchors, category growing in), "peers", or "rubric"."""
    from ..evaluator.consensus import calculate_letter_grade

    calibration = load_calibration()
    if calibration is None:
        if len(cohort) < PEER_MIN_PROJECTS:
            return calculate_letter_grade(score), "rubric", None
        return percentile_grade(percentile_of(score, cohort), score), "peers", None
    details = curve_details(score, cohort, calibration, all_scores)
    basis = "peers" if details["peer_weight"] >= 1 else "curve"
    return bell_grade(details["z"], details["anchor_position"]), basis, details


def describe_method(calibration: Dict[str, Any]) -> str:
    """Plain-language description of the curve, used by the public page and stored with each calibration."""
    bottom = float(calibration["anchors"]["bottom"]["mean"])
    top = float(calibration["anchors"]["top"]["mean"])
    runs = calibration.get("runs_per_anchor", "several")
    return (f"Each reference project was graded {runs} times with the same pipeline every submission goes through, and the "
            f"averages are used: {bottom:g} for the bottom reference and {top:g} for the top. LetsVibe grades on a bell curve: "
            "your letter depends on how far your raw score sits above or below the average, measured in standard deviations. "
            f"Until your category has projects of its own, the bell is set by the references: the average is their midpoint "
            f"({(top + bottom) / 2:g}) and one standard deviation is a quarter of the gap between them ({(top - bottom) / 4:g}), "
            "so the top reference sits two standard deviations above average and the bottom one two below. Each project "
            "added to your category shifts the bell toward that category's own average and spread, fully at "
            f"{PEER_MIN_PROJECTS} projects. Two rules tie letters to the references: an A also needs a score at least "
            f"{A_REQUIRES_POSITION:.0%} of the way from the bottom reference to the top one, and a score less than "
            f"{F_BELOW_POSITION:.0%} of the way is an F.")


WHY = ("AI reviewers are good at comparing projects but poor at absolute scores: they hedge toward the middle. In "
       "LetsVibe's own testing, a deliberately terrible app and a carefully built one, graded by the same pipeline, "
       "averaged about 28 and 68 on a 0-100 scale, and repeated runs of the same project varied by several points. "
       "A raw score therefore says little on its own, so LetsVibe grades on a bell curve: what counts is how your "
       "project compares, first with two published reference projects and then with the other projects in its category.")


def basis_for(category_size: int) -> str:
    if load_calibration() is None:
        return "peers" if category_size >= PEER_MIN_PROJECTS else "rubric"
    return "peers" if category_size >= PEER_MIN_PROJECTS else "curve"


def latest_reports_by_category(conn: sqlite3.Connection) -> Dict[str, List[sqlite3.Row]]:
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT r.*, ROW_NUMBER() OVER (PARTITION BY r.project_id ORDER BY r.id DESC) AS rn
            FROM reports r
        )
        SELECT id, project_id, final_score, letter_grade, coalesce(general_category, 'other') AS cat, report_data_json
        FROM latest WHERE rn = 1
        """
    ).fetchall()
    groups: Dict[str, List[sqlite3.Row]] = {}
    for r in rows:
        groups.setdefault(r["cat"], []).append(r)
    return groups


def apply_peer_grades(conn: sqlite3.Connection) -> int:
    """Re-letters every project's latest report from its category standing. Returns rows changed."""
    changed = 0
    groups = latest_reports_by_category(conn)
    all_scores = [float(r["final_score"]) for rows in groups.values() for r in rows]
    for cat, rows in groups.items():
        cohort = [float(r["final_score"]) for r in rows]
        ordered = sorted(rows, key=lambda r: -float(r["final_score"]))
        for rank, r in enumerate(ordered, start=1):
            letter, basis, details = grade_with_details(float(r["final_score"]), cohort, all_scores)
            blob = r["report_data_json"]
            data = None
            if blob:
                try:
                    data = json.loads(blob)
                except ValueError:
                    data = None
            needs = letter != r["letter_grade"] or (
                data is not None and (data.get("grade_basis") != basis or data.get("letter_grade") != letter
                                      or data.get("curve") != details)
            )
            if not needs:
                continue
            if data is not None:
                data["letter_grade"] = letter
                data["grade_basis"] = basis
                data["curve"] = details
                blob = json.dumps(data)
            conn.execute("UPDATE reports SET letter_grade = ?, report_data_json = ? WHERE id = ?", (letter, blob, r["id"]))
            changed += 1
    return changed


def category_standing(conn: sqlite3.Connection, project_id: int) -> Optional[Dict[str, object]]:
    """{rank, count, category, basis} for a project's latest report within its category."""
    for cat, rows in latest_reports_by_category(conn).items():
        ordered = sorted(rows, key=lambda r: -float(r["final_score"]))
        for i, r in enumerate(ordered, start=1):
            if r["project_id"] == project_id:
                return {
                    "category": cat,
                    "rank": i,
                    "count": len(ordered),
                    "basis": basis_for(len(ordered)),
                }
    return None
