"""
The one evaluation recipe, shared by the queue worker and the curve calibration script
(scripts/calibrate_curve.py), so the reference projects that set the grading curve are graded
exactly like every submission.

Recipe "v9-hybrid" (2026-11-13): grades what people experience, never how the code is written. Two sources of evidence,
compared item by item: the App Dossier (rubric v8) and per-factor 7B reads of the code in 5,200-token chunks (rubric v7's
chunking, one factor per session); conflicts are settled by the 14B rereading the quoted lines. See evaluator/experience.

Previous recipe "v8-experience" (2026-11-12), still the dossier half of v9:
1. Deterministic measurements (size, languages, platforms, packaging) and classification (category, platforms).
2. Experience grading (evaluator/experience): an App Dossier built by Python, a cliché check against common
   vibe-coded app types, three 14B checklists (real-world value, creativity, user journey) answered with fixed
   choices and quotes, a Python score from fixed points, and 14B descriptions of its own checked answers.
3. A plain-Python scan for clearly malicious code holds the report for the owner to explain.

The previous recipe ("v6-3b": chunked 7B code review + 14B whole-project review + checklist) is kept in
evaluator/consensus.py and in the letsvibe-og-bk backup repository.
"""
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..analyzers.complexity import ComplexityAnalyzer
from ..analyzers.functionality import FunctionalityAnalyzer
from ..analyzers.journey_signals import full_readme, gather_journey_signals
from ..analyzers.security import SecurityAnalyzer
from ..core.models import DeterministicMetrics
from ..core.platforms import detect_platforms
from .triage import select_for_review

PIPELINE_ID = "v9.3-profiles"
CHUNK_TOKENS = 5200


def gather_metrics(packed_repo) -> Tuple[DeterministicMetrics, Any]:
    """All deterministic measurements for a packed repository, plus the platform detection result."""
    file_paths = [packed_repo.source_path / p for p in packed_repo.files.keys()]
    avg_cc, max_cc, top_funcs = ComplexityAnalyzer.analyze_paths(file_paths)
    secret_count, secret_findings = SecurityAnalyzer.scan(packed_repo.files)
    (test_count, test_ratio, has_ci_cd, has_docker, has_desktop_packaging, has_license, has_readme,
     readme_excerpt) = FunctionalityAnalyzer.evaluate(packed_repo.files, packed_repo.total_loc)
    has_cli_fw, has_tui, has_rich, cli_details = FunctionalityAnalyzer.evaluate_cli_ux(packed_repo.files)
    platform_info = detect_platforms(packed_repo.source_path)
    metrics = DeterministicMetrics(
        total_loc=packed_repo.total_loc, data_loc=getattr(packed_repo, "data_loc", 0),
        total_files=len(packed_repo.files), languages=packed_repo.language_stats,
        avg_cyclomatic_complexity=avg_cc, max_cyclomatic_complexity=max_cc, top_complex_functions=top_funcs,
        detected_secrets_count=secret_count, secret_findings=secret_findings,
        test_files_count=test_count, test_to_code_ratio=test_ratio, has_ci_cd=has_ci_cd, has_docker=has_docker,
        has_desktop_packaging=has_desktop_packaging, has_license=has_license, has_readme=has_readme,
        readme_excerpt=readme_excerpt, detected_platforms=platform_info.platforms,
        device_profile=platform_info.device_profile, platform_signals=platform_info.signals,
        media_summary=packed_repo.media_summary(),
        journey=gather_journey_signals(packed_repo.source_path, packed_repo.files, full_readme(packed_repo)),
        has_cli_framework=has_cli_fw, has_tui_interactive=has_tui, has_rich_terminal_styling=has_rich,
        cli_ux_details=cli_details,
    )
    return metrics, platform_info


def build_batches(packed_repo) -> Tuple[List[Tuple[str, str]], Dict[str, Tuple[int, int]]]:
    """(chunks for the evaluator, {role: (files reviewed, files in role)})."""
    keep, coverage, _ = select_for_review(packed_repo.files)
    batches = packed_repo.get_stratified_manifests(max_batch_tokens=CHUNK_TOKENS, max_batches=8, distill=True, only=keep)
    return batches, coverage


def get_available_engines(include_testing: bool = False) -> List[Dict[str, Any]]:
    """Returns registered engine versions from letsvibe-engine (production only unless include_testing=True)."""
    try:
        from letsvibe_engine import get_available_engines as _get_engines
        return _get_engines(include_testing=include_testing)
    except ImportError:
        return [
            {
                "id": "blend",
                "engine_version": "v9.4",
                "module": "v9_4_experimental",
                "name": "✨ Rubric v9.4 — Submitter Context & Grounding (Production)",
                "short_name": "Rubric v9.4",
                "status": "production",
                "is_default": True,
                "description": "Evaluates real-world value, creativity, user journey, functionality, and UI/UX with submitter context weighting, grounding checks, and 7 domain archetype lenses.",
            }
        ]


def get_default_engine_id() -> str:
    """Returns the id of the current default production engine from letsvibe-engine."""
    engines = get_available_engines(include_testing=False)
    for eng in engines:
        if eng.get("is_default"):
            return str(eng["id"])
    if engines:
        return str(engines[0]["id"])
    return "blend"


def get_engine_info(engine_id: str) -> Optional[Dict[str, Any]]:
    """Looks up engine metadata by id or version."""
    try:
        from letsvibe_engine import get_engine_info as _get_info
        return _get_info(engine_id)
    except ImportError:
        if engine_id in ("blend", "fast", "deep", "v9.4"):
            return get_available_engines(include_testing=True)[0]
        return None


def is_testing_engine(engine_id: str) -> bool:
    """Returns True if the given engine id/version is marked as status='testing'."""
    info = get_engine_info(engine_id)
    return bool(info and str(info.get("status", "")).lower() == "testing")


def evaluate(
    client,
    packed_repo,
    repo_source: str,
    metrics: DeterministicMetrics,
    classification,
    progress_callback: Optional[Callable[[str, int], None]] = None,
    engine_id: str = "blend",
    submission_meta: Optional[Dict[str, Any]] = None,
):
    """Grades an already measured and classified repository via the versioned letsvibe-engine router."""
    try:
        from letsvibe_engine import run_engine
        return run_engine(
            engine_id=engine_id,
            client=client,
            packed_repo=packed_repo,
            repo_source=repo_source,
            metrics=metrics,
            classification=classification,
            progress_callback=progress_callback,
            submission_meta=submission_meta,
        )
    except ImportError:
        from .experience import evaluate as experience_evaluate
        return experience_evaluate(
            client,
            packed_repo,
            repo_source,
            metrics,
            classification,
            progress=progress_callback,
            submission_meta=submission_meta,
        )
