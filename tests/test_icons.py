"""Project icons (core/icons.py): found among every git-tracked file, public link for public repos, saved copy only
for private repos (inside the project's report folder, so deleting the project removes it), never SVG."""

import subprocess

from fastapi.testclient import TestClient

from letsvibe.core import icons
from letsvibe.core.icons import resolve_icon_url, tracked_files
from letsvibe.web.server import app

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def _git_repo(root, files):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def test_tracked_files_include_images(tmp_path):
    repo = _git_repo(tmp_path / "r", {"main.py": b"print(1)", "app/src/main/res/mipmap-xxxhdpi/ic_launcher.png": PNG})
    files = tracked_files(repo)
    assert "app/src/main/res/mipmap-xxxhdpi/ic_launcher.png" in files
    assert tracked_files(tmp_path / "not-a-repo") is None


def test_public_repo_uses_the_github_link(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path / "r", {"main.py": b"x", "icon.png": PNG})
    monkeypatch.setattr(icons, "is_publicly_reachable", lambda url: True)
    url = resolve_icon_url("alice", "app", repo, tmp_path / "report")
    assert url.startswith("https://raw.githubusercontent.com/alice/app/") and url.endswith("icon.png")
    assert not (tmp_path / "report").exists()  # nothing saved for public repos


def test_private_repo_saves_a_copy_in_its_report_folder(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path / "r", {"main.py": b"x", "icon.png": PNG})
    monkeypatch.setattr(icons, "is_publicly_reachable", lambda url: False)
    report = tmp_path / "reports" / "alice" / "app"
    assert resolve_icon_url("alice", "app", repo, report) == "/icons/alice/app"
    assert (report / "icon.png").read_bytes() == PNG


def test_svg_icons_are_never_saved(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path / "r", {"main.py": b"x", "icon.svg": b"<svg><script>alert(1)</script></svg>"})
    monkeypatch.setattr(icons, "is_publicly_reachable", lambda url: False)
    assert resolve_icon_url("alice", "app", repo, tmp_path / "report") is None
    assert not (tmp_path / "report").exists()


def test_saved_icons_are_served_only_for_listed_projects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the endpoint reads data/reports relative to the working folder
    folder = tmp_path / "data" / "reports" / "ghost" / "gone"
    folder.mkdir(parents=True)
    (folder / "icon.png").write_bytes(PNG)
    assert TestClient(app).get("/icons/ghost/gone").status_code == 404  # not a listed project
