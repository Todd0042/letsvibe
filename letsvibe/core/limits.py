"""
Central, easily-editable community limits for LetsVibe.

Change the values below (or override them in your `.env` file) and restart the
server. The owner/admin account (ADMIN_GITHUB_USER / ADMIN_GITHUB_ID) is exempt
from every limit.

    MAX_REGISTERED_USERS   - how many non-admin users may sign up in total
    SUBMISSIONS_PER_WINDOW - how many repositories each registered user may submit...
    WINDOW_HOURS           - ...per rolling window of this many hours (168 = 1 week)
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


# ---- EDIT THESE DEFAULTS (or set the env vars in .env) -------------------
DEFAULT_MAX_REGISTERED_USERS = 5
DEFAULT_SUBMISSIONS_PER_WINDOW = 2
DEFAULT_WINDOW_HOURS = 168  # 7 days
# --------------------------------------------------------------------------


def max_registered_users() -> int:
    return _env_int("MAX_REGISTERED_USERS", DEFAULT_MAX_REGISTERED_USERS)


def submissions_per_window() -> int:
    return _env_int("SUBMISSIONS_PER_WINDOW", DEFAULT_SUBMISSIONS_PER_WINDOW)


def window_hours() -> int:
    return _env_int("WINDOW_HOURS", DEFAULT_WINDOW_HOURS)
