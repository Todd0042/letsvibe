"""Tests for the focused classify step, entry-file discovery and reclassify-without-regrade."""

from pathlib import Path

from starlette.testclient import TestClient

from letsvibe.core.auth import ADMIN_GITHUB_USER, SESSION_COOKIE_NAME, create_session_token
from letsvibe.core.platforms import PlatformDetection, find_entry_file
from letsvibe.evaluator.classifier import CLASSIFIER_SYSTEM_PROMPT, classify_project, merge_classification
from letsvibe.storage.db import get_leaderboard, get_project_detail, init_db, save_report_to_db, update_report_classification
from letsvibe.storage.identity import parse_repo_identity
from tests.conftest import accepted_token


def _write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_find_entry_file_prefers_real_entry_points(tmp_path):
    _write(tmp_path, "src/main.cpp", "int main(){}")
    _write(tmp_path, "src/util.cpp")
    _write(tmp_path, "examples/main.cpp")
    _write(tmp_path, "tests/main.py")
    assert find_entry_file(tmp_path) == "src/main.cpp"

    py = tmp_path / "py"
    _write(py, "pkg/__main__.py")
    _write(py, "pkg/app.py")
    assert find_entry_file(py) == "pkg/__main__.py"

    assert find_entry_file(tmp_path / "empty") is None


def test_merge_keeps_build_file_platforms_authoritative():
    firmware = PlatformDetection(platforms=["embedded"], device_profile="board 'esp32-s3'", signals=["platformio.ini"])
    raw = {"general_category": "game", "platforms": ["windows", "embedded"], "device_profile": "Tiny round display"}
    out = merge_classification(raw, firmware)
    assert out.general_category == "game"
    assert out.platforms == ["embedded"]  # firmware does not also run on a PC
    assert out.device_profile == "board 'esp32-s3'"  # detected hardware beats the model's sentence
    assert merge_classification({"device_profile": "Typical smartphones"}, PlatformDetection(platforms=["android"], signals=["AndroidManifest.xml"])).device_profile == "Typical smartphones"

    # A weak "it's just code" guess lets the model decide
    weak = PlatformDetection(platforms=["windows", "macos", "linux"], signals=["cross-platform desktop/CLI code"])
    assert merge_classification({"platforms": ["web"], "general_category": "web_app"}, weak).platforms == ["web"]

    # Strong detection plus extra model platforms are combined
    android = PlatformDetection(platforms=["android"], signals=["AndroidManifest.xml"])
    assert merge_classification({"platforms": ["ios"]}, android).platforms == ["android", "ios"]
    # ...but never a desktop OS for a phone app with no desktop evidence
    assert merge_classification({"platforms": ["windows", "android"]}, android).platforms == ["android"]
    # ...and the model's device sentence cannot call a phone app a desktop PC
    assert merge_classification({"device_profile": "Standard desktop PC"}, android).device_profile == "Android phones and tablets (touch)"

    # Garbage category falls back to "other"
    assert merge_classification({"general_category": "spaceship"}, android).general_category == "other"


class _Client:
    def __init__(self, fail=False):
        self.fail, self.prompts = fail, []

    def list_models(self):
        return ["qwen2.5-coder:7b"]

    def generate_json(self, model, system_prompt, user_prompt, **kw):
        assert system_prompt == CLASSIFIER_SYSTEM_PROMPT
        self.prompts.append(user_prompt)
        if self.fail:
            raise RuntimeError("model offline")
        return {"evidence": "README says arcade game", "general_category": "game", "platforms": ["windows"], "device_profile": "Standard desktop PC"}


def test_classify_project_sends_entry_file_and_survives_failure(tmp_path):
    _write(tmp_path, "main.py", "import pygame  # ARCADE_MARKER")
    det = PlatformDetection(platforms=["windows", "macos", "linux"], signals=["cross-platform desktop/CLI code"])
    client = _Client()
    out = classify_project(client, tmp_path, det, readme="A neon arcade game")
    assert (out.general_category, out.platforms, out.entry_file) == ("game", ["windows"], "main.py")
    assert "ARCADE_MARKER" in client.prompts[0] and "A neon arcade game" in client.prompts[0]

    failed = classify_project(_Client(fail=True), tmp_path, det)
    assert failed.platforms == ["windows", "macos", "linux"] and failed.general_category == "other"
    assert "Classification call failed" in failed.issues[0]


def test_reclassify_updates_filters_but_not_scores(tmp_path):
    db = tmp_path / "r.db"
    init_db(db)
    rep = {"final_vibe_score": 70.0, "letter_grade": "C", "archetype": {"name": "Dash"}, "factor_evaluations": {}}
    save_report_to_db(rep, parse_repo_identity("https://github.com/a/dash"), db_path=db)

    assert update_report_classification("a/dash", "embedded", ["embedded"], "ESP32-S3", db_path=db)
    assert update_report_classification("a/missing", "game", [], "", db_path=db) is None

    row = get_leaderboard(db_path=db, platforms="embedded")[0]
    assert row["general_category"] == "embedded" and row["final_score"] == 70.0
    detail = get_project_detail("a/dash", db_path=db)
    blob = str(detail)
    assert "ESP32-S3" in blob


def test_reclassify_mode_is_admin_only():
    from letsvibe.web.server import app

    client = TestClient(app)
    from conftest import sign_in
    sign_in(client, "someone", "7")
    r = client.post("/api/queue/enqueue", json={"target": "https://github.com/a/b", "mode": "reclassify"})
    assert r.status_code == 400
    assert client.post("/api/admin/reclassify-all").status_code in (401, 403)

    client.cookies.set(SESSION_COOKIE_NAME, accepted_token("1", ADMIN_GITHUB_USER, True))
    r = client.post("/api/admin/reclassify-all")
    assert r.status_code == 200 and r.json()["status"] == "ok"
