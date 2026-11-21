"""
SSRF-safe reference context validator & extractor for multi-repo / header submissions.

Supports:
  - Uploaded local header/doc files (plain UTF-8 text, <= 64 KB each)
  - Public HTTPS URLs (single header/doc files <= 128 KB, or shallow git repo
    clones extracting README + public headers/interfaces only)

Never allows local server filesystem paths or private/loopback/link-local IPs.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import socket
import subprocess  # nosec B404 - fixed git list args only
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List

import httpx

MAX_REFERENCES = 5
MAX_UPLOAD_BYTES = 65536
MAX_FETCH_BYTES = 131072
MAX_TOTAL_REF_CHARS = 12000

ALLOWED_TEXT_EXT = (".h", ".hpp", ".hh", ".hxx", ".d.ts", ".pyi", ".idl", ".md", ".txt", ".rst")
HEADER_EXT = (".h", ".hpp", ".hh", ".hxx", ".d.ts", ".pyi", ".idl")


def validate_safe_public_https_url(raw_url: str) -> str:
    """Validates that raw_url is public HTTPS and does not resolve to loopback/private/link-local IPs."""
    url = (raw_url or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc or not parsed.hostname:
        raise ValueError("Reference URLs must use https://")
    host = parsed.hostname.strip().lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        raise ValueError("Reference URL host is not allowed.")

    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve reference host {host}: {exc}") from exc

    if not infos:
        raise ValueError(f"Could not resolve reference host {host}.")

    for info in infos:
        ip_str = info[4][0].split("%")[0]
        ip = ipaddress.ip_address(ip_str)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise ValueError(f"Reference URL resolves to disallowed IP range ({ip_str}).")
    return url


def _github_blob_to_raw(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname and parsed.hostname.lower() in ("github.com", "www.github.com"):
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 5 and parts[2] == "blob":
            owner, repo, _, branch = parts[:4]
            rest = "/".join(parts[4:])
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rest}"
    return url


def _fetch_https_text(url: str) -> str:
    cur = _github_blob_to_raw(validate_safe_public_https_url(url))
    with httpx.Client(timeout=10.0, follow_redirects=False) as client:
        for _ in range(3):
            validate_safe_public_https_url(cur)
            resp = client.get(cur, headers={"User-Agent": "LetsVibe-RefFetcher"})
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                cur = urllib.parse.urljoin(cur, resp.headers["location"])
                continue
            resp.raise_for_status()
            raw = resp.content[: MAX_FETCH_BYTES + 1]
            if len(raw) > MAX_FETCH_BYTES:
                raw = raw[:MAX_FETCH_BYTES]
            if b"\x00" in raw:
                raise ValueError("Reference URL returned binary content; only text headers/docs are allowed.")
            return raw.decode("utf-8", errors="replace")
    raise ValueError("Too many redirects fetching reference URL.")


def _digest_readme_for_context(md_text: str, max_chars: int = 1500) -> str:
    """Extracts title, 1-line summary, and prioritized API/WebSocket route endpoints from a README."""
    lines = (md_text or "").splitlines()
    summary_lines: List[str] = []
    route_lines: List[str] = []
    fallback_lines: List[str] = []
    in_toc = False

    for raw in lines:
        s = raw.strip()
        low = s.lower()
        if not s:
            continue
        if s.startswith("<") or s.startswith("[![") or s.startswith("![") or s == "---":
            continue
        if low.startswith("## ") and "table of contents" in low:
            in_toc = True
            continue
        if in_toc:
            if s.startswith("#"):
                in_toc = False
            else:
                continue
        if s.startswith("# ") and not summary_lines:
            summary_lines.append(s)
        elif (
            len(summary_lines) < 2
            and not s.startswith("#")
            and not s.startswith("|")
            and not s.startswith(("🤖", "🌐"))
        ):
            summary_lines.append(s.lstrip("> ").strip())
        elif re.search(r"\b(GET|POST|PUT|PATCH|DELETE|WS)\s+/[A-Za-z0-9_{}/.-]*", s):
            if s not in route_lines and len(route_lines) < 16:
                route_lines.append(s)
        elif re.search(r"`/[a-z0-9_{}/.-]+`", s):
            if s not in fallback_lines and len(fallback_lines) < 6:
                fallback_lines.append(s)

    endpoints = route_lines if route_lines else fallback_lines
    parts: List[str] = []
    if summary_lines:
        parts.append("Summary:\n" + "\n".join(summary_lines[:2]))
    if endpoints:
        parts.append("Endpoints / Interfaces:\n" + "\n".join(endpoints))
    out = "\n\n".join(parts).strip()
    return (out or md_text)[:max_chars]


def _extract_repo_headers(repo_url: str) -> str:
    validate_safe_public_https_url(repo_url)
    tmp = tempfile.mkdtemp(prefix="letsvibe_ref_")
    try:
        res = subprocess.run(  # nosec B603 B607 - fixed git args
            [
                "git", "clone", "--depth", "1", "--filter=blob:none", "--no-recurse-submodules",
                "-c", "core.hooksPath=/dev/null", "-c", "core.symlinks=false",
                repo_url, tmp,
            ],
            capture_output=True,
            text=True,
            timeout=45,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if res.returncode != 0:
            return f"[Could not clone reference repo {repo_url}: {res.stderr.strip()[:160]}]"
        root = Path(tmp)
        snippets: List[str] = []
        used = 0
        # README digest first (summary + endpoints, stripped of banners/badges/TOC)
        for p in sorted(root.iterdir()):
            if p.is_file() and p.name.lower().startswith("readme") and used < 1500:
                raw_txt = p.read_text(encoding="utf-8", errors="replace")
                txt = _digest_readme_for_context(raw_txt, max_chars=1200)
                snippets.append(f"--- {p.name} (digest) ---\n{txt}")
                used += len(txt)
                break
        # Public headers / interface files
        for p in sorted(root.rglob("*")):
            if used >= 5500:
                break
            if not p.is_file() or any(part.startswith(".") for part in p.relative_to(root).parts):
                continue
            if p.name.lower().endswith(HEADER_EXT):
                txt = p.read_text(encoding="utf-8", errors="replace")[:2000]
                if "\x00" not in txt:
                    rel = p.relative_to(root).as_posix()
                    snippets.append(f"--- {rel} ---\n{txt}")
                    used += len(txt)
        return "\n".join(snippets) or "[No README or header files found in reference repo]"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def validate_submission_references(refs: Any) -> List[Dict[str, str]]:
    """Validates and normalizes user-submitted reference contexts (at enqueue time)."""
    if not refs:
        return []
    if not isinstance(refs, list):
        raise ValueError("references must be a list.")
    if len(refs) > MAX_REFERENCES:
        raise ValueError(f"Maximum {MAX_REFERENCES} reference contexts allowed.")

    cleaned: List[Dict[str, str]] = []
    for item in refs:
        if not isinstance(item, dict):
            continue
        rtype = str(item.get("type") or "url").strip().lower()
        role = str(item.get("role") or "").strip()[:200]
        if rtype == "file":
            name = Path(str(item.get("name") or "header.h")).name[:100]
            content = str(item.get("content") or "")
            if not content.strip():
                continue
            if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
                raise ValueError(f"Uploaded file '{name}' exceeds {MAX_UPLOAD_BYTES // 1024} KB limit.")
            if "\x00" in content:
                raise ValueError(f"Uploaded file '{name}' appears to be binary.")
            cleaned.append({"type": "file", "name": name, "content": content, "role": role})
        else:
            url = validate_safe_public_https_url(str(item.get("url") or ""))
            cleaned.append({"type": "url", "url": url, "role": role})
    return cleaned


def _format_ref_text(label: str, text: str) -> str:
    """Summarizes C/C++ headers into compact capability + symbol lists; keeps short prose/README refs intact."""
    from ..analyzers.host_headers import HEADER_EXTS, extract_header_api_summary

    if label.lower().endswith(HEADER_EXTS):
        summary = extract_header_api_summary(Path(label).name, text)
        if summary["symbols"]:
            caps = ", ".join(summary["capabilities"]) or "General C/C++ API"
            syms = ", ".join(summary["symbols"][:20])
            return f"Host-provided capabilities: {caps}\nAPI symbols: {syms}"
    if "readme" in label.lower() or label.lower().endswith(".md"):
        return _digest_readme_for_context(text, max_chars=1200)
    return text[:4000]


def extract_reference_context(refs: List[Dict[str, Any]]) -> str:
    """Builds a bounded HOST / FRAMEWORK REFERENCE CONTEXT block for the grader."""
    if not refs:
        return ""
    blocks: List[str] = []
    total = 0
    for idx, item in enumerate(refs[:MAX_REFERENCES], start=1):
        if total >= MAX_TOTAL_REF_CHARS:
            break
        role = str(item.get("role") or "Host/Framework Reference").strip()
        if item.get("type") == "file":
            name = str(item.get("name") or f"ref_{idx}.h")
            txt = _format_ref_text(name, str(item.get("content") or ""))
            blk = f"[Reference {idx}: Uploaded file `{name}` — Role: {role}]\n{txt}"
        else:
            url = str(item.get("url") or "")
            path_l = urllib.parse.urlparse(url).path.lower()
            try:
                if path_l.endswith(ALLOWED_TEXT_EXT) or "/raw/" in path_l or "raw.githubusercontent.com" in url.lower():
                    txt = _format_ref_text(path_l, _fetch_https_text(url))
                else:
                    txt = _extract_repo_headers(url)[:4000]
            except Exception as exc:
                txt = f"[Could not fetch reference {url}: {exc}]"
            blk = f"[Reference {idx}: {url} — Role: {role}]\n{txt}"
        blocks.append(blk)
        total += len(blk)
    return "\n\n".join(blocks)[:MAX_TOTAL_REF_CHARS]
