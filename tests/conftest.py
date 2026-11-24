"""
Pytest configuration and global isolation fixtures.
Ensures the test suite NEVER touches production databases or live queue/appeals inboxes.
"""

import os
from pathlib import Path

# Tests must behave the same with or without a developer's private .env (CI has none). Set the test
# identity BEFORE any LetsVibe module is imported: module-level settings are read at import time, and
# load_dotenv() never overrides variables that are already set.
os.environ.update({
    "ADMIN_GITHUB_USER": "Todd0042",
    "ADMIN_GITHUB_ID": "",
    "ADMIN_EMAIL": "",
    "SESSION_SECRET": "test_secret_for_isolated_pytest_suite_1234567890",
    "GITHUB_CLIENT_ID": "mock_client_id_for_tests",
    "GITHUB_CLIENT_SECRET": "mock_client_secret_for_tests",
    "MAX_REGISTERED_USERS": "5",
    "LETSVIBE_ADMIN_TEST_SUBMISSIONS": "",  # test mode off unless a test turns it on
    "LETSVIBE_ENGINE_LOG": "",              # never write the developer's real engine-test.log
    # Never take real turns on the host GPU scheduler: an unreachable URL means "no scheduler"
    # (llm_lease proceeds without a lease), so tests can't block or be blocked by live sites.
    "LETSVIBE_SUPERVISOR_URL": "http://127.0.0.1:9",
})

# The engine package, as the site's containers get it (PYTHONPATH=/engine, the letsvibe-engine checkout):
# LETSVIBE_ENGINE_PATH, else the checkout next to this repo. Without it every v10 submission is "unknown audit mode".
import sys  # noqa: E402

_ENGINE = Path(os.environ.get("LETSVIBE_ENGINE_PATH") or Path(__file__).resolve().parents[2] / "letsvibe-engine")
if (_ENGINE / "letsvibe_engine").is_dir() and str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

import pytest  # noqa: E402
from letsvibe.storage.db import init_db  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_test_database(tmp_path, monkeypatch):
    """
    Globally isolates every single test to an ephemeral SQLite database in tmp_path.
    Auto-cleans on test teardown, leaving data/letsvibe.db 100% untouched.
    """
    test_db = tmp_path / "test_isolated_letsvibe.db"
    monkeypatch.setenv("LETSVIBE_DB_PATH", str(test_db))
    monkeypatch.setenv("ADMIN_GITHUB_USER", "Todd0042")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "mock_client_id_for_tests")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "mock_client_secret_for_tests")
    monkeypatch.setenv("SESSION_SECRET", "test_secret_for_isolated_pytest_suite_1234567890")
    # Never depend on the developer's real .env: pin admin identity and sign-up cap for tests
    from letsvibe.core import auth as _auth
    from letsvibe.web import server as _server
    monkeypatch.setattr(_auth, "ADMIN_GITHUB_USER", "Todd0042")
    monkeypatch.setattr(_auth, "ADMIN_GITHUB_ID", "")
    monkeypatch.setattr(_server, "ADMIN_GITHUB_USER", "Todd0042", raising=False)
    monkeypatch.setattr(_server, "ADMIN_GITHUB_ID", "", raising=False)
    monkeypatch.setenv("MAX_REGISTERED_USERS", "5")
    monkeypatch.setenv("LETSVIBE_ADMIN_TEST_SUBMISSIONS", "")
    monkeypatch.setenv("LETSVIBE_ENGINE_LOG", "")
    monkeypatch.setenv("LETSVIBE_ENGINE_LOG_PATH", str(tmp_path / "engine-test.log"))
    # Keep simulated emails and image caches out of the real data/ directory
    from letsvibe.core import mailer
    from letsvibe.web import image_proxy
    monkeypatch.setattr(mailer, "LOG_DIR", tmp_path)
    monkeypatch.setattr(image_proxy, "CACHE_DIR", tmp_path / "img_cache")
    init_db(test_db)
    yield test_db
