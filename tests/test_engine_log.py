"""Engine test log (opt-in): every step and model call of a grading, purged with the project it describes."""

import pytest

from letsvibe.evaluator.experience import enginelog


@pytest.fixture
def log_on(tmp_path, monkeypatch):
    monkeypatch.setenv("LETSVIBE_ENGINE_LOG", "1")
    monkeypatch.setenv("LETSVIBE_ENGINE_LOG_PATH", str(tmp_path / "engine-test.log"))
    return tmp_path / "engine-test.log"


def _grade(slug):
    g = enginelog.GradingLog(slug, "v9.3-profiles")
    g.step("dossier", "## 1. What it says it is")
    g.call("qwen2.5-coder:14b", {"user_prompt": "facts\nJSON only.", "temperature": 0},
           12.5, {"prompt_eval_count": 3600, "num_ctx": 6144, "eval_count": 210, "done_reason": "stop", "trimmed": False},
           {"clarity": {"answer": "CLEAR"}})
    g.call("qwen2.5-coder:7b", {"user_prompt": "chunk"}, 4.1, {}, {"core_flow": "WORKS"})
    g.finish("graded 80.0 B")


def test_log_is_off_unless_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("LETSVIBE_ENGINE_LOG", raising=False)
    monkeypatch.setenv("LETSVIBE_ENGINE_LOG_PATH", str(tmp_path / "engine-test.log"))
    _grade("alice/app")
    assert not (tmp_path / "engine-test.log").exists()


def test_log_records_steps_and_calls(log_on):
    _grade("alice/app")
    text = log_on.read_text()
    assert "BEGIN GRADING alice/app" in text and "END GRADING alice/app" in text
    assert "3600 tokens read of 6144" in text and "stop: stop" in text and "CLEAR" in text
    assert "[7B call 1]" in text and "graded 80.0 B" in text


def test_deleting_a_project_or_owner_purges_its_blocks(log_on):
    _grade("alice/app")
    _grade("bob/tool")
    _grade("alice/other")
    enginelog.purge_project("Alice/App")
    text = log_on.read_text()
    assert "alice/app" not in text and "bob/tool" in text and "alice/other" in text
    enginelog.purge_owner("alice")
    text = log_on.read_text()
    assert "alice/" not in text and "BEGIN GRADING bob/tool" in text
    enginelog.purge_all()
    assert not log_on.exists()


def test_delete_project_everywhere_purges_the_log(log_on):
    from letsvibe.storage.backups import delete_project_everywhere
    _grade("alice/app")
    delete_project_everywhere("alice/app")
    assert "alice/app" not in log_on.read_text()
