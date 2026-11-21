"""Metrics an engine didn't grade are stored empty ("not graded"), never as a 0 that reads like a real score.

Scenario: a current-engine report (v10.3: no maintainability/architecture), an old v5 report (graded them, one of them
a real 0), and a current-engine report that did grade maintainability. Then the 0s an older build stored by mistake
are repaired at startup without touching real scores, the owner's export explains the empty values, and sorting by a
retired metric lists the ungraded reports last.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import sign_in
from letsvibe.storage.db import get_db_connection, get_leaderboard, get_project_detail, init_db, save_report_to_db
from letsvibe.storage.identity import parse_repo_identity
from letsvibe.web.server import app

OWNER, OWNER_ID = "metrics-owner-m1", "930001"


def _report(version: str, factors: dict, weights: dict, score: float = 80.0) -> dict:
    return {"final_vibe_score": score, "rubric_version": version, "archetype": {"name": "app"},
            "factor_evaluations": factors, "weights": weights}


CURRENT = _report("v10.3", {
    "functionality": {"score": 84.0, "weight": 20.0},
    "user_journey": {"score": 92.0, "weight": 15.0},
    "code_health": {"score": 60.5, "weight": 15.0},
}, {"functionality": 20.0, "user_journey": 15.0, "code_health": 15.0, "maintainability": 0.0, "architecture": 0.0})

LEGACY = _report("v5", {
    "functionality": {"score": 70.0, "weight": 20.0},
    "maintainability": {"score": 72.0, "weight": 15.0},
    "architecture": {"score": 0.0, "weight": 10.0},  # a real 0: graded, and graded badly
}, {"functionality": 20.0, "maintainability": 15.0, "architecture": 10.0}, score=65.0)

CURRENT_WITH_MAINT = _report("v10.3", {
    "functionality": {"score": 75.0, "weight": 20.0},
    "maintainability": {"score": 55.0, "weight": 10.0},
}, {"functionality": 20.0, "maintainability": 10.0}, score=70.0)


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name, rep in (("current", CURRENT), ("legacy", LEGACY), ("current-maint", CURRENT_WITH_MAINT)):
        report_dir = Path("data/reports") / OWNER / name
        report_dir.mkdir(parents=True)
        (report_dir / "report.json").write_text("{}", encoding="utf-8")
        save_report_to_db(rep, parse_repo_identity(f"https://github.com/{OWNER}/{name}"),
                          raw_json_path=str(report_dir / "report.json"), commit_sha="abc1234")
    return tmp_path


def _detail(name: str) -> dict:
    return get_project_detail(f"{OWNER}/{name}")


def test_saving_stores_empty_not_zero(saved):
    cur = _detail("current")
    assert cur["maintainability_score"] is None and cur["architecture_score"] is None
    assert cur["code_health_score"] == 60.5 and cur["user_journey_score"] == 92.0

    old = _detail("legacy")
    assert old["maintainability_score"] == 72.0
    assert old["architecture_score"] == 0.0  # a real 0 stays a 0
    assert old["code_health_score"] is None and old["user_journey_score"] is None  # didn't exist in v5

    graded = _detail("current-maint")
    assert graded["maintainability_score"] == 55.0  # a later engine that did grade it keeps its score
    assert graded["architecture_score"] is None


def test_startup_repairs_old_false_zeros_only(saved):
    # What older builds stored: 0s for metrics the engine never graded.
    conn = get_db_connection()
    with conn:
        conn.execute("UPDATE reports SET maintainability_score = 0, architecture_score = 0 WHERE full_slug = ?",
                     (f"{OWNER}/current",))
        conn.execute("UPDATE reports SET code_health_score = 0, user_journey_score = 0 WHERE full_slug = ?",
                     (f"{OWNER}/legacy",))
        # A report from before engine versions were recorded: a 0 there can't be told apart, so it stays.
        conn.execute("UPDATE reports SET rubric_version = '', architecture_score = 0, "
                     "report_data_json = json_remove(report_data_json, '$.rubric_version') WHERE full_slug = ?",
                     (f"{OWNER}/current-maint",))
    conn.close()

    init_db()
    init_db()  # safe to run again

    cur, old, unknown = _detail("current"), _detail("legacy"), _detail("current-maint")
    assert cur["maintainability_score"] is None and cur["architecture_score"] is None
    assert old["code_health_score"] is None and old["user_journey_score"] is None
    assert old["maintainability_score"] == 72.0 and old["architecture_score"] == 0.0
    assert unknown["architecture_score"] == 0.0 and unknown["maintainability_score"] == 55.0


def test_export_explains_empty_scores(saved):
    client = TestClient(app)
    sign_in(client, OWNER, OWNER_ID)

    project = client.get(f"/api/projects/{OWNER}/current/export?format=json").json()["project"]
    assert project["maintainability_score"] is None and project["architecture_score"] is None
    assert set(project["not_graded"]) >= {"maintainability_score", "architecture_score"}
    assert "retired" in project["not_graded"]["maintainability_score"]
    assert "code_health_score" not in project["not_graded"]

    old = client.get(f"/api/projects/{OWNER}/legacy/export?format=json").json()["project"]
    assert "maintainability_score" not in old.get("not_graded", {})
    assert "introduced in rubric v6" in old["not_graded"]["code_health_score"]


def test_sorting_by_a_retired_metric_lists_ungraded_reports_last(saved):
    board = get_leaderboard(sort_by="maintainability", use_curve=False)
    rows = board if isinstance(board, list) else board.get("projects") or board.get("items") or []
    order = [r["full_slug"].split("/", 1)[1] for r in rows if r["full_slug"].startswith(OWNER + "/")]
    assert order == ["legacy", "current-maint", "current"]
