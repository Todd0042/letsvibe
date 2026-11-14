"""Project context (rubric v9.2): what a project says it is, gathered once and shared, never used as evidence."""

import json
import subprocess

from letsvibe.evaluator.experience import context, rubric, scoring

BIG_README = """# Tiki Lounge [![build](https://img.shields.io/badge/a-b%20(c)-green.svg)](https://x)

> Turn any TV and your guests' phones into a karaoke lounge in 60 seconds, with no apps to install.

## Features
- Fair round-robin singer queue
- Pitch and key shifting

## Quick Start
Run it.

## Configuration
""" + "\n".join(f"## Section {i}\nlots of text here\n" for i in range(40))


def test_readme_parts_reads_only_what_a_visitor_reads():
    parts = context.readme_parts(BIG_README)
    assert parts["title"] == "Tiki Lounge"
    assert parts["opening"].startswith("Turn any TV") and "shields" not in parts["opening"]
    assert parts["toc"][:3] == ["Features", "Quick Start", "Configuration"]
    assert parts["features"] == ["Fair round-robin singer queue", "Pitch and key shifting"]
    assert len(context.sources_text({"readme": parts})) < 2500   # a huge README costs no more than its outline


def test_readme_facts_and_note():
    f = context.readme_facts(BIG_README, ["README.md", "LICENSE"])
    assert f["quick_start_section"] == 2 and f["license"] and f["screenshots"] == 0
    assert "early on" in context.readme_note(f) and "no screenshots" in context.readme_note(f)
    none = context.readme_facts("", ["main.py"])
    assert not none["has_readme"] and context.readme_note(none).startswith("No README")


def test_kind_from_files():
    assert context.kind_from_files({"app/src/main/AndroidManifest.xml": "<manifest/>"}) == "mobile app"
    assert context.kind_from_files({"package.json": json.dumps({"dependencies": {"@modelcontextprotocol/sdk": "1"}})}) == "AI assistant plugin"
    assert context.kind_from_files({"manifest.json": '{"manifest_version": 3}'}) == "browser extension"
    assert context.kind_from_files({"hub.py": "from PyQt6 import QtWidgets"}) == "desktop app"
    assert context.kind_from_files({"hub.py": "from PyQt6 import QtWidgets", "server.py": "from fastapi import FastAPI"}) is None
    assert context.kind_from_files({"main.py": "print(1)"}) is None


SOURCES = {"github": {"description": "A layout engine for AI diagrams", "topics": [], "homepage": "", "releases": []},
           "readme": {"title": "Sketchy", "opening": "Sketchy lets your AI assistant draw while it explains.", "toc": ["Install"], "features": []},
           "commits": [], "package": "", "windows": [], "kind_from_files": "AI assistant plugin",
           "readme_facts": {"has_readme": True, "quick_start_section": 1, "screenshots": 2, "license": True}}


def _gen(answer):
    return lambda **kw: answer


def test_understand_uses_checked_quotes_and_firm_kinds():
    ctx = context.understand(_gen({"purpose": "Lets an AI assistant draw diagrams while it explains.", "audience": "people using AI chat",
                                   "kind": "web app", "stated_features": ["hand-drawn diagrams"],
                                   "evidence": ["lets your AI assistant draw while it explains"]}), "m", SOURCES)
    assert ctx["verified"] and ctx["purpose"].startswith("Lets an AI assistant")
    assert ctx["kind"] == "AI assistant plugin"            # the files decide; the model's "web app" is ignored
    assert ctx["strength"] == "strong"


def test_understand_falls_back_to_the_projects_own_words_on_invented_quotes():
    ctx = context.understand(_gen({"purpose": "A drawing and painting app.", "audience": "artists", "kind": "desktop app",
                                   "stated_features": [], "evidence": ["the best paint app ever made"]}), "m", SOURCES)
    assert not ctx["verified"] and ctx["purpose"] == "A layout engine for AI diagrams"


def test_platform_hints_can_be_refined():
    s = {**SOURCES, "kind_from_files": "mobile app"}
    ctx = context.understand(_gen({"purpose": "A voice-controlled fantasy RPG for Android.", "audience": "players", "kind": "game",
                                   "stated_features": [], "evidence": ["Sketchy lets your AI assistant draw"]}), "m", s)
    assert ctx["kind"] == "game"


def test_no_sources_means_thin_context_and_no_model_call():
    calls = []
    empty = {"github": {}, "readme": {}, "commits": [], "package": "", "windows": [], "kind_from_files": None,
             "readme_facts": {"has_readme": False}}
    ctx = context.understand(lambda **kw: calls.append(kw) or {}, "m", empty)
    assert calls == [] and ctx["strength"] == "thin" and ctx["readme_note"].startswith("No README")


