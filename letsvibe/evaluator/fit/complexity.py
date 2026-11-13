"""
Complexity: how many functions a project has, compared with what is typical for its form of program (terminal app,
desktop app, mobile app, web app, game, bot, library, hardware project). Text only; never part of the grade, and
neutral: a big, full-featured suite is "Complex", which is a description, not a criticism.

Functions are counted by lizard in the project's own code. Tests, vendored libraries, generated files and story or
content files (dialogue, levels, data) are never counted, so a story-heavy RPG or visual novel is judged on its
program, not its script. The typical range for each form is in data/complexity_forms.json.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

LABELS = ("Very simple", "Simple", "Typical", "Complex", "Very complex")
FORMS_FILE = Path(__file__).resolve().parents[2] / "data" / "complexity_forms.json"
# Distance outside the typical range (natural log) -> label; inside the range is "Typical"
VERY = math.log(2.5)
CONTENT_NOTE_AT = 2000   # mention the story/content/data lines left out only when there are this many
TERMINAL_HINT_AT = 2.0   # a terminal app this many times over its range gets the "might suit a GUI" hint

GUI = re.compile(r"\b(tkinter|PyQt\d|PySide\d|QWidget|QMainWindow|wx\.App|kivy|pygame|pyglet|arcade\.Window|"
                 r"customtkinter|flet|toga|Gtk\.|gi\.repository|electron|@tauri-apps|tauri::|egui|iced::|fyne|"
                 r"javafx|JFrame|Swing|WinForms|System\.Windows|Avalonia|raylib|SDL_|love\.graphics|"
                 r"@Composable|setContentView|SwiftUI|UIKit)\b")
TERMINAL = re.compile(r"\b(argparse|click\.command|typer\.Typer|docopt|fire\.Fire|curses|textual|rich\.console|"
                      r"clap::|structopt|ratatui|crossterm|cobra\.Command|urfave/cli|bubbletea|commander|yargs|"
                      r"inquirer|blessed|ink\b|picocli|System\.CommandLine)\b")


def load_forms(path: Path = FORMS_FILE) -> Dict[str, Any]:
    return json.loads(path.read_text())["forms"]


def count_functions(code: Dict[str, str]) -> int:
    """Functions lizard finds in {path: source} (callers pass only the project's own, non-test code)."""
    import lizard

    total = 0
    for path, text in code.items():
        try:
            total += len(lizard.analyze_file.analyze_source_code(path, text).function_list)
        except Exception:
            continue
    return total


def forms_of(platforms: Iterable[str], category: Optional[str], code: Dict[str, str]) -> List[str]:
    """The project's form(s), e.g. ["desktop", "web"], from its platforms, category and the toolkits in its code."""
    plats = {str(p).lower() for p in platforms or [] if p}
    category = (category or "").lower()
    if category == "game":
        return ["game"]
    if category in ("bot", "library", "embedded"):
        return [category]
    forms: List[str] = []
    if plats & {"android", "ios"}:
        forms.append("mobile")
    if plats & {"windows", "macos", "linux"} or not plats:
        joined = "\n".join(code.values())
        if GUI.search(joined) or not TERMINAL.search(joined):
            forms.append("desktop")
        else:
            forms.append("terminal")
    if "web" in plats:
        forms.append("web")
    return forms or ["desktop"]


def label_for(functions: int, low: int, high: int) -> str:
    functions = max(functions, 1)
    if functions < low:
        return "Very simple" if math.log(low / functions) >= VERY else "Simple"
    if functions > high:
        return "Very complex" if math.log(functions / high) >= VERY else "Complex"
    return "Typical"


def _and(words: List[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def assess(code: Dict[str, str], content_lines: int, platforms: Iterable[str], category: Optional[str],
           forms_table: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    {"label", "summary", "hint", "forms", "typical", "facts"} for a project's own code ({path: source}, no tests,
    vendored or content files) and the number of story/content lines left out of the count.
    """
    table = forms_table or load_forms()
    forms = forms_of(platforms, category, code)
    # A project spanning several forms (a desktop and web suite) gets the widest of their ranges
    low = min(table[f]["functions"][0] for f in forms)
    high = max(table[f]["functions"][1] for f in forms)
    functions = count_functions(code)
    label = label_for(functions, low, high)
    labels = [table[f]["label"] for f in forms]
    if len(labels) > 1 and all(lab.endswith(" app") for lab in labels):
        form_words = _and([lab[:-4] for lab in labels]) + " app"   # "desktop and web app"
    else:
        form_words = _and(labels)
    article = "an" if form_words[:1].lower() in "aeiou" else "a"
    summary = f"{label} for {article} {form_words}: {functions:,} function{'' if functions == 1 else 's'}, where {low:,}–{high:,} is typical."
    if content_lines >= CONTENT_NOTE_AT:
        summary += f" Its {content_lines:,} lines of story, content and data are not counted."
    hint = ""
    if forms == ["terminal"] and functions > high * TERMINAL_HINT_AT:
        hint = "A terminal app with this many functions might be easier to use with a graphical interface."
    return {"label": label, "summary": summary, "hint": hint, "forms": forms, "typical": [low, high],
            "facts": {"functions": functions, "content_lines": content_lines, "code_files": len(code)}}


def own_code(files: Dict[str, str]) -> Dict[str, str]:
    """The program's own source from the dossier's file map: code only, no tests and no story/content files."""
    from ..experience import dossier

    return {f: t for f, t in files.items()
            if f.endswith(dossier.CODE) and not f.endswith(".html")
            and not dossier.TEST.search(f) and not dossier.CONTENT_FILE.search(f)}


def content_line_count(files: Dict[str, str]) -> int:
    from ..experience import dossier

    return sum(t.count("\n") for f, t in files.items() if dossier.CONTENT_FILE.search(f))
