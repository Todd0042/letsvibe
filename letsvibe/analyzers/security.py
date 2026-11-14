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
    (r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE" + r" KEY-----", "Hardcoded Private Key Block"),   # split so scanners don't read the rule as a key
    (r"(?i)password\s*[:=]\s*['\"][^'\"]{8,64}['\"]", "Hardcoded Password Assignment"),
    (r"(?i)api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,64}['\"]", "Generic API Key Assignment"),
]

VULNERABLE_PATTERNS = [
    (r"(?<!\.)\beval\s*\(", "Use of eval() function"),
    (r"(?<!\.)\bexec\s*\(", "Use of exec() function"),
    (r"(?i)cursor\.execute\s*\(\s*f['\"].*?\{", "Potential SQL Injection (f-string in cursor.execute)"),
    (r"(?i)subprocess\.(?:call|Popen|run)\s*\([^)]*shell\s*=\s*True", "subprocess with shell=True"),
    (r"(?i)dangerouslySetInnerHTML", "React dangerouslySetInnerHTML usage"),
]


class SecurityAnalyzer:
    """Detects secrets, API keys, and unsafe security anti-patterns across repository files."""

    @classmethod
    def scan(cls, files: Dict[str, str]) -> Tuple[int, List[str]]:
        """
        Returns (total findings, one summary line per finding type).

        Findings end up in PUBLIC reports, so they name only the type and how many times it was
        seen -- never the file, line or any characters of the secret. Publishing "where the leaked
        key is" would hurt the very people being graded; owners can locate them with any scanner.
        """
        counts: Dict[str, int] = {}

        for rel_path, content in files.items():
            # Skip test files and documentation for secret scans to reduce false positives
            if "test" in rel_path.lower() or "mock" in rel_path.lower() or rel_path.endswith((".md", ".txt", ".rst")):
                continue
            for pattern, desc in list(SECRET_PATTERNS) + list(VULNERABLE_PATTERNS):
                n = sum(1 for _ in re.finditer(pattern, content))
                if n:
                    counts[desc] = counts.get(desc, 0) + n

        total = sum(counts.values())
        findings = [f"[{desc}] x{n} (location withheld from public reports)" for desc, n in counts.items()]
        return total, findings
