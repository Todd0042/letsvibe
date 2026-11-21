"""
"Badge of honor" images for a project's LetsVibe grade, served at /badge/{owner}/{repo}.svg?style=...

Owners paste them into a README or website (the report page builds the snippets). They are plain SVG drawn here,
always showing the project's latest public grade, with no scripts, no external fonts or images, and nothing that
identifies the viewer. Styles:
  classic        20px flat badge, like the usual README shields
  for-the-badge  28px bold uppercase badge
  medal          a round seal: a metal disc (gold for A, silver for B, bronze for C, steel below) with the grade
  card           a small dark card with the project name, grade, score and the five factor bars
Text widths are estimated (no font metrics here) and pinned with textLength so every badge fits its box exactly.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional
from html import escape   # escapes & < > " ' for the SVG text (never parses XML)

STYLES = ("classic", "for-the-badge", "medal", "card")
FONT = "Verdana,Geneva,'DejaVu Sans',sans-serif"
NAVY, NAVY_LIGHT = "#141a2e", "#1f2a47"
GOLD, CYAN = "#f5c451", "#22d3ee"

# Grade family -> (main colour, darker edge)
GRADE_COLOURS = {
    "A": ("#10b981", "#047857"),
    "B": ("#0ea5e9", "#0369a1"),
    "C": ("#f59e0b", "#b45309"),
    "D": ("#f97316", "#c2410c"),
    "F": ("#ef4444", "#b91c1c"),
}
NOT_GRADED = ("#64748b", "#475569")

# Medal metal by grade family: (highlight, light, mid, dark, deep)
METALS = {
    "A": ("#fffbe6", "#fde68a", "#f5b524", "#b45309", "#7c3a06"),   # gold
    "B": ("#ffffff", "#e2e8f0", "#b6c2d1", "#64748b", "#334155"),   # silver
    "C": ("#ffedd5", "#f8b878", "#d97a2b", "#9a4a12", "#5c2a08"),   # bronze
    "D": ("#e2e8f0", "#94a3b8", "#64748b", "#334155", "#1e293b"),   # steel
}

FACTORS = (("resonance", "Value"), ("creativity", "Creativity"), ("user_journey", "Journey"),
           ("functionality", "Function"), ("ui_ux", "UI/UX"))

# Rough Verdana advance widths at 11px
_NARROW = set("ijlI.,:;|!'` ")
_WIDE = set("mwMW@%")


def text_width(text: str, size: float = 11.0, bold: bool = False, spacing: float = 0.0) -> float:
    w = 0.0
    for ch in text:
        if ch in _NARROW:
            w += 3.6 if ch != " " else 3.9
        elif ch in _WIDE:
            w += 10.0
        elif ch.isupper() or ch.isdigit():
            w += 7.6 if ch.isupper() else 7.0
        else:
            w += 6.3
    w = w * size / 11.0 * (1.08 if bold else 1.0)
    return w + spacing * max(len(text) - 1, 0)


def _family(letter: str) -> str:
    first = (letter or "").strip()[:1].upper()
    return first if first in GRADE_COLOURS else ""


def _trophy(x: float, y: float, s: float) -> str:
    """The LetsVibe mark: a small gold trophy between cyan sound bars, drawn in a 14x14 box scaled by s."""
    return f'''<g transform="translate({x} {y}) scale({s})">
    <path d="M4 1.5h6v3.2a3 3 0 0 1-6 0z" fill="{GOLD}"/>
    <path d="M4 2.5H2.4a1.6 1.6 0 0 0 1.8 2.6M10 2.5h1.6a1.6 1.6 0 0 1-1.8 2.6" fill="none" stroke="{GOLD}" stroke-width="0.9"/>
    <path d="M6.3 7.6h1.4v2.2H6.3z" fill="{GOLD}"/><rect x="4.4" y="9.8" width="5.2" height="1.4" rx="0.4" fill="{GOLD}"/>
    <g fill="{CYAN}"><rect x="0" y="6.2" width="0.9" height="2.6" rx="0.45"/><rect x="1.7" y="5.4" width="0.9" height="4.2" rx="0.45"/>
    <rect x="11.4" y="5.4" width="0.9" height="4.2" rx="0.45"/><rect x="13.1" y="6.2" width="0.9" height="2.6" rx="0.45"/></g>
  </g>'''


def _svg(width: float, height: float, title: str, body: str) -> str:
    t = escape(title)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="{t}"><title>{t}</title>{body}</svg>')


def _summary(project: Optional[Dict[str, Any]], scale: str = "raw") -> Dict[str, Any]:
    if not project:
        return {"graded": False, "letter": "", "score": None, "name": "", "family": ""}
    score = project.get("final_score")
    letter = str(project.get("letter_grade") or project.get("raw_letter_grade") or "").strip()
    return {"graded": bool(letter) and score is not None, "letter": letter,
            "score": float(score) if score is not None else None,
            "name": str(project.get("repo_name") or ""), "family": _family(letter)}


def classic(project: Optional[Dict[str, Any]], scale: str = "raw") -> str:
    p = _summary(project, scale=scale)
    left = "LetsVibe"
    right = f"{p['letter']} · {p['score']:.1f}" if p["graded"] else "not graded"
    colour, edge = GRADE_COLOURS.get(p["family"], NOT_GRADED)
    lw = 22 + text_width(left) + 8
    rw = text_width(right) + 14
    w, h = lw + rw, 20
    body = f'''<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#fff" stop-opacity=".12"/><stop offset="1" stop-opacity=".12"/></linearGradient>
  <clipPath id="r"><rect width="{w:.1f}" height="{h}" rx="3.5"/></clipPath>
  <g clip-path="url(#r)"><rect width="{lw:.1f}" height="{h}" fill="{NAVY}"/><rect x="{lw:.1f}" width="{rw:.1f}" height="{h}" fill="{colour}"/>
  <rect x="{lw:.1f}" width="1" height="{h}" fill="{edge}" opacity=".6"/><rect width="{w:.1f}" height="{h}" fill="url(#s)"/></g>
  {_trophy(5, 3, 1.0)}
  <g font-family="{FONT}" font-size="11" fill="#fff">
    <text x="{22 + text_width(left) / 2:.1f}" y="15" fill="#000" fill-opacity=".3" text-anchor="middle" textLength="{text_width(left):.1f}">{left}</text>
    <text x="{22 + text_width(left) / 2:.1f}" y="14" text-anchor="middle" textLength="{text_width(left):.1f}">{left}</text>
    <text x="{lw + rw / 2:.1f}" y="15" fill="#000" fill-opacity=".3" text-anchor="middle" textLength="{text_width(right):.1f}">{escape(right)}</text>
    <text x="{lw + rw / 2:.1f}" y="14" text-anchor="middle" font-weight="bold" textLength="{text_width(right):.1f}">{escape(right)}</text>
  </g>'''
    return _svg(w, h, f"LetsVibe grade: {right}", body)


def for_the_badge(project: Optional[Dict[str, Any]], scale: str = "raw") -> str:
    p = _summary(project, scale=scale)
    left = "LETSVIBE GRADED"
    right = f"{p['letter']}  {p['score']:.1f}" if p["graded"] else "NOT GRADED"
    colour, edge = GRADE_COLOURS.get(p["family"], NOT_GRADED)
    lt, rt = text_width(left, 10, True, 1.2), text_width(right, 11, True, 1.2)
    lw, rw, h = 30 + lt + 12, rt + 26, 28
    w = lw + rw
    body = f'''<linearGradient id="g" x2="0" y2="100%"><stop offset="0" stop-color="{NAVY_LIGHT}"/><stop offset="1" stop-color="{NAVY}"/></linearGradient>
  <linearGradient id="c" x2="0" y2="100%"><stop offset="0" stop-color="{colour}"/><stop offset="1" stop-color="{edge}"/></linearGradient>
  <clipPath id="r"><rect width="{w:.1f}" height="{h}" rx="5"/></clipPath>
  <g clip-path="url(#r)"><rect width="{lw:.1f}" height="{h}" fill="url(#g)"/><rect x="{lw:.1f}" width="{rw:.1f}" height="{h}" fill="url(#c)"/>
  <rect width="{w:.1f}" height="1" fill="#fff" opacity=".12"/></g>
  {_trophy(9, 6, 1.15)}
  <g font-family="{FONT}" font-weight="bold" fill="#fff" text-anchor="middle">
    <text x="{30 + lt / 2:.1f}" y="18" font-size="10" letter-spacing="1.2" textLength="{lt:.1f}" fill="#e2e8f0">{left}</text>
    <text x="{lw + rw / 2:.1f}" y="19" font-size="11" letter-spacing="1.2" fill="#000" fill-opacity=".25" textLength="{rt:.1f}">{escape(right)}</text>
    <text x="{lw + rw / 2:.1f}" y="18" font-size="11" letter-spacing="1.2" textLength="{rt:.1f}">{escape(right)}</text>
  </g>'''
    return _svg(w, h, f"LetsVibe grade: {right}", body)


def medal(project: Optional[Dict[str, Any]], scale: str = "raw") -> str:
    p = _summary(project, scale=scale)
    if not p["graded"]:
        return classic(project, scale=scale)
    hi, light, mid, dark, deep = METALS.get(p["family"] if p["family"] in METALS else "D")
    if p["family"] == "F":
        hi, light, mid, dark, deep = METALS["D"]
    colour, _ = GRADE_COLOURS[p["family"]]
    cx = cy = 70
    leaves = []
    for side in (-1, 1):
        for i in range(8):
            a = math.radians(102 + i * 14)
            ang = math.pi - a if side > 0 else a
            x, y = cx + 48 * math.cos(ang), cy + 48 * math.sin(ang)
            tilt = math.degrees(ang) + (90 if side > 0 else -90) + 30 * side
            leaves.append(f'<ellipse cx="{x:.1f}" cy="{y:.1f}" rx="5.2" ry="2.2" transform="rotate({tilt:.0f} {x:.1f} {y:.1f})" fill="{deep}" opacity=".85"/>')
    letter = escape(p["letter"])
    lsize = 46 if len(p["letter"]) == 1 else 40
    body = f'''<defs>
    <linearGradient id="rim" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{light}"/><stop offset=".45" stop-color="{mid}"/><stop offset=".75" stop-color="{dark}"/><stop offset="1" stop-color="{light}"/></linearGradient>
    <radialGradient id="face" cx=".35" cy=".3" r=".85"><stop offset="0" stop-color="{hi}"/><stop offset=".35" stop-color="{light}"/><stop offset=".75" stop-color="{mid}"/><stop offset="1" stop-color="{dark}"/></radialGradient>
    <linearGradient id="ink" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{dark}"/><stop offset="1" stop-color="{deep}"/></linearGradient>
    <path id="ring" d="M {cx} {cy + 57} a 57 57 0 1 1 0.01 0"/>
  </defs>
  <circle cx="{cx}" cy="{cy + 2}" r="68" fill="#000" opacity=".25"/>
  <circle cx="{cx}" cy="{cy}" r="68" fill="{NAVY}"/>
  <circle cx="{cx}" cy="{cy}" r="66.5" fill="none" stroke="{colour}" stroke-width="1.5" opacity=".9"/>
  <text font-family="{FONT}" font-size="8.4" font-weight="bold" fill="{GOLD}">
    <textPath href="#ring" startOffset="2" textLength="{2 * math.pi * 57 - 6:.1f}" lengthAdjust="spacing">★ GRADED ON LETSVIBE ★ VIBE-CODED LEADERBOARD</textPath>
  </text>
  <circle cx="{cx}" cy="{cy}" r="51" fill="url(#rim)"/>
  <circle cx="{cx}" cy="{cy}" r="43" fill="url(#face)" stroke="{deep}" stroke-opacity=".5"/>
  <circle cx="{cx}" cy="{cy}" r="39.5" fill="none" stroke="{hi}" stroke-opacity=".55" stroke-dasharray="1.5 2"/>
  {''.join(leaves)}
  <text x="{cx}" y="{cy + 9}" text-anchor="middle" font-family="Georgia,'Times New Roman',serif" font-weight="bold" font-size="{lsize}" fill="{hi}" opacity=".7">{letter}</text>
  <text x="{cx}" y="{cy + 7.5}" text-anchor="middle" font-family="Georgia,'Times New Roman',serif" font-weight="bold" font-size="{lsize}" fill="url(#ink)">{letter}</text>
  <text x="{cx}" y="{cy + 25}" text-anchor="middle" font-family="{FONT}" font-weight="bold" font-size="10" fill="{deep}">{p["score"]:.1f}</text>
  <path d="M{cx - 32} {cy - 18}a37 37 0 0 1 34 -19" fill="none" stroke="#fff" stroke-opacity=".5" stroke-width="3" stroke-linecap="round"/>'''
    return _svg(140, 140, f"LetsVibe grade: {p['letter']} ({p['score']:.1f})", body)


def card(project: Optional[Dict[str, Any]], scale: str = "raw") -> str:
    p = _summary(project, scale=scale)
    if not p["graded"]:
        return classic(project, scale=scale)
    colour, edge = GRADE_COLOURS[p["family"]]
    w, h = 400, 118
    name = p["name"]
    while text_width(name, 15, True) > 230 and len(name) > 4:
        name = name[:-2] + "…"
    bars = []
    for i, (key, label) in enumerate(FACTORS):
        v = project.get(f"{key}_score")
        v = max(0.0, min(100.0, float(v))) if v is not None else 0.0
        y = 50 + i * 12.5
        bars.append(f'''<text x="70" y="{y + 6.5:.1f}" font-size="8.5" fill="#94a3b8" textLength="{text_width(label, 8.5):.1f}">{label}</text>
    <rect x="128" y="{y:.1f}" width="150" height="6" rx="3" fill="#ffffff" fill-opacity=".08"/>
    <rect x="128" y="{y:.1f}" width="{1.5 * v:.1f}" height="6" rx="3" fill="url(#bar)"/>
    <text x="284" y="{y + 6.5:.1f}" font-size="8.5" fill="#cbd5e1">{v:.0f}</text>''')
    letter = escape(p["letter"])
    body = f'''<defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{NAVY_LIGHT}"/><stop offset="1" stop-color="#0b1020"/></linearGradient>
    <linearGradient id="bar" x2="1"><stop offset="0" stop-color="{CYAN}"/><stop offset="1" stop-color="{colour}"/></linearGradient>
    <linearGradient id="tile" x2="0" y2="1"><stop offset="0" stop-color="{colour}" stop-opacity=".28"/><stop offset="1" stop-color="{colour}" stop-opacity=".1"/></linearGradient>
  </defs>
  <rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="12" fill="url(#bg)" stroke="{colour}" stroke-opacity=".55"/>
  <rect x="14" y="14" width="44" height="44" rx="10" fill="#0b1020" stroke="{GOLD}" stroke-opacity=".35"/>
  {_trophy(19, 19, 2.4)}
  <g font-family="{FONT}">
    <text x="70" y="30" font-size="15" font-weight="bold" fill="#fff" textLength="{text_width(name, 15, True):.1f}">{escape(name)}</text>
    <text x="70" y="43" font-size="8.5" letter-spacing="1" fill="{GOLD}">GRADED ON LETSVIBE · VIBE-CODED</text>
    {''.join(bars)}
    <rect x="312" y="16" width="74" height="86" rx="12" fill="url(#tile)" stroke="{colour}" stroke-width="1.5"/>
    <text x="349" y="66" text-anchor="middle" font-family="Georgia,'Times New Roman',serif" font-weight="bold" font-size="{38 if len(p['letter']) == 1 else 32}" fill="{colour}">{letter}</text>
    <text x="349" y="88" text-anchor="middle" font-size="12" font-weight="bold" fill="#fff">{p["score"]:.1f}</text>
  </g>'''
    return _svg(w, h, f"{p['name']} on LetsVibe: {p['letter']} ({p['score']:.1f})", body)


RENDERERS = {"classic": classic, "for-the-badge": for_the_badge, "medal": medal, "card": card}


def render(style: str, project: Optional[Dict[str, Any]], scale: str = "raw") -> str:
    """SVG for `style` (unknown styles fall back to classic); project None draws a 'not graded' badge."""
    return RENDERERS.get(style, classic)(project, scale=scale)
