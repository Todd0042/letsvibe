"""Draws the LetsVibe site icon (usage: python scripts/make_site_icon.py <out_dir> <i-dot x> <i-dot y>; then render the SVGs to
website-icon.png/.webp, apple-touch-icon.png, favicon-32x32.png and favicon.ico). Draws: a gold trophy between syntax-highlighted lines of code (no audio waves),
with LET'S VIBE / LEADERBOARD below. Writes site-icon.svg (full) and site-icon-small.svg (no text, for tiny sizes)."""
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
DOT_X, DOT_Y = (float(v) for v in (sys.argv[2:4] if len(sys.argv) > 3 else (0, 0)))
CYAN, MAGENTA, VIOLET, GREEN, MUTED = "#22d3ee", "#f472b6", "#a78bfa", "#34d399", "#475569"

# Each code line: (indent, [(width, colour), ...]) read left to right, like highlighted code
LEFT = [(0, [(34, VIOLET), (58, CYAN)]),
        (1, [(46, CYAN), (22, MUTED), (30, MAGENTA)]),
        (2, [(38, GREEN), (40, CYAN)]),
        (2, [(60, MAGENTA), (18, MUTED)]),
        (1, [(28, VIOLET), (44, CYAN)]),
        (0, [(18, MUTED)])]
RIGHT = [(0, [(40, MAGENTA), (36, CYAN)]),
         (1, [(30, VIOLET), (52, CYAN)]),
         (2, [(44, CYAN), (20, MUTED), (24, GREEN)]),
         (2, [(54, GREEN), (26, CYAN)]),
         (1, [(36, CYAN), (30, MAGENTA)]),
         (0, [(18, MUTED)])]


def code_block(lines, x0, y0, side, line_h=26, bar_h=11, step=18, gap=8):
    """Rounded token bars. The left block is right-aligned toward the trophy; both fade toward the outer edge."""
    out = []
    for i, (indent, toks) in enumerate(lines):
        y = y0 + i * line_h
        total = sum(w for w, _ in toks) + gap * (len(toks) - 1) + indent * step
        x = (x0 - total) if side == "left" else x0
        x += indent * step if side == "right" else 0
        for w, col in toks:
            out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w}" height="{bar_h}" rx="{bar_h / 2}" fill="{col}"/>')
            x += w + gap
    return "".join(out)


