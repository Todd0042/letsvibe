"""
Proven code findings (Python only; nothing from the repository is ever executed). Each finding is a fact a person can
check at the given file and line. Used two ways in rubric v8:
  - FUNCTIONALITY_KINDS feed the Functionality grade (things that make the program misbehave for its users);
  - everything else becomes the owner-only "maintainer notes" (code health is never graded or shown publicly).
"""
import ast
import hashlib
import json
import re
import subprocess  # nosec B404 - git/nvidia-smi run with fixed list arguments, never a shell
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import lizard
except ImportError:  # pragma: no cover
    lizard = None

CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".kt", ".java", ".go", ".rs", ".cs", ".gd", ".swift", ".sh", ".vue", ".svelte")
WEB_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".html")
SKIP = re.compile(r"(^|/)(node_modules|vendor|third_party|dist|build|\.git|venv|\.venv|__pycache__|site-packages|static/vendor)(/|$)|\.min\.(js|css)$")
TEST = re.compile(r"(^|/)(tests?|__tests__|spec|androidTest|testing)(/|$)|(_test|\.test|\.spec|Test)\.\w+$|(^|/)test_")
ENTRY = re.compile(r"(^|/)(page|layout|route|error|loading|not-found|template|default|opengraph-image|twitter-image|icon|apple-icon|"
                   r"sitemap|robots|manifest|middleware|instrumentation|global-error)\.(tsx?|jsx?|mjs)$"
                   r"|(^|/)(main|__main__|app|index|server|manage|setup|conftest|wsgi|asgi)\.(py|js|ts|mjs)$"
                   r"|(^|/)[^/]*\.config\.(js|ts|mjs|cjs)$|\.(sh|ps1|bat)$|\.d\.ts$|(^|/)\.[^/]+$|(^|/)(Makefile|Dockerfile)$"
                   r"|(Activity|Application|Service|Receiver|Provider|Worker)\.(kt|java)$")
JSONC = re.compile(r"(^|/)(tsconfig[^/]*|jsconfig[^/]*|devcontainer)\.json$|\.jsonc$|(^|/)\.vscode/")
RISKY = [(re.compile(r"\beval\s*\("), "eval() runs text as code"), (re.compile(r"\bexec\s*\("), "exec() runs text as code"),
         (re.compile(r"shell\s*=\s*True"), "runs a command through the shell (shell=True)"),
         (re.compile(r"\bos\.system\s*\("), "runs a command through the shell (os.system)"),
         (re.compile(r"new\s+Function\s*\("), "new Function() runs text as code")]
# Swallowed errors are maintainer notes only: checked by hand on tiki-karaoke (2026-11-12), even inside route handlers
# they were deliberate fallbacks ("try the next player", "default when the body is empty"), not defects users meet.
FUNCTIONALITY_KINDS = ("config does not parse", "download not checked", "unfinished feature", "unused route")
FILE_LINES, FUNC_LINES, FUNC_CCN = 500, 80, 15


def tracked_files(root: Path) -> Dict[str, str]:
    from ..core.ingest import is_safe_repo_file
    out = {}
    names = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True).stdout.split("\n")  # nosec B603 B607 - fixed command, list arguments, no shell
    for n in names:
        if not n or SKIP.search(n):
            continue
        p = root / n
        if not is_safe_repo_file(root, p):
            continue
        try:
            if p.stat().st_size <= 1_500_000:
                out[n] = p.read_text(errors="ignore")
        except Exception:  # nosec B110 - deliberate best-effort fallback
            pass
    return out


def _functions(path: str, content: str) -> List[Tuple[str, int, int, int]]:
    if lizard is None:
        return []
    try:
        return [(f.name, f.start_line, f.end_line, f.cyclomatic_complexity) for f in lizard.analyze_file.analyze_source_code(path, content).function_list]
    except Exception:
        return []


def _enclosing(funcs, line: int) -> Optional[str]:
    inside = [f for f in funcs if f[1] <= line <= f[2]]
    big = [f for f in inside if f[2] - f[1] >= 7] or inside
    f = min(big, key=lambda f: f[2] - f[1]) if big else None
    return f[0] if f else None


