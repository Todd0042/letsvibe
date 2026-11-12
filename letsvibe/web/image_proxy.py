"""
Same-origin image proxy for GitHub avatars and repository icons.

Visitors' browsers never contact GitHub directly (GitHub would otherwise see every visitor's IP
address). Only a strict allowlist of GitHub image URLs is fetched, so the endpoint cannot be used
to make this server request arbitrary addresses. Responses are cached on disk.
"""

import hashlib
import re
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urljoin, urlsplit

import httpx

CACHE_DIR = Path("data/img_cache")
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 6.0
MAX_REDIRECTS = 3

_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPO = r"[A-Za-z0-9._-]{1,100}"
_ALLOWED = [
    # https://github.com/<owner>.png (redirects to avatars.githubusercontent.com)
    re.compile(rf"^https://github\.com/{_NAME}\.png(\?size=\d{{1,4}})?$"),
    re.compile(r"^https://avatars\.githubusercontent\.com/[A-Za-z0-9/_?=&.%-]{1,200}$"),
    # https://raw.githubusercontent.com/<owner>/<repo>/HEAD/<path to an image>
    re.compile(rf"^https://raw\.githubusercontent\.com/{_NAME}/{_REPO}/[A-Za-z0-9._/-]{{1,300}}\.(png|jpe?g|gif|webp|svg|ico)$", re.I),
]
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/x-icon", "image/vnd.microsoft.icon"}
# SVG can carry script; it is served with a locked-down CSP and as an attachment-safe image type only.
_SVG = "image/svg+xml"


def is_allowed(url: str) -> bool:
    if not url or len(url) > 500 or ".." in url:
        return False
    return any(rx.match(url) for rx in _ALLOWED)


def _cache_paths(url: str) -> Tuple[Path, Path]:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{key}.bin", CACHE_DIR / f"{key}.type"


def owner_of(url: str) -> str:
    """GitHub account an image URL belongs to ('' when it cannot be told, e.g. numeric avatar URLs)."""
    m = re.match(rf"^https://(?:github\.com/({_NAME})\.png|raw\.githubusercontent\.com/({_NAME})/)", url or "")
    return (m.group(1) or m.group(2)).lower() if m else ""


def purge_url(url: str) -> None:
    for p in _cache_paths(url):
        p.unlink(missing_ok=True)
    _cache_paths(url)[0].with_suffix(".owner").unlink(missing_ok=True)


def purge_owner(owner: str) -> int:
    """Deletes every cached image tagged with this GitHub owner. Returns files removed."""
    owner = (owner or "").strip().lower()
    if not owner or not CACHE_DIR.exists():
        return 0
    removed = 0
    for tag in CACHE_DIR.glob("*.owner"):
        if tag.read_text().strip() == owner:
            for ext in (".bin", ".type", ".owner"):
                f = tag.with_suffix(ext)
                if f.exists():
                    f.unlink()
                    removed += 1
    return removed


def fetch_image(url: str) -> Optional[Tuple[bytes, str]]:
    """Returns (bytes, content_type) for an allowed image URL, from cache when possible; None otherwise."""
    if not is_allowed(url):
        return None
    data_path, type_path = _cache_paths(url)
    if data_path.exists() and type_path.exists():
        return data_path.read_bytes(), type_path.read_text().strip()

    current = url
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": "LetsVibe-image-proxy"}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            resp = client.get(current)
            if resp.status_code in (301, 302, 303, 307, 308):
                nxt = urljoin(current, resp.headers.get("location", ""))
                if not is_allowed(nxt):
                    return None
                current = nxt
                continue
            if resp.status_code != 200:
                return None
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype not in _IMAGE_TYPES and ctype != _SVG:
                return None
            body = resp.content
            if len(body) > MAX_BYTES:
                return None
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            data_path.write_bytes(body)
            type_path.write_text(ctype)
            data_path.with_suffix(".owner").write_text(owner_of(url))
            return body, ctype
    return None


def host_of(url: str) -> str:
    return urlsplit(url).netloc
