"""LLM evaluation engine for LetsVibe: Reality Check using shared round-robin llama-server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from ..evaluator.llama_supervisor_client import ensure_llama_awake, llama_url, llm_lease, touch_engine
from .grounding import gather_market_grounding

_SCHEMA_BLOCK = """Output STRICT JSON ONLY matching this schema:
{
  "verdict": "DO NOT BUILD | NICHE ONLY | ACTUALLY VIABLE",
  "headline": "5-10 word verdict headline",
  "viability_score": 0,
  "saturation_pct": 0,
  "demand_tier": "Wrapper Slop | Crowded Commodity | Tiny Hobby Niche | Underserved Pain | Creative / Entertainment Hit | Real Market Gap",
  "wasted_hours_estimate": "<estimated engineering hours>",
  "wasted_tokens_estimate": "<estimated LLM tokens>",
  "executive_summary": "2-3 paragraphs explaining why this verdict was reached, which recognizable incumbents exist, and whether real users would adopt it.",
  "dimensions": {
    "market_saturation": {
      "label": "Market Saturation & Crowding",
      "score": 5,
      "weight": 25,
      "reasoning": "0-10 score + 2-3 detailed sentences citing only well-known competitors (higher score = LESS saturated / better opportunity; for games, stories, or entertainment, judge whether the specific hook stands out rather than penalizing because the genre exists)."
    },
    "switching_friction": {
      "label": "Switching Motivation",
      "score": 5,
      "weight": 20,
      "reasoning": "0-10 score + 2-3 detailed sentences on why users would try or adopt this (for utility tools: switching incentive; for games/entertainment/creative apps: curiosity, fresh hook, vibe, and replay/share pull)."
    },
    "niche_moat": {
      "label": "Niche Twist & Moat",
      "score": 5,
      "weight": 20,
      "reasoning": "0-10 score + 2-3 detailed sentences evaluating whether the user's twist is genuinely useful."
    },
    "real_demand": {
      "label": "Real Demand & Distribution",
      "score": 5,
      "weight": 15,
      "reasoning": "0-10 score + 2-3 detailed sentences on whether real people actively look for, enjoy, share, or pay for this."
    },
    "effort_vs_payoff": {
      "label": "Build Effort vs. Payoff",
      "score": 5,
      "weight": 10,
      "reasoning": "0-10 score + 2-3 detailed sentences comparing engineering time to realistic adoption."
    },
    "wrapper_risk": {
      "label": "Anti-Slop / Originality",
      "score": 5,
      "weight": 10,
      "reasoning": "0-10 score + 2-3 detailed sentences on whether this is a thin AI/CRUD wrapper vs. genuine engineering (higher score = NOT slop)."
    }
  },
  "existing_competitors": [
    {
      "name": "Widely-Known Existing Tool / Brand",
      "type": "Open Source | Free SaaS | Paid SaaS | OS Built-in",
      "why_it_wins": "Concrete reason users already use this instead"
    }
  ],
  "fatal_flaws": ["4-6 specific, concrete hurdles or execution requirements"],
  "redeeming_angles": ["2-4 genuine strengths or useful sub-problems if any exist"],
  "pivots": [
    {
      "title": "Concrete Pivot, Feature Angle, or Positioning",
      "impact": "high | medium | low",
      "effort": "low | medium | high",
      "why_it_matters": "Why this improves adoption/viability",
      "how": "Specific concrete direction"
    }
  ],
  "pricing_recommendation": {
    "suggested_price": "<realistic price + billing cadence, $0 (Free/OSS), or Do Not Monetize>",
    "model": "One-Time License | Freemium | Subscription | Free / OSS | Do Not Monetize",
    "reasoning": "2-3 sentences comparing recognizable competitor prices, value delivered, and buyer willingness."
  },
  "marketing_playbook": {
    "difficulty": "Brutal | Hard | Moderate",
    "where_buyers_hang_out": ["2-3 exact niche subreddits, forums, Discords, or buyer channels"],
    "biggest_distribution_trap": "The #1 marketing mistake builders make in this niche.",
    "distribution_channels": [
      {
        "channel": "Specific distribution channel",
        "why": "Why this reaches actual buyers",
        "first_step": "Concrete Day-1 action"
      }
    ]
  },
  "liability_and_entity": {
    "recommendation": "Personal / Indie | Single-Member LLC | LLC + Commercial Insurance",
    "risk_level": "Low | Moderate | High",
    "reasoning": "2-3 sentences on real legal/financial exposure and whether LLC filing fees are justified yet.",
    "must_have_protections": ["2-3 practical protections (e.g. Merchant of Record like Paddle/Stripe, AS-IS Terms/warranty disclaimer, avoid storing sensitive PII)."]
  }
}"""

_PRICING_AND_SCALE = """Pricing calibration: anchor `suggested_price` to the actual buyer tier:
- Casual consumer / hobby utility: `$5–$29` one-time or `$3–$9/mo`
- Indie game / visual novel / entertainment app: `$4.99–$19.99` one-time (Steam/itch.io/App Store) or free + supporter/DLC
- Prosumer / gig worker / solo small-business tool (e.g. wedding DJs, photographers, trades): `$29–$99` one-time or `$9–$29/mo`
- B2B team / compliance / vertical SaaS: `$49–$199/mo`
- Developer CLI / OSS: `$0 (Free / OSS)` + optional Pro/Team license

