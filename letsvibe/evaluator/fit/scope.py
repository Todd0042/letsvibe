"""
Within scope: how far a project went beyond what it set out to be (did the coder get "carried away"?). Text only;
never part of the grade.

Python writes a short brief with three parts: what the project set out to be (earliest README and first commits from
git history, see intention.py), what its README says it does now, and what the code actually does (screens and
pages, API endpoints, stored data, libraries; from the experience dossier). The 14B lists the distinct things the
code does, each with an exact quote from the code part, whether the README or plan mentions it, and how it relates to
the stated purpose. Python keeps only answers whose quotes it finds in the brief, then picks the label:

  Focused       nothing unmentioned beyond the purpose
  Some extras   1-2 unmentioned extras, none unrelated
  Drifted       3-4 unmentioned extras, or one unrelated feature
  Carried away  5 or more unmentioned extras, or two or more unrelated features

Wording is hedged ("appears to"): this is a reading of the code, not a verdict.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

LABELS = ("Focused", "Some extras", "Drifted", "Carried away")
FITS = ("CORE", "SUPPORTING", "EXTRA", "UNRELATED")
STATED = ("MENTIONED", "NOT_MENTIONED")
SYSTEM = ("You compare what a program set out to be with what its code actually does. You answer with fixed choices "
          "and exact quotes. A feature that helps the main purpose is SUPPORTING, not EXTRA.")
MAX_BULLETS = 40


def readme_bullets(readme: str) -> List[str]:
    """The README's feature-style list items, cleaned (what it says the program does now)."""
    out = []
    for line in (readme or "").splitlines():
        m = re.match(r"\s*(?:[-*•+]|\d+[.)])\s+(.{6,200})$", line)
        if m:
            item = re.sub(r"!\[[^\]]*\]\([^)]*\)|\[([^\]]*)\]\([^)]*\)", r"\1", m.group(1))
            item = re.sub(r"[*_`#>]", "", item).strip()
            if item and not item.lower().startswith(("http", "git ", "npm ", "pip ", "cd ", "python ")):
                out.append(item)
    return list(dict.fromkeys(out))[:MAX_BULLETS]


def build_brief(d: Dict[str, Any], readme: str, intent: Dict[str, Any]) -> Tuple[str, str]:
    """(full brief, code part). `d` is the experience dossier dict; `readme` the current README's raw text."""
    plan = []
    if intent.get("first_readme"):
        plan.append(f"Earliest README (first version in the git history): {intent['first_readme']}")
    if intent.get("first_commits"):
        plan.append("First commit messages:\n" + "\n".join(f"- {c}" for c in intent["first_commits"]))
    if not plan:
        plan.append("No early README or meaningful first commits; use part B as the stated purpose.")
    now = [f"Title: {d.get('readme_title') or d.get('repo')}"]
    if d.get("readme_blurb"):
        now.append(f"Summary: {d['readme_blurb']}")
    meta = d.get("package_meta") or {}
    if meta.get("description"):
        now.append(f"Package description: {meta['description']}")
    bullets = readme_bullets(readme)
    if bullets:
        now.append("Features it lists:\n" + "\n".join(f"- {b}" for b in bullets))
    code = []
    for key, label, n in (("screens", "Screens, windows and pages", 40), ("api_endpoints", "API endpoints", 25),
                          ("data_models", "Data it stores (tables/models)", 20), ("libraries", "External libraries", 25)):
        items = d.get(key) or []
        if items:
            code.append(f"- {label}: " + ", ".join(str(i) for i in items[:n]))
    code_md = "\n".join(code) or "- (no screens, endpoints, stored data or libraries found)"
    brief = ("## A. What it set out to be\n" + "\n".join(plan) + "\n\n## B. What its README says it does now\n" +
             "\n".join(now) + "\n\n## C. What the code does\n" + code_md)
    return brief, code_md


def schema() -> dict:
    thing = {"type": "object", "properties": {
        "thing": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "mentioned": {"type": "string", "enum": list(STATED)},
        "fit": {"type": "string", "enum": list(FITS)},
    }, "required": ["thing", "evidence", "mentioned", "fit"]}
    return {"type": "object", "properties": {"purpose": {"type": "string"}, "things": {"type": "array", "items": thing}},
            "required": ["purpose", "things"]}


