"""
Authentication and Session Security Engine for LetsVibe.
Handles GitHub OAuth (Dual-Mode: Email Alerts vs Strict Privacy),
tamper-proof cryptographic session cookies, and Admin/Owner role assignment.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Load configuration from environment
def _load_session_secret() -> bytes:
    """
    Session tokens (including the is_admin claim) are HMAC-signed with this secret. A public
    default would let anyone forge admin sessions, so without a strong SESSION_SECRET we use a
    random per-process secret: safe, but every restart signs everyone out.
    """
    value = os.getenv("SESSION_SECRET", "").strip()
    if len(value) >= 32:
        return value.encode("utf-8")
    import logging
    import secrets as _secrets

    logging.getLogger("letsvibe.auth").warning(
        "SESSION_SECRET is missing or shorter than 32 characters; using a random per-process secret. "
        "Set a long random SESSION_SECRET in .env so sessions survive restarts."
    )
    return _secrets.token_hex(32).encode("utf-8")


SESSION_SECRET = _load_session_secret()
# The site admin comes only from .env (git-ignored; see .env.example). There is no built-in default, so a
# deployment without ADMIN_GITHUB_ID / ADMIN_GITHUB_USER simply has no admin.
ADMIN_GITHUB_USER = os.getenv("ADMIN_GITHUB_USER", "").strip()
ADMIN_GITHUB_ID = os.getenv("ADMIN_GITHUB_ID", "").strip()

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "").strip()
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI", "").strip()

SESSION_COOKIE_NAME = "letsvibe_session"
CSRF_COOKIE_NAME = "letsvibe_oauth_state"
SESSION_DURATION_SECONDS = 30 * 24 * 3600  # 30 days


def is_admin_user(username: str, github_id: Optional[str] = None) -> bool:
    """
    Checks whether the authenticated user is the designated owner/admin.

    When ADMIN_GITHUB_ID is set it is the ONLY thing that grants admin: GitHub usernames can be
    renamed and later claimed by someone else, but the numeric account ID never changes. The
    username is only used as a fallback when no ID is configured.
    """
    if ADMIN_GITHUB_ID:
        return bool(github_id) and str(github_id) == ADMIN_GITHUB_ID
    return bool(username) and bool(ADMIN_GITHUB_USER) and username.lower() == ADMIN_GITHUB_USER.lower()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64decode(s: str) -> bytes:
    padding = 4 - (len(s) % 4)
    if padding and padding < 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s.encode("utf-8"))


def create_session_token(payload: Dict[str, Any], secret: bytes = SESSION_SECRET) -> str:
    """Signs a dictionary payload into a tamper-proof session token."""
    payload_copy = dict(payload)
    if "exp" not in payload_copy:
        payload_copy["exp"] = int(time.time()) + SESSION_DURATION_SECONDS

    raw_json = json.dumps(payload_copy, separators=(",", ":"), sort_keys=True).encode("utf-8")
    b64_payload = _b64encode(raw_json)
    signature = hmac.new(secret, b64_payload.encode("utf-8"), hashlib.sha256).digest()
    b64_sig = _b64encode(signature)
    return f"{b64_payload}.{b64_sig}"


def verify_session_token(token: str, secret: bytes = SESSION_SECRET) -> Optional[Dict[str, Any]]:
    """Verifies HMAC signature and expiration of a session token."""
    if not token or "." not in token:
        return None

    try:
        b64_payload, b64_sig = token.rsplit(".", 1)
        expected_sig = hmac.new(secret, b64_payload.encode("utf-8"), hashlib.sha256).digest()
        actual_sig = _b64decode(b64_sig)

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        raw_json = _b64decode(b64_payload)
        payload = json.loads(raw_json.decode("utf-8"))

        if payload.get("exp", 0) < time.time():
            return None  # Expired

        return payload
    except Exception:
        return None


def generate_oauth_state(scope_level: str = "minimal") -> str:
    """
    Generates a tamper-proof signed OAuth state token containing:
    - Creation timestamp (valid for 10 minutes)
    - Requested scope level ('email' or 'minimal')
    - Cryptographic nonce
    - HMAC-SHA256 signature
    """
    ts = int(time.time())
    nonce = secrets.token_hex(16)
    data = f"{ts}:{scope_level}:{nonce}"
    sig = hmac.new(SESSION_SECRET, data.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{data}:{sig}"


def verify_oauth_state(state: str, max_age_seconds: int = 600) -> Tuple[bool, str]:
    """
    Cryptographically verifies a returned OAuth state token.
    Returns (is_valid, scope_level).
    """
    if not state or ":" not in state:
        return False, "minimal"

    parts = state.split(":")
    if len(parts) != 4:
        return False, "minimal"

    ts_str, scope_level, nonce, sig = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return False, "minimal"

    # Check age (within max_age_seconds)
    now = int(time.time())
    if now < ts or (now - ts) > max_age_seconds:
        return False, "minimal"

    data = f"{ts}:{scope_level}:{nonce}"
    expected_sig = hmac.new(SESSION_SECRET, data.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return False, "minimal"

    return True, scope_level


def build_github_authorize_url(scope_level: str = "minimal", state: Optional[str] = None) -> Tuple[str, str]:
    """
    Constructs the GitHub OAuth authorization URL. LetsVibe only ever requests `read:user`
    (public username, numeric ID and avatar); no email permission exists. `scope_level` is accepted
    for backwards compatibility and ignored.
    """
    state_token = state or generate_oauth_state(scope_level="minimal")
    scope = "read:user"

    client_id = os.getenv("GITHUB_CLIENT_ID", GITHUB_CLIENT_ID).strip()
    redirect_uri = os.getenv("GITHUB_REDIRECT_URI", GITHUB_REDIRECT_URI).strip()

    params = {
        "client_id": client_id,
        "scope": scope,
        "state": state_token,
    }
    if redirect_uri:
        params["redirect_uri"] = redirect_uri

    query_str = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://github.com/login/oauth/authorize?{query_str}"
    return url, state_token


async def exchange_github_code(code: str, scope_level: str = "minimal") -> Dict[str, Any]:
    """
    Exchanges an OAuth authorization code for GitHub user profile and optional verified email.
    """
    client_id = os.getenv("GITHUB_CLIENT_ID", GITHUB_CLIENT_ID).strip()
    client_secret = os.getenv("GITHUB_CLIENT_SECRET", GITHUB_CLIENT_SECRET).strip()
    if not client_id or not client_secret:
        raise ValueError("GitHub OAuth credentials (GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET) are not configured.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Exchange code for access token
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
            },
        )
        token_data = token_res.json()
        access_token = token_data.get("access_token")
        if not access_token:
            error_desc = token_data.get("error_description", "Failed to retrieve access token from GitHub")
            raise RuntimeError(f"GitHub OAuth Error: {error_desc}")

        # 2. Fetch user profile
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "LetsVibe-Beta-Auth",
        }
        user_res = await client.get("https://api.github.com/user", headers=headers)
        if user_res.status_code != 200:
            raise RuntimeError(f"Failed to fetch GitHub user profile: {user_res.text}")

        user_info = user_res.json()
        github_id = str(user_info["id"])
        username = user_info["login"]
        avatar_url = user_info.get("avatar_url")
        # Email addresses are never read or stored, even when public on the GitHub profile.

        return {
            "github_id": github_id,
            "username": username,
            "avatar_url": avatar_url,
            "email": None,
            "scope_level": "minimal",
        }
