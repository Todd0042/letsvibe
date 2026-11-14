"""
Vibe-coded check: is this project vibe-coded (written by AI from someone's prompts)? Runs before grading.

Three verdicts:
  reject   - the project's history starts before November 2022 (before vibe coding existed), or it is a fork:
             most commits are by someone else while the owner made a minority of them. Not graded.
  vibe     - strong evidence that AI wrote it: AI tool files (CLAUDE.md, AGENTS.md, .cursor/rules, ...), AI co-author
             lines, AI app-builder bot authors or "Generated with ..." lines anywhere in the history, an explicit statement
             in the README, or code whose style is clearly AI-written. Graded and published.
  unclear  - anything else. Graded, but held for the admin's approval before it is published (like the integrity
             hold). Copy-and-paste vibe coding from a chat window leaves no tool files or co-author lines and is
             cleaned up before it is committed, so it often lands here; the admin approves it in one click.

Weak signals (install/build scripts, README badges, conventional commit messages) are reported but never decide a
verdict: human projects have them too. Code-style patterns are only read from code files, never docs, so code
examples in a README can never pass a project. The history must be complete for the November 2022 rule: the
ingester clones every commit (without old file contents).
"""

import ast
import re
import statistics
import subprocess  # nosec B404 - git/nvidia-smi run with fixed list arguments, never a shell
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

VIBE_ERA_START = datetime(2022, 11, 1, tzinfo=timezone.utc)

# Files and folders AI coding tools create (exact names; a folder matches anything under it)
VIBE_TOOL_ARTIFACTS = {
    "CLAUDE.md": "Claude Code project instructions", ".claude": "Claude Code settings folder",
    "AGENTS.md": "AGENTS.md coding-agent instructions", "AGENT.md": "coding-agent instructions",
    "GEMINI.md": "Gemini CLI / Antigravity instructions", ".gemini": "Gemini settings folder",
    ".cursorrules": "Cursor rules", ".cursor": "Cursor settings folder",
    ".windsurfrules": "Windsurf rules", ".windsurf": "Windsurf settings folder", ".codeium": "Codeium folder",
    ".clinerules": "Cline rules", ".roomodes": "Roo Code modes", ".roo": "Roo Code folder",
    ".aider.conf.yml": "Aider configuration", ".aider.chat.history.md": "Aider chat transcript",
    ".aider.input.history": "Aider input history",
    ".github/copilot-instructions.md": "GitHub Copilot instructions", ".github/instructions": "GitHub Copilot instructions folder",
    "devin.json": "Devin configuration", ".vibe": "LetsVibe vibe-coded marker",
}
# AI tools named in commit trailers and statements. Names that are also people's names (Devin, Jules, Bolt) count only
# as bot accounts (AI_BOT_AUTHOR), never as co-authors.
_AI_NAMES = (r"\b(claude|anthropic|copilot|cursor|gemini|antigravity|codex|openai|chatgpt|gpt-?\d(\.\d)?|aider|windsurf|codeium|"
             r"cline|roo code|lovable|replit agent|deepseek|qwen|grok|kiro|amazon q|tabnine)\b")
AI_TRAILER = re.compile(rf"co-authored-by:[^\n]*{_AI_NAMES}|generated with \[?{_AI_NAMES}|claude-session:|🤖 generated", re.I)
AI_STATEMENT = re.compile(rf"\b(generated|written|created|scaffolded|built|made|coded)\s+(entirely\s+|mostly\s+|fully\s+)?(by|with|using)\s+"
                          rf"(an?\s+)?(ai\b|{_AI_NAMES})|\bvibe[- ]?cod(ed|ing)\b|\bai[- ](generated|written)\b", re.I)
AI_BOT_AUTHOR = re.compile(r"(claude|copilot|cursor|gemini|codex|openai|devin|jules|lovable|gpt-engineer|bolt|v0|replit|"
                           r"windsurf|kiro|amazon-q)[\w.-]*\[bot\]", re.I)
BOT_AUTHOR = re.compile(rf"{_AI_NAMES}[\w.-]*\[bot\]|gpt-engineer-app\[bot\]", re.I)
BOT_AUTHOR = re.compile(r"\[bot\]|^dependabot|^renovate|^github-actions|noreply@github\.com$", re.I)

CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".kt", ".java", ".go", ".rs", ".cpp", ".cc", ".c", ".h", ".hpp",
            ".cs", ".lua", ".gd", ".swift", ".dart", ".rb", ".php", ".vue", ".svelte")
_SKIP = re.compile(r"(^|/)(tests?|docs?|examples?|vendor|third_party|external|node_modules|dist|build|site-packages)(/|$)|"
                   r"(^|/)test_|\.min\.", re.I)
