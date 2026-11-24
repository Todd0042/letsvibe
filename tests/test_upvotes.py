"""Tests for Spark project upvotes and automatic cleanup on account deletion."""

from fastapi.testclient import TestClient

from letsvibe.storage.db import delete_user_account, get_db_connection
from letsvibe.web.server import app


def _seed_project(full_slug: str = "alice/spark-app"):
    owner, repo = full_slug.split("/", 1)
    conn = get_db_connection()
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO projects (owner, repo_name, full_slug, canonical_url, is_test, moderation_hidden)
                VALUES (?, ?, ?, ?, 0, 0)
                """,
                (owner, repo, full_slug, f"https://github.com/{full_slug}"),
            )
            pid = cur.lastrowid
            conn.execute(
                """
                INSERT INTO reports (
                    project_id, owner, repo_name, full_slug, final_score, letter_grade, archetype_name,
                    verdict_title, verdict_summary, general_category, platforms, total_loc, total_files,
                    rubric_version, rubric_rank, is_test, report_data_json
                )
                VALUES (?, ?, ?, ?, 85.0, 'B+', 'Web App', 'Great App', 'Clean.', 'web_app', ',web,', 400, 8, 'v9.4', 904, 0, '{}')
                """,
                (pid, owner, repo, full_slug),
            )
    finally:
        conn.close()


def test_spark_upvote_toggle_and_account_deletion_cleanup():
    from auth_helpers import sign_in

    _seed_project("alice/spark-app")
    _seed_project("alice/second-app")
    anon = TestClient(app)
    owner = TestClient(app)
    sign_in(owner, "alice", "501", is_admin=False)
    voter = TestClient(app)
    sign_in(voter, "bob", "502", is_admin=False)

    # Signed out -> 401
    assert anon.post("/api/upvote", json={"full_slug": "alice/spark-app"}).status_code == 401

    # Owner can upvote ONE of their own projects, but not a second
    assert owner.post("/api/upvote", json={"full_slug": "alice/spark-app"}).status_code == 200
    assert owner.post("/api/upvote", json={"full_slug": "alice/second-app"}).status_code == 403
    # Removing the first lets owner upvote the second instead
    assert owner.post("/api/upvote", json={"full_slug": "alice/spark-app"}).json()["upvoted"] is False
    assert owner.post("/api/upvote", json={"full_slug": "alice/second-app"}).status_code == 200

    # Voter toggles on -> count 1, upvoted True, +0.50 Spark bonus
    base_score = anon.get("/api/project/alice/spark-app").json()["project"]["curved_score"]
    r1 = voter.post("/api/upvote", json={"full_slug": "alice/spark-app"})
    assert r1.status_code == 200
    assert r1.json()["upvoted"] is True and r1.json()["upvote_count"] == 1 and r1.json()["upvote_bonus"] == 0.5

    # Leaderboard & individual project page reflect upvote_count, +0.5 score bonus, and per-viewer user_upvoted
    lb_voter = [it for it in voter.get("/api/leaderboard").json()["items"] if it["full_slug"] == "alice/spark-app"][0]
    lb_anon = [it for it in anon.get("/api/leaderboard").json()["items"] if it["full_slug"] == "alice/spark-app"][0]
    assert lb_voter["upvote_count"] == 1 and lb_voter["user_upvoted"] is True
    assert lb_anon["upvote_count"] == 1 and lb_anon["user_upvoted"] is False
    assert lb_voter["curved_score"] == round(min(100.0, base_score + 0.5), 1)
    proj_voter = voter.get("/api/project/alice/spark-app").json()["project"]
    assert proj_voter["upvote_count"] == 1 and proj_voter["user_upvoted"] is True and proj_voter["upvote_bonus"] == 0.5
    assert proj_voter["curved_score"] == round(min(100.0, base_score + 0.5), 1)

    # Deleting voter's account deletes their upvotes & removes the bonus
    assert delete_user_account("502", "bob") is True
    lb_after_delete = [it for it in anon.get("/api/leaderboard").json()["items"] if it["full_slug"] == "alice/spark-app"][0]
    assert lb_after_delete["upvote_count"] == 0
    assert lb_after_delete["curved_score"] == base_score

    # Re-seed voter, upvote then toggle off
    sign_in(voter, "bob", "502", is_admin=False)
    assert voter.post("/api/upvote", json={"full_slug": "alice/spark-app"}).json()["upvote_count"] == 1
    r_off = voter.post("/api/upvote", json={"full_slug": "alice/spark-app"})
    assert r_off.status_code == 200
    assert r_off.json()["upvoted"] is False and r_off.json()["upvote_count"] == 0
