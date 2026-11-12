"""
Project classification: general category, target platforms and device profile.

Runs before grading as its own small 7B call so the answer is focused and cheap (~5s), and so
existing projects can be reclassified without a full regrade. Build-file detection
(core.platforms.detect_platforms) is authoritative for platforms; the model fills gaps and
writes the human-readable device profile.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.platforms import (
    GENERAL_CATEGORIES,
    PLATFORM_LABELS,
    WEAK_SIGNALS,
    PlatformDetection,
    find_entry_file,
    normalize_category,
    normalize_platforms,
    read_entry_file,
)
from .ollama import CpuSpilloverError, OllamaClient

DEFAULT_CLASSIFIER_MODEL = "qwen2.5-coder:7b"

CLASSIFIER_SYSTEM_PROMPT = """You classify software repositories for a public leaderboard's filters.
Use ONLY the evidence given (build-file findings, README, file tree, main entry file).

general_category -- pick exactly ONE:
- game: anything primarily played (any platform, including microcontrollers and consoles)
- application: an end-user desktop or mobile app (not primarily a game)
- web_app: a website or browser-based service
- tool: a CLI, developer tool or utility
- library: code meant to be imported by other programs (SDK, framework, package)
- bot: chat bots, automation scripts, scrapers, scheduled jobs
- ai: projects whose core is an LLM/ML model (agents, RAG, model training)
- embedded: firmware or hardware projects that are not primarily games
- other: none of the above

platforms -- every platform it actually runs on: windows, macos, linux, android, ios, web, embedded, console.
Build-file findings are facts: always include those platforms.

device_profile -- ONE sentence on the target hardware the project must work within: chip/CPU, RAM, screen size and
type, input method. Take every number from the BUILD-FILE FINDINGS, README or code shown; NEVER invent a resolution,
memory size or screen shape that is not in the evidence -- leave it out instead. Use "Standard desktop PC" for
ordinary desktop software, "Typical smartphones, touch input" for generic mobile apps and "Any modern web browser"
for websites.

Respond with JSON only."""


def classifier_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "evidence": {"type": "string"},
            "general_category": {"type": "string", "enum": list(GENERAL_CATEGORIES)},
            "platforms": {
                "type": "array",
                "items": {"type": "string", "enum": list(PLATFORM_LABELS)},
                "minItems": 1,
                "maxItems": 8,
            },
            "device_profile": {"type": "string"},
        },
        "required": ["evidence", "general_category", "platforms", "device_profile"],
    }


@dataclass
class Classification:
    general_category: str = "other"
    platforms: List[str] = field(default_factory=list)
    device_profile: str = ""
    entry_file: Optional[str] = None
    evidence: str = ""
    issues: List[str] = field(default_factory=list)


def pick_model(client: OllamaClient, preferred: str = DEFAULT_CLASSIFIER_MODEL) -> str:
    available = client.list_models()
    if not available or preferred in available:
        return preferred
    matches = [m for m in available if "7b" in m or "qwen" in m or "llama" in m]
    return matches[0] if matches else available[0]


def build_classifier_prompt(
    detection: PlatformDetection, readme: str, file_tree: str, entry_file: Optional[str], entry_code: str
) -> str:
    found = ", ".join(detection.platforms) or "none"
    signals = "; ".join(detection.signals) or "none"
    return f"""BUILD-FILE FINDINGS (facts):
- Platforms: {found}
- Signals: {signals}
- Hardware: {detection.device_profile or "not detected"}

README (excerpt):
{(readme or "No README.").strip()[:2500]}

MAIN ENTRY FILE ({entry_file or "not found"}):
{entry_code.strip()[:5000] if entry_code else "(none)"}

FILE TREE (excerpt):
{(file_tree or "").strip()[:2500]}
"""


def merge_classification(raw: Dict[str, Any], detection: PlatformDetection) -> Classification:
    """Combines model output with build-file detection (detection wins on platforms unless it is only a weak guess)."""
    model_platforms = normalize_platforms(raw.get("platforms"))
    strong = [s for s in detection.signals if s not in WEAK_SIGNALS]
    if detection.platforms and strong:
        platforms = normalize_platforms(detection.platforms + [p for p in model_platforms if p != "embedded"])
        if "embedded" in detection.platforms:
            platforms = normalize_platforms(detection.platforms)  # firmware does not also run on a PC
    else:
        platforms = model_platforms or detection.platforms
    # A hardware profile read from build files and source is factual; the 7B's sentence is only used when
    # nothing was detected (it has been seen to copy prompt examples or call an ESP32 a "desktop PC").
    profile = detection.device_profile or str(raw.get("device_profile") or "").strip()
    return Classification(
        general_category=normalize_category(raw.get("general_category")),
        platforms=platforms,
        device_profile=profile,
        evidence=str(raw.get("evidence") or "").strip(),
    )


def classify_project(
    client: OllamaClient,
    root: Path,
    detection: PlatformDetection,
    readme: str = "",
    file_tree: str = "",
    model: Optional[str] = None,
) -> Classification:
    """Runs the focused classify call. Never raises (except hard GPU stops); falls back to detection alone."""
    entry_file = find_entry_file(root)
    entry_code = read_entry_file(root, entry_file)
    chosen = model or pick_model(client)
    try:
        raw = client.generate_json(
            model=chosen,
            system_prompt=CLASSIFIER_SYSTEM_PROMPT,
            user_prompt=build_classifier_prompt(detection, readme, file_tree, entry_file, entry_code),
            temperature=0.0,
            format_schema=classifier_schema(),
            max_output_tokens=350,
        )
        result = merge_classification(raw if isinstance(raw, dict) else {}, detection)
    except CpuSpilloverError:
        raise
    except Exception as e:
        result = merge_classification({}, detection)
        result.issues.append(f"Classification call failed ({e}); used build-file detection only.")
    result.entry_file = entry_file
    return result
