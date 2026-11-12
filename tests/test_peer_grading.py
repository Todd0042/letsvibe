"""Peer-relative letter grades: rubric curve below the threshold, category percentiles above it."""

import pytest

from letsvibe.storage import peer_grades as pg
from letsvibe.storage.db import get_db_connection, get_leaderboard, get_project_detail, init_db, save_report_to_db
from letsvibe.storage.identity import parse_repo_identity


def _add(db, slug, score, cat="game"):
    rep = {"final_vibe_score": score, "letter_grade": "?", "archetype": {"name": slug, "general_category": cat},
           "factor_evaluations": {}}
    save_report_to_db(rep, parse_repo_identity(f"https://github.com/{slug}"), db_path=db)


@pytest.fixture(autouse=True)
def _no_curve(request, monkeypatch):
    """These tests cover grading without a calibration file; the curve has its own tests below."""
    if not request.node.name.startswith("test_curve"):
        monkeypatch.setattr(pg, "load_calibration", lambda: None)


CAL = {"anchors": {"bottom": {"mean": 43.0}, "top": {"mean": 80.0}}}


def test_curve_places_scores_between_the_anchors():
    ends = pg.scale_ends(CAL, [])
    assert pg.anchor_position(43.0, ends) == 0.0 and pg.anchor_position(80.0, ends) == 1.0
    assert abs(pg.anchor_position(61.5, ends) - 0.5) < 1e-9
    assert pg.scale_ends(CAL, [50.0, 85.0]) == (43.0, 85.0)          # a project beating the top anchor is the new top


def test_curve_bell_letters_and_rails():
    assert pg.bell_grade(2.0, 1.0) == "A+" and pg.bell_grade(0.0, 0.5) == "C" and pg.bell_grade(-1.0, 0.3) == "D"
    assert pg.bell_grade(1.8, 0.6) == "B+"                            # an A needs to be near the top reference
    assert pg.bell_grade(1.0, 0.05) == "F"                            # as weak as the bottom reference is an F
    assert pg.bell_grade(-1.6, 0.2) == "F"
    mean, sd, w = pg.bell_params(CAL, [])
    assert (mean, sd, w) == (61.5, 9.25, 0.0)                         # references sit at +/-2 standard deviations
    shares = [pg.bell_share(-float("inf"), -1.5), pg.bell_share(-0.5, 0.5), pg.bell_share(1.5, float("inf"))]
    assert [round(x, 3) for x in shares] == [0.067, 0.383, 0.067]


def test_curve_hands_over_to_peers_gradually(monkeypatch):
    monkeypatch.setattr(pg, "load_calibration", lambda: CAL)
    few = pg.curve_details(70.0, [70.0, 50.0], CAL, [70.0, 50.0])
    assert few["peer_weight"] == round(2 / pg.PEER_MIN_PROJECTS, 4) and few["z"] > 0
    letter, basis, _ = pg.grade_with_details(70.0, [70.0] * 30, [70.0] * 30)
    assert basis == "peers"
    assert pg.grade_with_details(61.5, [61.5], [61.5])[1] == "curve"


def test_curve_grades_reach_the_leaderboard(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "load_calibration", lambda: CAL)
    db = tmp_path / "t.db"
    init_db(db)
    _add(db, "a/top", 80.0)
    _add(db, "b/low", 44.0)
    board = {i["full_slug"]: i for i in get_leaderboard(db_path=db)}
    assert board["a/top"]["grade_basis"] == "curve" and board["a/top"]["letter_grade"].startswith("A")
    assert board["b/low"]["letter_grade"] == "F"


def test_percentile_bands_and_guardrails():
    assert pg.percentile_grade(0.99, 90) == "A+"
    assert pg.percentile_grade(0.91, 80) == "A-"
    assert pg.percentile_grade(0.95, 70) == "B+"   # top of a weak group is not an A
    assert pg.percentile_grade(0.65, 60) == "B-"
    assert pg.percentile_grade(0.40, 60) == "C"
    assert pg.percentile_grade(0.05, 60) == "D"
    assert pg.percentile_grade(0.99, 35) == "F"    # failing scores always fail
    assert pg.percentile_of(10, [10, 20, 30]) == 0.0 and pg.percentile_of(30, [10, 20, 30]) == 1.0


def test_small_categories_use_the_rubric_curve(tmp_path):
    db = tmp_path / "p.db"
    init_db(db)
    for i, s in enumerate((62.0, 70.0, 86.0)):
        _add(db, f"u{i}/r", s)
    grades = {i["full_slug"]: (i["letter_grade"], i["category_rank"], i["grade_basis"]) for i in get_leaderboard(db_path=db)}
    assert grades["u0/r"] == ("C", 3, "rubric")      # 62 = C on the v6 curve
    assert grades["u2/r"] == ("A-", 1, "rubric")


def test_large_categories_grade_against_peers(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "PEER_MIN_PROJECTS", 10)
    db = tmp_path / "p.db"
    init_db(db)
    for i in range(20):
        _add(db, f"g{i:02d}/r", 50.0 + i * 1.5)      # games: 50.0 .. 78.5
    _add(db, "tool0/r", 62.0, cat="tool")            # a different, small category
    board = {i["full_slug"]: i for i in get_leaderboard(db_path=db)}
    top, bottom = board["g19/r"], board["g00/r"]
    assert top["grade_basis"] == "peers" and top["category_rank"] == 1 and top["category_count"] == 20
    assert top["letter_grade"].startswith("A")       # 78.5, best of 20, above the A guardrail
    assert bottom["letter_grade"] == "D"
    letters = [i["letter_grade"] for k, i in board.items() if k.startswith("g")]
    assert sum(1 for g in letters if g.startswith("A")) <= 2   # ~10% of 20 at most
    assert board["tool0/r"]["grade_basis"] == "rubric" and board["tool0/r"]["letter_grade"] == "C"
    detail = get_project_detail("g19/r", db_path=db)
    assert detail["standing"] == {"category": "game", "rank": 1, "count": 20, "basis": "peers"}
    assert detail["full_report"]["grade_basis"] == "peers"
