"""
The one evaluation recipe, shared by the queue worker and the curve calibration script
(scripts/calibrate_curve.py), so the reference projects that set the grading curve are graded
exactly like every submission.

Recipe "v6-3b" (chosen in the 2026-11 model bake-off):
1. Deterministic measurements (complexity, secrets, tests, packaging, platforms, user-journey evidence).
2. Classification (category, platforms, device profile).
3. Name-based file filter (no AI): docs, licenses, build/config files and tests are skipped (they reach
   the reviewers as measurements); story/dialogue/level content is sampled; everything else is read.
4. Chunks of about 5,200 tokens, each listing only its own files plus the project's top-level folders.
5. qwen2.5-coder 7B reviews each chunk; qwen2.5-coder 14B reviews the whole project (given the full
   file tree) and answers the yes/no checklist; scores are blended and adjusted by the measurements.
"""
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..analyzers.complexity import ComplexityAnalyzer
from ..analyzers.functionality import FunctionalityAnalyzer
from ..analyzers.journey_signals import full_readme, gather_journey_signals
from ..analyzers.security import SecurityAnalyzer
from ..core.models import DeterministicMetrics
from ..core.platforms import detect_platforms
from .triage import select_for_review

PIPELINE_ID = "v6-3b"
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
        total_loc=packed_repo.total_loc, total_files=len(packed_repo.files), languages=packed_repo.language_stats,
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
    """(chunks for the 7B, {role: (files reviewed, files in role)})."""
    keep, coverage, _ = select_for_review(packed_repo.files)
    batches = packed_repo.get_coverage_manifests(max_batch_tokens=CHUNK_TOKENS, distill=True, only=keep, local_tree=True)
    return batches, coverage


def evaluate(engine, packed_repo, repo_source: str, metrics: DeterministicMetrics, classification,
             mode: str = "blend", progress_callback: Optional[Callable[[str, int], None]] = None):
    """Runs steps 3-5 for an already measured and classified repository; returns the VibeReport."""
    batches, _ = build_batches(packed_repo)
    return engine.evaluate_pipeline(
        repo_name=packed_repo.repo_name, repo_source=repo_source, manifest_batches=batches, metrics=metrics,
        mode=mode, progress_callback=progress_callback, classification=classification,
        file_tree=packed_repo.file_tree,
    )
