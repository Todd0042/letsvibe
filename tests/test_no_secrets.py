"""
Guard: no secret may ever be committed. Scans every tracked file (and files staged for the next commit) for
secret-shaped values and secret file types. Matches are reported masked, never printed in full.
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    "GitHub token": r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
    "AWS key": r"\bAKIA[0-9A-Z]{16}\b",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE" + r" KEY-----",   # split so scanners don't read the rule as a key
    "Slack token": r"\bxox[abpr]-[A-Za-z0-9-]{10,}",
    "OpenAI/Anthropic key": r"\bsk-(ant-)?[A-Za-z0-9_-]{20,}",
    "Google key": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "JWT / Cloudflare token": r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}",
    # Same line only; obvious placeholders (change_me..., your_...) are fine
    "secret in .env-style line": r"(?m)^[ \t]*(SESSION_SECRET|GITHUB_CLIENT_SECRET|TUNNEL_TOKEN|[A-Z_]*API_KEY)[ \t]*=[ \t]*['\"]?(?!change_me|your_|<)[A-Za-z0-9_\-/+=.]{16,}['\"]?[ \t]*(#.*)?$",
}
# Deliberately fake values used to test LetsVibe's own secret detector
ALLOWED = {("tests/test_analyzers.py", "secret in .env-style line")}
FORBIDDEN_FILES = re.compile(r"(^|/)(\.env(\..+)?|.*\.(pem|key|p12|pfx|db|sqlite3?|log)|id_rsa.*|id_ed25519.*|.*credentials.*\.json|\.netrc)$")


def _tracked_files():
    out = subprocess.run(["git", "ls-files", "--cached"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [p for p in out.stdout.splitlines() if p]


def test_no_secret_files_are_tracked():
    bad = [p for p in _tracked_files() if FORBIDDEN_FILES.search(p) and p != ".env.example"]
    assert bad == [], f"secret-type files are tracked by git: {bad}"


def test_no_secret_values_in_tracked_files():
    found = []
    for rel in _tracked_files():
        path = ROOT / rel
        if not path.is_file() or path.suffix in (".png", ".webp", ".ico", ".jpg"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in PATTERNS.items():
            if (rel, name) in ALLOWED:
                continue
            for m in re.finditer(pattern, text):
                found.append(f"{rel}: {name} ({m.group(0)[:6]}…)")
    assert found == [], "possible secrets in tracked files:\n" + "\n".join(found)
