"""
HTML dashboard renderer for LetsVibe.
Loads and prepares the frontend dashboard HTML.
"""

import json
from pathlib import Path
from typing import Optional

TEMPLATES_DIR = Path(__file__).parent / "templates"

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
    """The grading-curve page, generated from the calibration file the grader itself uses."""
    from html import escape

    from ..core.models import CORE_FACTORS, FACTOR_LABELS
    from ..storage.peer_grades import BELL_GRADES, bell_share, describe_method, load_calibration

    e = lambda v: escape(str(v))  # noqa: E731
    parts = ["<h1>The grading curve</h1>",
             '<p class="lede">LetsVibe grades on a curve, often called a bell curve. This page explains why and shows every '
             "number used to build it. It is generated directly from the calibration file the grader uses, so it always "
             "matches how grades are actually given.</p>"]
    cal = load_calibration()
    if cal is None:
        parts.append("<p>The curve has not been calibrated yet. Until it is, letter grades come from the rubric's score "
                     'bands shown on <a href="/how-it-works">How grading works</a>.</p>')
        return "\n".join(parts)
    bottom, top = cal["anchors"]["bottom"], cal["anchors"]["top"]
    parts += [
        "<h2>Why a curve</h2>", f"<p>{e(cal['why'])}</p>",
        "<h2>How it works</h2>", f"<p>{e(describe_method(cal))}</p>",
    ]
    rows, upper = [], float("inf")
    for cut, grade in BELL_GRADES:
        span = f"{cut:+g} and above" if upper == float("inf") else f"{cut:+g} to {upper:+g}"
        rows.append(f"<tr><td><strong>{e(grade)}</strong></td><td>{span}</td><td>{bell_share(cut, upper):.1%}</td></tr>")
        upper = cut
    rows.append(f"<tr><td><strong>F</strong></td><td>below {upper:+g}</td><td>{bell_share(float('-inf'), upper):.1%}</td></tr>")
    parts.append('<div class="table-scroll"><table><thead><tr><th>Grade</th><th>Standard deviations from the average</th>'
                 f"<th>Share of a full bell curve</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>")
    parts.append("<p>Raw scores are never changed; reports show the raw score, the letter, and how far from the average the "
                 "score sits.</p>")
    parts.append("<h2>The reference projects</h2><p>Two small apps were written specifically to set the ends of the curve. "
                 "Both are public, both are vibe-coded by this site's own definition, and neither ever appears in the "
                 "rankings.</p>")
    for role, label in (("top", "Top of the curve (100%)"), ("bottom", "Bottom of the curve (0%)")):
        a = cal["anchors"][role]
        rep = a.get("report", {})
        head = "".join(f"<th>{e(FACTOR_LABELS.get(f, f))}</th>" for f in CORE_FACTORS)
        run_rows = "".join(
            f"<tr><td>{i}</td><td><strong>{r['score']:g}</strong></td><td>{r['minutes']:g} min</td>"
            + "".join(f"<td>{r['factors'].get(f, 0):g}</td>" for f in CORE_FACTORS) + "</tr>"
            for i, r in enumerate(a["runs"], 1))
        lists = "".join(f"<h4>{e(t)}</h4><ul>{''.join(f'<li>{e(x)}</li>' for x in rep.get(k) or [])}</ul>"
                        for t, k in (("What works", "top_solid_features"), ("Needs work", "top_features_needing_work"),
                                     ("What a pro team would do next", "pro_next_steps")) if rep.get(k))
        parts += [
            f"<h3>{e(label)}: {e(a['name'])}</h3>",
            f'<p><a href="{e(a["url"])}" rel="noopener">{e(a["repo"])}</a> (commit <code>{e(a["commit"])}</code>). {e(a["made_by"])}</p>',
            f"<p>The exact prompt:</p><blockquote>{e(a['prompt'])}</blockquote>",
            f"<p>Graded {len(a['runs'])} times: average <strong>{a['mean']:g}</strong>, range {a['min']:g}&ndash;{a['max']:g} "
            f"(standard deviation {a['stdev']:g}).</p>",
            f'<div class="table-scroll"><table><thead><tr><th>Run</th><th>Raw score</th><th>Time</th>{head}</tr></thead>'
            f"<tbody>{run_rows}</tbody></table></div>",
            f"<p>The review shown here is from the run closest to the average: <em>{e(rep.get('verdict_title', ''))}</em>. "
            f"{e(rep.get('peer_summary', ''))}</p>{lists}",
        ]
    models = cal.get("models", {})
    parts += [
        "<h2>Calibration details</h2><ul>",
        f"<li>Calibrated: {e(cal['created'])}</li>",
        f"<li>Rubric: {e(cal['rubric_version'])}; pipeline recipe: {e(cal['pipeline'])} (the same one every submission goes through)</li>",
        f"<li>Models: {e(models.get('chunk_reviewer', ''))} reads the code in chunks; {e(models.get('whole_project_reviewer', ''))} "
        "reviews the whole project and answers the checklist. Both run on LetsVibe's own hardware.</li>",
        f"<li>Runs per reference project: {e(cal['runs_per_anchor'])}</li>",
        "<li>The curve is rebuilt whenever the models, the rubric or the pipeline change, using "
        "<code>scripts/calibrate_curve.py</code>, and this page updates with it.</li>",
        f"<li>Note: {e(cal['notes'])}</li>" if cal.get("notes") else "",
        "</ul>",
    ]
    return "\n".join(parts)


def _fill_live_tables(content: str) -> str:
    """Tables generated from the code the grader actually uses, so the docs cannot drift from reality."""
    from html import escape

    from ..core.models import CORE_FACTORS
    from ..evaluator.consensus import GRADE_CUTOFFS
    from ..evaluator.rubric import RUBRIC_VERSION
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
