from pathlib import Path
from unittest.mock import patch

from letsvibe.core.screenshots import discover_screenshot_urls
from letsvibe.web import image_proxy


def test_discover_screenshots_from_readme_and_excludes_badges(tmp_path: Path):
    repo = tmp_path / "demo-app"
    repo.mkdir()
    (repo / "assets").mkdir()
    (repo / "assets" / "ui.png").write_bytes(b"png")
    (repo / "assets" / "logo-icon.png").write_bytes(b"png")
    (repo / "assets" / "hero_banner.png").write_bytes(b"png")

    (repo / "README.md").write_text(
        "# Demo App\n"
        "![badge](https://img.shields.io/badge/build-passing-green.svg)\n"
        "![icon](./assets/logo-icon.png)\n"
        "![banner](./assets/hero_banner.png)\n"
        "![UI](./assets/ui.png)\n"
        "<img src=\"https://github.com/alice/demo-app/blob/main/docs/screen1.png\" />\n"
        "![Raw](https://raw.githubusercontent.com/alice/demo-app/main/docs/screen2.jpg#gh-dark-mode-only)\n",
        encoding="utf-8",
    )

    tracked = [
        "README.md",
        "assets/ui.png",
        "assets/logo-icon.png",
        "assets/hero_banner.png",
        "docs/screen1.png",
        "docs/screen2.jpg",
    ]
    with patch("letsvibe.core.screenshots.tracked_files", return_value=tracked):
        urls = discover_screenshot_urls("alice", "demo-app", repo, limit=4)

    assert urls == [
        "https://raw.githubusercontent.com/alice/demo-app/HEAD/assets/ui.png",
        "https://raw.githubusercontent.com/alice/demo-app/main/docs/screen1.png",
        "https://raw.githubusercontent.com/alice/demo-app/main/docs/screen2.jpg",
    ]


def test_discover_screenshots_folder_and_os_naming_fallback_with_limit(tmp_path: Path):
    repo = tmp_path / "pixel-app"
    repo.mkdir()

    tracked = [
        "README.md",
        "src/sprites/hero.png",
        "mipmap-hdpi/ic_launcher.png",
        "screenshots/01_home.png",
        "screenshots/02_player.png",
        "docs/images/03_settings.webp",
        "Screenshot_20260101-120000.png",
        "capture_extra.png",
    ]
    with patch("letsvibe.core.screenshots.tracked_files", return_value=tracked):
        urls = discover_screenshot_urls("bob", "pixel-app", repo, limit=4)

    assert len(urls) == 4
    assert urls == [
        "https://raw.githubusercontent.com/bob/pixel-app/HEAD/docs/images/03_settings.webp",
        "https://raw.githubusercontent.com/bob/pixel-app/HEAD/screenshots/01_home.png",
        "https://raw.githubusercontent.com/bob/pixel-app/HEAD/screenshots/02_player.png",
        "https://raw.githubusercontent.com/bob/pixel-app/HEAD/capture_extra.png",
    ]


