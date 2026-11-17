"""
Plain-Python scan for clearly malicious code (used by experience grading, where no model reads the code).
Only unambiguous patterns: reverse shells, crypto miners, running base64-decoded code, stealing browser or chat-app
credentials, and piping a download from an unknown host straight into a shell. A hit holds the report for the owner to
explain (the same flow as before); it is never published as an accusation.
"""
import re
from typing import Dict, List

PATTERNS = [
    ("reverse shell", re.compile(r"/dev/tcp/\d|bash\s+-i\s*>&|nc\s+(-\w+\s+)*-e\s+/bin/(ba)?sh|socket\.socket\(.{0,120}subprocess\.(call|Popen)\(\[?['\"]/bin/(ba)?sh", re.S)),
    ("crypto miner", re.compile(r"stratum\+tcp://|xmrig|cryptonight|coinhive|minexmr|nicehash", re.I)),
    ("runs base64-decoded code", re.compile(r"(eval|exec)\s*\(\s*(base64\.b64decode|atob|Buffer\.from\([^)]*['\"]base64['\"]\))", re.I)),
    ("reads browser or chat-app credentials", re.compile(r"Login Data|Local Storage[\\/]+leveldb|Cookies['\"]?\s*\)|\\\\Discord\\\\|discord(app)?[\\/]+Local Storage|keychain-db|Local State['\"].{0,80}encrypted_key", re.I | re.S)),
    ("downloads and runs a script from an unknown host", re.compile(r"(curl|wget)\s+[^|\n]*https?://(?!(?:raw\.githubusercontent\.com|github\.com|get\.docker\.com|sh\.rustup\.rs|astral\.sh|install\.python-poetry\.org|deb\.nodesource\.com|bun\.sh|tailscale\.com)\b)[^\s|]+\s*\|\s*(sudo\s+)?(ba)?sh", re.I)),
    ("sends data to a hard-coded webhook", re.compile(r"discord(app)?\.com/api/webhooks/\d+/[\w-]{20,}|api\.telegram\.org/bot\d+:[\w-]{20,}/send", re.I)),
]
CODE_EXT = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".ps1", ".bat", ".cmd", ".kt", ".java", ".go", ".rs", ".cs", ".php", ".rb", ".lua", ".gd")


def scan(files: Dict[str, str], limit: int = 6) -> List[Dict[str, str]]:
    """Returns up to `limit` concerns as {"file", "code", "reason"}; an empty list when nothing matches."""
    out: List[Dict[str, str]] = []
    for path, content in files.items():
        if not path.endswith(CODE_EXT) or re.search(r"(^|/)(tests?|spec|__tests__)(/|$)", path):
            continue
        for reason, rx in PATTERNS:
            m = rx.search(content)
            if not m:
                continue
            start = content.rfind("\n", 0, m.start()) + 1
            end = content.find("\n", m.end())
            out.append({"file": path, "code": content[start:end if end != -1 else None].strip()[:600], "reason": reason})
            if len(out) >= limit:
                return out
    return out
