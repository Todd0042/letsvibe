"""
Deterministic deep static intelligence & AST audit for LetsVibe on Anna.

Produces verifiable, line-accurate findings (syntax errors, stub functions,
swallowed exceptions, unsafe primitives, duplicate code blocks, test assertion
density, and deterministic score guardrails) before LLM synthesis.
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict
from typing import Any, Dict, List, Tuple

_ASSERT_RE = re.compile(
    r"\b(assert\w*|expect\s*\(|pytest\.raises|assertEqual|assertTrue|assertFalse|assertRaises|t\.Run|t\.Error|t\.Fatal)\b"
)
_JS_EMPTY_CATCH_RE = re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*(?://[^\n]*\s*)*\}", re.MULTILINE)
_JS_DANGER_RE = re.compile(r"\b(eval\s*\(|new\s+Function\s*\(|innerHTML\s*=|dangerouslySetInnerHTML)\b")
_BRACE_UNSAFE_RE = re.compile(
    r"\b(Runtime\.getRuntime\(\)\.exec|eval\s*\(|new\s+Function\s*\(|innerHTML\s*=|dangerouslySetInnerHTML|unimplemented!\s*\(|unsafe\s*\{)"
)
_KT_NON_NULL_RE = re.compile(r"[A-Za-z0-9_)\]]!!")
_STUB_BODY_RE = re.compile(
    r"(?:fun|function|func|fn)\s+([A-Za-z_]\w*)\s*\([^)]*\)[^{=\n]*\{\s*(?://[^\n]*\s*)*(?:TODO\s*\([^)]*\)|todo!\s*\([^)]*\)|unimplemented!\s*\([^)]*\)|throw\s+new\s+(?:Error|UnsupportedOperationException|NotImplementedException)\b[^\n}]*)?\s*\}"
    r"|fun\s+([A-Za-z_]\w*)\s*\([^)]*\)[^{=\n]*=\s*TODO\s*\([^)]*\)",
    re.MULTILINE,
)
_FUNC_HEADER_RE = re.compile(
    r"^\s*(?:(?:public|private|internal|protected|export|default|async|suspend|override|open|inline|static|final)\s+)*(?:fun|function|func|fn)\s+([A-Za-z_]\w*)\s*\("
)
_TODO_RE = re.compile(r"(?:#|//|/\*)\s*(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)
_LOCALHOST_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?")

_LOCKFILES = {
    "uv.lock",
    "poetry.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "cargo.lock",
    "go.sum",
    "pipfile.lock",
}


def _is_test_path(rel_path: str) -> bool:
    low = rel_path.lower().replace("\\", "/")
    name = low.rsplit("/", 1)[-1]
    return (
        "/tests/" in low
        or "/test/" in low
        or low.startswith("tests/")
        or low.startswith("test/")
        or name.startswith("test_")
        or "_test." in name
        or ".test." in name
        or ".spec." in name
    )


def _is_stub_body(body: List[ast.stmt]) -> bool:
    """True when a function body is effectively a no-op stub."""
    stmts = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
    if not stmts:
        return True
    if len(stmts) == 1:
        s = stmts[0]
        if isinstance(s, ast.Pass):
            return True
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and s.value.value is Ellipsis:
            return True
        if isinstance(s, ast.Return) and (
            s.value is None or (isinstance(s.value, ast.Constant) and s.value.value is None)
        ):
            return True
        if isinstance(s, ast.Raise) and isinstance(s.exc, ast.Call):
            fn = getattr(s.exc.func, "id", "") or getattr(s.exc.func, "attr", "")
            if fn == "NotImplementedError":
                return True
    return False


def _analyze_python_file(rel_path: str, content: str, out: Dict[str, Any]) -> None:
    try:
        tree = ast.parse(content, filename=rel_path)
    except SyntaxError as exc:
        out["syntax_errors"].append(
            {
                "file": rel_path,
                "line": exc.lineno or 1,
                "col": exc.offset or 0,
                "message": f"SyntaxError: {exc.msg}",
            }
        )
        return

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            span = (getattr(node, "end_lineno", node.lineno) or node.lineno) - node.lineno + 1
            if span >= 80:
                out["oversized_functions"].append(
                    {"file": rel_path, "name": node.name, "line": node.lineno, "loc": span}
                )
            if not node.name.startswith("_") and _is_stub_body(node.body):
                out["stub_functions"].append({"file": rel_path, "name": node.name, "line": node.lineno})
            for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    out["reliability_smells"].append(
                        {
                            "file": rel_path,
                            "line": node.lineno,
                            "kind": "mutable_default_arg",
                            "detail": f"`{node.name}` uses a mutable default argument",
                        }
                    )
        elif isinstance(node, ast.ExceptHandler):
            is_bare = node.type is None
            is_broad = isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")
            if (is_bare or is_broad) and len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                out["swallowed_exceptions"].append(
                    {
                        "file": rel_path,
                        "line": node.lineno,
                        "kind": "bare_except_pass" if is_bare else "broad_except_pass",
                    }
                )
        elif isinstance(node, ast.Call):
            fn_name = ""
            if isinstance(node.func, ast.Name):
                fn_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                parent = getattr(node.func.value, "id", "")
                fn_name = f"{parent}.{node.func.attr}" if parent else node.func.attr

            if fn_name in ("eval", "exec", "pickle.loads", "pickle.load", "marshal.loads"):
                out["unsafe_calls"].append(
                    {"file": rel_path, "line": node.lineno, "call": fn_name}
                )
            elif fn_name == "yaml.load":
                has_safe = any(
                    kw.arg == "Loader" and "Safe" in ast.dump(kw.value) for kw in node.keywords
                )
                if not has_safe:
                    out["unsafe_calls"].append(
                        {"file": rel_path, "line": node.lineno, "call": "yaml.load (unsafe Loader)"}
                    )
            elif fn_name.startswith("subprocess.") and any(
                kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords
            ):
                out["unsafe_calls"].append(
                    {"file": rel_path, "line": node.lineno, "call": f"{fn_name}(shell=True)"}
                )


def _mask_comments_and_strings(content: str, *, allow_backtick: bool = False) -> str:
    """Replaces string literals and comments with spaces (preserving newlines) for brace/pattern checks."""
    out: List[str] = []
    i = 0
    n = len(content)
    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            while i < n and content[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and nxt == "*":
            out.append("  ")
            i += 2
            while i < n and not (content[i] == "*" and i + 1 < n and content[i + 1] == "/"):
                out.append("\n" if content[i] == "\n" else " ")
                i += 1
            if i < n:
                out.append("  ")
                i += 2
            continue
        if content.startswith('"""', i):
            out.append("   ")
            i += 3
            while i < n and not content.startswith('"""', i):
                out.append("\n" if content[i] == "\n" else " ")
                i += 1
            if i < n:
                out.append("   ")
                i += 3
            continue
        if ch in ('"', "'") or (allow_backtick and ch == "`"):
            quote = ch
            out.append(" ")
            i += 1
            while i < n:
                c = content[i]
                if c == "\\":
                    out.append(" ")
                    i += 1
                    if i < n:
                        out.append("\n" if content[i] == "\n" else " ")
                        i += 1
                    continue
                out.append("\n" if c == "\n" else " ")
                i += 1
                if c == quote:
                    break
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _analyze_brace_file(rel_path: str, content: str, out: Dict[str, Any]) -> None:
    low = rel_path.lower()
    is_js = low.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"))
    is_kt = low.endswith((".kt", ".kts"))
    masked = _mask_comments_and_strings(content, allow_backtick=is_js)

    # 1. Brace balance check for languages without /regex/ literals
    if low.endswith((".kt", ".kts", ".java", ".go", ".cs", ".dart", ".swift", ".scala", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp")):
        depth = 0
        for idx, ch in enumerate(masked):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    line = content.count("\n", 0, idx) + 1
                    out["syntax_errors"].append(
                        {"file": rel_path, "line": line, "col": 0, "message": "Unmatched closing brace '}'"}
                    )
                    break
        else:
            if depth > 0:
                out["syntax_errors"].append(
                    {"file": rel_path, "line": len(content.splitlines()) or 1, "col": 0, "message": f"Unclosed '{{' ({depth} unclosed)"}
                )

    # 2. Swallowed exceptions (empty catch blocks, ignoring comments inside catch)
    for m in _JS_EMPTY_CATCH_RE.finditer(content):
        line = content.count("\n", 0, m.start()) + 1
        out["swallowed_exceptions"].append({"file": rel_path, "line": line, "kind": "empty_catch"})

    # 3. Stub functions (empty concrete bodies or TODO()/unimplemented)
    for m in _STUB_BODY_RE.finditer(content):
        fn_name = m.group(1) or m.group(2) or "anonymous"
        line = content.count("\n", 0, m.start()) + 1
        out["stub_functions"].append({"file": rel_path, "name": fn_name, "line": line})

    # 4. Unsafe calls (in masked code so strings/comments don't trigger)
    danger_re = _JS_DANGER_RE if is_js else _BRACE_UNSAFE_RE
    for m in danger_re.finditer(masked):
        line = content.count("\n", 0, m.start()) + 1
        out["unsafe_calls"].append({"file": rel_path, "line": line, "call": m.group(1).strip()})
    if is_kt:
        for m in _KT_NON_NULL_RE.finditer(masked):
            line = content.count("\n", 0, m.start()) + 1
            out["unsafe_calls"].append({"file": rel_path, "line": line, "call": "!! (unchecked non-null assertion)"})

    # 5. Oversized functions (>= 80 LOC)
    lines = masked.splitlines()
    n_lines = len(lines)
    i = 0
    while i < n_lines:
        hm = _FUNC_HEADER_RE.match(lines[i])
        if hm:
            fn_name = hm.group(1)
            start_line = i + 1
            depth = 0
            found_open = False
            j = i
            while j < n_lines:
                for ch in lines[j]:
                    if ch == "{":
                        depth += 1
                        found_open = True
                    elif ch == "}":
                        depth -= 1
                if found_open and depth <= 0:
                    span = j - i + 1
                    if span >= 80:
                        out["oversized_functions"].append(
                            {"file": rel_path, "name": fn_name, "line": start_line, "loc": span}
                        )
                    i = j
                    break
                j += 1
        i += 1


def _find_duplicate_blocks(files: Dict[str, str], window: int = 6) -> List[Dict[str, Any]]:
    """Finds identical normalized multi-line code blocks across distinct source files."""
    seen: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    code_exts = (
        ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
        ".kt", ".kts", ".go", ".rs", ".java", ".cs", ".swift", ".dart", ".scala",
        ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
    )
    for rel_path, content in files.items():
        if not rel_path.lower().endswith(code_exts) or _is_test_path(rel_path):
            continue
        raw_lines = content.splitlines()
        norm: List[Tuple[int, str]] = []
        for idx, line in enumerate(raw_lines, start=1):
            s = line.strip()
            if not s or s.startswith(("#", "//", "/*", "*", "import ", "from ")):
                continue
            if len(s) < 16:
                continue
            norm.append((idx, s))
        if len(norm) < window:
            continue
        for i in range(len(norm) - window + 1):
            chunk = "\n".join(x[1] for x in norm[i : i + window])
            digest = hashlib.blake2b(chunk.encode("utf-8", errors="ignore"), digest_size=10).hexdigest()
            entries = seen[digest]
            if not any(f == rel_path for f, _ in entries):
                entries.append((rel_path, norm[i][0]))

    dups: List[Dict[str, Any]] = []
    for entries in seen.values():
        if len(entries) >= 2:
            dups.append(
                {
                    "locations": [{"file": f, "line": ln} for f, ln in entries[:4]],
                    "count": len(entries),
                }
            )
    dups.sort(key=lambda d: d["count"], reverse=True)
    return dups


def run_deep_audit(files: Dict[str, str]) -> Dict[str, Any]:
    """Runs deep static AST/cross-file checks across all ingested repository files."""
    out: Dict[str, Any] = {
        "syntax_errors": [],
        "stub_functions": [],
        "swallowed_exceptions": [],
        "unsafe_calls": [],
        "reliability_smells": [],
        "oversized_functions": [],
        "duplicate_blocks": [],
        "todo_count": 0,
        "localhost_hardcodes": 0,
        "test_assertions_count": 0,
        "has_lockfile": False,
        "lockfiles": [],
    }

    for rel_path, content in files.items():
        name = rel_path.rsplit("/", 1)[-1].lower()
        if name in _LOCKFILES:
            out["has_lockfile"] = True
            out["lockfiles"].append(rel_path)

        out["todo_count"] += len(_TODO_RE.findall(content))

        if _is_test_path(rel_path):
            out["test_assertions_count"] += len(_ASSERT_RE.findall(content))
            continue

        low = rel_path.lower()
        out["localhost_hardcodes"] += len(_LOCALHOST_RE.findall(content))

        if low.endswith(".py"):
            _analyze_python_file(rel_path, content, out)
        elif low.endswith((
            ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
            ".kt", ".kts", ".java", ".go", ".rs", ".cs", ".swift", ".dart", ".scala",
            ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
        )):
            _analyze_brace_file(rel_path, content, out)

    all_dups = _find_duplicate_blocks(files)
    out["duplicate_blocks_total"] = len(all_dups)
    out["duplicate_blocks"] = all_dups[:15]

    # Trim lists for clean JSON payload + prompt compactness
    for key in (
        "syntax_errors",
        "stub_functions",
        "swallowed_exceptions",
        "unsafe_calls",
        "reliability_smells",
        "oversized_functions",
    ):
        out[f"{key}_total"] = len(out[key])
        out[key] = out[key][:15]

    return out


def apply_deterministic_calibration(
    factor_scores: Dict[str, float],
    deep_audit: Dict[str, Any],
    total_files: int,
    test_files_count: int,
    secret_count: int,
) -> Tuple[Dict[str, float], List[str]]:
    """
    Applies transparent deterministic guardrails to prevent LLM score drift/hallucination.
    Returns (calibrated_scores, calibration_notes).
    """
    scores = {k: float(v) for k, v in factor_scores.items()}
    notes: List[str] = []

    syn_err = int(deep_audit.get("syntax_errors_total", 0))
    stubs = int(deep_audit.get("stub_functions_total", 0))
    swallowed = int(deep_audit.get("swallowed_exceptions_total", 0))
    unsafe = int(deep_audit.get("unsafe_calls_total", 0))
    dups = len(deep_audit.get("duplicate_blocks") or [])
    assertions = int(deep_audit.get("test_assertions_count", 0))
    has_lock = bool(deep_audit.get("has_lockfile", False))

    if syn_err > 0:
        cap = max(35.0, 65.0 - syn_err * 8.0)
        if scores.get("functionality", 100.0) > cap:
            scores["functionality"] = cap
            notes.append(f"Functionality capped at {cap:.0f} due to {syn_err} syntax error(s).")
        if scores.get("maintainability", 100.0) > cap:
            scores["maintainability"] = cap

    if stubs >= 4 and total_files > 0:
        penalty = min(18.0, stubs * 2.0)
        scores["functionality"] = max(20.0, scores.get("functionality", 65.0) - penalty)
        notes.append(f"Functionality -{penalty:.0f} for {stubs} unimplemented/stub functions.")

    if swallowed >= 3:
        penalty = min(15.0, swallowed * 2.5)
        scores["maintainability"] = max(25.0, scores.get("maintainability", 65.0) - penalty)
        notes.append(f"Maintainability -{penalty:.0f} for {swallowed} swallowed exception blocks.")

    if unsafe > 0 or secret_count > 0:
        sec_cap = max(30.0, 78.0 - unsafe * 7.0 - secret_count * 12.0)
        if scores.get("operational_safety", 100.0) > sec_cap:
            scores["operational_safety"] = sec_cap
            notes.append(
                f"Operational Safety capped at {sec_cap:.0f} ({unsafe} unsafe call(s), {secret_count} secret hit(s))."
            )

    if dups >= 4:
        arch_pen = min(10.0, dups * 1.5)
        scores["architecture"] = max(30.0, scores.get("architecture", 65.0) - arch_pen)
        notes.append(f"Architecture -{arch_pen:.0f} for {dups} cross-file duplicate blocks.")

    if test_files_count > 0 and assertions >= 15:
        bonus = min(5.0, 2.0 + assertions / 30.0)
        scores["maintainability"] = min(98.0, scores.get("maintainability", 65.0) + bonus)
        notes.append(f"Maintainability +{bonus:.1f} for {assertions} real test assertions.")
    elif test_files_count > 0 and assertions == 0:
        scores["maintainability"] = max(25.0, scores.get("maintainability", 65.0) - 6.0)
        notes.append("Maintainability -6 for hollow test files with 0 assertions.")

    if has_lock:
        scores["operational_safety"] = min(98.0, scores.get("operational_safety", 65.0) + 2.0)

    return {k: round(max(0.0, min(100.0, v)), 1) for k, v in scores.items()}, notes