def test_github_meta_is_skipped_quietly_for_private_or_missing_repos():
    class R:
        status_code = 404
    assert context.github_meta("someone", "private-repo", get=lambda url: R())["description"] == ""
    assert context.github_meta("local", "x", get=lambda url: 1 / 0)["topics"] == []


def test_commit_notes_keep_explanations_and_drop_trailers(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    msg = ("feat: add the singer queue\n\nGuests join from their phones and the queue rotates fairly so nobody sings twice "
           "in a row.\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg], check=True)
    notes = context.commit_notes(tmp_path)
    assert notes and notes[0].startswith("feat: add the singer queue: Guests join") and "Co-Authored" not in notes[0]


def test_checklist_quotes_from_the_context_never_count_as_evidence():
    """The checklists see the project's own claims first, but a quote found only there stays unverified."""
    dossier_md = "## 2. What it actually does\n- Screens: login, board"
    context_md = context.render({"purpose": "A karaoke lounge with fair singer queues", "kind": "web app", "strength": "strong"})
    answers = {"core_flow": {"answer": "WORKS", "evidence": ["karaoke lounge with fair singer queues"]}}
    checked = rubric.check(answers, dossier_md)
    assert checked["core_flow"]["verified"] is False
    assert "karaoke lounge" in rubric.prompt(dossier_md, "functionality", "", context_md)   # but the model did see it


def test_detect_project_profile_unknown_fallback():
    # If files and README give no strong signals, fall back to unknown_or_general
    key, data = context.detect_project_profile({"main.py": "x = 1\ny = 2"}, kind_hint=None, readme_raw="# My App\nA simple script.")
    assert key == "unknown_or_general"
    assert data["name"] == "General Software / Unknown Archetype"


def test_rubric_prompt_injects_profile_guidance():
    profile = {
        "name": "Developer Tool / MCP Server",
        "evaluation_lenses": {
            "ui_ux": {
                "focus": "Ergonomics: schema precision and clear CLI flags.",
                "exemptions": ["screen_reader_labels", "avoidance_of_technical_jargon"],
                "clarity_definition": "Technical terminology is an asset, not clutter."
            }
        }
    }
    p = rubric.prompt("dossier text", "ui_ux", profile_data=profile)
    assert "Specific Evaluation Lenses for Developer Tool / MCP Server" in p
    assert "screen_reader_labels" in p
    assert "Technical terminology is an asset" in p


def test_scoring_exemptions_full_credit():
    profile = {
        "evaluation_lenses": {
            "ui_ux": {
                "exemptions": ["screen_reader_labels", "avoidance_of_technical_jargon"]
            }
        }
    }
    # For a developer tool, lack of accessibility labels or presence of technical wording should not drop points
    answers = {
        "clarity": {"answer": "CLUTTERED_OR_TECHNICAL"},
        "consistency": {"answer": "CONSISTENT"},
        "feedback_while_working": {"answer": "CLEAR"},
        "fits_its_device": {"answer": "WELL"},
        "accessibility_labels": {"answer": "NONE"},
    }
    res = scoring.category("ui_ux", answers, hostile_found=[], profile_data=profile)
    # clarity (25) + consistency (20) + feedback (20) + fits (25) + accessibility (10) = 100/100
    assert res["score"] == 100.0


def test_detect_project_profile_media_lounge_precedence():
    files = {
        "app.py": "from fastapi import FastAPI\nimport websockets\n# karaoke party player\nrequestAnimationFrame(renderWaveform)",
        "README.md": "# Tiki Karaoke\nA self-hosted karaoke party server with live QR mobile queueing."
    }
    key, data = context.detect_project_profile(files, kind_hint=None, readme_raw=files["README.md"])
    assert key == "media_or_social_lounge"
    assert data["name"] == "Media / Audio / Local Social Lounge"


def test_drop_contradictions_zero_install():
    from letsvibe.evaluator.experience.describe import drop_contradictions
    text = (
        "Tiki Karaoke falls short by requiring guests to install an app, which can be a barrier for some users. "
        "It offers a self-hosted solution with live QR mobile queueing."
    )
    answers = {
        "is_default_prompt_clone": {
            "answer": False,
            "evidence": ["Zero app install for guests", "Local data ownership"]
        }
    }
    cleaned = drop_contradictions(answers, text, extra_findings=["- Goes beyond with: Zero app install for guests"], has_shortfall=False)
    assert "requiring guests to install an app" not in cleaned
    assert "It offers a self-hosted solution" in cleaned

