"""Engine v10.4+ judges only the author's own code: dependencies they can't change never produce findings.

A project with real history: its own React-style components/ and src/lib/ arrive in one big squash commit (like a
"sync from private" or an imported project), next to a bulk-imported Arduino library with its own manifest, a
vendor/ folder, and the host SDK header an addon is built against. Only the author's code may be judged.
"""

import subprocess
from pathlib import Path

from letsvibe.analyzers.code_scan import dependency_files, scan, tracked_files

SWALLOW_JS = "export function f(api) {\n  try { api.go(); } catch (e) {}\n}\n"
SWALLOW_CPP = "void f() {\n  try { go(); } catch (...) {}\n}\n"


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=root, check=True,
                   capture_output=True)


def make_repo(tmp_path: Path) -> Path:
    files = {
        # The author's own code, all in one squash commit of 40+ files (bigger than a "focused" commit).
        **{f"web/components/Widget{i}.js": SWALLOW_JS for i in range(30)},
        **{f"src/lib/util{i}.js": SWALLOW_JS for i in range(12)},
        "README.md": "Uses web/components/Widget0.js and src/lib/util0.js\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "squash: the whole app")
    # Dependencies: a bulk-imported Arduino library (manifest), a vendor/ folder, the host SDK header.
    deps = {
        **{f"lib/Display_Library/src/part{i}.js": SWALLOW_JS for i in range(40)},
        "lib/Display_Library/library.properties": "name=Display_Library\n",
        **{f"vendor/acme/mod{i}.js": SWALLOW_JS for i in range(5)},
        "Nexus.h": "#pragma once\n" + SWALLOW_CPP,
    }
    for rel, text in deps.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "import libraries")
    return tmp_path


def test_only_the_authors_code_is_judged(tmp_path):
    root = make_repo(tmp_path)
    deps = dependency_files(root, tracked_files(root))
    assert any(d.startswith("lib/Display_Library/") for d in deps)
    assert "Nexus.h" in deps
    assert not any(d.startswith(("web/components/", "src/lib/")) for d in deps), sorted(deps)[:5]

    found = scan(root, intent_aware=True)
    files = {f["file"] for f in found["functionality"] + found["maintainer"]}
    assert any(f.startswith("web/components/") for f in files)  # the author's own swallowed errors still count
    assert not any(f.startswith(("lib/Display_Library/", "vendor/")) or f == "Nexus.h" for f in files), sorted(files)
