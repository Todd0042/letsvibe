#!/usr/bin/env python3
"""
LetsVibe universal entrypoint.
Automatically detects the project's .venv environment and auto-starts local Ollama.

Usage:
    python letsvibe.py <repo-address>          # Ingests & evaluates repository (local path or remote URL)
    ./letsvibe.py <repo-address>               # Ingests & evaluates repository (local path or remote URL)
    python letsvibe.py web                     # Launches Web Leaderboard & Admin Console
    python letsvibe.py                         # Launches Web Leaderboard & Admin Console
"""

import os
import sys
from pathlib import Path

# Auto-activate local .venv if running with system python
root_dir = Path(__file__).parent.resolve()
venv_dir = root_dir / ".venv"
venv_python = venv_dir / "bin" / "python"
if venv_python.exists() and sys.prefix != str(venv_dir):
    os.execv(str(venv_python), [str(venv_python)] + sys.argv)

# Add package root to sys.path
sys.path.insert(0, str(root_dir))

from letsvibe.cli import main

if __name__ == "__main__":
    main()
