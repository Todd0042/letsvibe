"""Sign-in helpers for tests. Not in conftest.py: tests/ui has its own conftest.py, and `from conftest import ...`
picks whichever conftest Python imported first, so in a full run these imports failed."""


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
