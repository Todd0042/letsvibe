"""
User-journey evidence, gathered deterministically from the repository (no AI).

The User Journey factor asks how a user gets from "I found this" to "it did what I wanted":
install/launch, first-run setup, finding their way (including how deep settings are buried),
completing the main task, being told it worked, and recovering from problems. Models only see
summaries and samples of the code, so this module collects concrete evidence for them and for the
yes/no checklist; without it, "no evidence" would read as "no".

Everything here is a heuristic over file names and source text: it informs the reviewers rather
than producing a score on its own.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

MAX_EXAMPLES = 6
MAX_FILE_CHARS = 400_000
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "build", "dist", ".pio", "target", ".gradle"}

# ---- Get it running --------------------------------------------------------------------------
INSTALLER_PATTERNS = [
    (re.compile(r"\.(iss|nsi|wxs|msi)$", re.I), "Windows installer script"),
    (re.compile(r"(appimagebuilder\.ya?ml|\.appimage|appimage[^/]*\.(sh|ya?ml))$", re.I), "AppImage packaging"),
    (re.compile(r"(^|/)pkgbuild$", re.I), "Arch (AUR) package"),
    (re.compile(r"\.desktop$", re.I), "Linux desktop launcher"),
    (re.compile(r"(^|/)snapcraft\.ya?ml$|flatpak|\.flatpakref$", re.I), "Snap/Flatpak package"),
    (re.compile(r"(^|/)(debian/control|\.deb)$", re.I), "Debian package"),
    (re.compile(r"\.spec$", re.I), "PyInstaller/RPM spec"),
    (re.compile(r"(^|/)fastlane/", re.I), "Mobile release automation (fastlane)"),
    (re.compile(r"(^|/)(dockerfile|docker-compose\.ya?ml|compose\.ya?ml)$", re.I), "Docker setup"),
    (re.compile(r"(^|/)platformio\.ini$", re.I), "Firmware build & flash config (PlatformIO)"),
    (re.compile(r"\.ino$", re.I), "Arduino sketch (flash via Arduino IDE)"),
    (re.compile(r"(^|/)build\.gradle(\.kts)?$", re.I), "Android/Gradle build"),
    (re.compile(r"\.xcodeproj/", re.I), "Xcode project"),
]
LAUNCH_PATTERNS = re.compile(r"(^|/)(start|run|launch|play|setup|install)[\w-]*\.(sh|bat|cmd|ps1|command)$", re.I)
QUICKSTART_HEADING = re.compile(r"^#{1,4}\s*.*\b(install|installation|quick ?start|getting started|usage|setup|how to (run|use|play))\b", re.I | re.M)

# ---- First run -----------------------------------------------------------------------------
FIRST_RUN_PATH = re.compile(r"(onboard|wizard|first[_-]?run|welcome|intro|tutorial|setup(?!\.py$|\.cfg$))", re.I)
# Also catches wizards built inside a page (e.g. <section id="wizard"> with data-step="1" steps)
FIRST_RUN_TEXT = re.compile(r"\b(first[_ -]?run|is_?first_?launch|onboarding|setup wizard|welcome screen|tutorial|"
                            r"id=[\"']wizard[\"']|data-step=)", re.I)
ANDROID_PERMISSION = re.compile(r'<uses-permission[^>]+android:name="android\.permission\.(\w+)"')
RUNTIME_PERMISSION = re.compile(r"(requestPermissions|RequestPermission\(|RequestMultiplePermissions|rememberPermissionState|"
                                r"rememberMultiplePermissionsState|checkSelfPermission|shouldShowRequestPermissionRationale)")

# ---- Find your way ---------------------------------------------------------------------------
SCREEN_PATTERNS = [
    re.compile(r"@(?:app|router|bp|blueprint)\.(?:get|route)\(\s*['\"](/[^'\"]*)['\"]"),        # FastAPI/Flask pages
    re.compile(r"\bapp\.get\(\s*['\"](/[^'\"]*)['\"]"),                                          # Express
    re.compile(r"<Route[^>]*\bpath=['\"{]+([^'\"}]+)"),                                          # React Router
    re.compile(r"\bpath:\s*['\"](/[^'\"]*)['\"]"),                                               # Vue/Angular routes
    re.compile(r"\bcomposable\(\s*(?:route\s*=\s*)?['\"]([^'\"]+)['\"]"),                        # Jetpack Compose NavHost
    re.compile(r'<activity[^>]+android:name="([^"]+)"'),                                         # Android activities
    re.compile(r"\bclass\s+(\w+(?:Window|Dialog|Screen|Page|Activity|Fragment|Scene|Menu))\b"),  # desktop/game screens
    re.compile(r"@(?:\w+)\.command\(\s*(?:name\s*=\s*)?['\"]?([\w-]*)"),                         # click/typer commands
    re.compile(r"add_parser\(\s*['\"]([\w-]+)['\"]"),                                            # argparse subcommands
    re.compile(r"\bvoid\s+(\w*(?:[Ss]creen|[Pp]age|[Mm]enu|[Vv]iew))\s*\("),                         # C/C++ firmware screens
    re.compile(r"\blv_(?:scr|screen)_load\w*\(\s*(\w+)"),                                          # LVGL screen loads
]
SETTINGS_WORD = re.compile(r"(settings?|preferences?|prefs|options|config(uration)?)", re.I)
NESTED_MENU_PATTERNS = re.compile(r"(\.addMenu\(|add_cascade\(|<PreferenceScreen|SubMenu|submenu|\bnavigation\(\s*startDestination)", re.I)
CLI_FRAMEWORK = re.compile(r"\b(argparse|click|typer|clap|cobra|commander|yargs)\b")

# ---- Know it worked / Recover --------------------------------------------------------------------
CONFIRM_CALL = re.compile(r"(Toast\.makeText|Snackbar|showSnackbar|\btoast\(|messagebox\.showinfo|QMessageBox\.information|"
                          r"\bnotify\(|Notification\(|showNotification|flash\()")
CONFIRM_TEXT = re.compile(r"['\"`][^'\"`\n]{0,60}\b(saved|synced|success(fully)?|completed?|done|exported|uploaded|"
                          r"imported|installed|updated|level complete|game over|you win|finished)\b[^'\"`\n]{0,60}['\"`]", re.I)
ERROR_UI_CALL = re.compile(r"(messagebox\.showerror|QMessageBox\.(critical|warning)|AlertDialog|showErrorDialog|"
                           r"console\.error\(|st\.error\(|flash\([^)]*error)", re.I)
RECOVERY_TEXT = re.compile(r"['\"`][^'\"`\n]{0,60}\b(no results|nothing (here|found|to show)|not found|try again|"
                           r"could not|couldn't|failed to|please (check|connect|select|enter)|no (songs|items|files|data|devices?))\b"
                           r"[^'\"`\n]{0,60}['\"`]", re.I)

CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".html", ".kt", ".kts", ".java", ".swift", ".dart",
            ".cs", ".cpp", ".cc", ".c", ".h", ".hpp", ".rs", ".go", ".gd", ".lua", ".rb", ".php", ".xml", ".ino")


def _walk(root: Path) -> List[str]:
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, root)
        for f in filenames:
            out.append(os.path.normpath(os.path.join(rel_dir, f)).replace(os.sep, "/"))
    return out


def _read(root: Path, rel: str, files: Optional[Dict[str, str]]) -> str:
    if files and rel in files:
        return files[rel][:MAX_FILE_CHARS]
    try:
        with open(root / rel, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(MAX_FILE_CHARS)
    except OSError:
        return ""


def _uniq(items: Iterable[str], limit: int = MAX_EXAMPLES) -> List[str]:
    seen: List[str] = []
    for it in items:
        it = it.strip()
        if it and it not in seen:
            seen.append(it)
        if len(seen) >= limit:
            break
    return seen


def _route_depth(route: str) -> int:
    return len([p for p in route.split("/") if p and not p.startswith(("{", ":", "<"))])


def gather_journey_signals(root: Path, files: Optional[Dict[str, str]] = None, readme: str = "") -> Dict[str, Any]:
    """Collects user-journey evidence. `files` (ingested code) is preferred; other files are read from disk."""
    root = Path(root)
    paths = _walk(root)
    # Prefer the ingested code set: it already excludes vendored third-party libraries
    source_paths = list(files.keys()) if files else paths
    code_paths = [p for p in source_paths if p.lower().endswith(CODE_EXT) and "test" not in p.lower()][:1500]

    # Skip vendored third-party libraries (folders with their own package manifest)
    vendored = tuple(os.path.dirname(p) + "/" for p in paths
                     if os.path.basename(p).lower() in ("library.properties", "library.json", "idf_component.yml") and "/" in p)
    own_paths = [p for p in paths if not p.startswith(vendored)] if vendored else paths
    installers = _uniq(label + f" ({p})" for p in own_paths for rx, label in INSTALLER_PATTERNS if rx.search(p))
    launchers = _uniq(p for p in own_paths if LAUNCH_PATTERNS.search(p))
    quickstart = bool(QUICKSTART_HEADING.search(readme or ""))

    first_run_files = _uniq(p for p in code_paths if FIRST_RUN_PATH.search(os.path.basename(p)))
    permissions: List[str] = []
    runtime_requests = 0
    screens: List[str] = []
    settings_routes: List[str] = []
    nested_menus = 0
    confirm_calls = 0
    confirm_examples: List[str] = []
    error_ui = 0
    recovery_examples: List[str] = []
    first_run_mentions = 0
    cli = False

    for rel in code_paths:
        text = _read(root, rel, files)
        if not text:
            continue
        if rel.lower().endswith("androidmanifest.xml"):
            permissions += ANDROID_PERMISSION.findall(text)
        runtime_requests += len(RUNTIME_PERMISSION.findall(text))
        first_run_mentions += len(FIRST_RUN_TEXT.findall(text))
        for rx in SCREEN_PATTERNS:
            for m in rx.findall(text):
                name = m if isinstance(m, str) else m[0]
                if name and not name.startswith(("/api", "/static", "/ws", "/img")) and not re.search(r"\.\w{2,4}$", name):
                    screens.append(name)
                    if SETTINGS_WORD.search(name):
                        settings_routes.append(name)
        nested_menus += len(NESTED_MENU_PATTERNS.findall(text))
        confirm_calls += len(CONFIRM_CALL.findall(text))
        confirm_examples += [m.group(0).strip("'\"`")[:70] for m in CONFIRM_TEXT.finditer(text)][:3]
        error_ui += len(ERROR_UI_CALL.findall(text))
        recovery_examples += [m.group(0).strip("'\"`")[:70] for m in RECOVERY_TEXT.finditer(text)][:3]
        if CLI_FRAMEWORK.search(text) and rel.endswith((".py", ".rs", ".go", ".js", ".ts")):
            cli = True

    screens_u = _uniq(screens, limit=40)
    settings_u = _uniq(settings_routes, limit=10)
    return {
        "install": installers,
        "launch_scripts": launchers,
        "readme_quickstart": quickstart,
        "first_run_files": first_run_files,
        "first_run_mentions": first_run_mentions,
        "android_permissions": sorted(set(permissions))[:12],
        "runtime_permission_requests": runtime_requests,
        "screen_count": len(screens_u),
        "screens": screens_u[:12],
        "settings_entry_points": settings_u,
        "settings_max_route_depth": max((_route_depth(r) for r in settings_u if "/" in r), default=0),
        "nested_menu_constructs": nested_menus,
        "confirmation_calls": confirm_calls,
        "confirmation_examples": _uniq(confirm_examples),
        "error_ui_calls": error_ui,
        "recovery_examples": _uniq(recovery_examples),
        "cli_framework": cli,
    }


def journey_context(j: Dict[str, Any]) -> str:
    """Compact, factual text block for the model prompts and the checklist."""
    if not j:
        return ""

    def show(items: List[str], none: str = "none found") -> str:
        return "; ".join(items) if items else none

    lines = [
        "USER JOURNEY EVIDENCE (found in the repository, not by AI):",
        f"- Get it running: installers/packages: {show(j.get('install', []))}; launch scripts: {show(j.get('launch_scripts', []))}; "
        f"README has install/quick-start section: {'yes' if j.get('readme_quickstart') else 'no'}",
        f"- First run: setup/onboarding files: {show(j.get('first_run_files', []))} ({j.get('first_run_mentions', 0)} mentions in code)",
    ]
    if j.get("android_permissions"):
        lines.append(f"- Android permissions declared: {', '.join(j['android_permissions'])}; runtime permission requests in code: "
                     f"{j.get('runtime_permission_requests', 0)}")
    lines += [
        f"- Find your way: {j.get('screen_count', 0)} screens/pages/commands found (e.g. {show(j.get('screens', [])[:8])}); "
        f"CLI framework: {'yes' if j.get('cli_framework') else 'no'}",
        f"- Settings: entry points {show(j.get('settings_entry_points', []))}; deepest settings route depth: "
        f"{j.get('settings_max_route_depth', 0)}; nested menu/submenu constructs: {j.get('nested_menu_constructs', 0)}",
        f"- Know it worked: {j.get('confirmation_calls', 0)} confirmation/notification calls; examples: {show(j.get('confirmation_examples', []))}",
        f"- Recover: {j.get('error_ui_calls', 0)} error dialogs/messages; empty/error state texts: {show(j.get('recovery_examples', []))}",
    ]
    return "\n".join(lines)


def full_readme(packed_repo: Any) -> str:
    """The whole top-level README (the stored excerpt is truncated and can miss the install section)."""
    files = getattr(packed_repo, "files", {}) or {}
    for name in sorted(files, key=lambda p: (p.count("/"), len(p))):
        if os.path.basename(name).lower().startswith("readme"):
            return files[name]
    from ..core.ingest import is_safe_repo_file
    root = Path(getattr(packed_repo, "source_path", "."))
    for cand in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
        path = root / cand
        if is_safe_repo_file(root, path):
            return path.read_text(encoding="utf-8", errors="ignore")
    return ""
