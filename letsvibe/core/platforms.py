"""
Project category and target-platform taxonomy, plus deterministic platform detection.

Platforms are stored as leaf ids. The UI groups them into a tree (PC -> Windows/macOS/Linux,
Mobile -> Android/iOS) so a parent filter selects all of its children.

Detection reads marker files straight from the repo (not the ingested code set, which drops
manifests and vendored libraries) and builds a short device profile so the graders judge a
project against the hardware it targets instead of against a desktop PC.
"""

import configparser
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

# --- Taxonomy -------------------------------------------------------------------------------

GENERAL_CATEGORIES: Dict[str, str] = {
    "game": "Game",
    "application": "Application",
    "web_app": "Web App / Site",
    "tool": "Tool / Utility",
    "library": "Library / SDK",
    "bot": "Bot / Automation",
    "ai": "AI / LLM Project",
    "embedded": "Embedded / Hardware",
    "other": "Other",
}

PLATFORM_LABELS: Dict[str, str] = {
    "windows": "Windows",
    "macos": "macOS",
    "linux": "Linux",
    "android": "Android",
    "ios": "iOS",
    "web": "Web / Browser",
    "embedded": "Embedded / Microcontroller",
    "console": "Console",
}

# Parent group -> leaf platform ids (single-leaf groups map to themselves)
PLATFORM_GROUPS: Dict[str, Dict[str, object]] = {
    "pc": {"label": "PC", "children": ["windows", "macos", "linux"]},
    "mobile": {"label": "Mobile", "children": ["android", "ios"]},
    "web": {"label": "Web / Browser", "children": ["web"]},
    "embedded": {"label": "Embedded / Microcontroller", "children": ["embedded"]},
    "console": {"label": "Console", "children": ["console"]},
}

PC_PLATFORMS = ["windows", "macos", "linux"]


def normalize_category(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if v in GENERAL_CATEGORIES:
        return v
    for key, label in GENERAL_CATEGORIES.items():
        if v == label.lower():
            return key
    return "other"


def normalize_platforms(values: Optional[Iterable[str]]) -> List[str]:
    out: List[str] = []
    for raw in values or []:
        v = str(raw).strip().lower()
        if v in ("pc", "desktop"):
            leaves = PC_PLATFORMS
        elif v == "mobile":
            leaves = ["android", "ios"]
        elif v in PLATFORM_LABELS:
            leaves = [v]
        else:
            leaves = [k for k, label in PLATFORM_LABELS.items() if label.lower() == v]
        for leaf in leaves:
            if leaf not in out:
                out.append(leaf)
    return [p for p in PLATFORM_LABELS if p in out]  # canonical order


# --- Detection ------------------------------------------------------------------------------

# Rough specs for common microcontroller boards so graders know how little they have to work with
BOARD_SPECS = [
    (re.compile(r"esp32-?s3", re.I), "ESP32-S3 (dual-core 240 MHz, ~512 KB SRAM, optional 2-8 MB PSRAM)"),
    (re.compile(r"esp32-?c[36]", re.I), "ESP32-C3/C6 (single-core 160 MHz RISC-V, ~400 KB SRAM)"),
    (re.compile(r"esp32-?s2", re.I), "ESP32-S2 (single-core 240 MHz, ~320 KB SRAM)"),
    (re.compile(r"esp32", re.I), "ESP32 (dual-core 240 MHz, ~520 KB SRAM)"),
    (re.compile(r"esp8266|nodemcu|d1_mini", re.I), "ESP8266 (80 MHz, ~80 KB RAM)"),
    (re.compile(r"rp2040|pico", re.I), "RP2040 (dual-core 133 MHz, 264 KB SRAM)"),
    (re.compile(r"teensy4|teensy41", re.I), "Teensy 4.x (600 MHz Cortex-M7, 1 MB RAM)"),
    (re.compile(r"uno|nano|atmega328", re.I), "ATmega328 (16 MHz 8-bit, 2 KB SRAM)"),
    (re.compile(r"mega2560", re.I), "ATmega2560 (16 MHz 8-bit, 8 KB SRAM)"),
    (re.compile(r"stm32", re.I), "STM32 (Cortex-M microcontroller, typically 20-512 KB SRAM)"),
    (re.compile(r"nrf52", re.I), "nRF52 (64 MHz Cortex-M4, 64-256 KB RAM, BLE)"),
]

DISPLAY_LIBS = {
    "arduino_gfx": "Arduino_GFX",
    "gfx_library_for_arduino": "Arduino_GFX",
    "lvgl": "LVGL",
    "tft_espi": "TFT_eSPI",
    "lovyangfx": "LovyanGFX",
    "u8g2": "U8g2 (monochrome)",
    "adafruit_ssd1306": "SSD1306 OLED",
    "waveshare": "Waveshare display",
}

# Screen-size constants in firmware/game code, e.g. "#define MX5_HOR_RES 480" or "WIDTH = 480"
_RES_RE = re.compile(
    r"(?:#define\s+|\b)\w*?(HOR_RES|VER_RES|SCREEN_WIDTH|SCREEN_HEIGHT|TFT_WIDTH|TFT_HEIGHT|LCD_WIDTH|LCD_HEIGHT|"
    r"DISPLAY_WIDTH|DISPLAY_HEIGHT|WIDTH|HEIGHT)\b\s*(?:=\s*)?\(?\s*(\d{2,4})\b",
    re.I,
)
_SOURCE_EXT = (".h", ".hpp", ".c", ".cpp", ".cc", ".ino", ".py", ".rs", ".ini")


def _detect_resolution(root: Path, paths: List[str], skip_prefixes: List[str]) -> Optional[str]:
    """Most common width x height pair defined in the project's own source (not vendored libraries)."""
    from collections import Counter

    widths: Counter = Counter()
    heights: Counter = Counter()
    candidates = [
        p for p in paths
        if p.lower().endswith(_SOURCE_EXT) and not any(p.startswith(sp) for sp in skip_prefixes)
    ][:300]
    for rel in candidates:
        for name, val in _RES_RE.findall(_read(root, rel, 100_000)):
            n = int(val)
            if not 64 <= n <= 4096:
                continue
            key = name.upper()
            if "HOR" in key or "WIDTH" in key:
                widths[n] += 1
            else:
                heights[n] += 1
    if widths and heights:
        return f"{widths.most_common(1)[0][0]}x{heights.most_common(1)[0][0]} display"
    return None


SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "build", "dist", ".pio", "target", ".gradle"}
MAX_WALK_FILES = 20000