_EMOJI = re.compile(r"[✅❌⚠✨⭐✔✖\U0001F300-\U0001FAFF]")
_C = r"(#|//|/\*|\*|--)"
_STEP = re.compile(rf"^\s*{_C}\s*(step\s*)?\d+[.:)]\s+[A-Za-z]", re.I)
_CHAT_LEFTOVER = re.compile(rf"{_C}.*(\.\.\. ?(rest|existing|other|same) (of )?(the |your )?(code|file|functions?|logic)|"
                            r"\b(NEW|ADDED|CHANGED|UPDATED):|\badd this\b|<-+ ?(add|new|change)|keep (the )?existing)", re.I)
_HEDGE = re.compile(rf"{_C}.*\b(in a real (app|application|project|world)|in production,? you|for simplicity|replace with your|"
                    rf"adjust (as|if) needed)\b", re.I)
_ERR_PRINT = re.compile(r"(print|console\.(log|error)|log\w*\.\w+)\(\s*f?[\"'`][^\"'`\n]*(error|failed|exception)[^\"'`\n]*[{$]", re.I)

# AI-named commits confirm vibe coding only when they are a real share of the history: a few Copilot co-authored commits
# in a human project make it AI-assisted, not vibe-coded.
AI_COMMIT_SHARE_STRONG = 0.30

# Code-style evidence: how many of these hold. Thresholds come from comparing the owner's vibe-coded Python projects
# with human-written libraries (Click, Requests, tqdm, Rich); see tests/test_vibe_detector.py.
STYLE_STRONG_AT = 3


@dataclass
class VibeCheckResult:
    verdict: str                      # "vibe", "unclear" or "reject"
    confidence: float
    reasons: List[str]
    signals: List[str] = field(default_factory=list)          # strong evidence
    supporting: List[str] = field(default_factory=list)       # weak signals, never decisive
    is_legacy_pre_2022: bool = False
    is_fork: bool = False
    first_commit: Optional[str] = None
    style: Dict[str, float] = field(default_factory=dict)

    @property
    def is_vibe(self) -> bool:
        """Confirmed vibe-coded (published normally)."""
        return self.verdict == "vibe"

    @property
    def needs_review(self) -> bool:
        """Graded, but held for the admin before publishing."""
        return self.verdict == "unclear"

    @property
    def rejected(self) -> bool:
        return self.verdict == "reject"


@dataclass
class Commit:
    name: str
    email: str
    date: Optional[datetime]
    message: str


# ---- git history ----------------------------------------------------------------------------------

def read_history(repo: Path, limit: int = 20000) -> Tuple[List[Commit], bool]:
    """Every commit (author, email, author date, full message) and whether the clone is shallow (history incomplete)."""
    if not (Path(repo) / ".git").exists():
        return [], True
    try:
        shallow = subprocess.run(["git", "-C", str(repo), "rev-parse", "--is-shallow-repository"],  # nosec B603 B607 - fixed command, list arguments, no shell
                                 capture_output=True, text=True, timeout=10).stdout.strip() == "true"
        out = subprocess.run(["git", "-C", str(repo), "log", f"-n{limit}", "--format=%an%x1f%ae%x1f%aI%x1f%B%x1e"],  # nosec B603 B607 - fixed command, list arguments, no shell
                             capture_output=True, text=True, timeout=60).stdout
    except Exception:
        return [], True
    commits = []
    for rec in out.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) < 4:
            continue
        try:
            date = datetime.fromisoformat(parts[2].strip())
        except ValueError:
            date = None
        commits.append(Commit(parts[0].strip(), parts[1].strip().lower(), date, parts[3]))
    return commits, shallow


def github_fork_parent(full_slug: str) -> Optional[str]:
    """'owner/repo' of the project this one was forked from on GitHub, or None (not a fork, private, or unreachable).
    Public API; a GITHUB_TOKEN in the environment is used when present (higher rate limit, private repos)."""
    import os

    import httpx
    if not full_slug or full_slug.count("/") != 1:
        return None
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "LetsVibe-vibe-check"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        res = httpx.get(f"https://api.github.com/repos/{full_slug}", headers=headers, timeout=10.0)
        if res.status_code != 200:
            return None
        data = res.json()
        if data.get("fork"):
            return (data.get("parent") or {}).get("full_name") or "another repository"
    except Exception:
        return None
    return None


def _is_owner(c: Commit, owner: str) -> bool:
    o = owner.lower()
    local = c.email.split("@")[0]
    return bool(o) and (c.name.lower() == o or local == o or local.endswith("+" + o))