def _possibly_unused(code: Dict[str, str], everything: Dict[str, str]) -> List[str]:
    out = []
    for p in code:
        if ENTRY.search(p) or TEST.search(p) or not p.endswith(CODE_EXT) or p.endswith("__init__.py"):
            continue
        stem = Path(p).stem
        if p.endswith((".kt", ".java", ".cs", ".swift", ".gd")):
            names = {stem} | set(re.findall(r"^(?:\s*(?:public|internal|private|data|sealed|abstract|open|enum|inline|value|annotation)\s+)*"
                                            r"(?:class|object|interface|fun|val|var|typealias)\s+(?:<[^>]*>\s*)?([A-Za-z_]\w+)", everything.get(p, ""), re.M))
            rx = r"\b(" + "|".join(re.escape(n) for n in names) + r")\b"
        elif p.endswith(".py"):
            rx = r"(\bimport\s+[\w., ]*\b|\bfrom\s+[\w.]*\b|[\"'/.])" + re.escape(stem) + r"\b"
        else:
            rx = r"[\"'/.]" + re.escape(stem) + r"([\"'./]|\b)"
        if not any(q != p and re.search(rx, c) for q, c in everything.items()):
            out.append(p)
    return out


def _braced(text: str, i: int) -> str:
    depth = 0
    for j in range(i, min(len(text), i + 4000)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j]
    return text[i + 1:i + 400]


# Intent-aware rules (scan(..., intent_aware=True), used by engine v10.4+; older engines keep the original rules):
# exceptions that are an expected, normal outcome rather than a failure to report.
EXPECTED_PY = {"BlockingIOError", "ProcessLookupError", "FileNotFoundError", "FileExistsError", "CancelledError",
               "WebSocketDisconnect", "StopIteration", "StopAsyncIteration", "KeyboardInterrupt", "BrokenPipeError",
               "ConnectionResetError", "InterruptedError", "ChildProcessError", "GeneratorExit"}
# Logging that reaches whoever runs the software (debug-level logging doesn't).
_VISIBLE_LOG_PY = re.compile(r"^(print|(logging|logger|log|self\.log(ger)?)\.(error|warning|warn|exception|critical|info))$")
_VISIBLE_LOG_JS = re.compile(r"^(console\.(error|warn|info)|Log\.[ewi]|Timber\.[ewi])\s*\(")
# Best-effort cleanup whose failure is fine to ignore in Rust/Go (closing, killing, removing, a send to a closed channel).
_CLEANUP = re.compile(r"\b(kill|start_kill|shutdown|close|Close|remove_file|remove_dir(_all)?|Remove(All)?|flush|abort|send|unlink|set_nonblocking)\s*\(")


def dependency_files(root: Path, everything: Dict[str, str]) -> set:
    """Files the author doesn't control, by the same rules ingest uses for line counts and complexity: dependency
    folders (vendor/, sdk/, lib/, external/...) bulk-imported and never edited (from git history), Arduino/PlatformIO
    library folders, known single-file libraries (ImGui, stb, sqlite3, nlohmann/json...), plus host SDK headers."""
    from ..core.ingest import _is_known_vendored_lib_file, get_untouched_vendor_files

    deps = set(get_untouched_vendor_files(Path(root), list(everything), strict=True))  # ingest's v10.4 rules
    for rel, content in everything.items():
        if _is_known_vendored_lib_file(rel, content, include_host_sdk=True):  # + Nexus.h, arcdps.h...
            deps.add(rel)
    return deps


def _py_types(node) -> set:
    t = node.type
    if t is None:
        return set()
    elts = t.elts if isinstance(t, ast.Tuple) else [t]
    return {getattr(e, "attr", None) or getattr(e, "id", None) for e in elts}


def _swallow_rust(c: str) -> List[Tuple[int, str]]:
    """(line, how) for Rust errors thrown away: `let _ = fallible();`, a bare `.ok();`, an empty `Err(_) => {}`.
    Cleanup calls and lines explained by a comment (on the line or the one above) are deliberate."""
    lines, out = c.splitlines(), []
    for i, line in enumerate(lines):
        code = line.split("//", 1)[0]
        explained = "//" in line or (i and lines[i - 1].strip().startswith("//"))
        if explained or _CLEANUP.search(code):
            continue
        if re.search(r"\blet\s+_\s*=\s*[^;]*\)\s*;", code) and "?" not in code:
            out.append((i + 1, "ignored"))
        elif re.match(r"\s*(?!let\b|return\b)[^=;]+\.ok\(\)\s*;\s*$", code):  # a statement that only discards
            out.append((i + 1, "ignored"))
        elif re.search(r"\bErr\(\s*_?\w*\s*\)\s*=>\s*(\{\s*\}|\(\))\s*,?", code):
            out.append((i + 1, "ignored"))
    return out


