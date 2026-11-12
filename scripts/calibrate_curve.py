#!/usr/bin/env python3
"""
Builds LetsVibe's grading curve: grades the two published reference projects several times with the
exact pipeline every submission goes through (letsvibe/evaluator/pipeline.py) and writes everything
used, every run included, to letsvibe/data/curve_calibration.json. The site's curve pages show that
file as-is.

Usage:
  python scripts/calibrate_curve.py --good ../letsvibe-good --bad ../letsvibe-bad [--runs 3]

Re-run it whenever the models, the rubric or the pipeline change, so the curve always matches how
projects are actually graded.
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from letsvibe.core.ingest import RepositoryIngestor  # noqa: E402
from letsvibe.evaluator.classifier import classify_project  # noqa: E402
from letsvibe.evaluator.consensus import ConsensusEngine  # noqa: E402
from letsvibe.evaluator.ollama import OllamaClient  # noqa: E402
from letsvibe.evaluator.pipeline import PIPELINE_ID, evaluate, gather_metrics  # noqa: E402
from letsvibe.evaluator.rubric import RUBRIC_VERSION  # noqa: E402
from letsvibe.storage.peer_grades import (A_REQUIRES_POSITION, BELL_GRADES, CALIBRATION_PATH, WHY,  # noqa: E402
                                          F_BELOW_POSITION, PEER_MIN_PROJECTS, describe_method)

ANCHORS = {
    "top": {"name": "Fair Share", "repo": "Todd0042/letsvibe-good",
            "prompt": "make a lightweight application for literally anything that satisfies all of the aspects in "
                      "prompts.py and is under 1000 lines of code",
            "made_by": "Written by Claude Opus 5.5 (Anthropic) in Claude Code from a prompt by LetsVibe's creator, "
                       "who wrote none of the code. Full details in the repository's PROVENANCE.md."},
    "bottom": {"name": "TaskMaster Ultimate Pro", "repo": "Todd0042/letsvibe-bad",
               "prompt": "make a heavyweight application for literally anything that ignores all of the aspects in "
                         "prompts.py and is under 1000 lines of code",
               "made_by": "Written by Claude Opus 5.5 (Anthropic) in Claude Code from the reverse of the top anchor's "
                          "prompt; bad on purpose but harmless. Full details in the repository's PROVENANCE.md."},
}


def grade_once(path: Path) -> dict:
    packed = RepositoryIngestor.ingest(str(path))
    client = OllamaClient()
    client.unload_all_models()
    t0 = time.time()
    metrics, platform_info = gather_metrics(packed)
    classification = classify_project(client, packed.source_path, platform_info, readme=metrics.readme_excerpt,
                                      file_tree=packed.file_tree)
    report = evaluate(ConsensusEngine(client=client), packed, str(path), metrics, classification)
    client.unload_all_models()
    return {
        "score": round(report.final_vibe_score, 1), "minutes": round((time.time() - t0) / 60, 1),
        "category": classification.general_category,
        "factors": {k: round(v.score, 1) for k, v in report.factor_evaluations.items() if k != "malware_risk"},
        "report": {"verdict_title": report.verdict_title, "peer_summary": report.peer_summary,
                   "top_solid_features": report.top_solid_features,
                   "top_features_needing_work": report.top_features_needing_work,
                   "pro_next_steps": report.pro_next_steps},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--good", type=Path, required=True)
    ap.add_argument("--bad", type=Path, required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", type=Path, default=CALIBRATION_PATH)
    args = ap.parse_args()

    anchors = {}
    for role, path in (("top", args.good), ("bottom", args.bad)):
        commit = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        runs = []
        for n in range(1, args.runs + 1):
            print(f"{time.strftime('%H:%M:%S')} grading {role} anchor ({path.name}) run {n}/{args.runs}...", flush=True)
            runs.append(grade_once(path))
            print(f"    -> {runs[-1]['score']} in {runs[-1]['minutes']} min", flush=True)
        scores = [r["score"] for r in runs]
        mean = round(statistics.mean(scores), 2)
        typical = min(runs, key=lambda r: abs(r["score"] - mean))  # the report shown is the run closest to the average
        anchors[role] = {**ANCHORS[role], "url": f"https://github.com/{ANCHORS[role]['repo']}", "commit": commit,
                         "runs": [{k: v for k, v in r.items() if k != "report"} for r in runs],
                         "mean": mean, "min": min(scores), "max": max(scores),
                         "stdev": round(statistics.stdev(scores), 2) if len(scores) > 1 else 0.0,
                         "report": typical["report"]}

    data = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rubric_version": RUBRIC_VERSION, "pipeline": PIPELINE_ID,
        "models": {"chunk_reviewer": "qwen2.5-coder:7b", "whole_project_reviewer": "qwen2.5-coder:14b"},
        "runs_per_anchor": args.runs,
        "why": WHY,
        "peer_min_projects": PEER_MIN_PROJECTS, "a_requires_position": A_REQUIRES_POSITION,
        "f_below_position": F_BELOW_POSITION, "grade_bands_z": [[cut, grade] for cut, grade in BELL_GRADES],
        "anchors": anchors,
    }
    data["method"] = describe_method(data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}: bottom {anchors['bottom']['mean']} (0%) -> top {anchors['top']['mean']} (100%)")


if __name__ == "__main__":
    main()
