"""LLM evaluation engine for LetsVibe: Reality Check using shared round-robin llama-server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from ..evaluator.llama_supervisor_client import ensure_llama_awake, llama_url, llm_lease, touch_engine
from .grounding import gather_market_grounding

SYSTEM_PROMPT_RC = """You are LetsVibe: Reality Check — a candid, zero-sugarcoating product & engineering market analyst.
Your job is to save developers from wasting LLM tokens and weeks of effort on saturated wrappers, while accurately recognizing genuinely underserved technical niches.

Rules:
1. NEVER use empty flattery ("Exciting idea!", "Great potential!", "Innovative concept!"). Be clinical, direct, and fair.
2. Distinguish genuine engineering (DSP, protocols, hardware integration, compilers, offline sync engines) from thin AI/CRUD wrappers. Never call real DSP or hardware protocol engineering an "AI wrapper".
3. Ground your saturation assessment in the real market data provided AND your knowledge of existing commercial/open-source/hardware incumbents—including their **pricing** (free/OSS vs. paid subscriptions vs. one-time licenses). If a search query returned unrelated noise, ignore the noise and evaluate the actual product domain.
4. Scoring calibration on the **0 to 10** scale:
   - `1–3`: Wrapper slop, redundant gimmick, or zero switching incentive (`DO NOT BUILD`).
   - `4–6`: Real problem, but crowded market or niche-only adoption (`NICHE ONLY`).
   - `7–9`: Genuinely underserved pain with strong engineering moat and clear buyer willingness (`ACTUALLY VIABLE`).
   Keep `saturation_pct` as a 0–100 percentage.
