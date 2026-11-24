"""Is an idea sellable? One rule, used by Reality Check (the Pricing card) and Idea Finder (its pass/fail).

An idea passes when Reality Check finds it could viably be sold for some amount to some group:
  - the verdict is not DO NOT BUILD (NICHE ONLY and ACTUALLY VIABLE both pass, labelled as such), and
  - the pricing recommendation names a paid price (any amount above $0) and doesn't say "Do Not Monetize".
Free / open-source ideas pass only when the price also names something paid (e.g. a $29 Pro license).
"""

from __future__ import annotations

import re
from typing import Any, Dict

# a dollar amount above zero: "$5", "$0.99", "$29-$79", "$9/mo"
_PAID_AMOUNT = re.compile(r"\$\s*(?:[1-9]\d*(?:[.,]\d+)?|0[.,]\d*[1-9])")


def sellability(report: Dict[str, Any]) -> Dict[str, Any]:
    """{"sellable", "label", "level", "buyer", "price", "model", "why"} for a Reality Check report."""
    pr = report.get("pricing_recommendation") if isinstance(report.get("pricing_recommendation"), dict) else {}
    price = str(pr.get("suggested_price") or "").strip()
    model = str(pr.get("model") or "").strip()
    buyer = str(pr.get("buyer") or "").strip()
    verdict = str(report.get("verdict") or "").strip().upper()
    score = float(report.get("viability_score") or 0.0)

    if verdict == "DO NOT BUILD":
        why = f"Reality Check found no group likely to pay for this ({score:.1f}/10: do not build)."
        sellable = False
    elif "do not monetize" in f"{model} {price}".lower():
        why = "Reality Check recommends not charging for this."
        sellable = False
    elif not _PAID_AMOUNT.search(price):
        why = "Reality Check found no price a buyer would pay (free / open source only)."
        sellable = False
    else:
        who = buyer or "a focused group of buyers"
        why = f"Could be sold to {who} for {price}" + (f" ({model})" if model else "") + "."
        sellable = True

    if not sellable:
        label, level = "Not sellable", "none"
    elif verdict == "ACTUALLY VIABLE":
        label, level = "Sellable: actually viable", "viable"
    else:
        label, level = "Sellable: niche only", "niche"
    return {"sellable": sellable, "label": label, "level": level, "buyer": buyer, "price": price, "model": model,
            "why": why}
