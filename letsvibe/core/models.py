"""
Core Pydantic models defining the data structures for LetsVibe analysis.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FactorType(str, Enum):
    MALWARE_RISK = "malware_risk"
    # What users experience (most of the weight)
    FUNCTIONALITY = "functionality"
    USER_JOURNEY = "user_journey"
    UI_UX = "ui_ux"
    RESONANCE = "resonance"
    CREATIVITY = "creativity"
    # Engineering (lighter weight; framed as "what a pro team would do next")
    CODE_HEALTH = "code_health"
    OPERATIONAL_SAFETY = "operational_safety"
    # Rubric v1-v5 keys, kept only so older stored reports still load
    MAINTAINABILITY = "maintainability"
    ARCHITECTURE = "architecture"


# Rubric v6: graded as vibe-coded software, compared with other vibe-coded projects (written by AI from prompts).
CORE_FACTORS = (
    "functionality",
    "user_journey",
    "ui_ux",
    "resonance",
    "creativity",
    "code_health",
    "operational_safety",
)
EXPERIENCE_FACTORS = ("functionality", "user_journey", "ui_ux", "resonance", "creativity")
ENGINEERING_FACTORS = ("code_health", "operational_safety")

FACTOR_LABELS = {
    "functionality": "Functionality",
    "user_journey": "User Journey",
    "ui_ux": "UI / UX",
    "resonance": "Real-World Value",
    "creativity": "Creativity",
    "code_health": "Code Health",
    "operational_safety": "Operational Safety",
}


class DynamicWeights(BaseModel):
    """Seven weighted factors summing to 100%, weighted toward what users experience."""

    functionality: float = Field(default=20.0, ge=0.0, le=100.0)
    user_journey: float = Field(default=16.0, ge=0.0, le=100.0)
    ui_ux: float = Field(default=14.0, ge=0.0, le=100.0)
    resonance: float = Field(default=20.0, ge=0.0, le=100.0)
    creativity: float = Field(default=11.0, ge=0.0, le=100.0)
    code_health: float = Field(default=12.0, ge=0.0, le=100.0)
    operational_safety: float = Field(default=7.0, ge=0.0, le=100.0)
    justification: str = Field(
        default="Balanced baseline weighted toward what users experience; engineering factors carry less weight.",
        description="Explanation of why these weights suit this specific project."
    )

    def normalize(self) -> "DynamicWeights":
        """Ensures all 7 weights sum to exactly 100%."""
        total = sum(getattr(self, f) for f in CORE_FACTORS)
        if total <= 0:
            return DynamicWeights()
        factor = 100.0 / total
        values = {f: round(getattr(self, f) * factor, 1) for f in CORE_FACTORS}
        return DynamicWeights(**values, justification=self.justification)


class ProjectArchetype(BaseModel):
    name: str = Field(description="Name or inferred title of the project")
    category: str = Field(
        description="E.g. Web App, CLI Tool, Cryptography Library, Game, AI Agent, Data Pipeline"
    )
    primary_language: str
    target_audience: str
    core_purpose: str = Field(description="Summary of what the project aims to accomplish")
    intended_scale: str = Field(
        default="Production",
        description="E.g. Experimental Prototype, Library, Hackathon MVP, Production Enterprise"
    )
    general_category: str = Field(default="other", description="One of core.platforms.GENERAL_CATEGORIES keys")
    platforms: List[str] = Field(default_factory=list, description="Leaf ids from core.platforms.PLATFORM_LABELS")
    device_profile: str = Field(default="", description="Target hardware constraints the project is graded against")


class FunctionComplexity(BaseModel):
    name: str
    filename: str
    nloc: int
    cyclomatic_complexity: int


class DeterministicMetrics(BaseModel):
    total_loc: int = 0
    total_files: int = 0
    languages: Dict[str, int] = Field(default_factory=dict)
    avg_cyclomatic_complexity: float = 0.0
    max_cyclomatic_complexity: int = 0
    top_complex_functions: List[FunctionComplexity] = Field(default_factory=list)
    detected_secrets_count: int = 0
    secret_findings: List[str] = Field(default_factory=list)
    # Risky code patterns (eval, exec, shell=True, ...) found in actual code; counted separately from secrets
    risky_patterns_count: int = 0
    risky_findings: List[str] = Field(default_factory=list)
    test_files_count: int = 0
    test_to_code_ratio: float = 0.0
    has_ci_cd: bool = False
    has_docker: bool = False
    has_desktop_packaging: bool = False
    has_license: bool = False
    has_readme: bool = False
    readme_excerpt: str = ""
    has_cli_framework: bool = False
    has_tui_interactive: bool = False
    has_rich_terminal_styling: bool = False
    cli_ux_details: List[str] = Field(default_factory=list)
    detected_platforms: List[str] = Field(default_factory=list)
    device_profile: str = ""
    platform_signals: List[str] = Field(default_factory=list)
    # Images, audio, video, fonts and 3D models, by name only (contents are never read)
    media_summary: str = "none"
    # User-journey evidence found in the code (see analyzers/journey_signals.py)
    journey: Dict[str, Any] = Field(default_factory=dict)


class FactorEvaluation(BaseModel):
    factor: FactorType
    score: float = Field(ge=0.0, le=100.0, description="Raw score 0-100")
    weight: float = Field(ge=0.0, le=100.0, description="Factor weight percentage")
    weighted_score: float = Field(default=0.0, description="score * (weight / 100)")
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    reasoning: str = ""


class ModelPerspective(BaseModel):
    model_name: str
    role: str
    factor_scores: Dict[str, float] = Field(default_factory=dict)
    critique_summary: str = ""


class VibeReport(BaseModel):
    repo_name: str
    repo_source: str
    timestamp: str
    archetype: ProjectArchetype
    weights: DynamicWeights
    deterministic_metrics: DeterministicMetrics
    perspectives: List[ModelPerspective] = Field(default_factory=list)
    factor_evaluations: Dict[str, FactorEvaluation] = Field(default_factory=dict)
    final_vibe_score: float = Field(ge=0.0, le=100.0)
    malware_risk_score: float = Field(default=100.0, ge=0.0, le=100.0, description="Malware risk qualifier score (*0.00 to *1.00 multiplier)")
    operational_safety_score: float = Field(default=100.0, ge=0.0, le=100.0, description="Operational safety qualifier score")
    resonance_score: float = Field(default=100.0, ge=0.0, le=100.0, description="Resonance qualifier score (*0.00 to *1.00 multiplier)")
    letter_grade: str
    rubric_version: str = ""
    # Two-part feedback: standing among vibe coders, and what a professional team would do next
    peer_summary: str = ""
    pro_next_steps: List[str] = Field(default_factory=list)
    # Internal only, never published: set when the safety check fails so the report is held for an
    # owner explanation instead of being published. Stripped before any report is stored publicly.
    integrity_hold: bool = False
    integrity_concerns: List[Dict[str, str]] = Field(default_factory=list)
    verdict_title: str
    verdict_summary: str
    top_strengths: List[str] = Field(default_factory=list)
    priority_improvements: List[str] = Field(default_factory=list)
    top_solid_features: List[str] = Field(
        default_factory=list,
        description="Top 5 most solid, complete features requiring the least work"
    )
    top_features_needing_work: List[str] = Field(
        default_factory=list,
        description="Top 5 features or areas requiring the most attention or refactoring"
    )
    timing_breakdown: Dict[str, float] = Field(
        default_factory=dict,
        description="Elapsed seconds for each analysis phase"
    )
    issues_encountered: List[str] = Field(
        default_factory=list,
        description="Any warnings, retries, or issues encountered during analysis"
    )