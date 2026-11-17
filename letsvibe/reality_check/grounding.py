"""Free deterministic market-saturation grounding across GitHub, npm, App Store, Hacker News & Stack Overflow."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "with", "that", "this", "from", "into",
    "your", "my", "our", "app", "tool", "platform", "system", "using", "based",
    "powered", "simple", "easy", "fast", "modern", "smart", "best", "make", "build",
    "create", "helps", "users", "people", "like", "just", "only", "more", "very",
    "want", "need", "would", "could", "should", "will", "have", "has", "are", "is",
    "offline", "online", "small", "big", "shiny", "dark", "mode", "without", "requiring",
    "desktop", "mobile", "android", "ios", "web", "saas", "cli", "software", "program",
    "wedding", "event", "djs", "techs", "developers", "teams", "live", "real", "time",
    "local", "network", "over", "one", "click", "five", "minute", "minutes", "daily",
    "common", "expensive", "cheap", "free", "paid", "new", "better",
    "zero-install", "self-hosted", "built-in", "low-latency", "unlimited", "anywhere",
    "screens", "screen", "practice", "private", "before", "stage", "party",
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
    return seen or ["software"]


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


def _fetch_github(short_q: str) -> Dict[str, Any]:
    try:
        q = urllib.parse.quote_plus(short_q)
        gh = _get_json(f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=6")
        repos = [
            {
                "full_name": str(item.get("full_name") or ""),
                "stars": int(item.get("stargazers_count") or 0),
                "language": str(item.get("language") or ""),
                "description": str(item.get("description") or "")[:160],
            }
            for item in (gh.get("items") or [])[:6]
        ]
        return {"github_total_count": int(gh.get("total_count") or 0), "github_top_repos": repos}
    except Exception:
        return {"github_total_count": 0, "github_top_repos": []}


def _fetch_npm(short_q: str) -> Dict[str, Any]:
    try:
        nq = urllib.parse.quote_plus(short_q)
        npm = _get_json(f"https://registry.npmjs.org/-/v1/search?text={nq}&size=5")
        pkgs = [
            {
                "name": str((obj.get("package") or {}).get("name") or ""),
                "description": str((obj.get("package") or {}).get("description") or "")[:140],
            }
            for obj in (npm.get("objects") or [])[:5]
        ]
        return {"npm_total_count": int(npm.get("total") or 0), "npm_top_packages": pkgs}
    except Exception:
        return {"npm_total_count": 0, "npm_top_packages": []}


def _fetch_app_store(short_q: str, platform: str) -> Dict[str, Any]:
    try:
        entity = "macSoftware" if platform.lower() == "desktop" else "software"
        aq = urllib.parse.quote_plus(short_q)
        res = _get_json(f"https://itunes.apple.com/search?term={aq}&entity={entity}&limit=5")
        apps = [
            {
                "name": str(item.get("trackName") or ""),
                "price": str(item.get("formattedPrice") or "Free"),
                "rating": round(float(item.get("averageUserRating") or 0.0), 1),
                "reviews": int(item.get("userRatingCount") or 0),
            }
            for item in (res.get("results") or [])[:5]
            if item.get("trackName")
        ]
        return {"app_store_hits": apps}
    except Exception:
        return {"app_store_hits": []}


def _fetch_hacker_news(short_q: str) -> Dict[str, Any]:
    try:
        hq = urllib.parse.quote_plus(short_q)
        hn = _get_json(f"https://hn.algolia.com/api/v1/search?query={hq}&tags=story&hitsPerPage=5")
        stories = [
            {
                "title": str(h.get("title") or "")[:120],
                "points": int(h.get("points") or 0),
                "comments": int(h.get("num_comments") or 0),
            }
            for h in (hn.get("hits") or [])[:5]
            if h.get("title")
        ]
        return {"hn_total_hits": int(hn.get("nbHits") or 0), "hn_stories": stories}
    except Exception:
        return {"hn_total_hits": 0, "hn_stories": []}


def _fetch_stack_overflow(short_q: str) -> Dict[str, Any]:
    try:
        sq = urllib.parse.quote_plus(short_q)
        so = _get_json(
            f"https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=votes&q={sq}&site=stackoverflow&pagesize=4"
        )
        qs = [
            {"title": str(item.get("title") or "")[:120], "score": int(item.get("score") or 0)}
            for item in (so.get("items") or [])[:4]
            if item.get("title")
        ]
        return {"so_questions": qs}
    except Exception:
        return {"so_questions": []}


def gather_market_grounding(pitch: str, platform: str = "Web", twist: str = "") -> Dict[str, Any]:
    """Concurrently queries GitHub, npm, Apple App Store, Hacker News, and Stack Overflow."""
    keywords = extract_keywords(pitch, twist, limit=4)
    # Use a tight 2-word core query so AND-based search APIs don't artificially under-count
    short_q = " ".join(keywords[:2])
    out: Dict[str, Any] = {"keywords": keywords, "query": short_q, "platform": platform}

    with ThreadPoolExecutor(max_workers=5) as pool:
        f_gh = pool.submit(_fetch_github, short_q)
        f_npm = pool.submit(_fetch_npm, short_q)
        f_app = pool.submit(_fetch_app_store, short_q, platform)
        f_hn = pool.submit(_fetch_hacker_news, short_q)
        f_so = pool.submit(_fetch_stack_overflow, short_q)
        for fut in (f_gh, f_npm, f_app, f_hn, f_so):
            out.update(fut.result())

    return out
