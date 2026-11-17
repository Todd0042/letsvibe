"""Deterministic host/SDK header capability & API-surface extractor (zero LLM cost)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

HEADER_EXTS = (".h", ".hpp", ".hh", ".hxx")
C_SOURCE_EXTS = (".c", ".cc", ".cpp", ".cxx", ".m", ".mm", ".h", ".hpp", ".hh", ".hxx")

_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)
_TYPE_RE = re.compile(
    r"\b(?:typedef\s+)?(?:struct|class|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b"
    r"|^\s*typedef\b[^;{}]*?\b([A-Za-z_][A-Za-z0-9_]*)\s*;",
    re.M,
)
_FUNC_PTR_RE = re.compile(r"\(\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\(")
_FUNC_DECL_RE = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_:*&<>\s]{1,60}?)\s+([A-Za-z_][A-Za-z0-9_]{2,64})\s*\([^;{}]*\)\s*(?:const\s*)?(?:;|{)",
    re.M,
)

_C_KEYWORDS: Set[str] = {
    "if", "else", "for", "while", "switch", "return", "sizeof", "typedef",
    "struct", "class", "enum", "union", "const", "static", "inline", "void",
    "int", "char", "bool", "float", "double", "long", "short", "unsigned",
    "signed", "define", "ifdef", "ifndef", "endif", "pragma", "include",
    "true", "false", "nullptr", "NULL",
}

_CAPABILITY_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("UI / Rendering host", ("render", "imgui", "draw", "window", "panel", "overlay", "hud", "widget", "menu", "canvas")),
    ("Options / Settings hook", ("option", "setting", "config", "preference")),
    ("Plugin Lifecycle", ("load", "unload", "init", "shutdown", "addon", "plugin", "getaddondef", "signature", "apiversion")),
    ("Events / Input / Keybinds", ("event", "keybind", "hotkey", "wndproc", "input", "subscribe", "raise", "callback")),
    ("Host Logging / Alerts / IO", ("log", "alert", "notify", "toast", "texture", "font", "request", "datalink")),
]


def _unique_preserve(items: List[str], limit: int) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if item and item not in _C_KEYWORDS and item not in seen:
            seen.add(item)
            out.append(item)
            if len(out) >= limit:
                break
    return out


def extract_header_api_summary(header_name: str, header_text: str) -> Dict[str, Any]:
    """Deterministically parses types, function/callback symbols, and host capability tags from a C/C++ header."""
    cleaned = _COMMENT_RE.sub(" ", header_text[:120_000])

    types_raw: List[str] = []
    for m in _TYPE_RE.finditer(cleaned):
        name = m.group(1) or m.group(2)
        if name and len(name) >= 3:
            types_raw.append(name)

    funcs_raw: List[str] = []
    for m in _FUNC_PTR_RE.finditer(cleaned):
        funcs_raw.append(m.group(1))
    for m in _FUNC_DECL_RE.finditer(cleaned):
        funcs_raw.append(m.group(1))

    types = _unique_preserve(types_raw, limit=15)
    functions = _unique_preserve(funcs_raw, limit=25)
    symbols = _unique_preserve(types + functions, limit=30)

    haystack = " ".join(symbols).lower()
    capabilities: List[str] = []
    for label, keywords in _CAPABILITY_RULES:
        if any(kw in haystack for kw in keywords):
            capabilities.append(label)

    return {
        "name": header_name,
        "types": types,
        "functions": functions,
        "symbols": symbols,
        "capabilities": capabilities,
    }


def _collect_repo_headers(repo_root: Path) -> Dict[str, List[Path]]:
    """Indexes all .h/.hpp/.hh/.hxx files under repo_root (including vendor/third_party/include) by lowercase basename."""
    by_name: Dict[str, List[Path]] = {}
    if not repo_root.exists():
        return by_name
    count = 0
    for path in repo_root.rglob("*"):
        if count >= 4000:
            break
        parts_lower = {p.lower() for p in path.parts}
        if ".git" in parts_lower or "node_modules" in parts_lower:
            continue
        if path.is_file() and path.suffix.lower() in HEADER_EXTS:
            count += 1
            by_name.setdefault(path.name.lower(), []).append(path)
    return by_name


def discover_in_repo_host_headers(
    repo_root: Path,
    source_files: Dict[str, str],
    max_chars: int = 1200,
) -> str:
    """Finds headers #included by Repo 1's own code and returns a compact capability & symbol summary."""
    included_by: Dict[str, List[str]] = {}
    c_corpus_parts: List[str] = []

    for rel_path, content in source_files.items():
        if not rel_path.lower().endswith(C_SOURCE_EXTS):
            continue
        c_corpus_parts.append(content)
        for inc_target in _INCLUDE_RE.findall(content):
            base = Path(inc_target.replace("\\", "/")).name.lower()
            if base.endswith(HEADER_EXTS):
                included_by.setdefault(base, []).append(rel_path)

    if not included_by:
        return ""

    repo_headers = _collect_repo_headers(repo_root)
    if not repo_headers:
        return ""

    corpus = "\n".join(c_corpus_parts)
    candidates: List[Tuple[int, str, Path, Dict[str, Any], List[str]]] = []

    for base_name, includers in included_by.items():
        paths = repo_headers.get(base_name) or []
        if not paths:
            continue
        # Prefer headers in include/vendor/third_party or shortest relative path
        chosen = sorted(paths, key=lambda p: (len(p.parts), str(p)))[0]
        try:
            text = chosen.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        summary = extract_header_api_summary(chosen.name, text)
        caps = summary["capabilities"]
        syms = summary["symbols"]
        if not syms or len(caps) < 2:
            continue
        used = [s for s in syms if re.search(rf"\b{re.escape(s)}\b", corpus)][:12]
        # Rank higher when multiple host capabilities + actual usage in Repo 1
        score = len(caps) * 3 + len(used)
        candidates.append((score, base_name, chosen, summary, used))

    if not candidates:
        return ""

    candidates.sort(key=lambda item: item[0], reverse=True)
    lines: List[str] = []
    total = 0
    for _, _, chosen, summary, used in candidates[:5]:
        try:
            rel_hdr = chosen.relative_to(repo_root).as_posix()
        except ValueError:
            rel_hdr = chosen.name
        includer_list = ", ".join(sorted(set(included_by.get(chosen.name.lower(), [])))[:4])
        block = (
            f"- `{chosen.name}` (`{rel_hdr}`, included by `{includer_list}`):\n"
            f"  - Host-provided capabilities: {', '.join(summary['capabilities'])}\n"
            f"  - API symbols: {', '.join(summary['symbols'][:18])}\n"
            f"  - Used/implemented in Repo 1: {', '.join(used) if used else 'included directly'}"
        )
        if total + len(block) > max_chars and lines:
            break
        lines.append(block)
        total += len(block) + 1

    return "\n".join(lines)
