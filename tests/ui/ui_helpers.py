"""State shared between the UI fixtures (conftest.py) and the UI tests. Not in conftest.py: tests/ has its own
conftest.py, and `from conftest import ...` picks whichever conftest Python imported first."""

UI_SECRET = "ui-test-session-secret-0123456789abcdef-0123456789"
UI_STATE: dict = {}


def session_cookie_for(base: str, username: str, github_id: str) -> dict:
    """Registers a user in the UI test database and returns a Playwright cookie signed with the server's secret."""
    from letsvibe.core.auth import SESSION_COOKIE_NAME, create_session_token
    from letsvibe.storage.db import upsert_user

    from letsvibe.core.legal import TERMS_VERSION
    upsert_user(github_id=github_id, username=username, db_path=UI_STATE["db"], terms_version=TERMS_VERSION)
    token = create_session_token({"github_id": github_id, "username": username, "is_admin": False}, secret=UI_SECRET.encode())
    return {"name": SESSION_COOKIE_NAME, "value": token, "url": base}
