"""Tests for platform detection, platform-aware prompts and combined leaderboard filtering."""

from pathlib import Path

import pytest

from starlette.testclient import TestClient

from letsvibe.core.platforms import detect_platforms, normalize_platforms, platform_context
from letsvibe.storage.db import get_leaderboard, init_db, save_report_to_db
from letsvibe.storage.identity import parse_repo_identity


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_detects_esp32_board_and_display_stack(tmp_path):
    _write(tmp_path, "platformio.ini", "[env:dash]\nplatform = espressif32\nboard = esp32-s3-devkitc-1\nlib_deps = lvgl/lvgl\n")
    _write(tmp_path, "src/main.cpp", "void setup(){}")
    _write(tmp_path, "lib/GFX_Library_for_Arduino/src/font/u8g2_font.h", "")
    det = detect_platforms(tmp_path)
    assert det.platforms == ["embedded"]
    assert "ESP32-S3" in det.device_profile and "LVGL" in det.device_profile
    assert "U8g2" not in det.device_profile  # font files inside a vendored lib are not a display stack


def test_detects_mobile_web_and_console(tmp_path):
    _write(tmp_path, "app/src/main/AndroidManifest.xml", "<manifest/>")
    _write(tmp_path, "Game.xcodeproj/project.pbxproj", "SDKROOT = iphoneos;")
    assert detect_platforms(tmp_path).platforms == ["android", "ios"]

    web = tmp_path / "web"
    _write(web, "package.json", '{"devDependencies": {"vite": "5"}}')
    assert detect_platforms(web).platforms == ["web"]

    console = tmp_path / "switch"
    _write(console, "Makefile", "include $(DEVKITPRO)/libnx/switch_rules")
    _write(console, "source/main.c", "int main(){}")
    assert detect_platforms(console).platforms == ["console"]

    cli = tmp_path / "cli"
    _write(cli, "tool.py", "print('hi')")
    assert detect_platforms(cli).platforms == ["windows", "macos", "linux"]


def test_platform_helpers():
    assert normalize_platforms(["PC", "Android", "bogus"]) == ["windows", "macos", "linux", "android"]
    ctx = platform_context(["embedded"], "ESP32-S3 with 480x480 display")
    assert "GRADE RELATIVE TO THIS HARDWARE" in ctx and "ESP32-S3" in ctx
    assert platform_context([], "") == ""


def _seed(db):
    projects = [
        ("a/racer", 90.0, "A-", "game", ["android", "ios"]),
        ("b/dash", 70.0, "C", "embedded", ["embedded"]),
        ("c/cli", 81.0, "B", "tool", ["windows", "macos", "linux"]),
        ("d/arcade", 62.0, "C-", "game", ["windows"]),
    ]
    for slug, score, grade, cat, plats in projects:
        rep = {
            "final_vibe_score": score,
            "letter_grade": grade,
            "archetype": {"name": slug, "primary_language": "Python", "general_category": cat, "platforms": plats},
            "deterministic_metrics": {},
            "factor_evaluations": {},
        }
        save_report_to_db(rep, parse_repo_identity(f"https://github.com/{slug}"), db_path=db)


def test_filters_combine_across_groups(tmp_path):
    db = tmp_path / "f.db"
    init_db(db)
    _seed(db)

    slugs = lambda **kw: [i["full_slug"] for i in get_leaderboard(db_path=db, **kw)]  # noqa: E731
    assert slugs(categories="game") == ["a/racer", "d/arcade"]
    # OR within a group
    assert slugs(platforms="android,embedded") == ["a/racer", "b/dash"]
    # AND across groups: Game AND Windows AND tier C
    assert slugs(categories="game", platforms="windows", tier_filter="C") == ["d/arcade"]
    assert slugs(categories="game", platforms="embedded") == []
    # Rank stays the position in the full list, not the filtered one
    assert [i["rank"] for i in get_leaderboard(db_path=db, categories="game")] == [1, 4]

    items, facets = get_leaderboard(db_path=db, categories="game", with_facets=True)
    assert facets["category"] == {"game": 2, "embedded": 1, "tool": 1}  # category counts ignore the category filter
    assert facets["platform"] == {"android": 1, "ios": 1, "windows": 1}  # but respect it for other groups
    assert facets["tier"] == {"A": 1, "C": 1}


def test_leaderboard_api_and_taxonomy(monkeypatch, tmp_path):
    from letsvibe.storage import db as dbmod
    from letsvibe.web import server

    test_db = tmp_path / "api.db"
    init_db(test_db)
    _seed(test_db)
    monkeypatch.setattr(server, "get_leaderboard", lambda **kw: dbmod.get_leaderboard(db_path=test_db, **kw))

    client = TestClient(server.app)
    data = client.get("/api/leaderboard?category=game&platform=android,ios").json()
    assert [i["full_slug"] for i in data["items"]] == ["a/racer"]
    assert "facets" in data

    tax = client.get("/api/taxonomy").json()
    assert any(c["id"] == "embedded" for c in tax["categories"])
    pc = next(g for g in tax["platform_groups"] if g["id"] == "pc")
    assert [c["id"] for c in pc["children"]] == ["windows", "macos", "linux"]
    assert any(g["id"] == "console" for g in tax["platform_groups"])


def test_detects_display_resolution_from_own_code_not_vendored(tmp_path):
    _write(tmp_path, "platformio.ini", "[env:x]\nboard = esp32-s3-devkitc-1\n")
    _write(tmp_path, "lib/mx5_config/config.h", "#define MX5_HOR_RES 480\n#define MX5_VER_RES 320\n")
    _write(tmp_path, "lib/SomeGfx/library.properties", "name=SomeGfx")
    _write(tmp_path, "lib/SomeGfx/src/demo.h", "#define TFT_WIDTH 240\n#define TFT_HEIGHT 240\n#define SCREEN_WIDTH 240\n")
    assert "480x320 display" in detect_platforms(tmp_path).device_profile
