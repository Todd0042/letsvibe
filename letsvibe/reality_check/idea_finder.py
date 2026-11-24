"""Idea Finder: brainstorms niche ideas and recommends one only if Reality Check finds it sellable."""

from __future__ import annotations

import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .sellability import sellability

_TTL_SECONDS = 3 * 24 * 3600  # 3 days

_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "with", "that", "this", "from", "into",
    "your", "my", "our", "app", "tool", "platform", "system", "using", "based",
    "simple", "easy", "fast", "modern", "smart", "best", "make", "build", "local",
    "offline", "desktop", "mobile", "web", "saas", "cli", "software", "controller",
    "manager", "auditor", "tracker", "sync", "cloud", "internet", "users", "small",
}

_ANGLE_LENSES = [
    "offline-first local-network field tool (works where venue/jobsite Wi-Fi is dead)",
    "one-time-license desktop utility replacing a bloated $49/mo cloud subscription",
    "micro-B2B compliance, inspection, or audit-trail logbook for solo operators",
    "hardware / sensor / audio / peripheral companion app with zero-config setup",
    "automated quote, scheduling, or client-intake workflow for small local crews",
    "live event / tournament / league / club operations dashboard for organizers",
    "niche inventory, calibration, or maintenance tracker for specialized gear",
    "local-first media, rehearsal, or live-cue controller with instant hardware/mobile remote",
]

_OPEN_DOMAINS = [
    "independent automotive & motorsports shops",
    "wedding, mobile DJ & small live-event crews",
    "woodworking, CNC, 3D-print & maker workshops",
    "youth sports leagues, bowling alleys & local trivia hosts",
    "home inspectors, HVAC techs & mobile repair trades",
    "community theater, church AV & rehearsal studios",
    "small farms, greenhouses & local CSA co-ops",
    "marine, RV & off-grid equipment maintenance",
    "independent veterinary, pet grooming & boarding kennels",
    "local bakery, catering & food-truck prep kitchens",
    "commercial diving, arborist & tree-care rigging crews",
    "independent locksmith, safe & access-control technicians",
    "school band, music-lesson & instrument-repair shops",
    "local auction houses, estate-sale & consignment shops",
    "printing, screen-printing & embroidery sign shops",
    "property-preservation, pool-service & irrigation techs",
]



def _default_history_path() -> Path:
    env_p = os.environ.get("LETSVIBE_RC_IDEA_HISTORY_PATH")
    if env_p:
        return Path(env_p)
    return Path(__file__).resolve().parent.parent.parent / "data" / "rc_idea_history.json"


def _extract_topic_keywords(text: str, limit: int = 5) -> List[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9+-]{2,}", (text or "").lower())
    out: List[str] = []
    for tok in tokens:
        if tok in _STOPWORDS or tok.isdigit():
            continue
        if tok not in out:
            out.append(tok)
        if len(out) >= limit:
            break
    return out


