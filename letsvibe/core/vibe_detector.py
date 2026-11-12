"""
Pre-flight AI & Vibe-Coding Detection Engine for LetsVibe.
Evaluates repositories to ensure they are AI-assisted or vibe-coded before
dispatching heavy 14B model evaluations.
"""

import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

# Tool configuration artifacts characteristic of modern AI coding assistants
VIBE_TOOL_ARTIFACTS = {
    ".cursorrules": "Cursor configuration rules",
    ".cursor": "Cursor directory",
    ".windsurfrules": "Windsurf/Codeium configuration rules",
    ".codeium": "Codeium assistant directory",
    ".clinerules": "Cline autonomous coding rules",
    ".roomodes": "Roo-Code mode configuration",
    ".aider": "Aider terminal assistant file",
    ".aider.chat.history.md": "Aider chat transcript",
    ".aider.input.history": "Aider input history",
    "copilot-instructions.md": "GitHub Copilot instructions",
    ".github/copilot-instructions.md": "GitHub Copilot workspace instructions",
    "devin.json": "Devin AI software engineer configuration",
    "bolt.new": "Bolt.new full-stack prompt artifact",
    "v0.dev": "V0.dev frontend scaffold artifact",
    "lovable.dev": "Lovable.dev prompt scaffold artifact",
    ".vibe": "LetsVibe explicit descriptor",
}

# Regex patterns in commit logs and co-authors
AI_COMMIT_PATTERNS = [
    r"co-authored-by:\s*(cursor|copilot|claude|aider|devin|chatgpt)",
    r"(generated|assisted|scaffolded|created)\s+(by|with)\s+(cursor|copilot|claude|chatgpt|gpt-4|v0|bolt|aider|deepseek)",
    r"\b(cursor\s+tab|claude\s+code|v0\s+prompt|bolt\s+prompt)\b",
    r"\bprompt:\s*",
    r"\b(ai-assisted|vibe-coded|vibe\s*coding)\b",
]

# Code comment and docstring fingerprints typical of LLM output
LLM_COMMENT_PATTERNS = [
    r"//\s*helper\s+function\s+to",
    r"#\s*step\s*[1-5]:\s*",
    r"//\s*step\s*[1-5]:\s*",
    r"//\s*todo:\s*replace\s+with\s+(your\s+)?(actual\s+)?api\s*key",
    r"#\s*todo:\s*replace\s+with\s+(your\s+)?(actual\s+)?api\s*key",
    r"//\s*here\s+is\s+the\s+implementation",
    r"```(python|typescript|javascript|kotlin|go|rust)",
]


@dataclass
class VibeCheckResult:
    is_vibe: bool
    confidence: float
    reasons: List[str]
    signals: List[str] = field(default_factory=list)
    is_legacy_pre_2022: bool = False


