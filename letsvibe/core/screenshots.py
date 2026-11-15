"""
Repository screenshot discovery.
Locates up to N screenshot URLs (raw.githubusercontent.com links only, never storing image files)
from README embeds first, then common screenshot folder and OS naming conventions.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import unquote

import httpx

from .icons import tracked_files

_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

_EXCLUDED_PATH = re.compile(
    r"(^|/)(node_modules|\.git|\.venv|venv|dist|build|__pycache__|third[_-]?party|vendor|deps|external|submodules|"
    r"benchmarks?|fixtures?|testdata|sprites?|textures?|icons?|mipmap-[^/]*|drawable-[^/]*)/|"
    r"([_\W]|^)(favicon|avatar|badge|shields|ko-fi|kofi|buymeacoffee|sponsor|banner|thumb|glyph|cursor|icon|logo)([_\W]|$)",
    re.I,
)

_SHOT_FOLDER = re.compile(
    r"(^|/)(screenshots?|docs/screenshots?|docs/images?|docs/media|\.github/assets)/|"
    r"fastlane/metadata/android/.+/images/|"
    r"^(previews?|media)/",
    re.I,
)

_ANDROID_ASSET_FOLDER = re.compile(
    r"^(app/src/main/assets|app/src/main/res/drawable(-[^/]+)?|assets/images)/",
    re.I,
)

_ASSET_ROOT_FOLDER = re.compile(
    r"(^|/)(assets|app/src/main/assets|static|public|resources|res/drawable)/",
    re.I,
)

_ASSET_BG = re.compile(
    r"(^|/)(backgrounds?|backdrops?|scenes?|stages?|environments?)/|"
    r"(^|[/_-])(background|backdrop|bg)([._-]|$)",
    re.I,
)

_ASSET_SPRITE = re.compile(
    r"(^|/)(sprites?|battlers?|units?|enemies?|heroes?|players?|actors?|npcs?)/|"
    r"(^|[/_-])(sprite|battler|enemy|hero|player)([._-]|$)",
    re.I,
)

_ASSET_PORTRAIT = re.compile(
    r"(^|/)(portraits?|characters?)/|"
    r"(^|[/_-])(portrait|char)([._-]|$)",
    re.I,
)

_ASSET_EXCLUDED = re.compile(
    r"(^|/)(node_modules|\.git|\.venv|venv|dist|build|__pycache__|third[_-]?party|vendor|deps|external|submodules|"
    r"benchmarks?|fixtures?|testdata|mipmap-[^/]*|tiles?|fonts?|audio|sfx|music)/|"
    r"([_\W]|^)(favicon|avatar|badge|shields|ko-fi|kofi|buymeacoffee|sponsor|banner|thumb|glyph|cursor|icon|logo|ic_|sheet|atlas|tileset|placeholder|template|dummy|blank)([_\W]|$)",
    re.I,
)

_SHOT_NAME = re.compile(
    r"^(screenshot|screen[_-]?shot|scr[_-]|screencast|preview|demo|capture|snip)",
    re.I,
)


def _to_raw_github_url(owner: str, repo_name: str, relpath: str) -> str:
    clean = relpath.lstrip("./")
    return f"https://raw.githubusercontent.com/{owner}/{repo_name}/HEAD/{clean}"


def _extract_readme_images_from_text(text: str) -> List[str]:
    found: List[str] = []
    # Markdown ![alt](url) and HTML <img src="url">
    for m in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)|<img[^>]+src=[\"']([^\"']+)[\"']", text, re.I):
        url = (m.group(1) or m.group(2) or "").strip()
        if url:
            found.append(url)
    return found


def _extract_readme_images(source_path: Path) -> List[str]:
    readme = None
    for candidate in sorted(source_path.iterdir()):
        if candidate.is_file() and candidate.name.lower().startswith("readme") and candidate.suffix.lower() in (".md", ".markdown", ""):
            readme = candidate
            break
    if not readme:
        return []
    try:
        text = readme.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    return _extract_readme_images_from_text(text)


def _select_screenshot_urls(
    owner: str,
    repo_name: str,
    files: List[str],
    readme_images: List[str],
    limit: int = 4,
) -> List[str]:
    by_lower = {f.lower(): f for f in files}
    results: List[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        if url not in seen and len(results) < limit:
            seen.add(url)
            results.append(url)

    # Pass 1: README image embeds (highest signal)
    raw_prefix = f"https://raw.githubusercontent.com/{owner}/{repo_name}/".lower()
    blob_prefix = f"https://github.com/{owner}/{repo_name}/blob/".lower()
    for src in readme_images:
        low = src.lower().split("?", 1)[0].split("#", 1)[0]
        if not low.endswith(_IMG_EXTS):
            continue
        if _EXCLUDED_PATH.search(low):
            continue
        if low.startswith(raw_prefix):
            _add(src.split("#", 1)[0])
        elif low.startswith(blob_prefix):
            rel = src[len(f"https://github.com/{owner}/{repo_name}/blob/"):]
            _add(f"https://raw.githubusercontent.com/{owner}/{repo_name}/{rel.split('#', 1)[0]}")
        elif not re.match(r"^https?://", low):
            rel_clean = unquote(src.split("?", 1)[0].split("#", 1)[0]).lstrip("./")
            if not files:
                _add(_to_raw_github_url(owner, repo_name, rel_clean))
            else:
                matched = rel_clean if rel_clean in files else by_lower.get(rel_clean.lower())
                if matched:
                    _add(_to_raw_github_url(owner, repo_name, matched))

    if len(results) >= limit:
        return results

    # Pass 2: Tracked repo files matching common screenshot folders or OS naming conventions
    scored: List[tuple[int, str]] = []
    for rel in files:
        low = rel.lower()
        if not low.endswith(_IMG_EXTS):
            continue
        if _EXCLUDED_PATH.search(low):
            continue
        score = 0
        filename = low.rsplit("/", 1)[-1]
        if _SHOT_FOLDER.search(low):
            score += 4
        if _SHOT_NAME.search(filename):
            score += 3
        if score > 0:
            scored.append((score, rel))

    scored.sort(key=lambda item: (-item[0], item[1].lower()))
    for _, rel in scored:
        _add(_to_raw_github_url(owner, repo_name, rel))
        if len(results) >= limit:
            break

    return results


def discover_screenshot_urls(
    owner: str,
    repo_name: str,
    source_path: Union[Path, str],
    limit: int = 4,
) -> List[str]:
    """Returns up to `limit` raw.githubusercontent.com URLs for project screenshots."""
    root = Path(source_path)
    if not owner or not repo_name or not root.is_dir():
        return []

    files = tracked_files(root) or []
    readme_images = _extract_readme_images(root)
    return _select_screenshot_urls(owner, repo_name, files, readme_images, limit=limit)


def discover_asset_preview_from_files(
    owner: str,
    repo_name: str,
    files: List[str],
    max_bg: int = 2,
    max_chars: int = 4,
) -> Dict[str, List[str]]:
    """Extracts up to `max_bg` backgrounds and `max_chars` (2 per background) sprites/portraits from repo asset folders."""
    if not owner or not repo_name or not files:
        return {}

    bgs: List[str] = []
    sprites: List[str] = []
    portraits: List[str] = []
    for rel in sorted(files, key=str.lower):
        low = rel.lower()
        if not low.endswith(_IMG_EXTS):
            continue
        if _ASSET_EXCLUDED.search(low):
            continue
        if not _ASSET_ROOT_FOLDER.search(low):
            continue
        if _ASSET_SPRITE.search(low) and len(sprites) < max_chars:
            sprites.append(_to_raw_github_url(owner, repo_name, rel))
        elif _ASSET_BG.search(low) and len(bgs) < max_bg:
            bgs.append(_to_raw_github_url(owner, repo_name, rel))
        elif _ASSET_PORTRAIT.search(low) and len(portraits) < max_chars:
            portraits.append(_to_raw_github_url(owner, repo_name, rel))
        if len(bgs) >= max_bg and len(sprites) >= max_chars:
            break

    chars = sprites if sprites else portraits
    if not bgs and not chars:
        return {}
    return {"backgrounds": bgs, "characters": chars}


def discover_android_asset_urls_from_files(
    owner: str,
    repo_name: str,
    files: List[str],
    limit: int = 4,
) -> List[str]:
    """Fallback: non-icon raster images from Android assets/drawable folders (excluding third-party/ic_)."""
    if not owner or not repo_name or not files:
        return []
    urls: List[str] = []
    for rel in sorted(files, key=str.lower):
        low = rel.lower()
        if not low.endswith(_IMG_EXTS):
            continue
        if _ASSET_EXCLUDED.search(low):
            continue
        filename = low.rsplit("/", 1)[-1]
        if filename.startswith("ic_") or not _ANDROID_ASSET_FOLDER.search(low):
            continue
        urls.append(_to_raw_github_url(owner, repo_name, rel))
        if len(urls) >= limit:
            break
    return urls


def discover_visuals_local(
    owner: str,
    repo_name: str,
    source_path: Union[Path, str],
    limit: int = 4,
) -> tuple[List[str], Dict[str, List[str]]]:
    """Local repo discovery: (1) screenshots, (2) asset scene preview, (3) Android image assets."""
    root = Path(source_path)
    if not owner or not repo_name or not root.is_dir():
        return [], {}
    files = tracked_files(root) or []
    shots = _select_screenshot_urls(owner, repo_name, files, _extract_readme_images(root), limit=limit)
    if shots:
        return shots, {}
    preview = discover_asset_preview_from_files(owner, repo_name, files)
    if preview:
        return [], preview
    return discover_android_asset_urls_from_files(owner, repo_name, files, limit=limit), {}


def discover_asset_preview(
    owner: str,
    repo_name: str,
    source_path: Union[Path, str],
    max_bg: int = 2,
    max_chars: int = 4,
) -> Dict[str, List[str]]:
    root = Path(source_path)
    if not root.is_dir():
        return {}
    return discover_asset_preview_from_files(owner, repo_name, tracked_files(root) or [], max_bg=max_bg, max_chars=max_chars)


def discover_visuals_remote(
    owner: str,
    repo_name: str,
    limit: int = 4,
    token: Optional[str] = None,
) -> tuple[List[str], Dict[str, List[str]]]:
    """Fetches GitHub tree + README remotely; returns (screenshots, asset_preview)."""
    if not owner or not repo_name:
        return [], {}

    gh_token = token or os.getenv("GITHUB_TOKEN") or ""
    api_headers = {"Accept": "application/vnd.github+json", "User-Agent": "letsvibe-screenshots/1.0"}
    if gh_token:
        api_headers["Authorization"] = f"Bearer {gh_token}"

    files: List[str] = []
    readme_rel = "README.md"
    readme_text = ""

    with httpx.Client(timeout=10.0, follow_redirects=True) as client:
        try:
            tree_res = client.get(
                f"https://api.github.com/repos/{owner}/{repo_name}/git/trees/HEAD?recursive=1",
                headers=api_headers,
            )
            if tree_res.status_code == 200:
                data = tree_res.json()
                files = [
                    item["path"]
                    for item in data.get("tree", [])
                    if isinstance(item, dict) and item.get("type") == "blob" and item.get("path")
                ]
                for f in sorted(files):
                    base = f.rsplit("/", 1)[-1].lower()
                    if "/" not in f and base in ("readme.md", "readme.markdown", "readme"):
                        readme_rel = f
                        break
        except httpx.HTTPError:
            pass

        try:
            raw_res = client.get(
                f"https://raw.githubusercontent.com/{owner}/{repo_name}/HEAD/{readme_rel}",
                headers={"User-Agent": "letsvibe-screenshots/1.0"},
            )
            if raw_res.status_code == 200:
                readme_text = raw_res.text
        except httpx.HTTPError:
            pass

    readme_images = _extract_readme_images_from_text(readme_text) if readme_text else []
    shots = _select_screenshot_urls(owner, repo_name, files, readme_images, limit=limit)
    if shots:
        return shots, {}
    preview = discover_asset_preview_from_files(owner, repo_name, files)
    if preview:
        return [], preview
    return discover_android_asset_urls_from_files(owner, repo_name, files, limit=limit), {}


def discover_screenshot_urls_remote(
    owner: str,
    repo_name: str,
    limit: int = 4,
    token: Optional[str] = None,
) -> List[str]:
    """Fetches a GitHub repo tree + README remotely to discover up to `limit` screenshot URLs."""
    shots, _ = discover_visuals_remote(owner, repo_name, limit=limit, token=token)
    return shots


def backfill_missing_screenshots(
    db_path: Optional[Path] = None,
    limit: int = 4,
    token: Optional[str] = None,
    replace_existing: bool = False,
) -> Dict[str, Any]:
    """Populates or replaces `screenshots` / `asset_preview` on existing GitHub reports."""
    from ..storage.db import list_github_projects_missing_screenshots, update_report_screenshots

    targets = list_github_projects_missing_screenshots(db_path=db_path, only_missing=not replace_existing)
    updated = 0
    skipped = 0
    results: List[Dict[str, Any]] = []

    for item in targets:
        urls, preview = discover_visuals_remote(item["owner"], item["repo_name"], limit=limit, token=token)
        if urls or preview:
            update_report_screenshots(item["full_slug"], urls, asset_preview=preview, db_path=db_path)
            updated += 1
            results.append({
                "full_slug": item["full_slug"],
                "count": len(urls),
                "asset_preview": bool(preview),
            })
        else:
            if replace_existing:
                update_report_screenshots(item["full_slug"], [], asset_preview={}, db_path=db_path)
            skipped += 1

    return {
        "checked": len(targets),
        "updated": updated,
        "skipped": skipped,
        "results": results,
    }


if __name__ == "__main__":
    import json as _json
    summary = backfill_missing_screenshots(replace_existing=True)
    print(_json.dumps(summary, indent=2))
