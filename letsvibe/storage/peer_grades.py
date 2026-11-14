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
DEFAULT_CURVE_LOW = 20.0
DEFAULT_CURVE_HIGH = 90.0
OUTLIER_THRESHOLD = 5
GLOBAL_PRIOR_MEAN = 75.0
GLOBAL_PRIOR_SD = 8.5
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


def global_scale_ends(all_scores: List[float]) -> Tuple[float, float]:
    """(0% raw score, 100% raw score): [20, 90] unless >= 5 projects beat 90 or fall below 20."""
    low, high = DEFAULT_CURVE_LOW, DEFAULT_CURVE_HIGH
    if not all_scores:
        return low, high
    below = sorted(s for s in all_scores if s < DEFAULT_CURVE_LOW)
    if len(below) >= OUTLIER_THRESHOLD:
        low = float(below[OUTLIER_THRESHOLD - 1])
    above = sorted((s for s in all_scores if s > DEFAULT_CURVE_HIGH), reverse=True)
    if len(above) >= OUTLIER_THRESHOLD:
        high = float(above[OUTLIER_THRESHOLD - 1])
    return low, max(low + 1.0, high)


def _curved_scale_raw(z: float) -> float:
    pts = CURVED_SCALE
    if z <= pts[0][0]:
        return pts[0][1]
    if z >= pts[-1][0]:
        return pts[-1][1]
    return next(y0 + (y1 - y0) * (z - x0) / (x1 - x0) for (x0, y0), (x1, y1) in zip(pts, pts[1:]) if x0 <= z <= x1)


SCHOOL_GRADE_CUTOFFS: Tuple[Tuple[float, str], ...] = (
    (97.0, "A+"), (93.0, "A"), (90.0, "A-"),
    (87.0, "B+"), (83.0, "B"), (80.0, "B-"),
    (77.0, "C+"), (73.0, "C"), (70.0, "C-"),
    (67.0, "D+"), (63.0, "D"), (60.0, "D-"),
)


def curved_letter_for_score(score: float) -> str:
    """Maps a curved 0-100 score onto standard school letter-grade bands (75 = C, 90+ = A)."""
    for cutoff, grade in SCHOOL_GRADE_CUTOFFS:
        if score >= cutoff:
            return grade
    return "F"


def global_curve_details(score: float, all_scores: List[float]) -> Dict[str, float]:
    """Single site-wide bell curve inside [low, high], strictly monotonic and anchored at 0/75/100."""
    import statistics

    low, high = global_scale_ends(all_scores)
    w = peer_weight(len(all_scores)) if len(all_scores) >= 2 else 0.0
    mean, sd = GLOBAL_PRIOR_MEAN, GLOBAL_PRIOR_SD
    if w:
        mean = (1 - w) * mean + w * statistics.mean(all_scores)
        sd = (1 - w) * sd + w * max(statistics.stdev(all_scores), 1.0)
    # Keep mean safely inside (low, high) so upper and lower halves never invert
    mean = min(high - 1.0, max(low + 1.0, mean))
    z = (score - mean) / sd
    z_low = (low - mean) / sd
    z_high = (high - mean) / sd
    a_pos = anchor_position(score, (low, high))

    if score <= low:
        curved = 0.0
    elif score >= high:
        curved = 100.0
    else:
        raw_y = _curved_scale_raw(z)
        y_mid = _curved_scale_raw(0.0)
        if z >= 0.0:
            y_high = _curved_scale_raw(z_high)
            denom = max(1e-6, y_high - y_mid)
            curved = 75.0 + 25.0 * ((raw_y - y_mid) / denom)
        else:
            y_low = _curved_scale_raw(z_low)
            denom = max(1e-6, y_mid - y_low)
            curved = 75.0 * ((raw_y - y_low) / denom)
        curved = max(0.0, min(100.0, curved))

    return {
        "z": round(z, 3),
        "average": round(mean, 2),
        "sd": round(sd, 2),
        "peer_weight": round(w, 4),
        "anchor_position": round(a_pos, 4),
        "low_bound": round(low, 1),
        "high_bound": round(high, 1),
        "curved_score": round(curved, 1),
        "raw_score": round(score, 1),
    }


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