def prompt(brief: str) -> str:
    return (f"{brief}\n\n---\n"
            "1. purpose: in one plain sentence, what the program set out to be (from A, or B when A is empty).\n"
            "2. things: list 5 to 12 distinct things the program does, read from part C only. Group related facts into "
            "one thing (several endpoints for the same feature are one thing). For each:\n"
            "   - thing: a few plain words a user would understand (e.g. 'song queue for singers').\n"
            "   - evidence: 1-2 short exact quotes copied word for word from part C.\n"
            "   - mentioned: MENTIONED if part A or B describes it, NOT_MENTIONED if neither does.\n"
            "   - fit: CORE (the main purpose), SUPPORTING (helps the main purpose: settings, saving, sign-in, help), "
            "EXTRA (a separate feature the purpose doesn't need), UNRELATED (nothing to do with the purpose).\n"
            "JSON only.")


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def verified(things: List[Dict[str, Any]], code_md: str) -> List[Dict[str, Any]]:
    """Keeps answers with a valid choice and at least one quote found in the code part (exact, or 80% of its words)."""
    dn = _norm(code_md)
    words_in = set(dn.split())

    def ok(q: str) -> bool:
        qn = _norm(q)
        if len(qn) < 3:
            return False
        if qn in dn:
            return True
        w = qn.split()
        return len(w) >= 3 and sum(x in words_in for x in w) / len(w) >= 0.8

    out = []
    for t in things or []:
        if t.get("fit") in FITS and t.get("mentioned") in STATED and str(t.get("thing", "")).strip() \
                and any(ok(q) for q in t.get("evidence") or []):
            out.append({**t, "thing": str(t["thing"]).strip()})
    return out


def _and(items: List[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def assess(things: List[Dict[str, Any]], purpose: str = "") -> Dict[str, Any]:
    """{"label", "summary", "extras", "unrelated", "counts"} from verified things."""
    extras = [t["thing"] for t in things if t["mentioned"] == "NOT_MENTIONED" and t["fit"] in ("EXTRA", "UNRELATED")]
    unrelated = [t["thing"] for t in things if t["fit"] == "UNRELATED"]
    if len(extras) >= 5 or len(unrelated) >= 2:
        label = "Carried away"
    elif len(extras) >= 3 or len(unrelated) == 1:
        label = "Drifted"
    elif extras:
        label = "Some extras"
    else:
        label = "Focused"
    shown = extras[:5]
    if label == "Focused":
        summary = "Appears to stay within what it set out to do: everything found in the code is described in its README or plan."
    elif label == "Some extras":
        summary = f"Mostly within scope. It also appears to include {_and(shown)}, which its README doesn't describe."
    elif label == "Drifted":
        summary = f"Appears to drift beyond its stated purpose: {_and(shown)} {'is' if len(shown) == 1 else 'are'} not part of what its README describes."
    else:
        summary = f"Appears to go well beyond its stated purpose, adding {_and(shown)}, none of which its README describes."
    if len(things) < 3:
        label, summary = "", ""   # too little verified to say anything
    return {"label": label, "summary": summary, "purpose": purpose.strip(), "extras": extras, "unrelated": unrelated,
            "counts": {"things": len(things), "extras": len(extras), "unrelated": len(unrelated)}}


def run(generate: Callable[..., Dict[str, Any]], model: str, brief: str, code_md: str) -> Dict[str, Any]:
    """Asks the model (up to 3 tries for a cut-off answer), verifies the quotes and returns assess()'s result + things."""
    from ..ollama import CpuSpilloverError

    ans: Dict[str, Any] = {}
    for attempt in range(3):
        try:
            ans = generate(model=model, system_prompt=SYSTEM, user_prompt=prompt(brief), format_schema=schema(),
                           max_output_tokens=1200 + 600 * attempt, temperature=0.1)
            if isinstance(ans.get("things"), list):
                break
        except CpuSpilloverError:      # includes the admin's Stop: never swallow it
            raise
        except (ValueError, RuntimeError):
            continue
    things = verified(ans.get("things") or [], code_md)
    result = assess(things, str(ans.get("purpose") or ""))
    result["things"] = things
    result["unverified"] = len(ans.get("things") or []) - len(things)
    return result