@dataclass
class PlatformDetection:
    platforms: List[str] = field(default_factory=list)
    device_profile: str = ""
    signals: List[str] = field(default_factory=list)


def _list_paths(root: Path) -> List[str]:
    paths: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # also skip Python virtual environments of any name (they hold activate.bat / Activate.ps1)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.lower().startswith((".venv", "venv"))
                       and not os.path.exists(os.path.join(dirpath, d, "pyvenv.cfg"))]
        rel_dir = os.path.relpath(dirpath, root)
        for d in dirnames:  # directory markers such as Foo.xcodeproj
            paths.append(os.path.normpath(os.path.join(rel_dir, d)) + "/")
        for f in filenames:
            paths.append(os.path.normpath(os.path.join(rel_dir, f)))
            if len(paths) >= MAX_WALK_FILES:
                return paths
    return paths


def _read(root: Path, rel: str, limit: int = 200_000) -> str:
    try:
        with open(root / rel, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _platformio_profile(text: str) -> List[str]:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return []
    boards: List[str] = []
    for section in parser.sections():
        board = parser.get(section, "board", fallback="").strip()
        if board and board not in boards:
            boards.append(board)
    described = []
    for board in boards:
        spec = next((desc for rx, desc in BOARD_SPECS if rx.search(board)), None)
        described.append(f"board '{board}'" + (f": {spec}" if spec else ""))
    return described


TAURI_TARGETS = {"appimage": "linux", "deb": "linux", "rpm": "linux", "msi": "windows", "nsis": "windows",
                 "dmg": "macos", "app": "macos"}
LINUX_ONLY_MARKERS = ("import pty", "import termios", "import fcntl", "from pty", "/proc/", "systemctl", "podman ",
                      "journalctl", "apt-get install", "pacman -s", "inotify", "epoll")
WINDOWS_ONLY_MARKERS = ("import winreg", "import msvcrt", "win32api", "ctypes.windll", "pywin32", "win32con",
                        "#include <windows.h>", "#include <conio.h>")
# Code that branches by OS is cross-platform, whatever OS-specific calls sit inside the branches
PLATFORM_GUARDS = ("sys.platform", "os.name ==", "os.name !=", "platform.system()", "#ifdef _win32", "#if defined(_win32)",
                   "#ifdef __linux__", "#ifdef __apple__", "cfg(target_os", "cfg(windows)", "cfg(unix)", "runtime.goos",
                   "process.platform")
POSIX_MARKERS = ("#include <termios.h>", "#include <sys/ioctl.h>", "#include <unistd.h>", "#include <pthread.h>")


def _declared_desktop_targets(root: Path, paths: List[str]) -> Optional[Set[str]]:
    """OSes a Tauri/Electron app says it builds for; None when it doesn't say (then: all three)."""
    found: Set[str] = set()
    said = False
    for p in paths:
        base = os.path.basename(p).lower()
        if base == "tauri.conf.json":
            try:
                conf = json.loads(_read(root, p, 200_000) or "{}")
            except ValueError:
                continue
            targets = (conf.get("bundle") or {}).get("targets")
            if isinstance(targets, str):
                targets = [targets]
            if isinstance(targets, list) and targets and "all" not in [str(t).lower() for t in targets]:
                said = True
                found.update(TAURI_TARGETS[t] for t in (str(x).lower() for x in targets) if t in TAURI_TARGETS)
        elif base in ("electron-builder.yml", "electron-builder.yaml", "electron-builder.json"):
            txt = _read(root, p, 50_000).lower()
            for key, plat in (("\nlinux:", "linux"), ("\nwin:", "windows"), ("\nmac:", "macos"), ('"linux"', "linux"), ('"win"', "windows"), ('"mac"', "macos")):
                if key in "\n" + txt:
                    said = True
                    found.add(plat)
    return found if said and found else None


def _declared_signals(root: Path, paths: List[str], lower: List[str]) -> Dict[str, object]:
    """Evidence read from what the repo declares or does (engine v10.4+ only; see detect_platforms)."""
    android = next((p for p in paths if "/android/" in p.lower() and p.lower().endswith((".kt", ".java"))
                    and "import android." in _read(root, p, 30_000)), None)
    android = android or next((p for p in lower if "/gen/android/" in p), None)
    ios = next((p for p in lower if "/gen/apple/" in p), None)
    sample = [p for p in paths if p.lower().endswith((".py", ".sh", ".service", ".rs", ".go", ".ts", ".js", ".c", ".h", ".cpp", ".cc", ".hpp"))
              and not any(x in p.lower() for x in ("node_modules/", "vendor/", "third_party/", "site-packages/"))][:300]
    text = " ".join(_read(root, p, 40_000).lower() for p in sample)
    return {
        "android": android, "ios": ios,
        "linux_only": any(m in text for m in LINUX_ONLY_MARKERS) or any(p.endswith(".service") for p in lower),
        "windows_only": any(m in text for m in WINDOWS_ONLY_MARKERS),
        "posix": any(m in text for m in POSIX_MARKERS),
        "guarded": any(m in text for m in PLATFORM_GUARDS),
        # an OS check only proves Windows support if Windows is one of the branches
        "windows_aware": any(m in text for m in PLATFORM_GUARDS) and any(
            w in text for w in ("_win32", "win32", "'nt'", '"nt"', "cfg(windows)", 'target_os = "windows"', "winreg")),
    }


def detect_platforms(root: Path, paths: Optional[List[str]] = None, declared_targets: bool = False) -> PlatformDetection:
    """Deterministically infers target platforms and a device profile from repository markers.

    declared_targets=True (engine v10.4+) reads what the repo itself declares instead of assuming:
    Tauri/Electron build targets, Tauri-mobile Android/iOS sources, a Tauri/Electron UI is not a website,
    and the generic "code file -> every desktop OS" fallback narrows to the OS the code is written for.
    Older engines call it without the flag and get exactly the previous results.
    """
    root = Path(root)
    paths = paths if paths is not None else _list_paths(root)
    lower = [p.lower() for p in paths]
    names = {os.path.basename(p.rstrip("/")) for p in lower}
    found: Set[str] = set()
    signals: List[str] = []
    profile: List[str] = []

    def hit(platforms: Iterable[str], why: str) -> None:
        found.update(platforms)
        if why not in signals:
            signals.append(why)

    def any_path(pred) -> Optional[str]:
        return next((p for p in lower if pred(p)), None)

    # Embedded / microcontroller
    pio = next((p for p in paths if os.path.basename(p).lower() == "platformio.ini"), None)
    if pio:
        hit(["embedded"], "platformio.ini")
        profile.extend(_platformio_profile(_read(root, pio)))
    if any_path(lambda p: p.endswith(".ino")):
        hit(["embedded"], "Arduino sketch (.ino)")
    if "sdkconfig" in names or "sdkconfig.defaults" in names or "idf_component.yml" in names:
        hit(["embedded"], "ESP-IDF project")
    if "embedded" in found:
        # Only real library folders (lib/<name>/) and declared lib_deps, not file names inside them
        lib_names = {p.split("/")[1] for p in lower if p.startswith("lib/") and p.count("/") >= 2}
        lib_deps = _read(root, pio).lower() if pio else ""
        libs = sorted({
            label for key, label in DISPLAY_LIBS.items()
            if any(key in n for n in lib_names) or key in lib_deps
        })
        if libs:
            profile.append("display stack: " + ", ".join(libs))
        # Vendored libraries (dirs with a package manifest) define their own sample sizes; ignore them
        vendored = [
            os.path.dirname(p) + "/" for p in paths
            if os.path.basename(p).lower() in ("library.properties", "library.json", "idf_component.yml") and "/" in p
        ]
        resolution = _detect_resolution(root, paths, vendored)
        if resolution:
            profile.append(resolution)
        if any("ble" in p or "bluetooth" in p for p in lower):
            profile.append("uses Bluetooth/BLE")

    # Mobile (prioritize explicit mobile manifests before generic helper scripts)
    if "androidmanifest.xml" in names or any(
        "com.android.application" in _read(root, p, 30_000)
        for p in paths
        if os.path.basename(p).lower() in ("build.gradle", "build.gradle.kts")
    ):
        hit(["android"], "AndroidManifest.xml / com.android.application")
    if any_path(lambda p: ".xcodeproj/" in p or p.endswith(".xcworkspace/")) or "package.swift" in names:
        pbx = next((p for p in paths if p.lower().endswith("project.pbxproj")), None)
        pbx_text = _read(root, pbx) if pbx else ""
        if "iphoneos" in pbx_text.lower() or not pbx_text:
            hit(["ios"], "Xcode iOS project")
        if "macosx" in pbx_text.lower():
            hit(["macos"], "Xcode macOS target")
    if "pubspec.yaml" in names:
        for d, plat in (("android/", "android"), ("ios/", "ios"), ("web/", "web"),
                        ("windows/", "windows"), ("macos/", "macos"), ("linux/", "linux")):
            if d in lower:
                hit([plat], f"Flutter {plat} target")
    if "package.json" in names and not declared_targets:
        pkg = _read(root, next(p for p in paths if os.path.basename(p).lower() == "package.json")).lower()
        if '"react-native"' in pkg or '"expo"' in pkg:
            hit(["android", "ios"], "React Native / Expo")
        if '"electron"' in pkg or "@tauri-apps" in pkg:
            hit(PC_PLATFORMS, "Electron/Tauri desktop app")
        if any(k in pkg for k in ('"vite"', '"next"', '"react-scripts"', '"@sveltejs/kit"', '"nuxt"', '"astro"')):
            hit(["web"], "web frontend build (package.json)")
    elif "package.json" in names:
        # Every package.json, not just the first; a Tauri/Electron app's own UI build is not a website.
        desktop_app = False
        for p in [x for x in paths if os.path.basename(x).lower() == "package.json" and "node_modules/" not in x.lower()][:20]:
            pkg = _read(root, p).lower()
            if '"react-native"' in pkg or '"expo"' in pkg:
                hit(["android", "ios"], "React Native / Expo")
            if '"electron"' in pkg or "@tauri-apps" in pkg:
                desktop_app = True
            elif any(k in pkg for k in ('"vite"', '"next"', '"react-scripts"', '"@sveltejs/kit"', '"nuxt"', '"astro"')):
                hit(["web"], f"web frontend build ({p})")
        if desktop_app:
            declared = _declared_desktop_targets(root, paths)
            hit(sorted(declared) if declared else PC_PLATFORMS,
                "Tauri/Electron app, declared targets: " + ", ".join(sorted(declared)) if declared else "Tauri/Electron app (targets not stated)")

    # Game engines and consoles
    if "project.godot" in names:
        hit(PC_PLATFORMS, "Godot project")
        presets = next((p for p in paths if os.path.basename(p).lower() == "export_presets.cfg"), None)
        ptxt = _read(root, presets).lower() if presets else ""
        for key, plat in (("android", "android"), ("ios", "ios"), ("web", "web")):
            if f'platform="{key}' in ptxt:
                hit([plat], f"Godot {plat} export")
    if any_path(lambda p: p.startswith("projectsettings/projectsettings.asset")):
        hit(PC_PLATFORMS, "Unity project")
    console_markers = ("devkitpro", "libnx", "devkitarm", "pspsdk", "ps2sdk", "libdragon", "gbdk", "libogc", "libctru")
    mk = [p for p in paths if os.path.basename(p).lower() in ("makefile", "cmakelists.txt")][:10]
    mk_text = " ".join(_read(root, p, 50_000).lower() for p in mk)
    if any(m in mk_text for m in console_markers) or any_path(lambda p: p.endswith((".p8", ".nds", ".gba", ".nro"))):
        hit(["console"], "console/handheld homebrew toolchain")

    # Desktop packaging. Build-tool wrappers (gradlew.bat, mvnw.cmd) only say where a project can be BUILT
    # (every Android project ships gradlew.bat), not where it runs, so they never count.
    # Bundled third-party code and virtual-environment scripts belong to someone else's project and never count.
    build_wrappers = ("gradlew.bat", "gradlew", "mvnw.cmd", "mvnw", "activate.bat", "deactivate.bat", "activate.ps1")
    not_own = ("third_party/", "third-party/", "vendor/", "external/", "node_modules/", "/libs/", "site-packages/")
    # v10.4+: documentation builders (Sphinx's docs/make.bat) say where the docs build, not where the code runs
    docs_tooling = (lambda p: p.startswith("docs/") or "/docs/" in p or os.path.basename(p) == "make.bat") if declared_targets else (lambda p: False)
    if any_path(lambda p: p.endswith((".iss", ".msi", ".nsi", ".bat", ".ps1", ".cmd")) and os.path.basename(p) not in build_wrappers
                and not any(x in p for x in not_own) and not docs_tooling(p)):
        hit(["windows"], "Windows packaging/scripts")
    if any_path(lambda p: p.endswith((".desktop", "pkgbuild", ".appimage", ".service", "snapcraft.yaml", ".flatpakref"))
                or p.endswith("appimagebuilder.yml") or "appimage" in os.path.basename(p) or "flatpak" in os.path.basename(p)):
        hit(["linux"], "Linux packaging")
    if any_path(lambda p: p.endswith(("info.plist", ".dmg"))) and "ios" not in found:
        hit(["macos"], "macOS bundle")

    # Web servers / sites
    if any_path(lambda p: p in ("index.html", "public/index.html", "static/index.html")) or any_path(
        lambda p: ("/templates/" in p or p.startswith("templates/")) and p.endswith(".html")
    ):
        hit(["web"], "web pages/templates")

    # Raspberry Pi and similar single-board computers: Linux, not "any PC"
    pi_files = [p for p in paths if p.lower().endswith((".py", ".sh", ".service", ".txt", ".md", ".cfg"))][:400]
    pi_text = " ".join(_read(root, p, 20_000).lower() for p in pi_files if any(k in p.lower() for k in ("pi", "kiosk", "install", "setup", "start", "readme", "requirements", ".service")))
    if any(k in pi_text for k in ("raspberry pi", "raspberrypi", "raspios", "raspbian", "rpi.gpio", "gpiozero", "/boot/config.txt",
                                  "chromium-browser --kiosk", "chromium --kiosk", "pigpio")):
        hit(["linux"], "Raspberry Pi (Linux single-board computer)")
        if "Raspberry Pi" not in profile:
            profile.append("Raspberry Pi")

    if declared_targets:
        ev = _declared_signals(root, paths, lower)
        if ev["android"]:
            hit(["android"], f"Android sources ({ev['android']})")
        if ev["ios"]:
            hit(["ios"], "Tauri iOS project (gen/apple)")

    # Plain cross-platform desktop code (Python/Go/Rust/Node CLIs) when nothing more specific was found
    if not found and any_path(lambda p: p.endswith((".py", ".go", ".rs", ".js", ".ts", ".java", ".cs", ".cpp", ".c", ".rb"))):
        if declared_targets and ev["windows_aware"]:
            hit(PC_PLATFORMS, "cross-platform desktop/CLI code (handles Windows explicitly)")
        elif declared_targets and ev["windows_only"] and not (ev["linux_only"] or ev["posix"]):
            hit(["windows"], "desktop/CLI code written for Windows")
        elif declared_targets and ev["posix"] and not ev["windows_only"]:
            hit(["linux", "macos"], "desktop/CLI code written for POSIX (Linux/macOS)")
        elif declared_targets and ev["linux_only"] and not ev["windows_only"] and not ev["guarded"]:
            hit(["linux"], "desktop/CLI code written for Linux")
        else:
            hit(PC_PLATFORMS, "cross-platform desktop/CLI code")

    return PlatformDetection(
        platforms=normalize_platforms(found),
        device_profile="; ".join(profile),
        signals=signals,
    )


def platform_context(platforms: List[str], device_profile: str) -> str:
    """Prompt block telling graders which hardware to judge against."""
    if not platforms and not device_profile:
        return ""
    labels = ", ".join(PLATFORM_LABELS.get(p, p) for p in platforms) or "unknown"
    lines = [f"Target Platforms: {labels}"]
    if device_profile:
        lines.append(f"Device Profile: {device_profile}")
    if "embedded" in platforms or "console" in platforms:
        lines.append(
            "GRADE RELATIVE TO THIS HARDWARE: memory, CPU, screen size and input are tightly constrained. "
            "A small, fast, glanceable UI is GOOD design here, not a missing feature; desktop conventions "
            "(menus, Docker, rich help text, plugin systems) do not apply."
        )
    elif platforms and set(platforms) <= {"android", "ios"}:
        lines.append("GRADE RELATIVE TO THIS HARDWARE: touch input, small screens, battery and mobile OS conventions.")
    return "\n".join(lines)


# --- Entry point discovery ------------------------------------------------------------------

# (pattern on the lower-cased relative path, priority) -- lower priority wins, then shallower path
ENTRY_PATTERNS = [
    (re.compile(r"(^|/)__main__\.py$"), 0),
    (re.compile(r"(^|/)main\.py$"), 0),
    (re.compile(r"(^|/)src/main\.(cpp|c|cc|rs|go|ts|js|kt|java|zig)$"), 0),
    (re.compile(r"(^|/)main\.(cpp|c|cc|rs|go|lua|gd|zig)$"), 1),
    (re.compile(r"\.ino$"), 1),
    (re.compile(r"(^|/)cmd/[^/]+/main\.go$"), 1),
    (re.compile(r"(^|/)lib/main\.dart$"), 1),
    (re.compile(r"(^|/)mainactivity\.(kt|java)$"), 1),
    (re.compile(r"(^|/)(contentview|[a-z0-9_]+app)\.swift$"), 2),
    (re.compile(r"(^|/)program\.cs$"), 2),
    (re.compile(r"(^|/)(app|cli|server|bot|manage|run)\.py$"), 2),
    (re.compile(r"(^|/)src/(app|main|index)\.(tsx|jsx|ts|js|vue|svelte)$"), 2),
    (re.compile(r"(^|/)(index|server|app|main)\.(js|ts|mjs)$"), 3),
    (re.compile(r"(^|/)index\.html$"), 4),
]
ENTRY_SKIP = ("test", "example", "vendor/", "third_party/", "node_modules/", "lib/gfx", "docs/")


def find_entry_file(root: Path, paths: Optional[List[str]] = None) -> Optional[str]:
    """Best guess at the program's main/entry file (relative path), or None."""
    root = Path(root)
    paths = paths if paths is not None else _list_paths(root)
    best = None
    for p in paths:
        if p.endswith("/"):
            continue
        low = p.lower().replace(os.sep, "/")
        if any(s in low for s in ENTRY_SKIP):
            continue
        for rx, prio in ENTRY_PATTERNS:
            if rx.search(low):
                key = (prio, low.count("/"), len(low))
                if best is None or key < best[0]:
                    best = (key, p)
                break
    return best[1] if best else None


def read_entry_file(root: Path, rel: Optional[str], max_chars: int = 5000) -> str:
    return _read(Path(root), rel, max_chars) if rel else ""


# Signals that only mean "there is some code"; the model may override platforms based on these
WEAK_SIGNALS = {"cross-platform desktop/CLI code"}