Scoring calibration on the **0 to 10** scale:
- `1–3.9`: Wrapper slop, redundant gimmick, or zero appeal/switching incentive (`DO NOT BUILD`).
- `4.0–6.7`: Real problem or creative niche with a focused audience (`NICHE ONLY`).
- `6.8–9.5`: Underserved pain OR compelling creative/entertainment hook with strong execution and clear audience appeal (`ACTUALLY VIABLE`).
Keep `saturation_pct` as a 0–100 percentage."""

SYSTEM_PROMPT_RC = f"""You are LetsVibe: Reality Check — a grounded, constructive, realistic product & engineering analyst.
Your job is to give indie builders an honest, practical read on whether their idea can carve out a real user base—neither empty hype nor cynical nitpicking.

Rules:
1. Be candid, balanced, and constructive. Do NOT invent fake saturation or default to "DO NOT BUILD" when a tool solves a real workflow cleanly or an entertainment/creative app has a strong hook for a focused audience.
2. Consumer entertainment, games, visual novels, music/party apps, and creative tools are NOT zero-sum workflow software. People finish one game, song, or visual novel and want the next one—just like there is more than one book in a library. NEVER penalize entertainment or creative ideas simply because the genre has been done before; evaluate them on their hook, voice/aesthetic, replayability, and shareability.
3. Distinguish genuine engineering (real-time audio/DSP, mic ducking, offline local-network protocols, hardware integration, compilers, offline sync engines) and genuine creative craft from thin AI/CRUD wrappers.
4. Compare ONLY against widely-known, mainstream tools that real users actually know exist. NEVER cite obscure GitHub repos, tiny hobby apps, or unknown clones as "market saturation". If no widely-known incumbents were found in public registries, treat that as low open-source/app-store saturation.
5. Replacing bloated monthly cloud SaaS with a focused, offline-first, or one-time-license local tool IS genuine switching value.
6. {_PRICING_AND_SCALE}
7. {_SCHEMA_BLOCK}"""

SYSTEM_PROMPT_BRUTAL = f"""You are LetsVibe: Reality Check (No Mercy Mode) — a blunt, unforgiving product & market critic.
Pull zero punches. Roast thin wrappers, weak differentiation, and distribution delusions directly, while still acknowledging real engineering when present.

