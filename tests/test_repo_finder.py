"""TEST MODE (remove before launch): the admin's 'find & queue 5 random vibe-coded repos' button."""

import random

import pytest
from fastapi.testclient import TestClient

from letsvibe.storage.db import enqueue_task, get_db_connection
from letsvibe.web import repo_finder
from letsvibe.web.repo_finder import MAX_QUERIES, SearchLimitReached, find_random_repos
from letsvibe.web.server import app


def repo(name, size=500, stars=3, topics=("vibe-coded",), **extra):
    return {"full_name": name, "html_url": f"https://github.com/{name}", "size": size, "stargazers_count": stars,
            "topics": list(topics), "fork": False, "archived": False, "owner": {"login": name.split("/")[0]},
            "language": "Python", "description": "d", **extra}


class FakeGitHub:
    """Answers searches from a fixed pool, honouring the topic and size parts of the query."""

    def __init__(self, pool):
        self.pool, self.queries = pool, []

    def __call__(self, q, params):
        self.queries.append(q)
        want = [p.split(":", 1)[1] for p in q.split() if p.startswith("topic:")]
        lo, hi = (int(x) for x in next(p for p in q.split() if p.startswith("size:"))[5:].split(".."))
        return [r for r in self.pool if all(t in r["topics"] for t in want[1:]) and lo <= r["size"] <= hi]


def test_four_small_and_one_larger_from_different_types():
    pool = [repo(f"u{i}/small-{t}", 300, topics=("vibe-coded", "vibecoded", t))
            for i, t in enumerate(repo_finder.TYPE_TOPICS)]
    pool += [repo(f"big{i}/larger-{t}", 3000, topics=("vibe-coded", "vibecoded", t))
             for i, t in enumerate(repo_finder.TYPE_TOPICS)]
    gh = FakeGitHub(pool)
    out = find_random_repos(set(), rng=random.Random(7), fetch=gh)
    sizes = [r["size_class"] for r in out["repos"]]
    assert sizes.count("small") == 4 and sizes.count("larger") == 1
    types = [r["type_topic"] for r in out["repos"]]
    assert None not in types and len(set(types)) == 5  # five different project types
    assert len({r["full_slug"] for r in out["repos"]}) == 5
    assert all("stars:0..100" in q and "fork:false" in q for q in gh.queries)
    assert out["queries"] <= MAX_QUERIES


def test_falls_back_to_any_vibe_coded_repo_and_never_overspends():
    gh = FakeGitHub([repo(f"x{i}/app{i}", 200) for i in range(3)])  # no type topics at all, and no large repos
    out = find_random_repos(set(), rng=random.Random(1), fetch=gh)
    assert len(out["repos"]) == 3 and all(r["type_topic"] is None for r in out["repos"])
    assert out["notes"]  # the slots it couldn't fill are reported
    assert len(gh.queries) <= MAX_QUERIES


def test_skips_known_repos_forks_and_link_lists():
    pool = [repo("a/done"), repo("b/fork", fork=True), repo("c/awesome-vibe"), repo("d/list", topics=("vibe-coded", "awesome-list")),
            repo("e/fresh")]
    out = find_random_repos({"A/Done"}, count=1, rng=random.Random(0), fetch=FakeGitHub(pool))
    assert [r["full_slug"] for r in out["repos"]] in ([], ["e/fresh"])
    out = find_random_repos({"a/done"}, count=2, rng=random.Random(0), fetch=FakeGitHub(pool))
    assert {r["full_slug"] for r in out["repos"]} <= {"e/fresh"}


@pytest.fixture
def admin_client(monkeypatch):
    from auth_helpers import sign_in
    c = TestClient(app)
    sign_in(c, "Todd0042", "1", is_admin=True)
    return c


def test_endpoint_needs_test_mode(admin_client):
    assert admin_client.post("/api/admin/queue/find-random").status_code == 403


