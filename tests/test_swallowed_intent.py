"""Swallowed-error detection with intent (engine v10.4+, scan(..., intent_aware=True)) vs the original rules.

A small project with deliberate error handling (expected exceptions, explained drops, errors logged where whoever
runs it sees them, best-effort cleanup) next to real swallowed errors, in Python, TypeScript, Rust and Go. The
original rules must still report exactly what they always did (older engines stay reproducible); the intent-aware
rules report only the real ones, and Rust/Go are scanned too.
"""

import subprocess
from pathlib import Path

from letsvibe.analyzers.code_scan import scan

PY = '''import asyncio, logging, os
log = logging.getLogger(__name__)

def read_nonblocking(fd):
    try:
        return os.read(fd, 4096)
    except BlockingIOError:
        return b""

def stop(proc):
    try:
        proc.kill()
    except ProcessLookupError:
        pass

def load_settings(path):
    try:
        return open(path).read()
    except OSError:
        return ""  # first run: no settings file yet, defaults apply

def sync(client):
    try:
        client.push()
    except Exception as exc:
        log.warning("sync failed: %s", exc)

def save(path, data):
    try:
        open(path, "w").write(data)
    except Exception:
        pass

def fetch(url):
    try:
        return download(url)
    except Exception:
        return None

def debug_only(x):
    try:
        x.run()
    except Exception as exc:
        log.debug("run failed: %s", exc)
'''

TS = '''export async function load(api) {
  try { return await api.get("/items"); } catch { /* offline: the list shows cached items */ }
}
export async function save(api, item) {
  try { await api.put("/items", item); } catch (e) { console.error("save failed", e); }
}
export async function remove(api, id) {
  try { await api.del(`/items/${id}`); } catch (e) {}
}
export async function count(api) {
  try { return (await api.get("/count")).n; } catch (e) { return null; }
}
'''

RS = '''use std::fs;
pub fn cleanup(child: &mut std::process::Child, path: &str) {
    let _ = child.kill();
    let _ = fs::remove_file(path);
}
pub fn write_config(path: &str, text: &str) {
    let _ = fs::write(path, text);
}
pub fn parse(n: &str) -> u32 {
    match n.parse::<u32>() {
        Ok(v) => v,
        Err(_) => {}
    }
    0
}
pub fn best_effort(path: &str) {
    // the cache is optional: a failed write just means a slower next start
    let _ = fs::write(path, "cache");
    std::env::var("HOME").ok();
}
'''

GO = '''package main
import "os"
func save(p string, b []byte) {
	_ = os.WriteFile(p, b, 0o644)
}
func load(p string) []byte {
	b, err := os.ReadFile(p)
	if err != nil {}
	return b
}
func tidy(f *os.File) {
	_ = f.Close()
}
func optional(p string) {
	// a missing marker file is normal here
	_ = os.Remove(p)
}
'''


def make_repo(tmp_path: Path) -> Path:
    readme = "# demo\nParts: app/core.py, web/api.ts, native/src/lib.rs, tools/main.go\n"  # so none counts as unused
    for rel, text in {"app/core.py": PY, "web/api.ts": TS, "native/src/lib.rs": RS, "tools/main.go": GO,
                      "README.md": readme}.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def swallowed(result) -> list[tuple[str, int]]:
    allf = result["functionality"] + result["maintainer"]
    return sorted((f["file"], f["line"]) for f in allf if f["kind"] == "swallowed error")


def test_original_rules_unchanged(tmp_path):
    root = make_repo(tmp_path)
    found = swallowed(scan(root))
    # What the original rules have always reported for this code (Python and TS only, no intent).
    assert [f for f, _ in found].count("app/core.py") == 6   # what the original rules have always reported here
    assert all(f.endswith((".py", ".ts")) for f, _ in found)  # Rust and Go aren't scanned by the old rules


def test_intent_aware_rules(tmp_path):
    root = make_repo(tmp_path)
    found = swallowed(scan(root, intent_aware=True))
    by_file = {}
    for f, ln in found:
        by_file.setdefault(f, []).append(ln)
    # Python: save (pass), fetch (return None) and debug_only (debug log nobody sees) are real; BlockingIOError,
    # ProcessLookupError, the explained OSError and the logged warning are deliberate.
    assert len(by_file["app/core.py"]) == 3, by_file
    # TypeScript: remove (empty catch) and count (return null) are real; the explained one and console.error aren't.
    assert len(by_file["web/api.ts"]) == 2, by_file
    # Rust: write_config's `let _ = fs::write`, the empty Err(_) arm; kill/remove_file cleanup and the commented
    # best-effort write aren't. The bare `.ok();` on an env var lookup is.
    assert len(by_file["native/src/lib.rs"]) == 3, by_file
    # Go: `_ = os.WriteFile` and the empty `if err != nil {}`; Close and the commented Remove aren't.
    assert len(by_file["tools/main.go"]) == 2, by_file