Rules:
1. Never soften the blow. Call out every fatal flaw, switching barrier, and incumbent moat clearly.
2. Compare against real, well-known incumbents (never obscure noise).
3. {_PRICING_AND_SCALE}
4. {_SCHEMA_BLOCK}"""

SYSTEM_PROMPT_WHISPER = f"""You are LetsVibe: Reality Check (Whisper Sweet Nothings Mode) — an wildly enthusiastic, silver-tongued hype strategist.
Your mission is to:
1. Over-exaggerate the app like mad — pitch it like a category-defining, must-have breakthrough that buyers will obsess over.
2. Find EVERY possible angle in which the app IS genuinely viable for sale in the real world (score optimistically around 7.5–9.2, verdict `ACTUALLY VIABLE`, low perceived saturation).
3. In `fatal_flaws` (which represent "What You Must Deliver to Live Up to the Hype") and `pivots`, give concrete, actionable ways the builder must actually meet and justify that exaggeration (reliability, UX polish, killer workflow details, distribution proof) so it truly becomes sellable.
4. {_PRICING_AND_SCALE}
5. {_SCHEMA_BLOCK}"""

SYSTEM_PROMPTS_BY_TONE: Dict[str, str] = {
    "realistic": SYSTEM_PROMPT_RC,
    "brutal": SYSTEM_PROMPT_BRUTAL,
    "whisper": SYSTEM_PROMPT_WHISPER,
}


def _parse_json_obj(text: str) -> Dict[str, Any]:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start = s.find("{")
    if start != -1:
        s = s[start:]
    obj, _ = json.JSONDecoder().raw_decode(s)
    return obj if isinstance(obj, dict) else {}


def _call_shared_llama(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    temperature: float = 0.25,
) -> Dict[str, Any]:
    prompt = (
        f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
        f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
        f"<|im_start|>assistant\n<think>\n\n</think>\n\n{{"
    )
    payload = {
        "prompt": prompt,
        "temperature": float(temperature),
        "n_predict": int(max_tokens),
        "cache_prompt": False,
        "stop": ["<|im_end|>", "<|endoftext|>"],
    }
    req = urllib.request.Request(
        f"{llama_url()}/completion",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=240) as resp:  # nosec B310 - localhost llama-server
        raw = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    return _parse_json_obj("{" + str(raw.get("content") or ""))


def generate_idea_batch_llama(
    system_prompt: str,
    user_prompt: str,
    job_id: str = "",
) -> Dict[str, Any]:
    """Generates candidate niche ideas using the shared round-robin llama-server."""
    with llm_lease("letsvibe_rc", job_id=job_id):
        wake = ensure_llama_awake()
        if not wake.get("ok"):
            raise RuntimeError(wake.get("error") or "Local AI engine is unavailable.")
        res = _call_shared_llama(system_prompt, user_prompt, max_tokens=2200, temperature=0.75)
        touch_engine()
        return res


TONE_PROMPTS: Dict[str, str] = {
    "realistic": "Tone: REALISTIC — objective, candid, zero sugarcoating.",
    "brutal": "Tone: NO MERCY — extra blunt, zero punches pulled, roast weak wrapper ideas directly.",
    "whisper": (
        "Tone: WHISPER SWEET NOTHINGS — over-exaggerate the app like mad, hype it like a category-defining "
        "breakthrough, and find EVERY possible angle in which it could actually be viable for sale in the "
        "real world. Celebrate its potential enthusiastically—BUT in the pivots, fatal_flaws (framed as "
        "'What You Must Deliver to Live Up to the Hype'), pricing_recommendation, and marketing_playbook, "
        "spell out the concrete features, quality bar, distribution, and proof they MUST actually build to "
        "meet that exaggeration and make it genuinely sellable (still return valid JSON matching the exact schema)."
    ),
}


def normalize_tone(tone: str) -> str:
    """Normalizes a user-supplied feedback tone key to a supported preset."""
    key = str(tone or "").strip().lower()
    return key if key in TONE_PROMPTS else "realistic"


def evaluate_idea(
    pitch: str,
    platform: str = "Web",
    twist: str = "",
    tone: str = "realistic",
    job_id: str = "",
    on_progress: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """Runs grounding + round-robin locked llama-server evaluation for an app idea."""
    def emit(msg: str, pct: int) -> None:
        if on_progress:
            on_progress(msg, pct)

    tone = normalize_tone(tone)
    emit("Checking GitHub & package registries for existing competitors...", 15)
    grounding = gather_market_grounding(pitch=pitch, platform=platform, twist=twist)

    tone_note = TONE_PROMPTS[tone]
    gh_empty = (
        f"(GitHub search temporarily rate-limited: {grounding.get('github_error') or '429/403'} — rely on domain knowledge & App Store)"
        if grounding.get("github_rate_limited")
        else "(no widely-known >=500-star GitHub incumbents found)"
    )
    repos_txt = "\n".join(
        f"- {r['full_name']} ({r['stars']} stars, {r['language']}): {r['description']}"
        for r in (grounding.get("github_top_repos") or [])
    ) or gh_empty
    app_txt = "\n".join(
        f"- {a['name']} ({a['price']}, {a['rating']}★, {a['reviews']} reviews)"
        for a in (grounding.get("app_store_hits") or [])
    ) or "(no widely-known >=500-review App Store incumbents found)"

    user_prompt = f"""{tone_note}

