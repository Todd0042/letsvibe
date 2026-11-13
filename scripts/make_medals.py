"""Draws the podium medals (gold, silver, bronze) as self-contained SVG files.

Usage: python scripts/make_medals.py letsvibe/web/static/medals
"""
import math, sys
from pathlib import Path

OUT = Path(sys.argv[1])

METALS = {
    1: dict(name="gold",   hi="#fffbe6", light="#fde68a", mid="#f5b524", dark="#b45309", deep="#7c3a06",
            rib_a="#4c1d95", rib_b="#6d28d9", stripe="#fbbf24"),
    2: dict(name="silver", hi="#ffffff", light="#e2e8f0", mid="#b6c2d1", dark="#64748b", deep="#334155",
            rib_a="#1e3a8a", rib_b="#2563eb", stripe="#cbd5e1"),
    3: dict(name="bronze", hi="#ffedd5", light="#f8b878", mid="#d97a2b", dark="#9a4a12", deep="#5c2a08",
            rib_a="#134e4a", rib_b="#0f766e", stripe="#e59a55"),
}

def laurel(cx, cy, r, color, side):
    """Leaves along the lower part of the rim, curling up the left (-1) or right (+1) side."""
    leaves = []
    for i in range(7):
        a = math.radians(100 + i * 16)            # from the bottom, up the side
        ang = math.pi - a if side > 0 else a
        x, y = cx + r * math.cos(ang), cy + r * math.sin(ang)
        tilt = math.degrees(ang) + (90 if side > 0 else -90) + 28 * side
        leaves.append(f'<ellipse cx="{x:.2f}" cy="{y:.2f}" rx="3.1" ry="1.35" transform="rotate({tilt:.1f} {x:.2f} {y:.2f})" fill="{color}"/>')
    return "".join(leaves)

def medal(rank, m):
    cx, cy = 32, 52
    sparkles = ""
    if rank == 1:
        star = lambda x, y, s: (f'<path d="M{x} {y-s} Q{x} {y} {x+s} {y} Q{x} {y} {x} {y+s} Q{x} {y} {x-s} {y} Q{x} {y} {x} {y-s}Z" '
                                f'fill="#fffbe6" opacity=".95"/>')
        sparkles = star(55, 34, 4.2) + star(9, 40, 2.8) + star(52, 72, 2.4)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 80" role="img" aria-label="#{rank} {m["name"]} medal">
  <defs>
    <linearGradient id="r" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{m["rib_a"]}"/><stop offset=".5" stop-color="{m["rib_b"]}"/><stop offset="1" stop-color="{m["rib_a"]}"/></linearGradient>
    <linearGradient id="rim" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{m["light"]}"/><stop offset=".45" stop-color="{m["mid"]}"/><stop offset=".75" stop-color="{m["dark"]}"/><stop offset="1" stop-color="{m["light"]}"/></linearGradient>
    <radialGradient id="face" cx=".36" cy=".3" r=".8"><stop offset="0" stop-color="{m["hi"]}"/><stop offset=".35" stop-color="{m["light"]}"/><stop offset=".75" stop-color="{m["mid"]}"/><stop offset="1" stop-color="{m["dark"]}"/></radialGradient>
    <linearGradient id="num" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{m["dark"]}"/><stop offset="1" stop-color="{m["deep"]}"/></linearGradient>
  </defs>
  <!-- ribbon: two folded bands meeting behind the medal -->
  <path d="M12 0h15l12 34-11 6z" fill="url(#r)"/>
  <path d="M37 0h15L36 40l-11-6z" fill="url(#r)"/>
  <path d="M18 0h3.5l11 31-2.6 1.5z" fill="{m["stripe"]}" opacity=".9"/>
  <path d="M42.5 0H46L34 32.5l-2.6-1.5z" fill="{m["stripe"]}" opacity=".9"/>
  <path d="M12 0h40v3H12z" fill="#000" opacity=".25"/>
  <!-- clasp -->
  <rect x="25" y="29" width="14" height="6" rx="2" fill="url(#rim)" stroke="{m["deep"]}" stroke-width=".6"/>
  <!-- medal -->
  <circle cx="{cx}" cy="{cy + 1.5}" r="24" fill="#000" opacity=".3"/>
  <circle cx="{cx}" cy="{cy}" r="24" fill="url(#rim)"/>
  <circle cx="{cx}" cy="{cy}" r="19.6" fill="url(#face)" stroke="{m["deep"]}" stroke-opacity=".45" stroke-width=".8"/>
  <circle cx="{cx}" cy="{cy}" r="17.4" fill="none" stroke="{m["hi"]}" stroke-opacity=".55" stroke-width=".6" stroke-dasharray="1.2 1.6"/>
  {laurel(cx, cy, 21.8, m["deep"], -1)}
  {laurel(cx, cy, 21.8, m["deep"], +1)}
  <text x="{cx}" y="{cy + 7.6}" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-weight="700" font-size="22" fill="{m["hi"]}" opacity=".7">{rank}</text>
  <text x="{cx}" y="{cy + 7}" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-weight="700" font-size="22" fill="url(#num)">{rank}</text>
  <!-- shine -->
  <path d="M17 44a17 17 0 0 1 20-11" fill="none" stroke="#fff" stroke-opacity=".55" stroke-width="2.2" stroke-linecap="round"/>
  {sparkles}
</svg>
'''

for rank, m in METALS.items():
    (OUT / f"medal-{rank}.svg").write_text(medal(rank, m))
    print("wrote", OUT / f"medal-{rank}.svg")