def _swallow_go(c: str) -> List[Tuple[int, str]]:
    """(line, how) for Go errors thrown away: `_ = err`, `_ = f()`, an empty `if err != nil {}`."""
    lines, out = c.splitlines(), []
    for i, line in enumerate(lines):
        code = line.split("//", 1)[0]
        explained = "//" in line or (i and lines[i - 1].strip().startswith("//"))
        if explained or _CLEANUP.search(code):
            continue
        if re.search(r"^\s*_\s*=\s*(err\b|[\w.]+\()", code) or re.search(r"\bif\s+err\s*!=\s*nil\s*\{\s*\}", code):
            out.append((i + 1, "ignored"))
    return out


def _swallow_js(body: str, intent: bool = False) -> Optional[str]:
    if intent and re.search(r"//|/\*", body):
        return None  # explained: the comment says why the error is dropped
    if not body.strip("; \n\t") or re.fullmatch(r"\s*(//[^\n]*\s*|/\*.*?\*/\s*)+", body, flags=re.S):
        return None if re.search(r"//|/\*", body) else "ignored"     # an explained empty catch is deliberate
    stmts = [s.strip() for s in re.split(r";|\n", re.sub(r"//[^\n]*|/\*.*?\*/", "", body, flags=re.S)) if s.strip()]
    kinds = set()
    for s in stmts:
        if intent and _VISIBLE_LOG_JS.match(s):
            return None  # reported where whoever runs it sees it
        if re.match(r"(console\.(log|error|warn|info|debug)|Log\.[dewiv]|Timber\.\w|println|print|\w+\.printStackTrace)\s*\(", s) or s in (")", "})"):
            kinds.add("only logged")
        elif re.match(r"return\s*(\[\s*\]|null|undefined|\{\s*\}|false|''|\"\"|NextResponse\.json\(\s*\[\s*\]\s*\)|Response\.json\(\s*\[\s*\]\s*\))?\s*$", s):
            kinds.add("replaced with an empty result")
        elif re.match(r"^[\"'`].*[\"'`]\s*,?\s*\w*\)?$", s) or re.match(r"^\w+\)?$", s):
            continue
        else:
            return None
    return " and ".join(sorted(kinds)) if kinds else None


def _swallow_py(body, intent: bool = False) -> Optional[str]:
    kinds = set()
    for st in body:
        if isinstance(st, (ast.Pass, ast.Continue, ast.Break)):
            kinds.add("ignored")
        elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
            name = ast.unparse(st.value.func)
            if intent and _VISIBLE_LOG_PY.match(name):
                return None  # reported where whoever runs it sees it
            if name == "print" or name.startswith(("logging.", "logger.", "log.")):
                kinds.add("only logged")
            else:
                return None
        elif isinstance(st, ast.Return) and (st.value is None or (isinstance(st.value, ast.Constant) and st.value.value in (None, False, "", 0))
                                             or (isinstance(st.value, (ast.List, ast.Dict, ast.Tuple)) and not (getattr(st.value, "elts", None) or getattr(st.value, "keys", None)))):
            kinds.add("replaced with an empty result")
        elif isinstance(st, ast.Assign) and ((isinstance(st.value, (ast.List, ast.Dict)) and not (getattr(st.value, "elts", None) or getattr(st.value, "keys", None)))
                                             or (isinstance(st.value, ast.Constant) and st.value.value is None)):
            kinds.add("replaced with an empty result")
        else:
            return None
    return " and ".join(sorted(kinds)) if kinds else None