# The number shown with the letter comes from the same bell curve, on the familiar school scale, so an A always
# reads 90-something: (z, shown number) points, linearly interpolated, clamped to 0-100.
CURVED_SCALE: Tuple[Tuple[float, float], ...] = (
    (-4.0, 0.0), (-1.5, 60.0), (-0.5, 70.0), (-0.17, 73.0), (0.17, 77.0), (0.5, 80.0), (0.83, 83.0),
    (1.17, 87.0), (1.5, 90.0), (1.75, 93.0), (2.0, 97.0), (3.0, 100.0),
)


def curved_number(z: float, anchor_pos: float) -> float:
    """Shown score for a bell-curve position; follows the same rails as the letter (F below the floor, no A without the anchor)."""
    pts = CURVED_SCALE
    if z <= pts[0][0]:
        value = pts[0][1]
    elif z >= pts[-1][0]:
        value = pts[-1][1]
    else:
        value = next(y0 + (y1 - y0) * (z - x0) / (x1 - x0) for (x0, y0), (x1, y1) in zip(pts, pts[1:]) if x0 <= z <= x1)
    if anchor_pos < F_BELOW_POSITION:
        value = min(value, 59.0)          # an F always reads below 60
    elif anchor_pos < A_REQUIRES_POSITION:
        value = min(value, 89.0)          # without the anchor rail, never an A-range number
    return round(max(0.0, min(100.0, value)), 1)


def bell_grade(z: float, anchor_pos: float) -> str:
    if anchor_pos < F_BELOW_POSITION:
        return "F"
    for cut, grade in BELL_GRADES:
        if z >= cut:
            return "B+" if grade.startswith("A") and anchor_pos < A_REQUIRES_POSITION else grade
    return "F"


def curve_details(score: float, cohort: List[float], calibration: Dict[str, Any], all_scores: List[float]) -> Dict[str, float]:
    mean, sd, w = bell_params(calibration, cohort)
    z = (score - mean) / sd
    a_pos = anchor_position(score, scale_ends(calibration, all_scores))
    return {"z": round(z, 3), "average": round(mean, 2), "sd": round(sd, 2), "peer_weight": round(w, 4),
            "anchor_position": round(a_pos, 4), "curved_score": curved_number(z, a_pos), "raw_score": round(score, 1)}


def grade_for(score: float, cohort: List[float], all_scores: Optional[List[float]] = None) -> Tuple[str, str]:
    """(letter, basis) for one project given all scores in its category (including its own)."""
    letter, basis, _ = grade_with_details(score, cohort, all_scores if all_scores is not None else cohort)
    return letter, basis


def grade_with_details(score: float, cohort: List[float], all_scores: List[float]) -> Tuple[str, str, Optional[Dict[str, float]]]:
    """(letter, basis, curve details). basis: "curve" (bell set by the anchors, category growing in), "peers", or "rubric"."""
    from ..evaluator.rubric import calculate_letter_grade

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
    return (
        f"LetsVibe grades on a single site-wide bell curve computed dynamically from raw rubric scores "
        f"(never stored in the database). By default, a raw score of {DEFAULT_CURVE_LOW:g} anchors 0 and "
        f"{DEFAULT_CURVE_HIGH:g} anchors 100, with the curve centered at {GLOBAL_PRIOR_MEAN:g} (C) and blending "
        f"toward the real site-wide average and spread as more projects are graded (fully at {PEER_MIN_PROJECTS} "
        f"projects). If {OUTLIER_THRESHOLD} or more projects score above {DEFAULT_CURVE_HIGH:g} (or below "
        f"{DEFAULT_CURVE_LOW:g}), the top or bottom anchor automatically shifts to the {OUTLIER_THRESHOLD}th-highest "
        f"or {OUTLIER_THRESHOLD}th-lowest score so the scale adapts as coding tools improve."
    )


