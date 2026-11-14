"""The vibe-coded check (core/vibe_detector.py): hard rejections, strong evidence, and everything else held for review."""
import json
import os
import subprocess


from letsvibe.core.vibe_detector import VibeDetector, code_style, style_evidence


def _repo(tmp_path, commits, files=None, name="proj"):
    """commits: [(author name, email, ISO date, message)]; files: {path: text} committed with the first commit."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for i, (an, ae, date, msg) in enumerate(commits):
        if i == 0:
            for p, text in (files or {"main.py": "print('hi')\n"}).items():
                (repo / p).parent.mkdir(parents=True, exist_ok=True)
                (repo / p).write_text(text)
        (repo / "log.txt").write_text(str(i))
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": an, "GIT_AUTHOR_EMAIL": ae, "GIT_AUTHOR_DATE": date,
               "GIT_COMMITTER_NAME": an, "GIT_COMMITTER_EMAIL": ae, "GIT_COMMITTER_DATE": date}
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], check=True, env=env)
    files_now = {str(p.relative_to(repo)): p.read_text() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    return repo, files_now


def _check(repo, files, owner="maker", fork=None):
    return VibeDetector.evaluate(repo, files, owner=owner, full_slug=f"{owner}/proj", fork_lookup=lambda slug: fork)


ME = ("maker", "maker@example.com")


def test_history_before_november_2022_is_rejected_even_with_ai_files(tmp_path):
    repo, files = _repo(tmp_path, [(*ME, "2021-06-01T10:00:00+00:00", "first"), (*ME, "2026-01-01T10:00:00+00:00", "update")],
                        {"CLAUDE.md": "rules", "main.py": "print(1)\n"})
    v = _check(repo, files)
    assert v.rejected and v.is_legacy_pre_2022 and "2021-06-01" in v.reasons[0]


def test_a_recent_commit_does_not_rescue_an_old_project(tmp_path):
    repo, files = _repo(tmp_path, [(*ME, "2019-03-01T10:00:00+00:00", "start")] + [(*ME, f"2026-0{m}-01T10:00:00+00:00", "x") for m in range(1, 6)])
    assert _check(repo, files).rejected


def test_github_forks_are_rejected(tmp_path):
    repo, files = _repo(tmp_path, [(*ME, "2025-01-01T10:00:00+00:00", "start")], {"AGENTS.md": "rules", "a.py": "x = 1\n"})
    v = _check(repo, files, fork="someone/original")
    assert v.rejected and v.is_fork and "someone/original" in v.reasons[0]


def test_mostly_someone_elses_commits_is_a_fork(tmp_path):
    other = ("Backxtar", "admin@backxtar.example")
    repo, files = _repo(tmp_path, [(*other, f"2025-0{m}-01T10:00:00+00:00", "x") for m in range(1, 9)] +
                        [(*ME, "2025-09-01T10:00:00+00:00", "my change")])
    v = _check(repo, files)
    assert v.rejected and v.is_fork and "Backxtar" in v.reasons[0]


def test_an_owner_with_a_different_git_name_is_not_called_a_fork(tmp_path):
    repo, files = _repo(tmp_path, [("Scott B", "scott@example.com", f"2025-0{m}-01T10:00:00+00:00", "x") for m in range(1, 6)])
    v = _check(repo, files)
    assert not v.rejected and v.needs_review          # cannot tell: held for the admin instead
    assert any("does not match the owner" in s for s in v.supporting)


def test_ai_tool_files_confirm_vibe_coding(tmp_path):
    for f in ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursor/rules/style.mdc", ".github/copilot-instructions.md"):
        sub = tmp_path / f.replace("/", "_").replace(".", "_")
        sub.mkdir()
        repo, files = _repo(sub, [(*ME, "2025-05-01T10:00:00+00:00", "init")], {f: "rules", "main.py": "x = 1\n"})
        v = _check(repo, files)
        assert v.is_vibe, f


def test_ai_commits_must_be_a_real_share_of_the_history(tmp_path):
    ai = "add feature\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
    few = [(*ME, f"2025-01-{d:02d}T10:00:00+00:00", ai if d == 1 else "human change") for d in range(1, 21)]
    repo, files = _repo(tmp_path, few, name="few")
    v = _check(repo, files)
    assert v.needs_review and any("1 of 20 commits" in s for s in v.supporting)     # AI-assisted at most
    many = [(*ME, f"2025-01-{d:02d}T10:00:00+00:00", ai if d <= 8 else "tweak") for d in range(1, 21)]
    repo, files = _repo(tmp_path, many, name="many")
    assert _check(repo, files).is_vibe


def test_people_named_like_ai_tools_do_not_count(tmp_path):
    msgs = [f"x\n\nCo-authored-by: {n} <{n.lower()}@example.com>" for n in ("Devin Smith", "Jules Verne", "Coline Dupont", "Usain Bolt")]
    repo, files = _repo(tmp_path, [(*ME, f"2025-02-0{i + 1}T10:00:00+00:00", m) for i, m in enumerate(msgs)])
    assert not _check(repo, files).is_vibe


def test_ai_app_builder_bot_authors_confirm_vibe_coding(tmp_path):
    bot = ("lovable-dev[bot]", "lovable-dev[bot]@users.noreply.github.com")
    repo, files = _repo(tmp_path, [(*bot, "2025-04-01T10:00:00+00:00", "Use tech stack vite_react_shadcn_ts")] +
                        [(*ME, "2025-04-02T10:00:00+00:00", "tweak")])
    assert _check(repo, files).is_vibe


def test_a_readme_that_says_so_confirms_vibe_coding(tmp_path):
    repo, files = _repo(tmp_path, [(*ME, "2025-04-01T10:00:00+00:00", "init")],
                        {"README.md": "# Raid Guide\n\n> AI notice: this add-on is AI-generated.\n", "main.cpp": "int main(){}\n"})
    assert _check(repo, files).is_vibe


def test_code_examples_in_docs_never_count(tmp_path):
    readme = "# Library\n\n```python\nimport lib\n```\n" + "".join(f"\n## Step {i}: do it\n" for i in range(1, 6))
    repo, files = _repo(tmp_path, [(*ME, "2025-04-01T10:00:00+00:00", "feat: add parser")] * 1,
                        {"README.md": readme, "docs/guide.md": readme, "build.sh": "make\n", "lib/core.py": "def f(a):\n    return a\n"})
    v = _check(repo, files)
    assert v.needs_review and not v.signals         # docs, build scripts and conventional commits never decide


def test_ai_style_python_code_confirms_vibe_coding(tmp_path):
    body = []
    for i in range(20):
        body.append(f'def calculate_remaining_budget_for_period_{i}(amount):\n    try:\n        return amount * 2\n'
                    f'    except Exception as e:\n        print(f"❌ Error calculating budget: {{e}}")\n        return 0\n\n'
                    f'    # Step 1: Load the settings\n')
    code = "".join(body) * 2
    s = code_style({"budget/app.py": code})
    habits = style_evidence(s)
    assert len(habits) >= 3
    repo, files = _repo(tmp_path, [(*ME, "2025-04-01T10:00:00+00:00", "update")], {"budget/app.py": code})
    v = _check(repo, files)
    assert v.is_vibe and any(s.startswith("AI-style code") for s in v.signals)


def test_human_style_python_is_not_confirmed(tmp_path):
    code = "".join(f"def calc{i}(a, b):\n    try:\n        return a / b\n    except ZeroDivisionError:\n        return None  # TODO: log\n\n"
                   for i in range(60))
    assert style_evidence(code_style({"m.py": code})) == []


# ---- the hold: graded, waiting for the admin ---------------------------------------------------------

def test_vibe_review_hold_waits_for_the_admin_and_never_expires(tmp_path):
    from letsvibe.storage import integrity
    from letsvibe.storage.db import init_db

    db = tmp_path / "t.db"
    init_db(db)
    hid = integrity.hold_report({"repo_name": "proj"}, "maker", "proj", "maker/proj", "https://github.com/maker/proj",
                                [{"file": "What the vibe-coded check found", "start_line": None, "end_line": None, "code": "- x"}],
                                db_path=db, kind="vibe_review")
    held = integrity.list_held_for_owner("maker", db_path=db)
    assert held[0]["kind"] == "vibe_review" and held[0]["status"] == "awaiting_admin"
    assert integrity.list_held_for_admin(db_path=db)[0]["status"] == "awaiting_admin"
    counts = integrity.count_pending_reviews(db_path=db)
    assert counts["vibe_reviews"] == 1 and counts["total"] == 1
    assert integrity.submit_hold_response(hid, "maker", "it was vibe coded honestly", db_path=db) is False   # nothing to answer
    # far past the explanation deadline, it is still waiting
    import letsvibe.storage.integrity as mod
    real_now = mod._now
    try:
        from datetime import timedelta
        mod._now = lambda: real_now() + timedelta(days=365)
        assert integrity.overdue_held_ids(db_path=db) == []
    finally:
        mod._now = real_now


# ---- the worker: rejected projects stop, unclear ones are graded then held --------------------------

def _run_worker(tmp_path, monkeypatch, verdict):
    from letsvibe.core.vibe_detector import VibeCheckResult
    from letsvibe.storage.db import get_db_connection, init_db
    from letsvibe.web import worker as w

    db = tmp_path / "w.db"
    init_db(db)
    conn = get_db_connection(db)
    with conn:
        conn.execute("INSERT INTO audit_queue (target, owner, repo_name, full_slug, mode, status) "
                     "VALUES ('https://github.com/maker/proj', 'maker', 'proj', 'maker/proj', 'blend', 'running')")
        task = dict(conn.execute("SELECT * FROM audit_queue").fetchone())
    conn.close()
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "main.py").write_text("print('hi')\n")

    class Packed:
        source_path, files, file_tree, skipped_vendor_files, is_temporary = repo, {"main.py": "print('hi')\n"}, "", 0, False

    class Client:
        def __init__(self, *a, **k): pass
        def generate_json(self, **k): return {}
        def is_running(self): return True
        def unload_all_models(self): return 0

    class Report:
        integrity_hold = False
        def model_dump(self, mode="json"): return {"repo_name": "proj", "final_vibe_score": 70.0}

    graded = []
    monkeypatch.setattr(w.RepositoryIngestor, "ingest", staticmethod(lambda target: Packed()))
    monkeypatch.setattr(w.VibeDetector, "evaluate", classmethod(lambda cls, *a, **k: VibeCheckResult(*verdict)))
    monkeypatch.setattr(w, "OllamaClient", Client)
    monkeypatch.setattr(w, "gather_metrics", lambda packed: (type("M", (), {"readme_excerpt": ""})(), None))
    monkeypatch.setattr(w, "classify_project", lambda *a, **k: None)
    monkeypatch.setattr(w, "evaluate", lambda *a, **k: graded.append(1) or Report())
    published = []
    monkeypatch.setattr(w.ReportStorageManager, "persist_audit", staticmethod(lambda **k: published.append(1) or (None, None, None, None, None)))
    worker = w.AuditQueueWorker(db_path=db)
    worker._process_task(task)
    conn = get_db_connection(db)
    row = dict(conn.execute("SELECT status, progress_message, rejection_reason FROM audit_queue").fetchone())
    held = [dict(r) for r in conn.execute("SELECT kind, status, flagged_lines_json FROM held_reports").fetchall()]
    conn.close()
    return row, held, graded, published


def test_worker_rejects_without_grading(tmp_path, monkeypatch):
    row, held, graded, published = _run_worker(tmp_path, monkeypatch, ("reject", 0.0, ["GitHub lists this repository as a fork of a/b."]))
    assert row["status"] == "rejected_non_vibe" and "fork" in row["rejection_reason"]
    assert not graded and not published and not held


def test_worker_grades_unclear_projects_then_holds_them(tmp_path, monkeypatch):
    row, held, graded, published = _run_worker(tmp_path, monkeypatch, ("unclear", 0.1, ["Could not confirm.", "README badges"]))
    assert graded and not published
    assert row["status"] == "held_for_review"
    assert held[0]["kind"] == "vibe_review" and held[0]["status"] == "awaiting_admin"
    assert "README badges" in json.loads(held[0]["flagged_lines_json"])[0]["code"]


def test_worker_publishes_confirmed_projects(tmp_path, monkeypatch):
    row, held, graded, published = _run_worker(tmp_path, monkeypatch, ("vibe", 0.9, ["Vibe-coded: AI tool file"]))
    assert graded and published and not held and row["status"] == "completed"
