"""
HTML dashboard renderer for LetsVibe.
Loads and prepares the frontend dashboard HTML.
"""

import json
import os
from pathlib import Path
from typing import Optional

TEMPLATES_DIR = (
    Path(os.environ["LETSVIBE_TEMPLATES_DIR"])
    if os.environ.get("LETSVIBE_TEMPLATES_DIR")
    else (Path(__file__).parent / "templates")
)

# Content pages: slug -> (title, meta description). Bodies live in templates/pages/<slug>.html
PAGES = {
    "sign-in": ("Sign in", "Sign in to LetsVibe with GitHub. Accept the Terms of Use first; you must be 18 or older."),
    "grading-curve": ("Grading curve", "LetsVibe grades on a curve (a bell curve): why, how the curve was built, and every number behind it."),
    "how-it-works": ("How grading works", "How LetsVibe grades vibe-coded projects with local AI models, step by step."),
    "faq": ("FAQ", "Answers to common questions about LetsVibe scores, submissions and appeals."),
    "privacy": ("Privacy policy", "What LetsVibe collects, why, how long it is kept and how to delete it."),
    "cookies": ("Cookie policy", "The only cookies and browser storage LetsVibe uses."),
    "terms": ("Terms of use", "The terms for using LetsVibe and its scores."),
    "remove": ("Remove my project", "How to remove your project or your whole account from LetsVibe."),
    "support": ("Help & contact", "How to get help, report a problem or support LetsVibe."),
    "changelog": ("Rubric changelog", "Every change to how LetsVibe grades projects."),
    "my-data": ("My Data", "Manage, export or delete your LetsVibe projects and account."),
    "my-projects": ("My Projects", "Your graded projects, requests that need your input, and error reports."),
}
NAV_KEYS = {"how-it-works": "NAV_HOW", "faq": "NAV_FAQ", "my-data": "NAV_DATA", "my-projects": "NAV_PROJECTS"}


def render_footer() -> str:
    from datetime import date

    from ..evaluator.rubric import RUBRIC_VERSION

    html = (TEMPLATES_DIR / "partials" / "footer.html").read_text(encoding="utf-8")
    return html.replace("{{YEAR}}", str(date.today().year)).replace("{{RUBRIC_VERSION}}", RUBRIC_VERSION)


def _render_changelog() -> str:
    from html import escape

    from ..evaluator.rubric import RUBRIC_CHANGELOG, RUBRIC_VERSION

    parts = [
        "<h1>Rubric changelog</h1>",
        f'<p class="lede">Every report records the rubric version it was graded with. The current rubric is '
        f"<strong>{escape(RUBRIC_VERSION)}</strong>. Scores graded under different versions may not be directly "
        'comparable; owners can request a fresh grade after pushing a new commit. <a href="/how-it-works">How grading works</a></p>',
    ]
    for entry in RUBRIC_CHANGELOG:
        items = "".join(f"<li>{escape(str(c))}</li>" for c in entry["changes"])
        parts.append(
            f'<h2 id="{escape(str(entry["version"]))}">{escape(str(entry["version"]))}: {escape(str(entry["title"]))}</h2>'
            f'<p class="updated">{escape(str(entry["date"]))}</p><ul>{items}</ul>'
        )
    return "\n".join(parts)


