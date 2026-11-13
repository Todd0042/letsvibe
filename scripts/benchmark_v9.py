#!/usr/bin/env python3
"""
Benchmarks rubric v9 (hybrid grading) against the v8 baseline. Grades the two reference projects and any other
projects several times each with the exact pipeline every submission goes through, then prints how v9 did on each
target and writes every run to a JSON file.

Usage (Ollama running, qwen2.5-coder:7b, qwen2.5-coder:14b and mxbai-embed-large pulled):
  python scripts/benchmark_v9.py --good ../letsvibe-good --bad ../letsvibe-bad \\
      --extra ../tiki-karaoke ../echoes-of-the-logos ../deciflux [--runs 3] [--out benchmark-v9.json]

Targets (v8 baseline, from letsvibe/data/curve_calibration.json at commit 9f71754):
  - good/bad gap divided by the average run-to-run spread above 17.5
  - bad reference's User Journey well below 72
  - run-to-run spread about +/-2.5 or less
  - no claims without a checked quote
  - more specific detail (file and line) than v8, which had none
This does not change the grading curve; run scripts/calibrate_curve.py for that once the results look right.
"""
import argparse
import json
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from letsvibe.core.ingest import RepositoryIngestor  # noqa: E402
from letsvibe.evaluator.classifier import classify_project  # noqa: E402
from letsvibe.evaluator.ollama import OllamaClient  # noqa: E402
from letsvibe.evaluator.pipeline import PIPELINE_ID, evaluate, gather_metrics  # noqa: E402

V8 = {"separation": 17.5, "good_mean": 76.8, "bad_mean": 31.6, "spread": 2.6, "bad_user_journey": [72.0, 48.0, 72.0]}
_LOC = re.compile(r"\bline \d+\b")


def grade_once(path: Path) -> dict:
    packed = RepositoryIngestor.ingest(str(path))
    client = OllamaClient()
    client.unload_all_models()
    t0 = time.time()
    metrics, platform_info = gather_metrics(packed)
    classification = classify_project(client, packed.source_path, platform_info, readme=metrics.readme_excerpt,
                                      file_tree=packed.file_tree)
    report = evaluate(client, packed, str(path), metrics, classification)
    client.unload_all_models()
    exp = report.experience
    text = " ".join([report.verdict_summary, *report.top_solid_features, *report.top_features_needing_work,
                     *[fe.reasoning for fe in report.factor_evaluations.values()],
                     *[s for fe in report.factor_evaluations.values() for s in fe.strengths]])
    results = Counter(d["result"] for d in exp.get("decisions", []))
    return {
        "score": round(report.final_vibe_score, 1), "minutes": round((time.time() - t0) / 60, 1),
        "factors": {k: round(v.score, 1) for k, v in report.factor_evaluations.items()},
        "timing": report.timing_breakdown,
        "code_evidence": exp.get("code_evidence", {}),
        "unsupported": exp.get("unsupported_answers", {}),
        "decisions": dict(results),
        "file_line_references": len(_LOC.findall(text)),
        "complexity": report.complexity,
        "report": {"summary": report.verdict_summary, "solid": report.top_solid_features,
                   "needs_work": report.top_features_needing_work,
                   "factors": {k: v.reasoning for k, v in report.factor_evaluations.items()}},
    }


def _stats(xs):
    return (round(statistics.mean(xs), 1), round(statistics.stdev(xs), 2) if len(xs) > 1 else 0.0) if xs else (0.0, 0.0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--good", type=Path, required=True, help="path to Todd0042/letsvibe-good")
    ap.add_argument("--bad", type=Path, required=True, help="path to Todd0042/letsvibe-bad")
    ap.add_argument("--extra", type=Path, nargs="*", default=[], help="more projects (tiki-karaoke, echoes, deciflux...)")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", type=Path, default=Path("benchmark-v9.json"))
    args = ap.parse_args()

    projects = [("good", args.good), ("bad", args.bad)]
    for p in args.extra:
        label = p.resolve().name
        while label in dict(projects):
            label += "+"
        projects.append((label, p))
    data = {"pipeline": PIPELINE_ID, "runs_per_project": args.runs, "v8_baseline": V8, "projects": {}}
    for label, path in projects:
        runs = []
        for n in range(1, args.runs + 1):
            print(f"{time.strftime('%H:%M:%S')} grading {label} ({path.name}) run {n}/{args.runs}...", flush=True)
            runs.append(grade_once(path))
            r = runs[-1]
            print(f"    -> {r['score']} in {r['minutes']} min; 7B calls {r['timing'].get('calls_7b', 0):g} "
                  f"(avg {r['timing'].get('avg_call_7b_sec', 0)} s), 14B calls {r['timing'].get('calls_14b', 0):g} "
                  f"(avg {r['timing'].get('avg_call_14b_sec', 0)} s)", flush=True)
            data["projects"][label] = {"path": str(path), "runs": runs}
            args.out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")   # keep partial results

    P = data["projects"]
    print("\n=== Results ===")
    for label, p in P.items():
        scores = [r["score"] for r in p["runs"]]
        mean, sd = _stats(scores)
        mins = [r["minutes"] for r in p["runs"]]
        refs = statistics.mean(r["file_line_references"] for r in p["runs"])
        dropped = sum(r["unsupported"].get("code_dropped", 0) for r in p["runs"])
        unverified = sum(r["unsupported"].get("dossier_unverified", 0) for r in p["runs"])
        print(f"{label:24} scores {scores} mean {mean} sd {sd} | minutes {mins} | file/line refs per report {refs:.1f} "
              f"| code answers dropped {dropped}, dossier answers unverified {unverified}")
    g, b = [r["score"] for r in P["good"]["runs"]], [r["score"] for r in P["bad"]["runs"]]
    (gm, gsd), (bm, bsd) = _stats(g), _stats(b)
    spread = (gsd + bsd) / 2 or 0.01
    sep = round((gm - bm) / spread, 1)
    bad_uj = [r["factors"].get("user_journey") for r in P["bad"]["runs"]]
    print(f"\nSeparation (gap / spread): {sep}   v8: {V8['separation']}   -> {'BETTER' if sep > V8['separation'] else 'not better'}")
    print(f"Good {gm} / bad {bm} (v8: {V8['good_mean']} / {V8['bad_mean']}); average spread {round(spread, 2)} (v8: {V8['spread']}, target 2.5 or less)")
    print(f"Bad reference User Journey: {bad_uj}   v8: {V8['bad_user_journey']}")
    slow = [(label, max(r["minutes"] for r in p["runs"])) for label, p in P.items()]
    print("Longest run per project (minutes): " + ", ".join(f"{l} {m}" for l, m in slow))
    print(f"\nEvery run, with timings per call and the written reports: {args.out}")


if __name__ == "__main__":
    main()
