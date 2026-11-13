"""
What the project set out to be, for the "within scope" rating: the earliest README and first commit messages in
the git history, before later AI-written READMEs described everything that got built. Read-only git commands on the
grader's own clone (cloned with the last 50 commits, so very long histories start part-way).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

README = re.compile(r"^readme(\.[a-z0-9]+)?$", re.I)
MAX_README = 1500


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=15)
        return res.stdout if res.returncode == 0 else None
    except Exception:
        return None


def _clean(text: str) -> str:
    url = r"\((?:[^()]|\([^()]*\))*\)"                          # a link target, allowing one level of (brackets)
    text = re.sub(r"```.*?```|<!--.*?-->", " ", text, flags=re.S)  # code blocks and comments
    text = re.sub(r"\[!\[[^\]]*\]" + url + r"\]" + url, " ", text)    # linked badges [![alt](img)](link)
    text = re.sub(r"!\[[^\]]*\]" + url + r"|<img[^>]*>", " ", text)     # images
    text = re.sub(r"\[([^\]]+)\]" + url, r"\1", text)                  # links -> their text
    text = re.sub(r"<[^>]+>|[#*_`>|]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def earliest_intention(repo: Path) -> Dict[str, Any]:
    """
    {"first_commits": [oldest first], "first_readme": text, "readme_changes": n, "commits_seen": n,
     "history_complete": bool}. Empty values when the folder isn't a git checkout.
    """
    repo = Path(repo)
    out: Dict[str, Any] = {"first_commits": [], "first_readme": "", "readme_changes": 0, "commits_seen": 0,
                           "history_complete": False}
    log = _git(repo, "log", "--reverse", "--format=%s")
    if log is None:
        return out
    subjects: List[str] = [s.strip() for s in log.splitlines() if s.strip()]
    out["commits_seen"] = len(subjects)
    out["first_commits"] = subjects[:6]
    out["history_complete"] = not (repo / ".git" / "shallow").exists()

    top_level = _git(repo, "log", "--reverse", "--format=%H", "--name-only", "--diff-filter=AM", "--", ".") or ""
    readme_path, first_sha, changes = None, None, 0
    sha = None
    for line in top_level.splitlines():
        line = line.strip()
        if re.fullmatch(r"[0-9a-f]{40}", line):
            sha = line
        elif line and "/" not in line and README.match(line):
            changes += 1
            if first_sha is None:
                readme_path, first_sha = line, sha
    out["readme_changes"] = changes
    if first_sha and readme_path:
        text = _git(repo, "show", f"{first_sha}:{readme_path}") or ""
        out["first_readme"] = _clean(text)[:MAX_README]
    return out