def _render_curve() -> str:
    """The grading-curve page, generated from the live curve constants in storage/peer_grades.py."""
    from html import escape

    from ..storage.peer_grades import (
        DEFAULT_CURVE_HIGH,
        DEFAULT_CURVE_LOW,
        GLOBAL_PRIOR_MEAN,
        OUTLIER_THRESHOLD,
        PEER_MIN_PROJECTS,
        SCHOOL_GRADE_CUTOFFS,
        WHY,
        describe_method,
        load_calibration,
    )

    e = lambda v: escape(str(v))  # noqa: E731
    cal = load_calibration() or {}
    parts = [
        "<h1>The grading curve</h1>",
        '<p class="lede">LetsVibe grades on a single site-wide bell curve computed dynamically from raw rubric scores '
        "(never stored in the database). You can switch between <strong>Bell Curve</strong> and "
        "<strong>Raw Rubric</strong> on any leaderboard or project page.</p>",
        "<h2>Why a curve</h2>",
        f"<p>{e(WHY)}</p>",
        "<h2>How it works</h2>",
        f"<p>{e(describe_method(cal))}</p>",
        "<ul>"
        f"<li><strong>Default anchors:</strong> Raw <strong>{DEFAULT_CURVE_LOW:g}</strong> maps to <strong>0</strong>, "
        f"raw <strong>{DEFAULT_CURVE_HIGH:g}</strong> maps to <strong>100</strong>, and the prior midpoint "
        f"<strong>{GLOBAL_PRIOR_MEAN:g}</strong> maps to <strong>75 (C)</strong>.</li>"
        f"<li><strong>Automatic scaling over time:</strong> Once <strong>{OUTLIER_THRESHOLD}</strong> or more "
        f"projects score above {DEFAULT_CURVE_HIGH:g} (or below {DEFAULT_CURVE_LOW:g}), that endpoint shifts "
        f"automatically to the {OUTLIER_THRESHOLD}th-highest (or {OUTLIER_THRESHOLD}th-lowest) raw score.</li>"
        f"<li><strong>Adaptive spread:</strong> As more public projects are graded, the curve blends smoothly from "
        f"the prior toward the real site-wide average and standard deviation (fully at {PEER_MIN_PROJECTS} projects).</li>"
        "<li><strong>Single global scale:</strong> Every project shares the same base curve regardless of category, so "
        "base curved rank order matches Raw Rubric rank order before community Sparks are added.</li>"
        "</ul>",
        "<h2>Community Sparks (upvotes)</h2>",
        "<p>Signed-in users can give a <strong>Spark (&#10024;)</strong> upvote to public projects. Sparks act as a "
        "bounded community nudge on the <strong>Bell Curve</strong> view while keeping the underlying AI evaluation intact:</p>",
        "<ul>"
        "<li><strong>Who can Spark:</strong> Signed-in GitHub accounts only (unlimited across other people's projects; "
        "at most <strong>one</strong> of your own repositories).</li>"
        "<li><strong>Exact formula:</strong> Each project receives a diminishing-returns bonus of "
        "<code>min(3.0, 0.5 &times; log<sub>2</sub>(1 + sparks))</code> added to its Bell Curve score (clamped at 100.0). "
        "For example: 1 Spark = <strong>+0.50</strong>, 3 Sparks = <strong>+1.00</strong>, 7 Sparks = <strong>+1.50</strong>, "
        "and the maximum possible boost is <strong>+3.00</strong>.</li>"
        "<li><strong>Raw Rubric stays pure:</strong> Sparks never change the <strong>Raw Rubric</strong> score or factor scores. "
        "Toggle <strong>Raw Rubric</strong> on any leaderboard or project page to see the uncurved AI score alone.</li>"
        "<li><strong>Privacy &amp; deletion:</strong> Your Spark is removed if you click it again or delete your account.</li>"
        "</ul>",
    ]

    rows, upper = [], 100.0
    for cut, grade in SCHOOL_GRADE_CUTOFFS:
        span = f"{cut:g}&ndash;100" if upper == 100.0 else f"{cut:g}&ndash;{upper - 0.1:.1f}"
        rows.append(f"<tr><td><strong>{e(grade)}</strong></td><td>{span}</td></tr>")
        upper = cut
    rows.append(f"<tr><td><strong>F</strong></td><td>below {upper:g}</td></tr>")

    parts.append("<h2>Curved letter grades</h2>")
    parts.append(
        '<div class="table-scroll"><table><thead><tr><th>Grade</th><th>Curved score range (0&ndash;100)</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
    parts.append(
        "<p>Raw scores are never modified; reports and API responses always preserve the uncurved rubric score "
        "alongside the Bell Curve score.</p>"
    )
    return "\n".join(parts)


def _fill_live_tables(content: str) -> str:
    """Tables generated from the code the grader actually uses, so the docs cannot drift from reality."""
    from html import escape

    from ..core.models import CORE_FACTORS
    from ..evaluator.rubric import GRADE_CUTOFFS, RUBRIC_VERSION
    from ..evaluator.weights import CATEGORY_ROW, GLOBAL_RANGES, WEIGHT_RANGE_ROWS
    from ..core.platforms import GENERAL_CATEGORIES

    from ..core.models import FACTOR_LABELS as labels
    if "{{WEIGHT_TABLE}}" in content:
        head = "".join(f"<th>{labels[f]}</th>" for f in CORE_FACTORS)
        rows = []
        for row_key, (label, ranges) in WEIGHT_RANGE_ROWS.items():
            cats = [GENERAL_CATEGORIES[c] for c, r in CATEGORY_ROW.items() if r == row_key]
            if not cats:
                continue
            cells = "".join(f"<td>{ranges[f][0]:g}&ndash;{ranges[f][1]:g}%</td>" for f in CORE_FACTORS)
            rows.append(f"<tr><th scope=\"row\">{escape(', '.join(cats))}</th>{cells}</tr>")
        others = [GENERAL_CATEGORIES[c] for c in GENERAL_CATEGORIES if c not in CATEGORY_ROW]
        cells = "".join(f"<td>{GLOBAL_RANGES[f][0]:g}&ndash;{GLOBAL_RANGES[f][1]:g}%</td>" for f in CORE_FACTORS)
        rows.append(f"<tr><th scope=\"row\">{escape(', '.join(others))}</th>{cells}</tr>")
        table = f'<div class="table-scroll"><table><thead><tr><th>Category</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        content = content.replace("{{WEIGHT_TABLE}}", table)
    if "{{POINTS_TABLE}}" in content:
        # Rubric v8: every checklist answer and the points it earns, straight from evaluator/experience/scoring.py
        try:
            from ..evaluator.experience.scoring import POINTS, WEIGHTS
            titles = {"value": "Real-world value", "creativity": "Creativity", "journey": "User journey", "functionality": "Functionality", "ui_ux": "UI/UX"}
            rows = []
            for cat, fields in POINTS.items():
                for field, table in fields.items():
                    if isinstance(table, dict):
                        choices = ", ".join(f"{str(k).replace('_', ' ').lower()} {v:+g}" for k, v in table.items())
                        rows.append(f"<tr><td>{titles[cat]}</td><td>{escape(field.replace('_', ' '))}</td><td>{escape(choices)}</td></tr>")
                if cat == "value":
                    rows.append(f"<tr><td>{titles[cat]}</td><td>features beyond the basic idea (quoted from the dossier)</td>"
                                f"<td>{fields['verified_twist_each']:+g} each, up to {fields['verified_twist_max']:+g}</td></tr>")
                if cat == "functionality":
                    rows.append(f"<tr><td>{titles[cat]}</td><td>proven problems found by the code scan (for example endpoints nothing calls, "
                                f"config files that don't parse)</td><td>&minus;{fields['proven_problem_rate_penalty']:g} per problem per 1,000 lines "
                                f"of code, up to &minus;{fields['proven_problem_penalty_max']:g}</td></tr>")
                if cat == "journey":
                    rows.append(f"<tr><td>{titles[cat]}</td><td>ads, payments or a paywall, or a required sign-in service found in the code (a library or code name; never words in story or on-screen text)</td>"
                                f"<td>{fields['hostile_penalty']:+g}</td></tr>")
            weights = ", ".join(f"{titles[c]} {w:g}%" for c, w in WEIGHTS.items())
            table = ('<div class="table-scroll"><table><thead><tr><th>Factor</th><th>Checklist item</th><th>Answers and points</th></tr></thead>'
                     f'<tbody>{"".join(rows)}</tbody></table></div><p>Each factor scores the share of its points earned (0&ndash;100). '
                     f'The overall score weights them: {escape(weights)}.</p>')
        except ImportError:
            table = '<p>Overall factor weights: Real-world value 25%, Creativity 22%, User journey 20%, Functionality 18%, UI/UX 15%.</p>'
        content = content.replace("{{POINTS_TABLE}}", table)
    if "{{GRADE_TABLE}}" in content:
        rows = []
        upper = 100.0
        for cutoff, grade in GRADE_CUTOFFS:
            rows.append(f"<tr><td><strong>{grade}</strong></td><td>{cutoff:g}&ndash;{upper:g}</td></tr>")
            upper = cutoff - 0.1
        rows.append(f"<tr><td><strong>F</strong></td><td>below {GRADE_CUTOFFS[-1][0]:g}</td></tr>")
        content = content.replace(
            "{{GRADE_TABLE}}",
            f'<div class="table-scroll"><table><thead><tr><th>Grade</th><th>Score</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>',
        )
    from ..core.legal import TERMS_VERSION

    return content.replace("{{RUBRIC_VERSION}}", RUBRIC_VERSION).replace("{{TERMS_VERSION}}", TERMS_VERSION)


def render_page(slug: str) -> str:
    """Renders a content page inside the shared shell (header, footer). Raises KeyError for unknown pages."""
    from html import escape

    title, description = PAGES[slug]
    if slug == "changelog":
        content, script = _render_changelog(), ""
    elif slug == "grading-curve":
        content, script = _render_curve(), ""
    else:
        raw = (TEMPLATES_DIR / "pages" / f"{slug}.html").read_text(encoding="utf-8")
        content, _, script = raw.partition("<!--SCRIPT-->")
    content = _fill_live_tables(content)
    html = (TEMPLATES_DIR / "page.html").read_text(encoding="utf-8")
    for key in NAV_KEYS.values():
        html = html.replace("{{" + key + "}}", ' aria-current="page"' if NAV_KEYS.get(slug) == key else "")
    return (
        html.replace("{{TITLE}}", escape(title))
        .replace("{{DESCRIPTION}}", escape(description))
        .replace("{{CONTENT}}", content)
        .replace("{{FOOTER}}", render_footer())
        .replace("{{SCRIPT}}", script)
    )


def render_dashboard_html(view: str = "user", project_slug: Optional[str] = None) -> str:
    template_path = TEMPLATES_DIR / "dashboard.html"
    html = template_path.read_text(encoding="utf-8")
    # JSON-encode (and break "</script>") so a crafted URL can never inject script
    def js(value: Optional[str]) -> str:
        return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    from ..core.legal import TERMS_VERSION

    injection = (f"<script>window.__TERMS_VERSION__ = {js(TERMS_VERSION)};</script>\n"
                 f"<script>window.__INITIAL_VIEW__ = {js(view)}; window.__PROJECT_SLUG__ = {js(project_slug)};</script>\n</head>")
    html = html.replace("<!--SITE_FOOTER-->", render_footer(), 1)
    return html.replace("</head>", injection, 1)


def render_admin_messages_html() -> str:
    """Serves the dedicated full-page admin direct-chat & tag triage workspace."""
    return (TEMPLATES_DIR / "admin_messages.html").read_text(encoding="utf-8")