def _unused_routes(code: Dict[str, str], everything: Dict[str, str]) -> List[Tuple[str, int, str]]:
    """Back-end API routes that no screen or script in the project ever calls (web projects only)."""
    routes = []
    for p, c in code.items():
        for m in re.finditer(r"@\w+\.(?:get|post|put|delete|patch|route)\(\s*['\"](/api/[^'\"]+)['\"]", c):
            routes.append((p, c.count("\n", 0, m.start()) + 1, m.group(1)))
        for m in re.finditer(r"\b(?:app|router)\.(?:get|post|put|delete|patch)\(\s*['\"](/api/[^'\"]+)['\"]", c):
            routes.append((p, c.count("\n", 0, m.start()) + 1, m.group(1)))
    if not routes:
        return []
    if len("\n".join(c for p, c in everything.items() if p.endswith(WEB_EXT) or "templates/" in p or "static/" in p)) < 200:
        return []                                   # an API without its own front end: other programs call it
    out, seen = [], set()
    for p, line, path in routes:
        if (p, path) in seen:
            continue
        seen.add((p, path))
        callers = "\n".join(c for q, c in everything.items() if q != p and not q.endswith((".md", ".txt", ".rst")))
        parts = [x for x in re.split(r"\{[^}]+\}|<[^>]+>|:\w+", path) if x.strip("/")]
        if parts and not all(x.rstrip("/") in callers for x in parts):
            out.append((p, line, path))
    return out


def _user_facing_functions(code: Dict[str, str]) -> set:
    """(file, function) pairs that answer a user directly: decorated web route handlers."""
    out = set()
    for p, c in code.items():
        if p.endswith(".py"):
            for m in re.finditer(r"@\w+\.(?:get|post|put|delete|patch|route)\([^\n]*\)\s*\n(?:\s*@[^\n]*\n)*\s*(?:async\s+)?def\s+(\w+)", c):
                out.add((p, m.group(1)))
    return out


