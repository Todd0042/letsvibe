"""Rubric v8 (experience grading): fixed-point scoring, quote checks, the code scan, and owner-only maintainer notes."""
import json
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from auth_helpers import sign_in
from letsvibe.analyzers.code_scan import FUNCTIONALITY_KINDS, scan
from letsvibe.evaluator.experience import rubric, scoring
from letsvibe.evaluator.experience.describe import problems
from letsvibe.web.server import app


def test_weights_follow_todds_order_and_add_to_100():
    w = scoring.WEIGHTS
    assert list(w) == ["value", "creativity", "journey", "functionality", "ui_ux"]
    assert w["value"] > w["creativity"] > w["journey"] > w["functionality"] > w["ui_ux"]
    assert round(sum(w.values()), 1) == 100.0


def test_points_are_fixed_and_absence_is_never_a_penalty():
    best = {"tells_user_about_problems": {"answer": "CLEARLY"}, "confirms_success": {"answer": "CLEARLY"},
            "core_flow": {"answer": "WORKS_END_TO_END"}, "features_connected": {"answer": "CONNECTED"}}
    unknown = dict(best, tells_user_about_problems={"answer": "NOT_SHOWN_IN_DOSSIER"}, confirms_success={"answer": "NOT_SHOWN_IN_DOSSIER"})
    assert scoring.category("functionality", best, [])["score"] == 100.0
    # "not shown in the dossier" earns middle points and is never listed as needing work
    assert 70 < scoring.category("functionality", unknown, [])["score"] < 100
    assert problems("functionality", unknown) == []


def test_proven_problems_count_per_thousand_lines():
    answers = {"core_flow": {"answer": "WORKS_END_TO_END"}, "features_connected": {"answer": "CONNECTED"},
               "tells_user_about_problems": {"answer": "CLEARLY"}, "confirms_success": {"answer": "CLEARLY"}}
    small = scoring.category("functionality", answers, [], proven_problems=3, lines_of_code=400)["score"]
    big = scoring.category("functionality", answers, [], proven_problems=3, lines_of_code=30000)["score"]
    assert small < big < 100.0          # the same 3 problems weigh far less in a 30,000-line project


def test_hostile_patterns_cost_journey_points():
    answers = {"launch_experience": {"answer": "RUNS_IMMEDIATELY"}, "navigation_friction": {"answer": "STRAIGHT_TO_CORE"},
               "setup_burden": {"answer": "NONE"}, "state_and_save": {"answer": "AUTO_SAVE"}}
    assert scoring.category("journey", answers, [])["score"] == 100.0
    assert scoring.category("journey", answers, ["ads"])["score"] == 60.0   # -50 of 125 points


def test_quote_check_finds_real_quotes_only():
    dossier = "## 6. On-screen text\n- Get ready! Head up to the microphone!\n- Scan to join the stage"
    out = rubric.check({"has_distinct_voice": {"answer": True, "evidence": ["Get ready! Head up to the microphone"]},
                        "subverts_standard_ui": {"answer": True, "evidence": ["make me a space shooter"]}}, dossier)
    assert out["has_distinct_voice"]["verified"] is True
    assert out["subverts_standard_ui"]["verified"] is False


@pytest.fixture
def tiny_repo(tmp_path):
    repo = tmp_path / "tiny"
    repo.mkdir()
    (repo / "app.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/api/used')\ndef used():\n    return {}\n\n"
        "@app.get('/api/forgotten')\ndef forgotten():\n    return {}\n")
    (repo / "index.html").write_text("<html><body><script>fetch('/api/used')</script>" + "<p>padding</p>" * 40 + "</body></html>")
    (repo / "settings.example.toml").write_text("editor = null\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    return repo


def test_code_scan_finds_unwired_routes_and_broken_configs(tiny_repo):
    found = scan(tiny_repo)
    kinds = {f["kind"] for f in found["functionality"]}
    assert "unused route" in kinds and "config does not parse" in kinds
    assert any("/api/forgotten" in f["detail"] for f in found["functionality"])
    assert not any("/api/used" in f["detail"] for f in found["functionality"])
    assert all(f["kind"] in FUNCTIONALITY_KINDS for f in found["functionality"])


def test_maintainer_notes_only_for_owner_or_admin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = Path("data/reports/notes-owner/proj")
    folder.mkdir(parents=True)
    (folder / "maintainer_notes.json").write_text(json.dumps({"lines_of_code": 10, "notes": [{"kind": "duplicated code", "file": "a.py", "line": 1, "detail": "x"}]}))
    client = TestClient(app)
    url = "/api/projects/notes-owner/proj/maintainer-notes"
    assert client.get(url).status_code == 401
    sign_in(client, "someone-else", "930002")
    assert client.get(url).status_code == 403
    client.cookies.clear()
    sign_in(client, "notes-owner", "930001")
    r = client.get(url)
    assert r.status_code == 200 and r.json()["notes"][0]["kind"] == "duplicated code"
