"""
Application Launcher Icon discovery engine.
Intelligently locates the primary application/program launcher icon within a repository
while filtering out in-app buttons, gameplay sprites, glyphs, and transient build assets.
"""

import re
from pathlib import Path
from typing import List, Optional, Union

# Disqualifier regex patterns: in-app UI widgets, action buttons, glyphs, and transient build assets
DISQUALIFIERS = [
    r"([_\W]|^)(btn|button|arrow|pause|play|stop|next|prev|forward|back|sprite)([_\W]|$)",
    r"([_\W]|^)(close|exit|cancel|nav|menu|search|checkbox|radio|spinner)([_\W]|$)",
    r"([_\W]|^)(thumb|banner|badge|frame|hair|body|beard|stubble|glasses|border)([_\W]|$)",
    r"([_\W]|^)(screenshot|preview|bg|background|glyph|divider|bullet|cursor)([_\W]|$)",
    r"([_\W]|^)(status|toolbar|action|dropdown|indicator|slider)([_\W]|$)",
    r"(intermediates|packaged_res|merge.*Assets|node_modules|\.git|\.venv|site-packages)",
]


def is_disqualified(path_str: str) -> bool:
    """Checks whether a relative path is an in-app widget or disqualified asset."""
    s = path_str.lower().replace("\\", "/")
    return any(re.search(pat, s) for pat in DISQUALIFIERS)


def tracked_files(source_path) -> Optional[List[str]]:
    """Every file git tracks in the repository (images included), or None if it isn't a git checkout.
    The icon search needs this: the grader's own file list holds only the code it reads, never images."""
    import subprocess  # nosec B404 - git/nvidia-smi run with fixed list arguments, never a shell
    try:
        res = subprocess.run(["git", "-C", str(source_path), "ls-files"], capture_output=True, text=True, timeout=10)  # nosec B603 B607 - fixed command, list arguments, no shell
        files = [f for f in res.stdout.splitlines() if f]
        return files or None
    except Exception:
        return None


def discover_launcher_icon_relpath(
    source_path: Optional[Union[Path, str, List[str]]] = None,
    known_files: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Intelligently searches for the primary program launcher icon within a repository.
    Returns the repository-relative path string, or None if no valid launcher icon is found.
    """
    if isinstance(source_path, (list, tuple, set)):
        known_files = list(source_path)
        source_path = None

    root_p = Path(source_path).resolve() if source_path else None
    candidates = []

    # If list of git-indexed files is provided, use it to avoid traversing untracked bloat
    if known_files:
        valid_exts = {".png", ".webp", ".svg", ".ico"}
        file_candidates = [
            (Path(f), f.replace("\\", "/"))
            for f in known_files
            if Path(f).suffix.lower() in valid_exts
        ]
    else:
        if not root_p or not root_p.exists():
            return None
        file_candidates = []
        for ext in ("*.png", "*.webp", "*.svg", "*.ico"):
            for f in root_p.rglob(ext):
                rel = str(f.relative_to(root_p)).replace("\\", "/")
                file_candidates.append((f, rel))

    for f_path, rel in file_candidates:
        if is_disqualified(rel):
            continue

        score = 0
        lower = rel.lower()
        name = Path(rel).stem.lower()

        # Prioritize standard launcher naming
        if name in ("icon", "app_icon", "app", "logo", "launcher"):
            score += 50
        elif "launcher" in name or "icon" in name:
            score += 35
        elif "logo" in name:
            score += 25

        # Location scoring bonuses
        if "mipmap-xxxhdpi" in lower:
            score += 45
        elif "mipmap-xxhdpi" in lower:
            score += 40
        elif "mipmap-xhdpi" in lower:
            score += 35
        elif lower.startswith("packaging/"):
            score += 30
        elif lower.startswith("static/"):
            score += 25
        elif lower.startswith("assets/"):
            score += 20
        elif lower.startswith("public/"):
            score += 20
        elif "/" not in rel:
            # Root file bonus
            score += 30

        # Extension preference
        if Path(rel).suffix.lower() in (".png", ".webp"):
            score += 10

        # Round icon preference for modern Android launchers
        if "launcher_round" in name:
            score += 5

        # Must meet minimum affinity threshold to qualify as an app launcher icon
        if score < 30:
            continue

        candidates.append((score, rel))

    if not candidates:
        return None

    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def get_github_raw_icon_url(owner: str, repo_name: str, relpath: str) -> str:
    """Constructs the canonical GitHub raw content URL for the launcher icon."""
    clean_rel = relpath.lstrip("/").replace("\\", "/")
    return f"https://raw.githubusercontent.com/{owner}/{repo_name}/HEAD/{clean_rel}"


SAVED_ICON_EXTS = {".png": "image/png", ".webp": "image/webp", ".ico": "image/x-icon", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
SAVED_ICON_MAX_BYTES = 512 * 1024


def is_publicly_reachable(url: str) -> bool:
    """True when GitHub serves the file to anyone (the repository is public)."""
    import httpx
    try:
        r = httpx.head(url, timeout=6.0, follow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def resolve_icon_url(owner: str, repo_name: str, source_path, report_dir: Path) -> Optional[str]:
    """
    The project's icon for the leaderboard. Public repositories use GitHub's public file link (shown through the
    same-origin image proxy). Only for private repositories, whose files GitHub won't serve, a copy is saved in the
    project's own report folder (data/reports/<owner>/<repo>/), so deleting the project or account removes it, and
    backups (database only) never hold it. SVG icons are never saved (they can contain script).
    """
    rel = discover_launcher_icon_relpath(source_path, tracked_files(source_path))
    if not rel:
        return None
    url = get_github_raw_icon_url(owner, repo_name, rel)
    if is_publicly_reachable(url):
        return url
    src = Path(source_path) / rel
    ext = src.suffix.lower()
    from .ingest import is_safe_repo_file
    if ext not in SAVED_ICON_EXTS or not is_safe_repo_file(Path(source_path), src) or src.stat().st_size > SAVED_ICON_MAX_BYTES:
        return None
    report_dir.mkdir(parents=True, exist_ok=True)
    for old in report_dir.glob("icon.*"):
        old.unlink()
    (report_dir / f"icon{ext}").write_bytes(src.read_bytes())
    return f"/icons/{owner}/{repo_name}"