def authorship(commits: List[Commit], owner: str) -> Tuple[Optional[str], float, float, int]:
    """(top other author, their share, the owner's share, human commits). Bots are left out."""
    human = [c for c in commits if not BOT_AUTHOR.search(c.name) and not BOT_AUTHOR.search(c.email)]
    if not human:
        return None, 0.0, 0.0, 0
    owner_n = sum(1 for c in human if _is_owner(c, owner))
    others = Counter(c.email or c.name.lower() for c in human if not _is_owner(c, owner))
    names = {c.email or c.name.lower(): c.name for c in human}
    top, n = (others.most_common(1)[0] if others else (None, 0))
    return (names.get(top) if top else None), n / len(human), owner_n / len(human), len(human)


# ---- code style -----------------------------------------------------------------------------------

def code_style(files: Dict[str, str]) -> Dict[str, float]:
    """Style measurements over the project's own code files (never docs, tests or vendored code)."""
    code = {p: c for p, c in files.items() if p.lower().endswith(CODE_EXT) and not _SKIP.search(p)}
    lines = [l for c in code.values() for l in c.splitlines()]
    n = max(1, len(lines))
    per_k = lambda k: 1000.0 * k / n
    stats = {
        "code_lines": float(len(lines)),
        "emoji_per_1k": per_k(sum(1 for l in lines if _EMOJI.search(l) and re.search(r"[\"'`]", l))),
        "step_comments_per_1k": per_k(sum(1 for l in lines if _STEP.match(l))),
        "chat_leftovers": float(sum(1 for l in lines if _CHAT_LEFTOVER.search(l))),
        "hedge_comments": float(sum(1 for l in lines if _HEDGE.search(l))),
        "error_prints_per_1k": per_k(sum(1 for l in lines if _ERR_PRINT.search(l))),
    }
    # Python: parsed, not pattern-matched
    names, handlers, broad = [], 0, 0
    for p, c in code.items():
        if not p.endswith(".py"):
            continue
        try:
            tree = ast.parse(c)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("__"):
                names.append(len(node.name.lstrip("_")))
            elif isinstance(node, ast.ExceptHandler):
                handlers += 1
                broad += node.type is None or (isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException"))
    stats["py_functions"] = float(len(names))
    stats["py_avg_function_name_len"] = statistics.mean(names) if names else 0.0
    stats["py_except_handlers"] = float(handlers)
    stats["py_broad_except_share"] = broad / handlers if handlers else 0.0
    return stats


def style_evidence(s: Dict[str, float]) -> List[str]:
    """The AI-style habits present, in plain English. STYLE_STRONG_AT or more counts as strong evidence."""
    out = []
    if s.get("code_lines", 0) < 200:
        return out                      # too little code to judge style
    if s.get("py_functions", 0) >= 15 and s.get("py_avg_function_name_len", 0) >= 13.0:
        out.append(f"long, spelled-out function names (average {s['py_avg_function_name_len']:.1f} characters)")
    if s.get("py_except_handlers", 0) >= 5 and s.get("py_broad_except_share", 0) >= 0.5:
        out.append(f"{s['py_broad_except_share']:.0%} of error handlers catch every exception")
    if s.get("error_prints_per_1k", 0) >= 0.3:
        out.append("generic error printouts such as print(f\"Error: {e}\")")
    if s.get("emoji_per_1k", 0) >= 2.0:
        out.append("emoji in program text and log messages")
    if s.get("step_comments_per_1k", 0) >= 1.0:
        out.append("numbered step-by-step comments")
    if s.get("chat_leftovers", 0) >= 2:
        out.append("leftovers from pasting chat answers (\"... rest of the code\", \"NEW:\" markers)")
    if s.get("hedge_comments", 0) >= 2:
        out.append("tutorial-style comments (\"in a real app...\", \"for simplicity\")")
    return out


# ---- the check ------------------------------------------------------------------------------------

class VibeDetector:
    """Decides whether a repository is vibe-coded (see the module docstring)."""

    @classmethod
    def evaluate(cls, source_path: Path, files: Optional[Dict[str, str]] = None, git_log: Optional[str] = None,
                 owner: str = "", commits: Optional[List[Commit]] = None, full_slug: str = "",
                 fork_lookup=github_fork_parent) -> VibeCheckResult:
        """`files`: {path: contents} of tracked files. `owner`: the GitHub account that submitted the repository;
        `full_slug`: 'owner/repo' on GitHub (fork check). `git_log`/`commits`/`fork_lookup` replace git and GitHub (tests)."""
        root = Path(source_path).resolve()
        paths: Set[str] = {p.replace("\\", "/") for p in (files or {})}
        if not paths and root.exists() and git_log is None and commits is None:
            paths = {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}
        files = files or {}
        shallow = False
        if commits is None:
            if git_log is not None:
                commits = [Commit("", "", None, git_log)]
            else:
                commits, shallow = read_history(root)
        signals: List[str] = []
        supporting: List[str] = []

        # 1. Hard rules
        dates = [c.date for c in commits if c.date]
        first = min(dates) if dates else None
        if first and not shallow and first < VIBE_ERA_START:
            why = f"This project's history starts on {first.date().isoformat()}, before vibe coding existed (November 2022)."
            return VibeCheckResult("reject", 0.0, [why], is_legacy_pre_2022=True, first_commit=first.date().isoformat())
        parent = fork_lookup(full_slug) if (full_slug and fork_lookup) else None
        if parent:
            why = f"GitHub lists this repository as a fork of {parent}: forks of someone else's project are not graded."
            return VibeCheckResult("reject", 0.0, [why], is_fork=True, first_commit=first.date().isoformat() if first else None)
        top_other, other_share, owner_share, human_n = authorship(commits, owner)
        if owner and human_n >= 3 and top_other and other_share >= 0.5 and owner_share > 0 and owner_share < 0.5:
            why = (f"Most of this project's commits ({other_share:.0%}) are by {top_other}, and {owner_share:.0%} by {owner}: "
                   "it looks like a fork of someone else's project.")
            return VibeCheckResult("reject", 0.0, [why], is_fork=True, first_commit=first.date().isoformat() if first else None)

        # 2. Strong evidence
        for artifact, desc in VIBE_TOOL_ARTIFACTS.items():
            hit = next((p for p in sorted(paths) if p == artifact or p.startswith(artifact + "/")), None)
            if hit:
                signals.append(f"AI tool file: {desc} ({hit})")
        history = "\n".join(c.message for c in commits)
        ai_commits = [c for c in commits if AI_TRAILER.search(c.message) or AI_BOT_AUTHOR.search(c.name) or AI_BOT_AUTHOR.search(c.email)]
        if ai_commits:
            note = f"{len(ai_commits)} of {len(commits)} commits name an AI tool as author or co-author"
            (signals if len(ai_commits) / max(1, len(commits)) >= AI_COMMIT_SHARE_STRONG else supporting).append(note)
        m = AI_STATEMENT.search(history)
        if m:
            signals.append(f"Commit history says so: \"{m.group(0)}\"")
        readme = next((files[p] for p in sorted(files) if p.lower() in ("readme.md", "readme.txt", "readme.rst", "readme")), "")
        m = AI_STATEMENT.search(readme)
        if m:
            signals.append(f"README says so: \"{m.group(0)}\"")
        style = code_style(files)
        habits = style_evidence(style)
        if len(habits) >= STYLE_STRONG_AT:
            signals.append("AI-style code: " + "; ".join(habits))
        elif habits:
            supporting.append("Some AI-style code habits: " + "; ".join(habits))

        # 3. Weak signals: reported, never decisive
        if any(p.rsplit("/", 1)[-1] in ("build.sh", "install.sh", "start.sh", "run.sh", "build_windows.ps1", "AppRun") for p in paths):
            supporting.append("install/build/launch scripts")
        if "img.shields.io" in readme:
            supporting.append("README badges")
        subjects = [c.message.strip().splitlines()[0] for c in commits if c.message.strip()]
        if len(subjects) >= 4 and sum(bool(re.match(r"(feat|fix|chore|docs|refactor|test|style)(\(.+\))?!?:", s)) for s in subjects) / len(subjects) >= 0.6:
            supporting.append("conventional commit messages (feat:, fix:)")
        if owner and human_n >= 3 and top_other and other_share >= 0.5 and owner_share == 0:
            supporting.append(f"most commits are by {top_other}, whose name or email does not match the owner's GitHub account")
        if shallow:
            supporting.append("the full history was not available, so the November 2022 rule could not be checked")

        first_s = first.date().isoformat() if first else None
        if signals:
            conf = min(1.0, 0.6 + 0.2 * len(signals))
            return VibeCheckResult("vibe", conf, ["Vibe-coded: " + signals[0]] + signals[1:] + supporting, signals, supporting,
                                   first_commit=first_s, style=style)
        reasons = ["LetsVibe could not confirm from the code and its history that this project was vibe-coded. It is graded, "
                   "but held for review before it is published."] + (supporting or ["No AI tool files, AI co-authors or AI-style code were found."])
        return VibeCheckResult("unclear", 0.3 if supporting else 0.1, reasons, signals, supporting, first_commit=first_s, style=style)