5. Output STRICT JSON ONLY matching this schema:
{
  "verdict": "DO NOT BUILD | NICHE ONLY | ACTUALLY VIABLE",
  "headline": "Blunt 5-10 word verdict headline",
  "viability_score": 0,
  "saturation_pct": 0,
  "demand_tier": "Wrapper Slop | Crowded Commodity | Tiny Hobby Niche | Underserved Pain | Real Market Gap",
  "wasted_hours_estimate": "<estimated engineering hours>",
  "wasted_tokens_estimate": "<estimated LLM tokens>",
  "executive_summary": "2-3 blunt paragraphs explaining exactly why this verdict was reached, who already owns this market, and whether real users would actually switch.",
  "dimensions": {
    "market_saturation": {
      "label": "Market Saturation & Crowding",
      "score": 5,
      "weight": 25,
      "reasoning": "0-10 score + 2-3 detailed sentences citing existing competitors and crowd density (higher score = LESS saturated / better opportunity)."
    },
    "switching_friction": {
      "label": "Switching Motivation",
      "score": 5,
      "weight": 20,
      "reasoning": "0-10 score + 2-3 detailed sentences on why users would or wouldn't leave their current tool (higher score = stronger reason to switch)."
    },
    "niche_moat": {
      "label": "Niche Twist & Moat",
      "score": 5,
      "weight": 20,
      "reasoning": "0-10 score + 2-3 detailed sentences auditing whether the user's twist is defensible or trivial."
    },
    "real_demand": {
      "label": "Real Demand & Distribution",
      "score": 5,
      "weight": 15,
      "reasoning": "0-10 score + 2-3 detailed sentences on whether real people actively look for/pay for this."
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
      "name": "Existing Tool / Repo Name",
      "type": "Open Source | Free SaaS | Paid SaaS | OS Built-in",
      "why_it_wins": "Concrete reason users already use this instead"
    }
  ],
  "fatal_flaws": ["4-6 specific, concrete reasons this fails or struggles as pitched"],
  "redeeming_angles": ["2-4 genuine strengths or useful sub-problems if any exist (or empty if none)"],
  "pivots": [
    {
      "title": "Concrete Pivot or Alternative",
      "impact": "high | medium | low",
      "effort": "low | medium | high",
      "why_it_matters": "Why this fixes the saturation/demand problem",
      "how": "Specific concrete direction (or blunt advice to scrap it)"
    }
  ],
  "pricing_recommendation": {
    "suggested_price": "<realistic price + billing cadence, $0 (Free/OSS), or Do Not Monetize>",
    "model": "One-Time License | Freemium | Subscription | Free / OSS | Do Not Monetize",
    "reasoning": "2-3 blunt sentences explaining what price—compared to happy, established competitors—would actually lead to sales, or why even free won't make users switch."
  },
  "marketing_playbook": {
    "difficulty": "Brutal | Hard | Moderate",
    "where_buyers_hang_out": ["2-3 exact niche subreddits, forums, Discords, or buyer channels"],
    "biggest_distribution_trap": "The #1 marketing mistake builders make in this niche (e.g. posting to generic dev forums instead of actual buyers).",
    "distribution_channels": [
      {
        "channel": "Specific distribution channel",
        "why": "Why this reaches actual buyers",
        "first_step": "Concrete Day-1 action (never generic 'use social media' advice)"
      }
    ]
  },
  "liability_and_entity": {
    "recommendation": "Personal / Indie | Single-Member LLC | LLC + Commercial Insurance",
    "risk_level": "Low | Moderate | High",
    "reasoning": "2-3 blunt sentences on real legal/financial exposure (handling money, PII, health/safety, live hardware, or B2B contracts vs. harmless OSS/free utility) and whether LLC filing fees are justified yet.",
    "must_have_protections": ["2-3 practical protections (e.g. Merchant of Record like Paddle/Stripe, AS-IS Terms/warranty disclaimer, avoid storing sensitive PII)."]
  }
}"""


def _parse_json_obj(text: str) -> Dict[str, Any]:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start = s.find("{")
    if start != -1:
        s = s[start:]
    obj, _ = json.JSONDecoder().raw_decode(s)
    return obj if isinstance(obj, dict) else {}


def _call_shared_llama(system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> Dict[str, Any]:
    prompt = (
        f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
        f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
        f"<|im_start|>assistant\n<think>\n\n</think>\n\n{{"
    )
    payload = {
        "prompt": prompt,
        "temperature": 0.25,
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

    emit("Checking GitHub & package registries for existing competitors...", 15)
    grounding = gather_market_grounding(pitch=pitch, platform=platform, twist=twist)

    tone_note = (
        "Tone: NO MERCY — extra blunt, zero punches pulled, roast weak wrapper ideas directly."
        if str(tone).strip().lower() == "brutal"
        else "Tone: REALISTIC — objective, candid, zero sugarcoating."
    )
    repos_txt = "\n".join(
        f"- {r['full_name']} ({r['stars']} stars, {r['language']}): {r['description']}"
        for r in (grounding.get("github_top_repos") or [])
    ) or "(no direct GitHub matches found)"
    npm_txt = "\n".join(
        f"- {p['name']}: {p['description']}"
        for p in (grounding.get("npm_top_packages") or [])
    ) or "(none)"
    app_txt = "\n".join(
        f"- {a['name']} ({a['price']}, {a['rating']}★, {a['reviews']} reviews)"
        for a in (grounding.get("app_store_hits") or [])
    ) or "(none)"
    hn_txt = "\n".join(
        f"- {h['title']} ({h['points']} pts, {h['comments']} comments)"
        for h in (grounding.get("hn_stories") or [])
    ) or "(none)"
    so_txt = "\n".join(
        f"- {q['title']} (score {q['score']})"
        for q in (grounding.get("so_questions") or [])
    ) or "(none)"

    user_prompt = f"""{tone_note}

Target Platform: {platform}
App Idea Pitch:
{pitch}

Supposed Unique Twist / Niche Feature:
{twist or "(none provided)"}

Live Market Grounding (query: "{grounding.get('query', '')}"):
- GitHub matching repositories: {grounding.get('github_total_count', 0)} total
Top GitHub matches:
{repos_txt}
- npm matching packages: {grounding.get('npm_total_count', 0)} total
Top npm matches:
{npm_txt}
- Apple App Store / macOS Store matches:
{app_txt}
- Hacker News stories ({grounding.get('hn_total_hits', 0)} total):
{hn_txt}
- Stack Overflow top questions:
{so_txt}
"""

    emit("Acquiring shared AI engine turn (round-robin with LetsVibe)...", 35)
    with llm_lease("letsvibe_rc", job_id=job_id, on_wait=lambda msg: emit(msg, 40)):
        wake = ensure_llama_awake(on_progress=lambda msg: emit(msg, 50))
        if not wake.get("ok"):
            raise RuntimeError(wake.get("error") or "Local AI engine is unavailable.")
        emit("Running deep market saturation & viability analysis...", 65)
        parsed = _call_shared_llama(SYSTEM_PROMPT_RC, user_prompt, max_tokens=2000)
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

    raw_verdict = str(parsed.get("verdict") or "").strip().upper()
    if raw_verdict not in ("DO NOT BUILD", "NICHE ONLY", "ACTUALLY VIABLE"):
        if computed_viability < 4.2:
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
