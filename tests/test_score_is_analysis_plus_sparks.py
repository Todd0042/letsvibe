"""The one scoring rule (Todd, 2026-11-21): a project's score is its analysis (the rubric score) plus its Spark upvote
bonus, and nothing else. The letter comes from that number on the fixed bands; there is no curve, so other projects'
scores never change yours. Checked end to end through the API, on a site with several categories and Sparks that
reorder a category, and on the Anna side (its own database)."""

from fastapi.testclient import TestClient

from letsvibe.evaluator.consensus import calculate_letter_grade
from letsvibe.storage.db import save_report_to_db, upvote_score_bonus
from letsvibe.storage.identity import parse_repo_identity
from letsvibe.web.server import app

# (slug, analysis score, category)
PROJECTS = [
    ("ann/tracker", 89.6, "web_app"), ("ben/notes", 89.2, "web_app"), ("cat/blog", 71.0, "web_app"),
    ("dan/shooter", 64.0, "game"), ("eve/puzzle", 58.3, "game"), ("fay/idle", 99.9, "game"),
    ("gus/cli", 40.0, "tool"),
]


def _grade(slug, score, cat):
    rep = {"final_vibe_score": score, "letter_grade": "?", "archetype": {"name": slug, "general_category": cat},
           "factor_evaluations": {}}
    save_report_to_db(rep, parse_repo_identity(f"https://github.com/{slug}"))


def _board(client, **params):
    return {i["full_slug"]: i for i in client.get("/api/leaderboard", params=params).json()["items"]}


def test_score_is_the_analysis_plus_sparks_everywhere():
    from conftest import sign_in

    for p in PROJECTS:
        _grade(*p)
    anon = TestClient(app)
    before = _board(anon)
    for slug, score, _ in PROJECTS:  # no Sparks yet: the score IS the analysis, the letter its band
        item = before[slug]
        assert item["final_score"] == score and item["curved_score"] == score
        assert item["letter_grade"] == calculate_letter_grade(score) == item["curved_letter_grade"]
    # The worst project in a category isn't pushed down, the best isn't pushed up: 99.9 stays 99.9, 40.0 is just an F
    assert before["fay/idle"]["letter_grade"] == "A+" and before["gus/cli"]["letter_grade"] == "F"
    assert before["eve/puzzle"]["letter_grade"] == "F"  # 58.3 alone in "last place" of nothing: still its band
    assert before["ann/tracker"]["category_rank"] == 1 and before["ben/notes"]["category_rank"] == 2

    # Three people Spark ben/notes: +1.0 (0.5*log2(1+3)) moves 89.2 to 90.2, past ann (89.6) and into the A band
    for n in range(3):
        voter = TestClient(app)
        sign_in(voter, f"voter{n}", str(700 + n))
        assert voter.post("/api/upvote", json={"full_slug": "ben/notes"}).status_code == 200
    after = _board(anon)
    ben = after["ben/notes"]
    assert ben["upvote_count"] == 3 and ben["upvote_bonus"] == upvote_score_bonus(3) == 1.0
    assert ben["rubric_score"] == 89.2 and ben["final_score"] == 90.2 and ben["letter_grade"] == "A-"
    assert ben["category_rank"] == 1 and after["ann/tracker"]["category_rank"] == 2
    # ...and nobody else's score moved (no curve: other projects never change yours)
    for slug, score, _ in PROJECTS:
        if slug != "ben/notes":
            assert after[slug]["final_score"] == score and after[slug]["letter_grade"] == before[slug]["letter_grade"]

    # The project page and the badge show the same number and letter, and the same rank as the leaderboard
    page = anon.get("/api/project/ben/notes").json()["project"]
    assert page["final_score"] == page["curved_score"] == 90.2 and page["letter_grade"] == "A-"
    assert page["standing"] == {"category": "web_app", "rank": 1, "count": 3}
    assert anon.get("/api/project/ann/tracker").json()["project"]["standing"]["rank"] == 2
    badge = anon.get("/badge/ben/notes.svg").text
    assert "A-" in badge and "90.2" in badge

    # Old links that still ask for a curve get the same answer (the switch no longer exists)
    assert _board(anon, curve="true") == _board(anon, curve="false") == after
    assert "A-" in anon.get("/badge/ben/notes.svg", params={"scale": "curve"}).text


def test_a_new_low_scorer_changes_no_one_elses_grade():
    for p in PROJECTS[:3]:
        _grade(*p)
    anon = TestClient(app)
    before = {k: (v["final_score"], v["letter_grade"]) for k, v in _board(anon).items()}
    for i in range(6):  # a wave of weak submissions in the same category (a curve would have lifted the others)
        _grade(f"weak{i}/app", 20.0 + i, "web_app")
    after = _board(anon)
    assert {k: (after[k]["final_score"], after[k]["letter_grade"]) for k in before} == before
    assert after["cat/blog"]["category_rank"] == 3 and after["cat/blog"]["category_count"] == 9


def test_anna_scores_are_the_analysis_plus_sparks(tmp_path):
    from letsvibe.storage import anna_db

    db = tmp_path / "anna.db"
    anna_db.init_anna_db(db)

    def submit(slug, score):
        rep = {"repo_source": f"https://github.com/{slug}", "final_vibe_score": score, "rubric_version": "v11a",
               "archetype": {"name": slug, "general_category": "web_app"}, "factor_evaluations": {}}
        return anna_db.save_anna_report(rep, submitted_by=slug.split("/")[0], db_path=db)

    first = submit("ann/lounge", 81.0)
    assert first["curved_score"] == first["final_score"] == 81.0 and first["curved_letter_grade"] == "B-"
    for i in range(5):  # weak projects arrive: a curve used to move ann's number on every submission
        later = submit(f"w{i}/app", 30.0 + i)
        assert later["curved_score"] == 30.0 + i and later["curved_letter_grade"] == "F"
    for n in range(3):
        anna_db.toggle_anna_upvote("ann", "lounge", f"fan{n}", db_path=db)
    detail = anna_db.get_anna_project_detail("ann", "lounge", db_path=db)
    assert detail["rubric_score"] == 81.0 and detail["final_score"] == 82.0 and detail["letter_grade"] == "B-"
    assert [h["curved_score"] for h in detail["history"]] == [81.0]  # stored history: the analysis, never curved
    board = {i["full_slug"]: i for i in anna_db.get_anna_leaderboard(db_path=db)["projects"]}
    assert board["ann/lounge"]["final_score"] == 82.0 and board["w0/app"]["final_score"] == 30.0