class VibeDetector:
    """Detects AI assistance and vibe-coding signatures in a repository."""

    @classmethod
    def evaluate(
        cls,
        source_path: Path,
        files: Optional[Dict[str, str]] = None,
        git_log: Optional[str] = None,
    ) -> VibeCheckResult:
        source_p = Path(source_path).resolve()
        signals = []
        confidence_points = 0
        is_legacy = False

        # 1. Check Tool Configuration Artifacts (High Confidence)
        existing_rel_paths: Set[str] = set()
        if files:
            existing_rel_paths = set(p.replace("\\", "/") for p in files.keys())
        elif source_p.exists():
            for p in source_p.rglob("*"):
                if p.is_file():
                    existing_rel_paths.add(str(p.relative_to(source_p)).replace("\\", "/"))

        for artifact_path, desc in VIBE_TOOL_ARTIFACTS.items():
            for f in existing_rel_paths:
                if f == artifact_path or f.endswith("/" + artifact_path) or f.startswith(artifact_path + "/"):
                    signals.append(f"AI Assistant Tool Config: {desc} ({f})")
                    confidence_points += 60
                    break

        # Check for AI documentation fingerprints (shields.io badges, structured READMEs)
        if cls._check_readme_ai_fingerprints(existing_rel_paths, files):
            signals.append("AI Documentation Pattern: Hyper-structured README & badge array detected")
            confidence_points += 35

        # Check for automated packaging/distribution scaffolding scripts
        if cls._check_distribution_scaffolding(existing_rel_paths):
            signals.append("AI Packaging Pattern: Automated build/install scripting detected")
            confidence_points += 30

        # 2. Check Git Commit History & Co-Authors
        commit_text = git_log or cls._extract_git_log(source_p)
        if commit_text:
            lower_commits = commit_text.lower()
            for pat in AI_COMMIT_PATTERNS:
                matches = re.findall(pat, lower_commits)
                if matches:
                    signals.append(f"AI Git Signature: matched '{matches[0]}'")
                    confidence_points += 45
                    break

            # Check for rapid-fire automated commit clusters (AI agent loops)
            if cls._check_fast_succession_commits(source_p):
                signals.append("AI Agent Pattern: Rapid-fire successive commit cluster detected")
                confidence_points += 40

            # Check for pristine conventional commit uniformity (typical of LLM workflows)
            if cls._check_commit_formality(commit_text):
                signals.append("AI Coding Pattern: Syntactically uniform conventional commit history")
                confidence_points += 30

            # Check if project is legacy pre-November 2022 (pre-ChatGPT/Copilot era)
            latest_commit_date = cls._extract_latest_commit_date(source_p)
            if latest_commit_date and latest_commit_date.replace(tzinfo=None) < datetime(2022, 11, 1):
                is_legacy = True
                signals.append(f"Pre-LLM Era: Latest commit dated {latest_commit_date.strftime('%Y-%m-%d')} (prior to Nov 2022)")
                confidence_points -= 50

        # 3. Check Source Code Content (LLM Comments & Prompt Artifacts)
        content_matches = 0
        if files:
            for rel_path, content in list(files.items())[:50]:  # sample up to 50 files
                for pat in LLM_COMMENT_PATTERNS:
                    if re.search(pat, content, re.IGNORECASE):
                        content_matches += 1
                        signals.append(f"LLM Coding Signature in {rel_path}: '{pat}'")
                        confidence_points += 25
                        if content_matches >= 3:
                            break
                if content_matches >= 3:
                    break

        # 4. Final Verdict Calculation
        confidence = max(0.0, min(1.0, confidence_points / 60.0))
        is_vibe = (confidence >= 0.50) and not (is_legacy and confidence < 0.90)

        reasons = []
        if is_vibe:
            reasons.append(f"Verified AI-assisted repository (Confidence: {int(confidence*100)}%).")
            reasons.extend(signals)
        else:
            if is_legacy:
                reasons.append("Project created prior to modern LLM tools (pre-Nov 2022) with no AI coding artifacts.")
            else:
                reasons.append("No AI configuration files, AI co-authors, or vibe-coding fingerprints detected.")

        return VibeCheckResult(
            is_vibe=is_vibe,
            confidence=confidence,
            reasons=reasons,
            signals=signals,
            is_legacy_pre_2022=is_legacy,
        )

    @classmethod
    def _extract_git_log(cls, repo_dir: Path) -> str:
        """Extracts the recent 50 git commit messages and authors."""
        if not (repo_dir / ".git").exists():
            return ""
        try:
            res = subprocess.run(
                ["git", "log", "-n", "50", "--format=%s %b %an %ae"],
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=5,
            )
            return res.stdout if res.returncode == 0 else ""
        except Exception:
            return ""

    @classmethod
    def _extract_latest_commit_date(cls, repo_dir: Path) -> Optional[datetime]:
        """Extracts the date of the latest commit to check for legacy pre-2022 projects."""
        if not (repo_dir / ".git").exists():
            return None
        try:
            res = subprocess.run(
                ["git", "log", "-1", "--format=%cI"],
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                return datetime.fromisoformat(res.stdout.strip())
        except Exception:
            pass
        return None

    @classmethod
    def _check_fast_succession_commits(cls, repo_dir: Path) -> bool:
        """Detects rapid-fire automated commits typical of AI agent loops."""
        if not (repo_dir / ".git").exists():
            return False
        try:
            res = subprocess.run(
                ["git", "log", "-n", "30", "--format=%ct"],
                cwd=str(repo_dir),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return False
            
            timestamps = [int(t) for t in res.stdout.splitlines() if t.strip().isdigit()]
            if len(timestamps) < 5:
                return False

            rapid_count = 0
            for i in range(len(timestamps) - 1):
                diff = abs(timestamps[i] - timestamps[i+1])
                if diff < 90:  # less than 90 seconds between commits
                    rapid_count += 1
                    if rapid_count >= 4:
                        return True
                else:
                    rapid_count = 0
            return False
        except Exception:
            return False

    @classmethod
    def _check_commit_formality(cls, commit_text: str) -> bool:
        """Detects high syntactic formality and conventional prefix usage typical of LLMs."""
        lines = [l.strip() for l in commit_text.splitlines() if l.strip()]
        if len(lines) < 4:
            return False
        
        conventional_prefixes = ("feat:", "fix:", "refactor:", "chore:", "docs:", "style:", "test:")
        conventional_matches = sum(1 for l in lines if l.lower().startswith(conventional_prefixes))
        
        # If more than 60% of recent commits follow strict conventional headers with zero casual typos/shorthand
        return (conventional_matches / len(lines)) >= 0.60
    
    @classmethod
    def _check_readme_ai_fingerprints(cls, existing_rel_paths: Set[str], files: Optional[Dict[str, str]] = None) -> bool:
        """Detects hyper-structured AI documentation patterns (badge grids, exhaustive READMEs)."""
        readme_content = ""
        for path in existing_rel_paths:
            if path.lower() in ("readme.md", "README.md"):
                if files and path in files:
                    readme_content = files[path]
                break
        
        if not readme_content:
            return False

        # Check for signature AI documentation traits: shields.io badges + Table of Contents + Architecture sections
        has_badges = "img.shields.io" in readme_content
        has_toc = "table of contents" in readme_content.lower() or "contents" in readme_content.lower()
        has_architecture = "architecture" in readme_content.lower() or "system" in readme_content.lower()

        return has_badges and has_toc and has_architecture

    @classmethod
    def _check_distribution_scaffolding(cls, existing_rel_paths: Set[str]) -> bool:
        """Detects automated build/packaging scripts typical of prompt-to-AppImage workflows."""
        build_artifacts = {"build.sh", "install.sh", "start.sh", "build_windows.ps1", "AppRun"}
        return any(artifact in existing_rel_paths for artifact in build_artifacts)
