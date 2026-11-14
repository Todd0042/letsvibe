"""
Repository identity parser and canonical naming engine.
Structures storage, database references, and file naming based on GitHub address
and repository owner to support local usage and future multi-tier cloud deployments.
"""

import getpass
import os
import re
import subprocess  # nosec B404 - git/nvidia-smi run with fixed list arguments, never a shell
from pathlib import Path
from typing import Optional
from pydantic import BaseModel


class RepoIdentity(BaseModel):
    """Canonical identity of a codebase target."""
    provider: str               # "github", "gitlab", "local"
    owner: str                  # e.g. "Todd0042"
    repo_name: str              # e.g. "deciflux"
    full_slug: str              # e.g. "Todd0042/deciflux"
    canonical_url: str          # e.g. "https://github.com/Todd0042/deciflux"
    avatar_url: Optional[str]   # e.g. "https://github.com/Todd0042.png" (Creator avatar)
    repo_icon_url: Optional[str] = None  # e.g. raw GitHub launcher icon URL
    target_input: str           # The raw input passed by the user
    is_remote: bool             # True if target is a remote URL

    @property
    def file_slug(self) -> str:
        """Safe filesystem slug: e.g. github_Todd0042_deciflux."""
        safe_owner = re.sub(r"[^\w\-.]", "_", self.owner)
        safe_repo = re.sub(r"[^\w\-.]", "_", self.repo_name)
        return f"{self.provider}_{safe_owner}_{safe_repo}"

    def get_report_dir(self, base_data_dir: Path = Path("data/reports")) -> Path:
        """Returns structured directory: data/reports/{owner}/{repo_name}/."""
        safe_owner = re.sub(r"[^\w\-.]", "_", self.owner)
        safe_repo = re.sub(r"[^\w\-.]", "_", self.repo_name)
        d = base_data_dir / safe_owner / safe_repo
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_json_path(self, base_data_dir: Path = Path("data/reports")) -> Path:
        return self.get_report_dir(base_data_dir) / "vibe_report.json"

    def get_html_path(self, base_data_dir: Path = Path("data/reports")) -> Path:
        return self.get_report_dir(base_data_dir) / "vibe_report.html"

    def get_markdown_path(self, base_data_dir: Path = Path("data/reports")) -> Path:
        return self.get_report_dir(base_data_dir) / "VIBE_REPORT.md"


def _extract_git_remote_url(local_path: Path) -> Optional[str]:
    """Attempts to inspect git remote origin URL for a local directory."""
    try:
        res = subprocess.run(  # nosec B603 B607 - fixed command, list arguments, no shell
            ["git", "-C", str(local_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2.0,
        )
        url = res.stdout.strip()
        if url:
            return url
    except Exception:  # nosec B110 - deliberate best-effort fallback
        pass
    return None


_GH_URL_RE = re.compile(
    r"^(?:https?://(?:www\.)?github\.com/|git@github\.com:)([\w\-.]+)/([\w\-.]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_GL_URL_RE = re.compile(
    r"^(?:https?://(?:www\.)?gitlab\.com/|git@gitlab\.com:)([\w\-.]+)/([\w\-.]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_SLUG_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def _valid_slug_parts(owner: str, repo_name: str) -> bool:
    return (
        bool(_SLUG_PART_RE.fullmatch(owner))
        and bool(_SLUG_PART_RE.fullmatch(repo_name))
        and owner not in (".", "..")
        and repo_name not in (".", "..")
    )


def parse_repo_identity(target: str) -> RepoIdentity:
    """
    Parses a repository string (remote URL, owner/repo slug, or local directory)
    into a canonical RepoIdentity.
    """
    target = target.strip()

    # Pattern for GitHub URLs: https://github.com/owner/repo or git@github.com:owner/repo.git
    gh_match = _GH_URL_RE.match(target)
    if gh_match and _valid_slug_parts(gh_match.group(1), gh_match.group(2)):
        owner = gh_match.group(1)
        repo_name = gh_match.group(2)
        return RepoIdentity(
            provider="github",
            owner=owner,
            repo_name=repo_name,
            full_slug=f"{owner}/{repo_name}",
            canonical_url=f"https://github.com/{owner}/{repo_name}",
            avatar_url=f"https://github.com/{owner}.png",
            target_input=target,
            is_remote=True,
        )

    # GitLab pattern
    gl_match = _GL_URL_RE.match(target)
    if gl_match and _valid_slug_parts(gl_match.group(1), gl_match.group(2)):
        owner = gl_match.group(1)
        repo_name = gl_match.group(2)
        return RepoIdentity(
            provider="gitlab",
            owner=owner,
            repo_name=repo_name,
            full_slug=f"{owner}/{repo_name}",
            canonical_url=f"https://gitlab.com/{owner}/{repo_name}",
            avatar_url=f"https://gitlab.com/{owner}.png",
            target_input=target,
            is_remote=True,
        )

    # Short form "owner/repo" (when not an existing path)
    if "/" in target and not os.path.exists(target) and not target.startswith("."):
        parts = target.split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            owner, repo_name = parts[0], parts[1].removesuffix(".git")
            if _valid_slug_parts(owner, repo_name):
                return RepoIdentity(
                    provider="github",
                    owner=owner,
                    repo_name=repo_name,
                    full_slug=f"{owner}/{repo_name}",
                    canonical_url=f"https://github.com/{owner}/{repo_name}",
                    avatar_url=f"https://github.com/{owner}.png",
                    target_input=f"https://github.com/{owner}/{repo_name}",
                    is_remote=True,
                )

    # Local Path Handling
    local_p = Path(target).resolve()
    # Check if local directory has a git origin pointing to GitHub or GitLab
    if local_p.is_dir():
        remote_url = _extract_git_remote_url(local_p)
        if remote_url:
            gh_match = _GH_URL_RE.match(remote_url)
            if gh_match and _valid_slug_parts(gh_match.group(1), gh_match.group(2)):
                owner = gh_match.group(1)
                repo_name = gh_match.group(2)
                return RepoIdentity(
                    provider="github",
                    owner=owner,
                    repo_name=repo_name,
                    full_slug=f"{owner}/{repo_name}",
                    canonical_url=f"https://github.com/{owner}/{repo_name}",
                    avatar_url=f"https://github.com/{owner}.png",
                    target_input=str(local_p),
                    is_remote=False,
                )

    # Fallback for generic local paths without git remote
    try:
        owner = getpass.getuser()
    except Exception:
        owner = "local"

    repo_name = local_p.name or "repository"
    return RepoIdentity(
        provider="local",
        owner=owner,
        repo_name=repo_name,
        full_slug=f"{owner}/{repo_name}",
        canonical_url=str(local_p),
        avatar_url=None,
        target_input=str(local_p),
        is_remote=False,
    )