def scan(root: Path, intent_aware: bool = False) -> Dict[str, List[Dict[str, object]]]:
    everything = tracked_files(Path(root))
    # v10.4+ (intent_aware): only the author's own code is judged; dependencies they can't change never produce findings.
    deps = dependency_files(root, everything) if intent_aware else set()
    code = {p: c for p, c in everything.items() if p.endswith(CODE_EXT) and not TEST.search(p) and p not in deps}
    funcs = {p: _functions(p, c) for p, c in code.items()}
    unused = set(_possibly_unused(code, everything))
    findings: List[Dict[str, object]] = []

    def add(kind, file, line, detail, snippet=""):
        findings.append({"kind": kind, "file": file, "line": line, "function": _enclosing(funcs.get(file, []), line or 0) if line else None,
                         "detail": detail, "code": (snippet or "").strip()[:160], "in_unused_file": file in unused})

    for f in sorted(unused):
        add("unused file", f, None, "no other file in the project refers to this file, so it is probably never used")
    for p, c in code.items():
        lines = c.splitlines()
        if p.endswith(".py"):
            try:
                tree = ast.parse(c)
            except SyntaxError:
                tree = None
            if tree:
                in_loop = set()
                for loop in ast.walk(tree):
                    if isinstance(loop, (ast.For, ast.While, ast.AsyncFor)):
                        in_loop |= {id(n) for n in ast.walk(loop) if isinstance(n, ast.ExceptHandler)}
                for node in ast.walk(tree):
                    if isinstance(node, ast.ExceptHandler) and id(node) in in_loop:
                        continue
                    if isinstance(node, ast.ExceptHandler):
                        names = {getattr(node.type, "id", None)} | {getattr(e, "id", None) for e in getattr(node.type, "elts", [])}
                        if names & {"ImportError", "ModuleNotFoundError", "KeyboardInterrupt"}:
                            continue
                        if intent_aware:
                            types = _py_types(node)
                            if types and types <= EXPECTED_PY:
                                continue  # an expected outcome, not a failure
                            end = getattr(node, "end_lineno", node.lineno)
                            if any("#" in ln for ln in lines[node.lineno - 1:end]):
                                continue  # explained: the comment says why
                        how = _swallow_py(node.body, intent_aware)
                        if how:
                            add("swallowed error", p, node.lineno, f"an error is caught and {how}; the user is never told", lines[node.lineno - 1])
        elif intent_aware and p.endswith((".rs", ".go")):
            for ln, how in (_swallow_rust(c) if p.endswith(".rs") else _swallow_go(c)):
                add("swallowed error", p, ln, f"an error is caught and {how}; the user is never told", lines[ln - 1])
        elif p.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".kt", ".java")):
            for m in re.finditer(r"catch\s*(\([^)]*\))?\s*\{", c):
                how = _swallow_js(_braced(c, m.end() - 1), intent_aware)
                if how:
                    ln = c.count("\n", 0, m.start()) + 1
                    add("swallowed error", p, ln, f"an error is caught and {how}; the user is never told", lines[ln - 1])
            for m in re.finditer(r"const\s*\[\s*(\w+)\s*,\s*(\w+)\s*\]\s*=\s*useState", c):
                val, setter = m.group(1), m.group(2)
                ln = c.count("\n", 0, m.start()) + 1
                if len(re.findall(r"\b" + val + r"\b", c)) == 1:
                    add("unfinished feature", p, ln, f"state `{val}` is created but never used", lines[ln - 1])
                elif len(re.findall(r"\b" + setter + r"\b", c)) == 1:
                    add("unfinished feature", p, ln, f"state `{val}` is never changed (`{setter}` is never called)", lines[ln - 1])
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if s.startswith(("#", "//", "*")):
                continue
            for rx, what in RISKY:
                if rx.search(line):
                    add("risky call", p, i, what, line)
        if p.endswith((".sh", ".ps1")) or p.endswith("Makefile"):
            for i, line in enumerate(lines, 1):
                if re.search(r"\bcurl\b", line) and re.search(r"\s-o\s|--output\b|\s-[a-zA-Z]*o\b", line) and not re.search(r"\s-[a-zA-Z]*f[a-zA-Z]*\b|--fail\b", line):
                    add("download not checked", p, i, "a download is saved without checking it worked, so an error page can be saved as the file", line)
        n = sum(1 for l in lines if l.strip())
        if n > FILE_LINES:
            add("oversized file", p, None, f"{n} lines of code in one file")
        for name, start, end, ccn in funcs.get(p, []):
            span = end - start + 1
            if name != "(anonymous)" and (span > 150 or ccn > FUNC_CCN or (span > FUNC_LINES and ccn > 8)):
                add("oversized function", p, start, f"`{name}` is {span} lines with {ccn} decision points")
    for path, content in everything.items():
        base = re.sub(r"\.(example|sample|dist)$", "", path)
        try:
            if base.endswith(".toml"):
                tomllib.loads(content)
            elif base.endswith(".json") and not JSONC.search(base) and not path.endswith("package-lock.json"):
                json.loads(content)
            else:
                continue
        except Exception as e:
            ln = getattr(e, "lineno", None)
            if ln is None:
                m = re.search(r"line (\d+)", str(e))
                ln = int(m.group(1)) if m else None
            add("config does not parse", path, ln, f"this config file is not valid ({str(e)[:80]})")
    for p, line, path in _unused_routes(code, everything):
        add("unused route", p, line, f"the endpoint {path} exists, but no screen or script in the project calls it")
    seen, dups = {}, []
    _DUP_SKIP_EXT = (".html", ".htm", ".css", ".scss", ".sass", ".svg")
    for p, c in code.items():
        if p.lower().endswith(_DUP_SKIP_EXT):
            continue
        ls = c.splitlines()
        for i in range(len(ls) - 9):
            win = [re.sub(r"\s+", " ", l).strip() for l in ls[i:i + 10]]
            if sum(1 for w in win if len(w) > 12 and not w.startswith(("import ", "from ", "//", "#"))) < 8:
                continue
            h = hashlib.md5("\n".join(win).encode(), usedforsecurity=False).hexdigest()   # a fingerprint, not security
            if h in seen and seen[h][0] != p or (h in seen and abs(seen[h][1] - i) > 10):
                p0, i0 = seen[h]
                if not dups or not (dups[-1][0] == p0 and dups[-1][2] == p and abs(dups[-1][3] - (i + 1)) <= 16):
                    dups.append([p0, i0 + 1, p, i + 1])
            else:
                seen.setdefault(h, (p, i))
    for p0, l0, p1, l1 in [d for d in dups if d[0] not in unused and d[2] not in unused][:12]:
        add("duplicated code", p1, l1, f"the same 10-line block of code also appears in {p0} at line {l0}")
    live = [f for f in findings if not f["in_unused_file"] or f["kind"] == "unused file"]
    handlers = _user_facing_functions(code)

    def counts_for_functionality(f) -> bool:
        if f["kind"] not in FUNCTIONALITY_KINDS:
            return False
        if f["kind"] == "download not checked":
            # only scripts the user runs (install / start / setup); release-building scripts are maintainer notes
            return bool(re.search(r"(^|/)(install|setup|start|run|launch|get)[\w-]*\.(sh|ps1|bat|cmd)$", f["file"], re.I))
        if f["kind"] != "swallowed error":
            return True
        # only where a user is waiting: a web route handler, or front-end code reacting to the user
        return f["file"].endswith(WEB_EXT) or (f["file"], f["function"]) in handlers
    return {"functionality": [f for f in live if counts_for_functionality(f)],
            "maintainer": [f for f in live if not counts_for_functionality(f)],
            "lines_of_code": sum(sum(1 for l in c.splitlines() if l.strip()) for c in code.values()),
            "option_docs": _analyze_surface_option_docs(code, everything)}


