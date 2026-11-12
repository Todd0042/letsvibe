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
})

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
    # Keep simulated emails and image caches out of the real data/ directory
    from letsvibe.core import mailer
    from letsvibe.web import image_proxy
    monkeypatch.setattr(mailer, "LOG_DIR", tmp_path)
    monkeypatch.setattr(image_proxy, "CACHE_DIR", tmp_path / "img_cache")
    init_db(test_db)
    yield test_db


def sign_in(client, username: str, github_id: str, is_admin: bool = False) -> None:
    """Registers the user (sessions are only valid for existing accounts) and sets a signed session cookie."""
    from letsvibe.core.auth import SESSION_COOKIE_NAME, create_session_token
    from letsvibe.storage.db import upsert_user

    client.cookies.set(SESSION_COOKIE_NAME, accepted_token(github_id, username, is_admin))


def accepted_token(github_id: str, username: str, is_admin: bool = False) -> str:
    """A signed session for an account that exists and has accepted the current Terms of Use (core/legal.py)."""
    from letsvibe.core.auth import create_session_token
    from letsvibe.core.legal import TERMS_VERSION
    from letsvibe.storage.db import upsert_user

    upsert_user(github_id=github_id, username=username, terms_version=TERMS_VERSION)
    return create_session_token({"github_id": github_id, "username": username, "is_admin": is_admin})