def trophy(cx, top, s=1.0, plaque=True):
    """A polished gold cup: rim showing the opening, bevelled body, two-tone handles, knotted stem, stepped base with
    an engraved LV plaque (as on the original icon)."""
    def P(x, y):
        return f"{cx + x * s:.1f} {top + y * s:.1f}"
    w = lambda v: f"{v * s:.1f}"
    body = (f"M{P(-80, 0)} C{P(-80, 72)} {P(-56, 128)} {P(-14, 146)} L{P(-10, 158)} L{P(10, 158)} L{P(14, 146)} "
            f"C{P(56, 128)} {P(80, 72)} {P(80, 0)} Z")
    handle_l = f"M{P(-80, 22)} C{P(-132, 18)} {P(-134, 92)} {P(-60, 104)}"
    handle_r = f"M{P(80, 22)} C{P(132, 18)} {P(134, 92)} {P(60, 104)}"
    plate = ""
    if plaque:
        plate = (f'<rect x="{cx - 46 * s:.1f}" y="{top + 206 * s:.1f}" width="{w(92)}" height="{w(34)}" rx="{w(5)}" fill="#0f1a33" stroke="url(#gold-edge)" stroke-width="{w(2.5)}"/>'
                 f'<text x="{cx:.1f}" y="{top + 232 * s:.1f}" text-anchor="middle" font-family="Georgia,\'DejaVu Serif\',serif" '
                 f'font-weight="bold" font-size="{w(26)}" letter-spacing="{w(2)}" fill="url(#gold)">LV</text>')
    return f'''
  <g filter="url(#gold-glow)">
    <path d="{handle_l}" fill="none" stroke="#7c4a0c" stroke-width="{w(17)}" stroke-linecap="round"/>
    <path d="{handle_r}" fill="none" stroke="#7c4a0c" stroke-width="{w(17)}" stroke-linecap="round"/>
    <path d="{handle_l}" fill="none" stroke="url(#gold-h)" stroke-width="{w(11)}" stroke-linecap="round"/>
    <path d="{handle_r}" fill="none" stroke="url(#gold-h)" stroke-width="{w(11)}" stroke-linecap="round"/>
    <path d="{body}" fill="url(#gold-body)" stroke="#8a520e" stroke-width="{w(1.5)}"/>
    <path d="M{P(-58, 12)} C{P(-58, 70)} {P(-42, 112)} {P(-18, 130)}" fill="none" stroke="#fffbe6" stroke-opacity=".7" stroke-width="{w(8)}" stroke-linecap="round"/>
    <path d="M{P(-36, 14)} C{P(-36, 50)} {P(-30, 76)} {P(-22, 92)}" fill="none" stroke="#fffbe6" stroke-opacity=".35" stroke-width="{w(4)}" stroke-linecap="round"/>
    <ellipse cx="{cx:.1f}" cy="{top:.1f}" rx="{w(81)}" ry="{w(13)}" fill="url(#gold-edge)"/>
    <ellipse cx="{cx:.1f}" cy="{top + 1 * s:.1f}" rx="{w(70)}" ry="{w(8.5)}" fill="url(#gold-inside)"/>
    <ellipse cx="{cx:.1f}" cy="{top + 166 * s:.1f}" rx="{w(17)}" ry="{w(8)}" fill="url(#gold-h)" stroke="#8a520e" stroke-width="{w(1)}"/>
    <path d="M{P(-9, 172)} L{P(9, 172)} L{P(14, 194)} L{P(-14, 194)} Z" fill="url(#gold-h)"/>
    <path d="M{P(-60, 196)} L{P(60, 196)} L{P(70, 248)} L{P(-70, 248)} Z" fill="url(#gold-body)" stroke="#8a520e" stroke-width="{w(1.5)}"/>
    <rect x="{cx - 84 * s:.1f}" y="{top + 246 * s:.1f}" width="{w(168)}" height="{w(16)}" rx="{w(5)}" fill="url(#gold-dark)"/>
    <rect x="{cx - 84 * s:.1f}" y="{top + 246 * s:.1f}" width="{w(168)}" height="{w(3)}" rx="{w(1.5)}" fill="#fff3c4" fill-opacity=".6"/>
    {plate}
  </g>'''


DEFS = f'''<defs>
  <radialGradient id="bg" cx=".5" cy=".38" r=".75"><stop offset="0" stop-color="#1d2d52"/><stop offset=".6" stop-color="#111a33"/><stop offset="1" stop-color="#0a0f1f"/></radialGradient>
  <linearGradient id="gold" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff3c4"/><stop offset=".35" stop-color="#f7c948"/><stop offset=".7" stop-color="#d4951c"/><stop offset="1" stop-color="#a86410"/></linearGradient>
  <linearGradient id="gold-body" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#9a5a0e"/><stop offset=".18" stop-color="#e0a52a"/><stop offset=".36" stop-color="#fff1bd"/><stop offset=".52" stop-color="#f7c948"/><stop offset=".8" stop-color="#c47f16"/><stop offset="1" stop-color="#7c4a0c"/></linearGradient>
  <linearGradient id="gold-h" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff1bd"/><stop offset=".45" stop-color="#f2c14e"/><stop offset="1" stop-color="#a86410"/></linearGradient>
  <linearGradient id="gold-edge" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#b8741a"/><stop offset=".4" stop-color="#fff3c4"/><stop offset="1" stop-color="#b8741a"/></linearGradient>
  <linearGradient id="gold-inside" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5c3608"/><stop offset="1" stop-color="#b8741a"/></linearGradient>
  <linearGradient id="gold-dark" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d4951c"/><stop offset="1" stop-color="#7c4a0c"/></linearGradient>
  <linearGradient id="fade-l" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".45" stop-color="#fff" stop-opacity="1"/></linearGradient>
  <linearGradient id="fade-r" x1="1" y1="0" x2="0" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".45" stop-color="#fff" stop-opacity="1"/></linearGradient>
  <mask id="mask-l"><rect x="0" y="0" width="310" height="620" fill="url(#fade-l)"/></mask>
  <mask id="mask-r"><rect x="310" y="0" width="310" height="620" fill="url(#fade-r)"/></mask>
  <filter id="gold-glow" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#000" flood-opacity=".45"/></filter>
  <filter id="neon" x="-20%" y="-60%" width="140%" height="220%"><feGaussianBlur stdDeviation="5" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  <filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3"/></filter>
</defs>'''


