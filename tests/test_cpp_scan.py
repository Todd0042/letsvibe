"""C/C++ in the intent-aware scan (engine v10.4+): an ESP32/Arduino-style project with real git history.

Its own code has every kind of C++ catch block (empty, only written to the debugger, explained, visibly logged,
returning a failure value, rethrowing), files the build compiles without anyone naming them, a truly orphaned file,
an image converted to a C array, a bundled library (one bulk import, with its own library.properties) and a host SDK
header (Nexus.h), both full of swallowed errors. Older engines (the default mode) must not see any of it.
"""

import subprocess
from pathlib import Path

import pytest

from letsvibe.analyzers.code_scan import scan

MAIN = """#include <Arduino.h>
#include "Sensor.h"

Sensor sensor;

void setup() {
    Serial.begin(115200);
    sensor.begin();
}

int scaled(int raw);

void loop() {
    if (sensor.poll()) Serial.println(scaled(analogRead(34)));
    delay(50);
}
"""

SENSOR_H = """#pragma once
class Sensor {
public:
    void begin();
    bool poll();
    bool saveCalibration();
    void flushCache();
    bool readRegister(int reg);
    void dropSample();
};
"""

SENSOR_CPP = """#include "Sensor.h"
#include <stdexcept>
#include <Windows.h>

void Sensor::begin() {
    try {
        initBus();
    } catch (...) {}
}

bool Sensor::poll() {
    try {
        return readRegister(0x10);
    } catch (const std::exception& e) {
        Serial.println(e.what());
    }
    return false;
}

bool Sensor::saveCalibration() {
    try {
        writeFlash();
        return true;
    } catch (...) {
        return false;
    }
}

void Sensor::flushCache() {
    try {
        cache.clear();
    } catch (...) {
        // cache may already be gone during shutdown; nothing to do
    }
}

bool Sensor::readRegister(int reg) {
    try {
        return bus.read(reg) >= 0;
    } catch (const std::runtime_error&) {
        throw;
    }
}

void Sensor::dropSample() {
    try {
        samples.pop();
    } catch (...) {
        OutputDebugStringA("pop failed");
    }
}
"""

# Compiled by PlatformIO because they are in src/; nothing includes them, but their functions are called elsewhere.
UTIL_CPP = """int clampReading(int v) {
    if (v < 0) return 0;
    return v > 1023 ? 1023 : v;
}
"""
USES_UTIL = """int clampReading(int v);
int scaled(int raw) { return clampReading(raw) * 2; }
"""
ORPHAN_CPP = """static int forgottenHelperNobodyCalls(int x) {
    return x * 42;
}
"""
VENDOR_CPP = """int parse_{i}(const char* s) {{
    try {{ return atoi(s); }} catch (...) {{}}
    return 0;
}}
"""
NEXUS_H = """#pragma once
inline void nexus_safe() { try { throw 1; } catch (...) {} }
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@localhost", *args],
                   check=True, capture_output=True)


@pytest.fixture
def esp32_project(tmp_path):
    root = tmp_path / "dash"
    (root / "src").mkdir(parents=True)
    (root / "include").mkdir()
    (root / "platformio.ini").write_text("[env:esp32]\nplatform = espressif32\nframework = arduino\n")
    (root / "src/main.cpp").write_text(MAIN)
    (root / "include/Sensor.h").write_text(SENSOR_H)
    (root / "src/Sensor.cpp").write_text(SENSOR_CPP)
    (root / "src/util.cpp").write_text(UTIL_CPP)
    (root / "src/scale.cpp").write_text(USES_UTIL)
    (root / "src/orphan.cpp").write_text(ORPHAN_CPP)
    (root / "src/Nexus.h").write_text(NEXUS_H)
    rows = "\n".join("    " + ", ".join(f"0x{(i * 7 + j) % 256:02x}" for j in range(16)) + "," for i in range(400))
    (root / "src/logo_img.c").write_text(f"#include \"lvgl.h\"\nconst uint8_t logo_map[] = {{\n{rows}\n}};\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "Dash firmware")
    lib = root / "lib/TinyParse"
    lib.mkdir(parents=True)
    (lib / "library.properties").write_text("name=TinyParse\nversion=1.0.0\n")
    for i in range(40):
        (lib / f"parse_{i}.cpp").write_text(VENDOR_CPP.format(i=i))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "Add TinyParse library")
    return root


def _all(result):
    return result["functionality"] + result["maintainer"]


def test_cpp_catch_blocks_judged_by_intent(esp32_project):
    found = _all(scan(esp32_project, intent_aware=True))
    swallowed = {(f["file"], f["function"], f["detail"]) for f in found if f["kind"] == "swallowed error"}
    funcs = {fn for _, fn, _ in swallowed}
    # empty, and only written to the debugger: swallowed
    assert any("begin" in (fn or "") for fn in funcs)
    assert any("dropSample" in (fn or "") and "debug output" in d for _, fn, d in swallowed)
    # logged where the user sees it, returns a failure value, explained by a comment, rethrown: handled
    for handled in ("poll", "saveCalibration", "flushCache", "readRegister"):
        assert not any(fn and fn.endswith(handled) for fn in funcs), handled
    assert len(swallowed) == 2
    # the bundled library and the host SDK header are someone else's code: never judged
    assert not any(f["file"].startswith("lib/") or f["file"].endswith("Nexus.h") for f in found)


def test_cpp_files_the_build_compiles_are_not_unused(esp32_project):
    result = scan(esp32_project, intent_aware=True)
    unused = {f["file"] for f in _all(result) if f["kind"] == "unused file"}
    assert unused == {"src/orphan.cpp"}  # main.cpp: entry (setup/loop); Sensor.cpp: its header is included;
    # util.cpp / scale.cpp: their functions are called from another file; orphan.cpp: nothing refers to it or to it


def test_images_compiled_into_c_arrays_are_not_code(esp32_project):
    result = scan(esp32_project, intent_aware=True)
    assert not any(f["file"] == "src/logo_img.c" for f in _all(result))  # 400 rows of bytes: no "oversized file"
    assert result["lines_of_code"] < 150  # the firmware itself, not the image or the library


def test_older_engines_never_see_cpp(esp32_project):
    result = scan(esp32_project)
    assert not any(f["file"].endswith((".cpp", ".c", ".h")) for f in _all(result))
    assert result["lines_of_code"] == 0
