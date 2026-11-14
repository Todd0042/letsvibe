"""
Browser test fixtures: a real uvicorn server on a throwaway, seeded database.

Run with:  .venv/bin/python -m pytest tests/ui
Needs:     pip install -e .[dev] && python -m playwright install chromium
Screenshots land in test-results/screens/ for manual review.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from letsvibe.core.models import (  # noqa: E402
    CORE_FACTORS, DeterministicMetrics, DynamicWeights, FactorEvaluation, FactorType, ProjectArchetype, VibeReport,
)
from letsvibe.evaluator.rubric import calculate_letter_grade  # noqa: E402
from letsvibe.storage.db import init_db, save_report_to_db, update_queue_settings  # noqa: E402
from letsvibe.storage.identity import parse_repo_identity  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_SECRET = "ui-test-session-secret-0123456789abcdef-0123456789"
UI_STATE = {}
SCREENS = REPO_ROOT / "test-results" / "screens"

SEED = [
    # slug, score, category, platforms, profile
    ("pixelforge/neon-racer", 88.4, "game", ["android", "ios"], "Mid-range phones, touch input"),
    ("todd-demo/mx5-dash", 71.2, "embedded", ["embedded"], "ESP32-S3 with 480x480 display, ~512 KB SRAM"),
    ("cliwright/fastgrep-plus-an-extremely-long-repository-name", 79.5, "tool", ["windows", "macos", "linux"], ""),
    ("arcadia/retro-arcade", 61.0, "game", ["windows", "console"], "Nintendo Switch homebrew"),
    ("weblab/recipe-share", 66.3, "web_app", ["web"], ""),
    ("botsmith/discord-helper", 52.4, "bot", ["linux"], ""),
]


def _report(slug: str, score: float, category: str, platforms, profile: str) -> dict:
    factors = {
        f: FactorEvaluation(
            factor=FactorType(f), score=max(0.0, min(100.0, score + (i - 3) * 4)), weight=100 / 7,
            strengths=[f"{f} strength"], weaknesses=[f"{f} weakness"], reasoning=f"Reasoning for {f}.",
        )
        for i, f in enumerate(CORE_FACTORS)
    }
    factors["malware_risk"] = FactorEvaluation(factor=FactorType.MALWARE_RISK, score=97, weight=0)
    rep = VibeReport(
        repo_name=slug.split("/")[1], repo_source=f"https://github.com/{slug}", timestamp="2026-11-11 12:00:00",
        archetype=ProjectArchetype(
            name=slug.split("/")[1].replace("-", " ").title(), category="Demo", primary_language="Python",
            target_audience="Players", core_purpose="A demo project used by the responsive layout tests.",
            general_category=category, platforms=list(platforms), device_profile=profile,
        ),
        weights=DynamicWeights(), deterministic_metrics=DeterministicMetrics(total_loc=12345, total_files=42),
        factor_evaluations=factors, final_vibe_score=score, malware_risk_score=97,
        letter_grade=calculate_letter_grade(score), verdict_title="Demo Verdict",
        verdict_summary="A concise reviewer verdict for layout testing.",
        top_solid_features=["Feature A: solid"], top_features_needing_work=["Feature B: needs tests"],
        complexity={"summary": "Average complexity is 4.2 paths per function (moderate: typical for a working app).",
                    "details": ["render_dash in display.cpp: 31 paths over 140 lines"], "graded": False},
    )
    return rep.model_dump(mode="json")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    db = tmp_path_factory.mktemp("ui") / "ui.db"
    init_db(db)
    for slug, score, cat, plats, prof in SEED:
        save_report_to_db(_report(slug, score, cat, plats, prof), parse_repo_identity(f"https://github.com/{slug}"), db_path=db)
    update_queue_settings(is_paused=True, db_path=db)

    port = _free_port()
    UI_STATE["db"] = db
    env = {**os.environ, "LETSVIBE_DB_PATH": str(db), "SESSION_SECRET": UI_SECRET}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "letsvibe.web.server:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/api/taxonomy", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise RuntimeError(proc.stderr.read().decode())
        time.sleep(0.1)
    yield base
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def session_cookie_for(base: str, username: str, github_id: str) -> dict:
    """Registers a user in the UI test database and returns a Playwright cookie signed with the server's secret."""
    from letsvibe.core.auth import SESSION_COOKIE_NAME, create_session_token
    from letsvibe.storage.db import upsert_user

    from letsvibe.core.legal import TERMS_VERSION
    upsert_user(github_id=github_id, username=username, db_path=UI_STATE["db"], terms_version=TERMS_VERSION)
    token = create_session_token({"github_id": github_id, "username": username, "is_admin": False}, secret=UI_SECRET.encode())
    return {"name": SESSION_COOKIE_NAME, "value": token, "url": base}
