"""
One-shot AI review of a statement an owner says is wrong.

The model sees only the disputed statement, the owner's explanation and the most relevant code from
the repository at the graded commit, and must decide whether the statement is contradicted by the
code. It is told to be skeptical of BOTH sides: owners can be wrong too.
"""

import os
import re
import subprocess  # nosec B404 - git/nvidia-smi run with fixed list arguments, never a shell
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .ollama import CpuSpilloverError, OllamaClient

EVIDENCE_BUDGET_CHARS = 24000
MAX_FILES = 6
WINDOW = 15

ERROR_REVIEW_SYSTEM_PROMPT = """You are verifying one statement from an automated code review.
The project's owner says the statement is wrong. Decide, using ONLY the code shown, which side the code supports.

- owner_is_right: the code clearly contradicts the statement (e.g. it claims a feature is missing and the code
  implements it). Write corrected_statement: an accurate replacement sentence, or an empty string if the statement
  should simply be removed.
- review_is_right: the code supports the statement, or the owner's explanation does not hold up.
- cannot_tell: the code shown is not enough to decide.

Be skeptical of both sides. Do not accept the owner's claim without code evidence. Respond with JSON only."""

_STOP = {
    "this", "that", "with", "from", "have", "there", "their", "which", "would", "could", "should", "about", "does",
    "into", "only", "when", "what", "were", "been", "being", "they", "them", "than", "then", "also", "because",
    "lacks", "lack", "missing", "code", "file", "files", "project", "statement", "review", "implemented", "implementation",
    "support", "supports", "handling", "error", "errors", "there", "some", "more", "less", "very",
}


def schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "evidence": {"type": "string"},
            "verdict": {"type": "string", "enum": ["owner_is_right", "review_is_right", "cannot_tell"]},
            "explanation": {"type": "string"},
            "corrected_statement": {"type": "string"},
        },
        "required": ["evidence", "verdict", "explanation", "corrected_statement"],
    }


def checkout_commit(repo: Path, sha: Optional[str]) -> bool:
    """Moves a shallow clone to the graded commit (GitHub allows fetching a commit by id). Best effort."""
    if not sha or not (repo / ".git").exists():
        return False
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)  # nosec B603 B607 - fixed command, list arguments, no shell
        if head.stdout.strip() == sha:
            return True
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        fetched = subprocess.run(["git", "-C", str(repo), "fetch", "--depth", "1", "origin", sha],  # nosec B603 B607 - fixed command, list arguments, no shell
                                 capture_output=True, text=True, timeout=60, env=env)
        if fetched.returncode != 0:
            return False
        done = subprocess.run(["git", "-C", str(repo), "checkout", "--quiet", sha],  # nosec B603 B607 - fixed command, list arguments, no shell
                              capture_output=True, text=True, timeout=30)
        return done.returncode == 0
    except Exception:
        return False


def keywords(*texts: str) -> List[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_./-]{3,}", " ".join(texts))
    out: List[str] = []
    for w in words:
        w = w.strip("./-")
        if len(w) >= 4 and w.lower() not in _STOP and w not in out:
            out.append(w)
    return out[:25]


def gather_evidence(repo: Path, file_paths: List[str], terms: List[str]) -> Tuple[str, int]:
    """Numbered code windows around keyword hits in the most relevant files. Returns (text, files used)."""
    lowered = [t.lower() for t in terms]
    scored = []
    for rel in file_paths:
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        low = text.lower()
        name = rel.lower()
        score = sum(5 for t in lowered if t in name) + sum(min(low.count(t), 10) for t in lowered)
        if score:
            scored.append((score, rel, text))
    scored.sort(key=lambda x: -x[0])
    parts: List[str] = []
    used = 0
    budget = EVIDENCE_BUDGET_CHARS
    for _, rel, text in scored[:MAX_FILES]:
        lines = text.splitlines()
        hits = [i for i, ln in enumerate(lines) if any(t in ln.lower() for t in lowered)]
        keep = set()
        for i in hits[:40]:
            keep.update(range(max(0, i - WINDOW), min(len(lines), i + WINDOW + 1)))
        if not keep:
            continue
        chunk, prev = [f"--- FILE: {rel} ---"], None
        for i in sorted(keep):
            if prev is not None and i != prev + 1:
                chunk.append("   ...")
            chunk.append(f"{i + 1:>5}: {lines[i]}")
            prev = i
        block = "\n".join(chunk) + "\n"
        if len(block) > budget:
            block = block[:budget]
        parts.append(block)
        used += 1
        budget -= len(block)
        if budget <= 500:
            break
    return "\n".join(parts), used


def review_disputed_statement(client: OllamaClient, packed_repo: Any, commit_sha: Optional[str],
                              error_report: Dict[str, Any], model: str = "qwen2.5-coder:7b") -> Dict[str, Any]:
    repo = Path(packed_repo.source_path)
    exact = checkout_commit(repo, commit_sha)
    terms = keywords(error_report["statement_text"], error_report["explanation"])
    evidence, used = gather_evidence(repo, list(packed_repo.files.keys()), terms)
    if not evidence.strip():
        return {"verdict": "cannot_tell", "explanation": "No code related to the statement could be located automatically.",
                "corrected_statement": ""}
    note = "" if exact else "\n(Note: the exact graded commit could not be fetched; this is the latest code.)\n"
    prompt = (
        f"STATEMENT FROM THE REVIEW:\n{error_report['statement_text']}\n\n"
        f"OWNER'S EXPLANATION:\n{error_report['explanation'][:2000]}\n{note}\n"
        f"RELEVANT CODE ({used} file(s), line-numbered):\n{evidence}"
    )
    try:
        raw = client.generate_json(model=model, system_prompt=ERROR_REVIEW_SYSTEM_PROMPT, user_prompt=prompt,
                                   temperature=0.0, format_schema=schema(), max_output_tokens=500)
    except CpuSpilloverError:
        raise
    except Exception as exc:
        return {"verdict": "cannot_tell", "explanation": f"Automatic check failed ({exc}).", "corrected_statement": ""}
    verdict = raw.get("verdict") if raw.get("verdict") in ("owner_is_right", "review_is_right", "cannot_tell") else "cannot_tell"
    return {
        "verdict": verdict,
        "explanation": str(raw.get("explanation") or "").strip()[:1500],
        "corrected_statement": str(raw.get("corrected_statement") or "").strip()[:500],
    }