def _analyze_surface_option_docs(code: Dict[str, str], everything: Dict[str, str]) -> Dict[str, Any]:
    """
    Detects whether the repo exposes CLI, GUI, or both surfaces and counts how many
    user-facing options/flags/controls have inline explanations or docs.
    """
    cli_total = 0
    cli_doc = 0
    gui_total = 0
    gui_doc = 0
    has_cli = False
    has_gui = False

    cli_arg_re = re.compile(r"(?:add_argument|click\.option|typer\.Option|Option\()\s*\(([^)]{0,320})\)")
    gui_html_re = re.compile(r"<(?:input|select|textarea)\b([^>]{0,280})>", re.I)
    qt_widget_re = re.compile(r"<widget\b[^>]*class=\"Q(?:CheckBox|ComboBox|SpinBox|DoubleSpinBox|Slider|LineEdit|PushButton)\"[^>]*>(.*?)</widget>", re.S)

    for p, content in everything.items():
        pl = p.lower()
        if pl.endswith((".py", ".js", ".ts", ".rs", ".go", ".c", ".cpp", ".h", ".hpp")):
            if any(k in content for k in ("argparse", "click.option", "typer.Option", "getopt(", "QCommandLineOption")):
                has_cli = True
            for m in cli_arg_re.finditer(content):
                cli_total += 1
                if "help=" in m.group(1) or "description" in m.group(1).lower():
                    cli_doc += 1
            if any(k in content for k in ("QMainWindow", "QDialog", "tkinter", "ttk.", "pygame.", "setCentralWidget")):
                has_gui = True
            for m in re.finditer(r"set(?:ToolTip|WhatsThis|AccessibleDescription)\s*\(", content):
                gui_doc += 1

        if pl.endswith((".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte")):
            has_gui = True
            for m in gui_html_re.finditer(content):
                attrs = m.group(1).lower()
                if 'type="hidden"' in attrs or "type='hidden'" in attrs:
                    continue
                gui_total += 1
                if any(k in attrs for k in ("title=", "aria-label=", "placeholder=")):
                    gui_doc += 1
            gui_doc += len(re.findall(r"<label\b", content, re.I))

        if pl.endswith(".ui"):
            has_gui = True
            for m in qt_widget_re.finditer(content):
                gui_total += 1
                if "tooltip" in m.group(1).lower() or "whatsthis" in m.group(1).lower():
                    gui_doc += 1

    gui_doc = min(gui_doc, gui_total) if gui_total else gui_doc
    has_docs_dir = any(
        p.lower().startswith("docs/") and p.lower().endswith((".md", ".rst", ".txt"))
        for p in everything
    )
    readme_len = max((len(v) for k, v in everything.items() if Path(k).name.lower().startswith("readme")), default=0)

    surfaces = "BOTH (GUI + CLI)" if (has_cli and has_gui) else ("GUI" if has_gui else ("CLI" if has_cli else "LIBRARY_OR_OTHER"))
    parts = [f"Surfaces: {surfaces}"]
    if has_cli:
        parts.append(f"CLI options with inline help: {cli_doc}/{cli_total}")
    if has_gui:
        parts.append(f"GUI controls with inline tooltip/label: {gui_doc}/{gui_total}")
    parts.append(f"docs/ folder: {'yes' if has_docs_dir else 'no'}, README chars: {readme_len}")

    return {
        "surfaces": surfaces,
        "has_cli": has_cli,
        "has_gui": has_gui,
        "cli_options_total": cli_total,
        "cli_options_documented": cli_doc,
        "gui_options_total": gui_total,
        "gui_options_documented": gui_doc,
        "has_docs_dir": has_docs_dir,
        "summary": " | ".join(parts),
    }