def _load_recent_history(history_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = history_path or _default_history_path()
    cutoff = time.time() - _TTL_SECONDS
    try:
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8") or "[]")
        if not isinstance(raw, list):
            return []
        return [e for e in raw if isinstance(e, dict) and float(e.get("ts") or 0.0) >= cutoff]
    except Exception:
        return []


def _record_history(candidates: List[Dict[str, str]], history_path: Optional[Path] = None) -> None:
    path = history_path or _default_history_path()
    now = time.time()
    entries = _load_recent_history(path)
    for c in candidates:
        title = str(c.get("title") or "").strip()
        if not title:
            continue
        kws = _extract_topic_keywords(f"{title} {c.get('target_buyer', '')} {c.get('pitch', '')}", limit=6)
        entries.append({"ts": now, "title": title, "keywords": kws})
    # Keep last 80 entries within 3 days
    entries = entries[-80:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except Exception:
        pass


def _build_generator_prompts(
    interests: str,
    platform: str,
    count: int = 4,
    recent_history: Optional[List[Dict[str, Any]]] = None,
) -> tuple[str, str]:
    lenses = random.sample(_ANGLE_LENSES, k=min(4, len(_ANGLE_LENSES)))
    recent = recent_history or []
    recent_titles = [str(e.get("title") or "") for e in recent if e.get("title")]
    recent_kw_set = {str(k).lower() for e in recent for k in (e.get("keywords") or [])}

    clean_interests = (interests or "").strip()
    if clean_interests:
        domain_line = f"User's Personal Hobbies / Likes / Skills (optional): {clean_interests}"
    else:
        fresh_domains = [
            d for d in _OPEN_DOMAINS
            if not any(kw in d.lower() for kw in recent_kw_set)
        ]
        pool = fresh_domains if len(fresh_domains) >= 3 else _OPEN_DOMAINS
        picked = random.sample(pool, k=min(3, len(pool)))
        domain_line = (
            "User did not specify hobbies — pick fresh, grounded real-world niches inspired by domains like: "
            + "; ".join(picked)
        )

    avoid_block = ""
    if recent_titles or recent_kw_set:
        titles_txt = ", ".join(recent_titles[-12:]) if recent_titles else "(none)"
        kws_txt = ", ".join(sorted(recent_kw_set)[:24]) if recent_kw_set else "(none)"
        avoid_block = (
            f"\nAnti-Repeat Filter (Last 3 Days — DO NOT repeat these ideas, buyers, or core keywords):\n"
            f"- Avoid recent titles: {titles_txt}\n"
            f"- Eliminate/avoid these recently used topic keywords: {kws_txt}\n"
        )

    system_prompt = """You are LetsVibe: Idea Finder — an elite niche-product strategist who finds genuinely viable, high-moat software gaps.
Your job is to invent concrete software ideas that a specific group of people would pay for, so they pass Reality Check.

Rules:
1. NEVER suggest generic AI wrappers, TODO lists, habit trackers, generic chatbots, or "AI assistant for X" CRUD apps.
2. Every idea MUST have a concrete technical or workflow moat: e.g. offline-first local Wi-Fi/LAN operation, real-time audio/DSP/hardware/USB/OBD2/serial integration, local PDF/compliance audit trails, or replacing an overpriced $49–$199/mo SaaS with a fast $29–$79 one-time local tool.
3. Explicitly state in the pitch WHY mainstream tools (Excel, Notion, Google Forms, cloud SaaS) fail at this exact job (e.g., venue has no internet, gloves/greasy hands, live latency, hardware protocol).
4. Make every candidate genuinely distinct in audience, workflow, and monetization angle—and NEVER repeat any topic or keyword listed in the 3-day Anti-Repeat Filter.
5. Output STRICT JSON ONLY matching this schema:
{
  "candidates": [
    {
      "title": "Short 3-6 word product name/concept",
      "pitch": "2-3 concrete sentences describing what it does, who buys it, what expensive/clunky workaround it replaces, and why mainstream cloud tools fail here (min 60 chars).",
      "twist": "The specific technical or offline/hardware/workflow moat that makes switching obvious.",
      "target_buyer": "Exact paying buyer persona",
      "why_monetizable": "1 sentence on why buyers gladly pay $29-$99 one-time or $15-$49/mo for this"
    }
  ]
}"""

    user_prompt = f"""Target Platform: {platform}
{domain_line}
{avoid_block}
Generate {count} distinct, non-obvious product candidates. Vary them across these angles:
- {lenses[0]}
- {lenses[1]}
- {lenses[2]}
- {lenses[3]}
"""
    return system_prompt, user_prompt


def idea_text(candidate: Dict[str, str]) -> str:
    """The exact text Reality Check grades for a candidate; also what its Reality Check tab is filled with, so both
    always look at the same idea."""
    title = str(candidate.get("title") or "").strip()
    pitch = str(candidate.get("pitch") or "").strip()
    return f"{title}: {pitch}" if title and not pitch.lower().startswith(title.lower()) else pitch


def run_idea_finder(
    generate_json_fn: Callable[[str, str], Dict[str, Any]],
    evaluate_fn: Callable[[str, str, str], Dict[str, Any]],
    interests: str = "",
    platform: str = "Web",
    max_cycles: int = 3,
    on_progress: Optional[Callable[[str, int], None]] = None,
    history_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Brainstorms candidates once (avoiding 3-day repeat keywords), then runs each through Reality Check itself
    (`evaluate_fn`: the same check as the Reality Check tab, realistic tone) until one is sellable.

    An idea is recommended only if Reality Check says it can be sold for some amount to some group
    (sellability.py). If none of the `max_cycles` candidates is, nothing is recommended: the result says so and
    lists what was tested with its real Reality Check result."""
    def emit(msg: str, pct: int) -> None:
        if on_progress:
            on_progress(msg, pct)

    capped_cycles = max(1, min(3, int(max_cycles or 3)))
    recent_history = _load_recent_history(history_path)
    emit("Brainstorming diverse monetizable niche ideas...", 12)
    sys_p, usr_p = _build_generator_prompts(
        interests=interests,
        platform=platform,
        count=max(4, capped_cycles + 1),
        recent_history=recent_history,
    )
    raw = generate_json_fn(sys_p, usr_p)

    candidates: List[Dict[str, str]] = []
    for item in (raw.get("candidates") or []):
        if not isinstance(item, dict):
            continue
        pitch = str(item.get("pitch") or "").strip()
        if len(pitch) < 15:
            continue
        candidates.append({
            "title": str(item.get("title") or "Niche Tool").strip(),
            "pitch": pitch,
            "twist": str(item.get("twist") or "").strip(),
            "target_buyer": str(item.get("target_buyer") or "").strip(),
            "why_monetizable": str(item.get("why_monetizable") or "").strip(),
        })

    if not candidates:
        raise RuntimeError("Idea Finder could not generate valid candidates. Please try again.")

    _record_history(candidates, history_path)

    attempts: List[Dict[str, Any]] = []
    winner: Optional[Dict[str, Any]] = None
    closest: Optional[Dict[str, Any]] = None
    for idx, cand in enumerate(candidates[:capped_cycles]):
        pct_base = 25 + int((idx / capped_cycles) * 65)
        emit(f"Reality Check ({idx + 1}/{capped_cycles}): '{cand['title']}'...", pct_base)
        text = idea_text(cand)
        report = evaluate_fn(text, platform, cand["twist"])
        sell = report.get("sellability") or sellability(report)
        report["sellability"] = sell
        attempt = {
            "cycle": idx + 1,
            "title": cand["title"],
            "pitch": text,
            "twist": cand["twist"],
            "target_buyer": cand["target_buyer"],
            "verdict": str(report.get("verdict") or "DO NOT BUILD").upper(),
            "viability_score": float(report.get("viability_score") or 0.0),
            "headline": str(report.get("headline") or ""),
            "suggested_price": str((report.get("pricing_recommendation") or {}).get("suggested_price") or "N/A"),
            "sellability": sell,
            "passed": bool(sell["sellable"]),
            "report_id": report.get("id"),
        }
        attempts.append(attempt)
        if closest is None or attempt["viability_score"] > closest["attempt"]["viability_score"]:
            closest = {"attempt": attempt, "report": report, "candidate": cand}
        if attempt["passed"]:
            winner = {"attempt": attempt, "report": report, "candidate": dict(cand, pitch=text)}
            break

    emit("Idea Finder complete.", 100)
    return {
        "interests": (interests or "").strip(),
        "platform": platform,
        "passed_reality_check": winner is not None,
        "cycles_used": len(attempts),
        "max_cycles": capped_cycles,
        "saved_candidates": candidates,
        "attempts": attempts,
        # only a sellable idea is ever recommended
        "selected_idea": winner["candidate"] if winner else None,
        "winning_report": winner["report"] if winner else None,
        # shown as "closest, not recommended" when nothing passed
        "closest_report": None if winner else closest["report"],
    }