WHY = ("AI reviewers are good at comparing projects but poor at absolute scores: they hedge toward the middle. In "
       "LetsVibe's own testing, a deliberately terrible app and a carefully built one, graded by the same pipeline, "
       "averaged about 28 and 68 on a 0-100 scale, and repeated runs of the same project varied by several points. "
       "A raw score therefore says little on its own, so LetsVibe grades on a bell curve: what counts is how your "
       "project compares, first with two published reference projects and then with the other projects in its category.")


def basis_for(category_size: int) -> str:
    if load_calibration() is None:
        return "peers" if category_size >= PEER_MIN_PROJECTS else "rubric"
    return "peers" if category_size >= PEER_MIN_PROJECTS else "curve"


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


def compute_peer_overlays(conn: sqlite3.Connection) -> Dict[int, Dict[str, Any]]:
    """
    Computes presentation-layer bell-curve / peer-grade overlays in memory for every report
    WITHOUT mutating the database.
    Public curve statistics are anchored strictly on the latest production report of public projects;
    historical and testing engine scans are evaluated against that same public cohort without shifting it.
    """
    from ..evaluator.rubric import calculate_letter_grade

    overlays: Dict[int, Dict[str, Any]] = {}
    groups = latest_reports_by_category(conn)
    all_scores = [float(r["final_score"]) for rows in groups.values() for r in rows]
    public_ids = {int(r["id"]) for rows in groups.values() for r in rows}

    jobs = []
    for cat, rows in groups.items():
        cohort = [float(r["final_score"]) for r in rows]
        jobs += [(r, cohort, all_scores) for r in rows]

    # All other reports (historical scans, testing engine runs, or admin test projects) are evaluated
    # against the public cohort without altering public curve statistics.
    extra_rows = conn.execute(
        "SELECT id, project_id, final_score, letter_grade, curved_score, coalesce(general_category, 'other') AS cat FROM reports"
    ).fetchall()
    for r in extra_rows:
        if int(r["id"]) in public_ids:
            continue
        cat = r["cat"]
        public = [float(pr["final_score"]) for pr in groups.get(cat, [])]
        sc = float(r["final_score"])
        jobs.append((r, public + [sc], all_scores + [sc]))

    use_global = load_calibration() is not None
    for r, cohort, scores in jobs:
        raw_score = float(r["final_score"])
        raw_letter = calculate_letter_grade(raw_score)
        if use_global:
            details = global_curve_details(raw_score, scores)
            curved_val = details["curved_score"]
            curved_letter = curved_letter_for_score(curved_val)
            basis = "peers" if details["peer_weight"] >= 1 else "curve"
        else:
            curved_letter, basis, details = grade_with_details(raw_score, cohort, scores)
            curved_val = details["curved_score"] if details else round(raw_score, 1)
        overlays[int(r["id"])] = {
            "raw_score": round(raw_score, 1),
            "raw_letter_grade": raw_letter,
            "curved_score": curved_val,
            "curved_letter_grade": curved_letter,
            "grade_basis": basis,
            "curve": details,
        }
    return overlays


def apply_peer_grades(conn: sqlite3.Connection) -> int:
    """
    Deprecated no-op: peer/bell-curve grades are now computed dynamically on read via `compute_peer_overlays(conn)`
    so `reports.letter_grade` in SQLite always preserves the uncurved raw rubric grade.
    """
    return 0


def category_standing(conn: sqlite3.Connection, project_id: int) -> Optional[Dict[str, object]]:
    """{rank, count, category, basis} for a project's latest report within its category (public projects, plus the
    project itself when it is an admin test project)."""
    public = latest_reports_by_category(conn)
    for cat, rows in latest_reports_by_category(conn, test=True).items():
        public[cat] = public.get(cat, []) + [r for r in rows if r["project_id"] == project_id]
    for cat, rows in public.items():
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
