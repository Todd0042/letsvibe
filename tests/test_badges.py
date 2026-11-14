"""Badge of honor images (web/badges.py) and the public /badge/{owner}/{repo}.svg endpoint."""

import xml.dom.minidom  # nosec B408 - parses only SVG this project generates

import pytest
from fastapi.testclient import TestClient

from letsvibe.storage import peer_grades as pg
from letsvibe.storage.db import save_report_to_db
from letsvibe.storage.identity import parse_repo_identity
from letsvibe.web.badges import STYLES, render
from letsvibe.web.server import app

PROJECT = {"repo_name": "my-app", "letter_grade": "A-", "curved_score": 92.9, "resonance_score": 90,
           "creativity_score": 80, "user_journey_score": 70, "functionality_score": 60, "ui_ux_score": None}


@pytest.fixture(autouse=True)
def _no_curve(monkeypatch):
    monkeypatch.setattr(pg, "load_calibration", lambda: None)


@pytest.mark.parametrize("style", STYLES)
def test_every_style_is_valid_svg_with_the_grade(style):
    svg = render(style, PROJECT)
    doc = xml.dom.minidom.parseString(svg)  # nosec B318 - our own generated SVG
    assert doc.documentElement.tagName == "svg"
    assert "A-" in svg and "92.9" in svg
    assert "<script" not in svg and "href=\"http" not in svg  # nothing that runs or loads from elsewhere


@pytest.mark.parametrize("style", STYLES)
def test_not_graded_badge(style):
    svg = render(style, None)
    xml.dom.minidom.parseString(svg)  # nosec B318 - our own generated SVG
    assert "not graded" in svg.lower()


def test_names_are_escaped_and_long_names_shortened():
    svg = render("card", {**PROJECT, "repo_name": "<b>&\"x" + "y" * 80})
    xml.dom.minidom.parseString(svg)  # nosec B318 - our own generated SVG
    assert "<b>" not in svg and "…" in svg


def test_medal_metal_follows_the_grade():
    assert "#f5b524" in render("medal", {**PROJECT, "letter_grade": "A"})      # gold
    assert "#b6c2d1" in render("medal", {**PROJECT, "letter_grade": "B+"})     # silver
    assert "#d97a2b" in render("medal", {**PROJECT, "letter_grade": "C"})      # bronze


def _add(slug, score, is_test=False):
    rep = {"final_vibe_score": score, "letter_grade": "?", "archetype": {"name": slug, "general_category": "game"},
           "factor_evaluations": {}}
    save_report_to_db(rep, parse_repo_identity(f"https://github.com/{slug}"), is_test=is_test)


def test_endpoint_serves_the_latest_public_grade():
    _add("Todd0042/app", 60.0)
    _add("Todd0042/app", 80.0)
    c = TestClient(app)
    for style in STYLES:
        r = c.get(f"/badge/Todd0042/app.svg?style={style}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
        assert "set-cookie" not in r.headers
        assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "80.0" in c.get("/badge/Todd0042/app.svg").text  # the newest scan


def test_endpoint_never_reveals_test_projects_or_unknown_repos():
    _add("stranger/secret", 90.0, is_test=True)
    c = TestClient(app)
    assert "not graded" in c.get("/badge/stranger/secret.svg").text.lower()
    assert "not graded" in c.get("/badge/nobody/nothing.svg?style=medal").text.lower()
    odd = c.get("/badge/..%2F..%2Fetc/passwd.svg")
    assert odd.status_code == 404 or "not graded" in odd.text.lower()
    assert c.get("/badge/Todd0042/app.svg?style=<script>").status_code in (200, 422)
