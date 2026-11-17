"""Free deterministic market-saturation grounding via GitHub Search & npm/PyPI registries."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List

_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "with", "that", "this", "from", "into",
    "your", "my", "our", "app", "tool", "platform", "system", "using", "based",
    "powered", "simple", "easy", "fast", "modern", "smart", "best", "make", "build",
    "create", "helps", "users", "people", "like", "just", "only", "more", "very",
    "want", "need", "would", "could", "should", "will", "have", "has", "are", "is",
}


def extract_keywords(pitch: str, twist: str = "", limit: int = 4) -> List[str]:
    text = f"{pitch} {twist}".lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9+-]{2,}", text)
    seen: List[str] = []
    for tok in tokens:
        if tok in _STOPWORDS or tok.isdigit():
            continue
        if tok not in seen:
            seen.append(tok)
        if len(seen) >= limit:
            break
    return seen or ["app"]


def _get_json(url: str, timeout: float = 4.0) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "LetsVibe-RealityCheck/1.0 (+https://letsviberc.todd0042.dev)",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - fixed public registry URLs only
        return json.loads(resp.read().decode("utf-8", errors="replace") or "{}")


def gather_market_grounding(pitch: str, platform: str = "Web", twist: str = "") -> Dict[str, Any]:
    """Queries public GitHub Search and npm registry for real competing projects."""
    keywords = extract_keywords(pitch, twist, limit=4)
    query_str = " ".join(keywords)
    out: Dict[str, Any] = {
        "keywords": keywords,
        "platform": platform,
        "github_total_count": 0,
        "github_top_repos": [],
        "npm_total_count": 0,
        "npm_top_packages": [],
    }

    try:
        q = urllib.parse.quote_plus(query_str)
        gh = _get_json(f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=6")
        out["github_total_count"] = int(gh.get("total_count") or 0)
        repos = []
        for item in (gh.get("items") or [])[:6]:
            repos.append({
                "full_name": str(item.get("full_name") or ""),
                "stars": int(item.get("stargazers_count") or 0),
                "language": str(item.get("language") or ""),
                "description": str(item.get("description") or "")[:160],
                "url": str(item.get("html_url") or ""),
            })
        out["github_top_repos"] = repos
    except Exception:
        pass

    if platform.lower() in ("web", "saas / api", "cli", "desktop"):
        try:
            nq = urllib.parse.quote_plus(query_str)
            npm = _get_json(f"https://registry.npmjs.org/-/v1/search?text={nq}&size=5")
            out["npm_total_count"] = int(npm.get("total") or 0)
            pkgs = []
            for obj in (npm.get("objects") or [])[:5]:
                pkg = obj.get("package") or {}
                pkgs.append({
                    "name": str(pkg.get("name") or ""),
                    "description": str(pkg.get("description") or "")[:140],
                })
            out["npm_top_packages"] = pkgs
        except Exception:
            pass

    return out