def test_endpoint_queues_finds_as_admin_test_gradings(admin_client, monkeypatch):
    monkeypatch.setenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", "1")
    enqueue_task("https://github.com/already/queued")
    pool = [repo("already/queued"), repo("Todd0042/mine")] + [repo(f"s{i}/app", 300) for i in range(6)] + [repo("big/app", 3000)]
    monkeypatch.setattr(repo_finder, "github_search", FakeGitHub(pool))
    r = admin_client.post("/api/admin/queue/find-random")
    assert r.status_code == 200
    queued = r.json()["queued"]
    slugs = {q["full_slug"] for q in queued}
    assert "already/queued" not in slugs and "Todd0042/mine" not in slugs and len(queued) >= 4
    conn = get_db_connection()
    rows = conn.execute("SELECT full_slug, is_test, requested_by_user FROM audit_queue WHERE full_slug != 'already/queued'").fetchall()
    conn.close()
    assert {row["full_slug"] for row in rows} == slugs
    assert all(row["is_test"] == 1 and row["requested_by_user"] == "Todd0042" for row in rows)


def test_endpoint_reports_the_github_limit(admin_client, monkeypatch):
    monkeypatch.setenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", "1")

    def limited(q, params):
        raise SearchLimitReached("GitHub's search limit was reached. Wait a minute and try again.")
    monkeypatch.setattr(repo_finder, "github_search", limited)
    r = admin_client.post("/api/admin/queue/find-random")
    assert r.status_code == 429 and "limit" in r.json()["detail"]


def test_only_the_admin_can_use_it(monkeypatch):
    from auth_helpers import sign_in
    monkeypatch.setenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", "1")
    c = TestClient(app)
    assert c.post("/api/admin/queue/find-random").status_code == 403
    sign_in(c, "someone", "77")
    assert c.post("/api/admin/queue/find-random").status_code == 403


# --- "likely good": one well-starred vibe-coded app -------------------------------------------------------------

from letsvibe.web.repo_finder import GOOD_TOP_N, find_good_repo  # noqa: E402


def test_good_pick_is_a_well_starred_app_not_a_guide():
    pool = [repo("a/Awesome-Vibe-Guide", stars=900, language=None),          # no code
            repo("b/vibe-coding-for-dummies", stars=800),                  # a guide by name
            repo("c/prompts", stars=700, topics=("vibe-coded", "prompts")),  # a list by topic
            repo("d/real-app", stars=300), repo("e/other-app", stars=40)]
    gh = FakeGitHub(pool)
    picks = {find_good_repo(set(), rng=random.Random(i), fetch=gh)["repos"][0]["full_slug"] for i in range(20)}
    assert picks <= {"d/real-app", "e/other-app"} and picks
    assert all("stars:20..5000" in q for q in gh.queries) and len(gh.queries) == 40  # two searches per click


def test_good_pick_chooses_among_the_top_few_and_skips_known_repos():
    pool = [repo(f"u{i}/app{i}", stars=1000 - i) for i in range(12)]
    picks = {find_good_repo({"u0/app0"}, rng=random.Random(i), fetch=FakeGitHub(pool))["repos"][0]["full_slug"]
             for i in range(40)}
    assert "u0/app0" not in picks and picks <= {f"u{i}/app{i}" for i in range(1, 1 + GOOD_TOP_N)}
    out = find_good_repo({r["full_name"] for r in pool}, fetch=FakeGitHub(pool))
    assert out["repos"] == [] and out["notes"]


def test_good_endpoint_queues_one_admin_test_grading(admin_client, monkeypatch):
    monkeypatch.setenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", "1")
    monkeypatch.setattr(repo_finder, "github_search", FakeGitHub([repo("star/app", stars=250)]))
    r = admin_client.post("/api/admin/queue/find-good")
    assert r.status_code == 200 and [q["full_slug"] for q in r.json()["queued"]] == ["star/app"]
    conn = get_db_connection()
    row = conn.execute("SELECT is_test, requested_by_user FROM audit_queue WHERE full_slug = 'star/app'").fetchone()
    conn.close()
    assert row["is_test"] == 1 and row["requested_by_user"] == "Todd0042"
    assert admin_client.post("/api/admin/queue/find-good").json()["queued"] == []  # already queued: nothing new


def test_good_endpoint_is_admin_and_test_mode_only(admin_client, monkeypatch):
    assert admin_client.post("/api/admin/queue/find-good").status_code == 403  # test mode off
    monkeypatch.setenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", "1")
    assert TestClient(app).post("/api/admin/queue/find-good").status_code == 403  # signed out