def test_fetch_image_nocache_writes_nothing_to_cache_dir(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / "img_cache"
    monkeypatch.setattr(image_proxy, "CACHE_DIR", cache_dir)

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = b"\x89PNG\r\n\x1a\nfake"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(image_proxy.httpx, "Client", FakeClient)

    url = "https://raw.githubusercontent.com/alice/demo-app/HEAD/screenshots/01.png"
    res = image_proxy.fetch_image(url, cache=False)
    assert res == (b"\x89PNG\r\n\x1a\nfake", "image/png")
    assert not cache_dir.exists()


def test_discover_screenshot_urls_remote_tree_and_readme(monkeypatch):
    from letsvibe.core import screenshots as sc

    class FakeResp:
        def __init__(self, status_code, json_data=None, text=""):
            self.status_code = status_code
            self._json = json_data
            self.text = text

        def json(self):
            return self._json

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            if "api.github.com/repos/alice/demo/git/trees/HEAD" in url:
                return FakeResp(200, {
                    "tree": [
                        {"type": "blob", "path": "README.md"},
                        {"type": "blob", "path": "assets/ui.png"},
                        {"type": "blob", "path": "screenshots/01_main.png"},
                    ]
                })
            if "raw.githubusercontent.com/alice/demo/HEAD/README.md" in url:
                return FakeResp(200, text="![UI](./assets/ui.png)\n")
            return FakeResp(404)

    monkeypatch.setattr(sc.httpx, "Client", FakeClient)

    urls = sc.discover_screenshot_urls_remote("alice", "demo", limit=4)
    assert urls == [
        "https://raw.githubusercontent.com/alice/demo/HEAD/assets/ui.png",
        "https://raw.githubusercontent.com/alice/demo/HEAD/screenshots/01_main.png",
    ]


def test_backfill_missing_screenshots_updates_db(tmp_path: Path, monkeypatch):
    import json
    from letsvibe.core import screenshots as sc
    from letsvibe.storage.db import get_db_connection, init_db

    db_file = tmp_path / "test.db"
    init_db(db_file)
    conn = get_db_connection(db_file)
    with conn:
        conn.execute(
            "INSERT INTO projects (owner, repo_name, full_slug, canonical_url, provider) VALUES (?, ?, ?, ?, ?)",
            ("alice", "demo", "alice/demo", "https://github.com/alice/demo", "github"),
        )
        pid = conn.execute("SELECT id FROM projects WHERE full_slug = 'alice/demo'").fetchone()["id"]
        conn.execute(
            "INSERT INTO reports (project_id, owner, repo_name, full_slug, final_score, letter_grade, archetype_name, report_data_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, "alice", "demo", "alice/demo", 82.0, "B", "CLI", json.dumps({"repo_name": "demo"})),
        )
    conn.close()

    monkeypatch.setattr(
        sc,
        "discover_visuals_remote",
        lambda owner, repo_name, limit=4, token=None: (
            ["https://raw.githubusercontent.com/alice/demo/HEAD/screenshots/01.png"],
            {},
        ),
    )

    summary = sc.backfill_missing_screenshots(db_path=db_file)
    assert summary["checked"] == 1
    assert summary["updated"] == 1
    assert summary["skipped"] == 0

    conn = get_db_connection(db_file)
    blob = conn.execute("SELECT report_data_json FROM reports WHERE full_slug = 'alice/demo'").fetchone()["report_data_json"]
    conn.close()
    assert json.loads(blob)["screenshots"] == ["https://raw.githubusercontent.com/alice/demo/HEAD/screenshots/01.png"]


def test_discover_screenshots_ignores_third_party_demo_assets(tmp_path: Path):
    repo = tmp_path / "mx5-dash-android"
    repo.mkdir()
    tracked = [
        "README.md",
        "app/src/main/cpp/third_party/lvgl/demos/benchmark/assets/img_cogwheel_argb.png",
        "app/src/main/cpp/third_party/lvgl/demos/benchmark/assets/img_cogwheel_rgb.png",
    ]
    with patch("letsvibe.core.screenshots.tracked_files", return_value=tracked):
        urls = discover_screenshot_urls("Todd0042", "mx5-dash-android", repo, limit=4)
    assert urls == []


def test_discover_asset_preview_backgrounds_and_characters():
    from letsvibe.core.screenshots import discover_asset_preview_from_files

    files = [
        "assets/backgrounds/forest_bg.jpg",
        "assets/backgrounds/dungeon.png",
        "assets/backgrounds/extra_castle.png",
        "assets/sprites/hero_idle.png",
        "assets/sprites/mage.png",
        "assets/sprites/rogue.png",
        "assets/sprites/slime.png",
        "assets/portraits/boss.png",
        "assets/characters/hero_sheet.png",
    ]
    preview = discover_asset_preview_from_files("alice", "rpg-game", files, max_bg=2, max_chars=4)
    assert len(preview["backgrounds"]) == 2
    assert len(preview["characters"]) == 4
    assert all("/sprites/" in u for u in preview["characters"])

    portrait_only = [
        "assets/backgrounds/forest_bg.jpg",
        "assets/portraits/alice.png",
        "assets/portraits/bob.png",
        "assets/portraits/carol.png",
        "assets/portraits/dave.png",
    ]
    preview2 = discover_asset_preview_from_files("alice", "vn-game", portrait_only, max_bg=2, max_chars=4)
    assert len(preview2["backgrounds"]) == 1
    assert len(preview2["characters"]) == 4
    assert all("/portraits/" in u for u in preview2["characters"])


def test_discover_android_asset_urls_fallback():
    from letsvibe.core.screenshots import discover_android_asset_urls_from_files

    files = [
        "app/src/main/assets/dash_gauge.png",
        "app/src/main/res/drawable-xhdpi/bg_cluster.png",
        "app/src/main/res/drawable/ic_back.png",
        "app/src/main/cpp/third_party/lvgl/demos/benchmark/assets/img_cogwheel_argb.png",
    ]
    urls = discover_android_asset_urls_from_files("alice", "android-app", files, limit=4)
    assert urls == [
        "https://raw.githubusercontent.com/alice/android-app/HEAD/app/src/main/assets/dash_gauge.png",
        "https://raw.githubusercontent.com/alice/android-app/HEAD/app/src/main/res/drawable-xhdpi/bg_cluster.png",
    ]
