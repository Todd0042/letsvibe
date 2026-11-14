"""Images, audio and other media: names reach the reviewers, contents never do."""

import subprocess

import pytest

from letsvibe.core.ingest import RepositoryIngestor


def _make_repo(root, use_git: bool):
    (root / "src").mkdir(parents=True)
    (root / "src" / "game.py").write_text("def play():\n    return 1\n")
    (root / "assets" / "sprites").mkdir(parents=True)
    for i in range(7):
        (root / "assets" / "sprites" / f"hero_{i}.png").write_bytes(b"\x89PNG\x00SECRET-PIXELS")
    (root / "assets" / "sfx").mkdir(parents=True)
    (root / "assets" / "sfx" / "jump.wav").write_bytes(b"RIFF\x00SECRET-AUDIO")
    (root / "screenshots").mkdir()
    (root / "screenshots" / "title_screen.png").write_bytes(b"\x89PNG\x00")
    if use_git:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)


@pytest.mark.parametrize("use_git", [True, False])
def test_media_names_kept_contents_never_read(tmp_path, use_git):
    _make_repo(tmp_path, use_git)
    p = RepositoryIngestor.ingest(str(tmp_path))
    assert list(p.files) == ["src/game.py"]                         # media never becomes "code"
    assert p.total_loc == 3                                         # only the code file counts (ingest counts the trailing line)
    assert sorted(k for _, k, _ in p.assets) == ["audio"] + ["image"] * 8
    assert "SECRET" not in p.file_tree and all("SECRET" not in c for c in p.files.values())
    assert "jump.wav" in p.file_tree and "title_screen.png" in p.file_tree
    summary = p.media_summary()
    assert summary.startswith("8 images, 1 audio clips") and "assets/sprites (7)" in summary


def test_large_asset_folders_collapse_to_a_count(tmp_path):
    _make_repo(tmp_path, use_git=True)
    tree = RepositoryIngestor.ingest(str(tmp_path)).file_tree
    sprite_lines = [l for l in tree.splitlines() if "hero_" in l]
    assert len(sprite_lines) == 1 and "(+3 more images)" in sprite_lines[0]
