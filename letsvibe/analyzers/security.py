"""
Deterministic security analyzer detecting hardcoded secrets and high-risk code patterns.
"""

import re
from typing import Dict, List, Tuple


SECRET_PATTERNS = [
    (r"(?i)aws_?(?:access|secret|token)?_?(?:key)?\s*[:=]\s*['\"][A-Za-z0-9/+=]{20,40}['\"]", "AWS Credential / Secret Key"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"(?i)gh[pousr]_[A-Za-z0-9_]{36,255}", "GitHub Personal Access Token"),
    (r"(?i)xox[baprs]-[0-9A-Za-z-]{10,72}", "Slack Token"),
    (r"sk-[A-Za-z0-9]{20,48}", "OpenAI / LLM API Key"),
    (r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "Hardcoded Private Key Block"),
    (r"(?i)password\s*[:=]\s*['\"][^'\"]{8,64}['\"]", "Hardcoded Password Assignment"),
    (r"(?i)api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,64}['\"]", "Generic API Key Assignment"),
]

# Risky code patterns: not secrets, and only counted when they appear in actual code (string literals and
# comments are stripped first, so a project that merely mentions eval() -- in docs, rules or a detector like
# this one -- is not flagged). The SQL pattern needs the f-string itself, so it is checked on the raw text.
VULNERABLE_PATTERNS = [
    (r"(?<!\.)\beval\s*\(", "Use of eval() function", True),
    (r"(?<!\.)\bexec\s*\(", "Use of exec() function", True),
    (r"(?i)cursor\.execute\s*\(\s*f['\"].*?\{", "Potential SQL Injection (f-string in cursor.execute)", False),
    (r"(?i)subprocess\.(?:call|Popen|run)\s*\([^)]*shell\s*=\s*True", "subprocess with shell=True", True),
    (r"dangerouslySetInnerHTML", "React dangerouslySetInnerHTML usage", True),
]

_C_LIKE = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".kts", ".c", ".h", ".cc", ".cpp", ".hpp",
           ".cs", ".go", ".rs", ".swift", ".dart", ".scala", ".php")
_C_LIKE_TOKENS = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`|/\*.*?\*/|//[^\n]*', re.S)


def code_only(rel_path: str, content: str) -> str:
    """The file's code with string literals and comments blanked out (line breaks kept)."""
    blank = lambda m: re.sub(r"[^\n]", " ", m.group(0))  # noqa: E731
    if rel_path.endswith(".py"):
        import io
        import tokenize

        try:
            lines = content.splitlines(keepends=True)
            out = list(lines)
            for tok in tokenize.generate_tokens(io.StringIO(content).readline):
                if tok.type in (tokenize.STRING, tokenize.COMMENT) or tok.type == getattr(tokenize, "FSTRING_MIDDLE", -1):
                    (r1, c1), (r2, c2) = tok.start, tok.end
                    for r in range(r1, r2 + 1):
                        line = out[r - 1]
                        a = c1 if r == r1 else 0
                        b = c2 if r == r2 else len(line.rstrip("\n"))
                        out[r - 1] = line[:a] + " " * (b - a) + line[b:]
            return "".join(out)
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return _C_LIKE_TOKENS.sub(blank, re.sub(r"#[^\n]*", "", content))
    if rel_path.endswith(_C_LIKE):
        return _C_LIKE_TOKENS.sub(blank, content)
    return content


def _skipped(rel_path: str) -> bool:
    # Test files and documentation are skipped to reduce false positives
    return "test" in rel_path.lower() or "mock" in rel_path.lower() or rel_path.endswith((".md", ".txt", ".rst"))


def _summarize(counts: Dict[str, int]) -> Tuple[int, List[str]]:
    findings = [f"[{desc}] x{n} (location withheld from public reports)" for desc, n in counts.items()]
    return sum(counts.values()), findings


class SecurityAnalyzer:
    """Detects hardcoded secrets and, separately, risky code patterns across repository files."""

    @classmethod
    def scan(cls, files: Dict[str, str]) -> Tuple[int, List[str]]:
        """
        Hardcoded secrets only: (total findings, one summary line per type).

        Findings end up in PUBLIC reports, so they name only the type and how many times it was
        seen -- never the file, line or any characters of the secret. Publishing "where the leaked
        key is" would hurt the very people being graded; owners can locate them with any scanner.
        """
        counts: Dict[str, int] = {}
        for rel_path, content in files.items():
            if _skipped(rel_path):
                continue
            for pattern, desc in SECRET_PATTERNS:
                n = sum(1 for _ in re.finditer(pattern, content))
                if n:
                    counts[desc] = counts.get(desc, 0) + n
        return _summarize(counts)

    @classmethod
    def scan_risky(cls, files: Dict[str, str]) -> Tuple[int, List[str]]:
        """Risky code patterns (eval, exec, shell=True, ...) found in actual code; same public-report rules."""
        counts: Dict[str, int] = {}
        for rel_path, content in files.items():
            if _skipped(rel_path):
                continue
            code = None
            for pattern, desc, in_code_only in VULNERABLE_PATTERNS:
                if in_code_only and code is None:
                    code = code_only(rel_path, content)
                n = sum(1 for _ in re.finditer(pattern, code if in_code_only else content))
                if n:
                    counts[desc] = counts.get(desc, 0) + n
        return _summarize(counts)