def frame():
    return ('<rect width="620" height="620" rx="118" fill="url(#bg)"/>'
            '<rect x="6" y="6" width="608" height="608" rx="112" fill="none" stroke="#22d3ee" stroke-opacity=".18" stroke-width="3"/>')


def sparkle(x, y, r, col):
    return (f'<path d="M{x} {y - r} Q{x} {y} {x + r} {y} Q{x} {y} {x} {y + r} Q{x} {y} {x - r} {y} Q{x} {y} {x} {y - r}Z" '
            f'fill="{col}" filter="url(#neon)"/>')


full = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 620 620" width="620" height="620">{DEFS}
  {frame()}
  <g opacity=".95" filter="url(#neon)">
    <g mask="url(#mask-l)">{code_block(LEFT, 192, 140, "left")}</g>
    <g mask="url(#mask-r)">{code_block(RIGHT, 428, 140, "right")}</g>
  </g>
  {trophy(310, 62, 1.0)}
  <g font-family="'Montserrat','Poppins','DejaVu Sans',Arial,sans-serif" font-weight="800" text-anchor="middle">
    <text x="310" y="430" font-size="84" letter-spacing="3" fill="#22d3ee" opacity=".55" filter="url(#soft)">LET'S V\u0131BE</text>
    <text x="310" y="430" font-size="84" letter-spacing="3" fill="#e6fbff" id="title">LET'S V\u0131BE</text>
    <text x="310" y="500" font-size="38" letter-spacing="9" fill="#f2c14e">LEADERBOARD</text>
  </g>
  <text x="310" y="560" text-anchor="middle" font-family="'DejaVu Sans Mono',Menlo,Consolas,monospace" font-size="30" font-weight="bold" fill="#22d3ee" opacity=".75">&lt;/&gt;</text>
  {sparkle(DOT_X, DOT_Y, 15, "#67e8f9")}
</svg>
'''

small = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 620 620" width="620" height="620">{DEFS}
  {frame()}
  <g filter="url(#neon)">
    <g mask="url(#mask-l)">{code_block([(0, [(70, CYAN)]), (1, [(60, MAGENTA)]), (1, [(80, CYAN)]), (0, [(54, VIOLET)])], 196, 230, "left", line_h=52, bar_h=24, step=30, gap=12)}</g>
    <g mask="url(#mask-r)">{code_block([(0, [(70, MAGENTA)]), (1, [(80, CYAN)]), (1, [(56, GREEN)]), (0, [(64, CYAN)])], 424, 230, "right", line_h=52, bar_h=24, step=30, gap=12)}</g>
  </g>
  {trophy(310, 104, 1.55, plaque=False)}
</svg>
'''
(OUT / "site-icon.svg").write_text(full)
(OUT / "site-icon-small.svg").write_text(small)
print("wrote", OUT / "site-icon.svg", OUT / "site-icon-small.svg")
