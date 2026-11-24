"""
TEST MODE (remove before launch): finds random vibe-coded GitHub repositories for the admin to test-grade.

The admin console's "Find & queue 5 random vibe-coded repos" button uses this to build a larger evaluation
dataset. Every slot asks GitHub's repository search for a repo tagged as vibe-coded (topic `vibecoded` or
`vibe-coded`) plus one randomly chosen *type* topic (android, cli, game, chrome-extension, finance, ...), with
0-100 stars, not a fork and not archived. Four slots look for a small repo and one for a slightly larger one.
When a type topic has nothing new, the slot tries another type, then falls back to any vibe-coded repo of that
size. Repos already graded or queued here are skipped. The results are queued as admin test gradings (only the
admin sees them; see web/server.py TEST MODE).

GitHub allows 10 unauthenticated searches a minute, so one click makes at most MAX_QUERIES searches.
GitHub's `size` is the repository's size on GitHub in KB (code, assets and git history), so it is only a rough
guide to how much code there is.
"""

from __future__ import annotations

import random
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urlencode

from ..core.github_client import github_get_json

SEARCH_URL = "https://api.github.com/search/repositories"

# Both spellings mean "made by vibe coding"; search can't OR two topics, so each search picks one.
BASE_TOPICS = ("vibecoded", "vibe-coded")

# Types of project, not only platforms: one is picked at random per slot so a batch covers different kinds.
TYPE_TOPICS = (
    "android", "ios", "mobile", "flutter", "pwa",
    "cli", "terminal", "tui", "command-line",
    "web", "webapp", "website", "nextjs", "react", "vue", "svelte",
    "game", "browser-game", "puzzle", "simulation",
    "desktop", "electron", "tauri", "windows", "macos", "linux",
    "chrome-extension", "browser-extension", "firefox-extension", "vscode-extension", "obsidian-plugin",
    "discord-bot", "telegram-bot", "slack-bot",
    "productivity", "todo", "notes", "finance", "budget", "health", "fitness", "education",
    "music", "audio", "video", "photo", "image-processing",
    "ai", "chatbot", "llm", "automation", "dashboard", "visualization", "tool", "utility",
    "raspberry-pi", "home-automation", "self-hosted", "weather", "calculator", "portfolio",
)

STARS = "0..100"
SIZE_KB = {"small": "1..1500", "larger": "1500..6000"}  # GitHub repo size in KB
MAX_QUERIES = 9

# Lists of links and templates are not apps
_NOT_APPS = {"awesome", "awesome-list", "list", "template", "boilerplate", "starter", "dotfiles", "prompts"}

Fetch = Callable[[str, Dict[str, str]], List[Dict[str, Any]]]


class SearchLimitReached(RuntimeError):
    """GitHub refused the search (rate limit). Wait a minute and try again."""