Target Platform: {platform}
App Idea Pitch:
{pitch}

Supposed Unique Twist / Niche Feature:
{twist or "(none provided)"}

Recognizable High-Traction Incumbents Check (query: "{grounding.get('query', '')}"):
- Widely-known GitHub repositories (>=500 stars):
{repos_txt}
- Widely-known Apple App Store / macOS Store apps (>=500 reviews):
{app_txt}
"""

    system_prompt = SYSTEM_PROMPTS_BY_TONE.get(tone, SYSTEM_PROMPT_RC)
    emit("Acquiring shared AI engine turn (round-robin with LetsVibe)...", 35)
    with llm_lease("letsvibe_rc", job_id=job_id, on_wait=lambda msg: emit(msg, 40)):
        wake = ensure_llama_awake(on_progress=lambda msg: emit(msg, 50))
        if not wake.get("ok"):
            raise RuntimeError(wake.get("error") or "Local AI engine is unavailable.")
        emit("Running deep market saturation & viability analysis...", 65)
        parsed = _call_shared_llama(system_prompt, user_prompt, max_tokens=4096)
        touch_engine()

    dims = parsed.get("dimensions") if isinstance(parsed.get("dimensions"), dict) else {}
    weighted_sum = 0.0
    total_w = 0.0
    for key, d in dims.items():
        if isinstance(d, dict):
            sc = float(d.get("score") or 0.0)
            if sc > 10.0:
                sc = sc / 10.0
            sc = max(0.0, min(10.0, sc))
            w = float(d.get("weight") or 0.0)
            d["score"] = round(sc, 1)
            weighted_sum += sc * w
            total_w += w
    raw_vs = float(parsed.get("viability_score") or 0.0)
    if raw_vs > 10.0:
        raw_vs = raw_vs / 10.0
    computed_viability = round(weighted_sum / total_w, 1) if total_w > 0 else round(max(0.0, min(10.0, raw_vs)), 1)

    if computed_viability < 4.0:
        raw_verdict = "DO NOT BUILD"
    elif computed_viability < 6.8:
        raw_verdict = "NICHE ONLY"
    else:
        raw_verdict = "ACTUALLY VIABLE"

    emit("Finalizing Reality Check report...", 95)
    return {
        "pitch": pitch,
        "platform": platform,
        "twist": twist,
        "tone": tone,
        "verdict": raw_verdict,
        "headline": str(parsed.get("headline") or raw_verdict),
        "viability_score": computed_viability,
        "saturation_pct": round(max(0.0, min(100.0, float(parsed.get("saturation_pct") or 50.0))), 1),
        "demand_tier": str(parsed.get("demand_tier") or "Crowded Commodity"),
        "wasted_hours_estimate": str(parsed.get("wasted_hours_estimate") or "20-60 hrs"),
        "wasted_tokens_estimate": str(parsed.get("wasted_tokens_estimate") or "~100k-300k tokens"),
        "executive_summary": str(parsed.get("executive_summary") or ""),
        "dimensions": dims,
        "existing_competitors": list(parsed.get("existing_competitors") or [])[:6],
        "fatal_flaws": list(parsed.get("fatal_flaws") or [])[:6],
        "redeeming_angles": list(parsed.get("redeeming_angles") or [])[:4],
        "pivots": list(parsed.get("pivots") or [])[:4],
        "pricing_recommendation": parsed.get("pricing_recommendation") if isinstance(parsed.get("pricing_recommendation"), dict) else {},
        "marketing_playbook": parsed.get("marketing_playbook") if isinstance(parsed.get("marketing_playbook"), dict) else {},
        "liability_and_entity": parsed.get("liability_and_entity") if isinstance(parsed.get("liability_and_entity"), dict) else {},
        "grounding": grounding,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