def github_search(query: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
    """One page (up to 100) of GitHub repository search results. No token: public data only."""
    qs = urlencode({"q": query, "per_page": "100", **params})
    res = github_get_json(f"{SEARCH_URL}?{qs}", timeout=15.0, cache_ttl=120.0)
    if res.rate_limited:
        wait = res.reset_in_sec or 60
        raise SearchLimitReached(f"GitHub's search limit was reached. Wait ~{wait}s and try again.")
    if not res.ok:
        raise RuntimeError(res.error or f"GitHub search failed (HTTP {res.status_code})")
    data = res.data if isinstance(res.data, dict) else {}
    return list(data.get("items") or [])


def _usable(item: Dict[str, Any], skip: set) -> bool:
    name = str(item.get("full_name") or "")
    if not name or name.lower() in skip:
        return False
    if item.get("fork") or item.get("archived") or item.get("disabled") or item.get("private"):
        return False
    if not (item.get("owner") or {}).get("login"):
        return False
    topics = {str(t).lower() for t in item.get("topics") or []}
    repo = name.split("/", 1)[-1].lower()
    return not (topics & _NOT_APPS) and not repo.startswith("awesome")


def find_random_repos(
    exclude: Iterable[str],
    count: int = 5,
    rng: Optional[random.Random] = None,
    fetch: Optional[Fetch] = None,
) -> Dict[str, Any]:
    """
    Picks `count` random vibe-coded repos: count-1 small and one slightly larger, each from a different random
    project type where possible. `exclude` holds "owner/repo" slugs (already graded or queued, or the admin's own).
    Returns {"repos": [...], "notes": [...], "queries": n}.
    """
    rng = rng or random.SystemRandom()
    fetch = fetch or github_search
    skip = {s.lower() for s in exclude}
    types = list(TYPE_TOPICS)
    rng.shuffle(types)
    sizes = ["small"] * max(count - 1, 0) + ["larger"] * min(count, 1)
    repos: List[Dict[str, Any]] = []
    notes: List[str] = []
    queries = 0

    def search(base: str, type_topic: Optional[str], size: str) -> List[Dict[str, Any]]:
        nonlocal queries
        queries += 1
        q = f"topic:{base}" + (f" topic:{type_topic}" if type_topic else "") + \
            f" stars:{STARS} size:{SIZE_KB[size]} fork:false archived:false"
        # Vary the order so repeated clicks don't keep returning the same first page
        params = {"sort": rng.choice(("updated", "stars", "forks")), "order": rng.choice(("desc", "asc"))}
        return [i for i in fetch(q, params) if _usable(i, skip)]

    for slot, size in enumerate(sizes):
        slots_left = len(sizes) - slot
        found = None
        tries = 0
        # Up to two project types per slot, always keeping one search back for this slot's fallback and one for
        # every later slot, so an unlucky early slot can't use up the whole budget.
        while found is None and types and tries < 2 and MAX_QUERIES - queries > slots_left:
            tries += 1
            type_topic = types.pop()
            base = rng.choice(BASE_TOPICS)
            items = search(base, type_topic, size)
            if items:
                found = (rng.choice(items), type_topic, base)
        if found is None and queries < MAX_QUERIES:
            base = rng.choice(BASE_TOPICS)
            items = search(base, None, size)
            if items:
                found = (rng.choice(items), None, base)
        if found is None:
            notes.append(f"No new {size} vibe-coded repo found for one slot.")
            continue
        item, type_topic, base = found
        skip.add(item["full_name"].lower())
        repos.append({
            "full_slug": item["full_name"],
            "url": item.get("html_url") or f"https://github.com/{item['full_name']}",
            "stars": int(item.get("stargazers_count") or 0),
            "size_kb": int(item.get("size") or 0),
            "size_class": size,
            "language": item.get("language") or "",
            "description": (item.get("description") or "")[:200],
            "type_topic": type_topic,
            "base_topic": base,
        })
    return {"repos": repos, "notes": notes, "queries": queries}


# ---------------------------------------------------------------------------------------------------------------
# "Likely good" pick: one well-starred vibe-coded app (TEST MODE, remove before launch)
# ---------------------------------------------------------------------------------------------------------------
GOOD_STARS = "20..5000"
GOOD_SIZE_KB = "1..20000"   # LetsVibe itself caps a repo at 50 MB / 2,000 files
GOOD_TOP_N = 5              # pick at random among the best-starred few not seen yet, so repeat clicks vary
# Guides, courses and link lists collect stars too but are not apps
_NOT_APPS_GOOD = _NOT_APPS | {"guide", "tutorial", "tutorials", "course", "learning-resources", "documentation", "docs",
                              "cheatsheet", "resources", "examples", "beginners"}
_GUIDE_WORDS = ("awesome", "guide", "tutorial", "for-dummies", "for-beginners", "course", "cheatsheet")


def _looks_like_app(item: Dict[str, Any], skip: set) -> bool:
    if not _usable(item, skip) or not item.get("language"):
        return False  # no programming language: documentation, prompts or a link list
    topics = {str(t).lower() for t in item.get("topics") or []}
    name = str(item.get("full_name") or "").split("/", 1)[-1].lower()
    return not (topics & _NOT_APPS_GOOD) and not any(w in name for w in _GUIDE_WORDS)


def find_good_repo(
    exclude: Iterable[str],
    rng: Optional[random.Random] = None,
    fetch: Optional[Fetch] = None,
) -> Dict[str, Any]:
    """
    One vibe-coded repo that is likely to be good: the most-starred apps tagged `vibecoded` or `vibe-coded`
    (20-5000 stars, not a fork, not archived, written in a programming language), skipping anything already graded
    or queued. Two searches (one per topic spelling). Returns {"repos": [one or none], "notes": [...], "queries": 2}.
    """
    rng = rng or random.SystemRandom()
    fetch = fetch or github_search
    skip = {s.lower() for s in exclude}
    pool: Dict[str, Dict[str, Any]] = {}
    for base in BASE_TOPICS:
        q = f"topic:{base} stars:{GOOD_STARS} size:{GOOD_SIZE_KB} fork:false archived:false"
        for item in fetch(q, {"sort": "stars", "order": "desc"}):
            if _looks_like_app(item, skip):
                pool.setdefault(item["full_name"].lower(), {**item, "_base": base})
    best = sorted(pool.values(), key=lambda i: -int(i.get("stargazers_count") or 0))[:GOOD_TOP_N]
    if not best:
        return {"repos": [], "notes": ["Every well-starred vibe-coded app found has already been graded or queued here."],
                "queries": len(BASE_TOPICS)}
    item = rng.choice(best)
    return {"repos": [{
        "full_slug": item["full_name"],
        "url": item.get("html_url") or f"https://github.com/{item['full_name']}",
        "stars": int(item.get("stargazers_count") or 0),
        "size_kb": int(item.get("size") or 0),
        "size_class": "well-starred",
        "language": item.get("language") or "",
        "description": (item.get("description") or "")[:200],
        "type_topic": None,
        "base_topic": item["_base"],
    }], "notes": [], "queries": len(BASE_TOPICS)}
