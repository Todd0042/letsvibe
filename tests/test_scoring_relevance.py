"""Weight-range enforcement, per-batch factor relevance (with failsafes) and 14B input budgeting."""

import pytest

from letsvibe.core.models import CORE_FACTORS, DeterministicMetrics
from letsvibe.evaluator.checklist import CHECKLIST
from letsvibe.evaluator.classifier import Classification
from letsvibe.evaluator.consensus import ConsensusEngine
from letsvibe.evaluator.ollama import CHARS_PER_TOKEN, estimate_tokens, max_ctx_for
from letsvibe.evaluator.prompts import SYSTEM_PROMPT_ARCHETYPE, SYSTEM_PROMPT_MACRO_14B, SYSTEM_PROMPT_MICRO_7B
from letsvibe.evaluator.weights import WEIGHT_RANGE_ROWS, clamp_weights, ranges_for


# ---------------- weights ----------------

def test_clamp_moves_weights_only_to_the_nearest_range_edge():
    # Shaped like the real mx5-dash Stage 1 output: real-world value 5.9% / ops safety 2.4%, far below the row
    observed = {"code_health": 18.8, "functionality": 21.2, "creativity": 16.5, "ui_ux": 15.3,
                "user_journey": 20.0, "operational_safety": 2.4, "resonance": 5.9}
    out, moved = clamp_weights(observed, "embedded")
    _, ranges = ranges_for("embedded")
    assert abs(sum(out.values()) - 100) < 0.2
    for f in CORE_FACTORS:
        assert ranges[f][0] - 0.05 <= out[f] <= ranges[f][1] + 0.05
    assert out["resonance"] == ranges["resonance"][0] and out["operational_safety"] == ranges["operational_safety"][0]  # row minimum, not past it
    assert abs(out["ui_ux"] - observed["ui_ux"]) < 1.5  # in-range values only move slightly to make room


def test_in_range_weights_are_left_alone_and_unknown_categories_use_global_rule():
    label, ranges = WEIGHT_RANGE_ROWS["cli"]
    mid = {f: (lo + hi) / 2 for f, (lo, hi) in ranges.items()}
    scale = 100 / sum(mid.values())
    inside = {f: v * scale for f, v in mid.items()}
    out, moved = clamp_weights(inside, "tool")
    assert moved == [] and all(abs(out[f] - inside[f]) < 0.1 for f in CORE_FACTORS)

    out, _ = clamp_weights({f: 100 / 7 for f in CORE_FACTORS}, "ai")
    assert 18 <= out["resonance"] <= 28 and 5 <= out["operational_safety"] <= 12


def test_prompt_ranges_are_rendered_from_the_table():
    for label, ranges in WEIGHT_RANGE_ROWS.values():
        assert label in SYSTEM_PROMPT_ARCHETYPE
    assert "resonance 18-26, functionality 16-24, user_journey 14-20" in SYSTEM_PROMPT_ARCHETYPE


# ---------------- relevance ----------------

class _Client:
    """Fake models. `micro` is a list of per-batch (ui_ux_score, ui_relevant) pairs."""

    def __init__(self, micro, macro_ui=40.0):
        self.micro, self.macro_ui, self.calls = list(micro), macro_ui, []
        self.last_call = {}

    def list_models(self):
        return ["qwen2.5-coder:7b", "qwen2.5-coder:14b"]

    def unload_all_models(self):
        return 0

    def generate_json(self, model, system_prompt, user_prompt, **kw):
        self.calls.append((system_prompt, user_prompt))
        if system_prompt == SYSTEM_PROMPT_ARCHETYPE:
            return {"archetype": {"name": "Dash"}, "weights": {f: 100 / 7 for f in CORE_FACTORS}, "qualifiers": {"malware_risk": 95}}
        if system_prompt == SYSTEM_PROMPT_MICRO_7B:
            ui, relevant = self.micro.pop(0)
            findings = {f: {"strengths": [], "weaknesses": [], "reasoning": ""} for f in CORE_FACTORS}
            findings["ui_ux"] = {
                "relevant": relevant, "strengths": ["Clear gauge layout"] if relevant else [],
                "weaknesses": ["Gauges flicker on redraw"] if relevant else ["None applicable as this module does not contain any UI/UX code"],
                "reasoning": "",
            }
            return {"detailed_findings": findings, "factor_scores": {**{f: 60.0 for f in CORE_FACTORS}, "ui_ux": ui},
                    "qualifier_scores": {"malware_risk": 95}, "micro_critique_summary": "ok"}
        if system_prompt == SYSTEM_PROMPT_MACRO_14B:
            return {"findings": {f: {"reasoning": "r", "weakness": "N/A"} for f in CORE_FACTORS},
                    "factor_scores": {**{f: 60.0 for f in CORE_FACTORS}, "ui_ux": self.macro_ui},
                    "qualifier_scores": {"malware_risk": 95}, "macro_critique_summary": "Summary.", "verdict_title": "T",
                    "top_solid_features": [], "top_features_needing_work": [], "priority_improvements": []}
        return {f: {q: "na" for q, _ in qs} for f, qs in CHECKLIST.items()}  # checklist: no anchoring


def _run(client, mode="blend", n=3, readme=""):
    batches = [(f"B{i}", "# REPOSITORY DIRECTORY LAYOUT:\nsrc/x.cpp\n--- START FILE: src/x.cpp ---\nint x;") for i in range(n)]
    metrics = DeterministicMetrics(total_loc=10, total_files=1, readme_excerpt=readme)
    cls = Classification(general_category="embedded", platforms=["embedded"], device_profile="ESP32-S3")
    return ConsensusEngine(client=client).evaluate_pipeline("dash", "local", batches, metrics, mode=mode, classification=cls)


def test_batches_without_ui_code_do_not_score_ui():
    # One UI batch scored 80; two BLE/config batches would have dragged it down with 20s
    report = _run(_Client([(80.0, True), (20.0, False), (20.0, False)], macro_ui=80.0))
    micro = report.perspectives[0].factor_scores
    assert micro["ui_ux"] == 80.0
    fe = report.factor_evaluations["ui_ux"]
    assert "Gauges flicker on redraw" in fe.weaknesses
    assert not any("applicable" in w.lower() or w.strip().upper() == "N/A" for w in fe.weaknesses)


def test_irrelevant_batches_cannot_raise_a_score_either():
    # Accuracy, not inflation: a weak UI is not rescued by high scores from non-UI batches
    report = _run(_Client([(30.0, True), (95.0, False), (95.0, False)], macro_ui=30.0))
    assert report.perspectives[0].factor_scores["ui_ux"] == 30.0


def test_failsafe_when_no_batch_has_ui_code():
    report = _run(_Client([(20.0, False)] * 3, macro_ui=10.0))
    assert "ui_ux" not in report.perspectives[0].factor_scores
    assert any("ui_ux: no code batch contained relevant code" in i for i in report.issues_encountered)
    # Whole-repo review decides: a project with no UI code is not rewarded for it
    assert report.factor_evaluations["ui_ux"].score < 40


def test_fast_mode_failsafe_keeps_the_factor():
    report = _run(_Client([(35.0, False)] * 3), mode="fast")
    assert report.perspectives[0].factor_scores["ui_ux"] == 35.0
    assert any("fast mode used all batch scores" in i for i in report.issues_encountered)


# ---------------- 14B budget ----------------

def test_macro_input_is_sized_to_fit_without_client_trimming():
    client = _Client([(60.0, True)] * 3)
    report = _run(client, readme="R" * 20000)
    macro_prompt = next(u for sp, u in client.calls if sp == SYSTEM_PROMPT_MACRO_14B)
    budget = (max_ctx_for("qwen2.5-coder:14b") - estimate_tokens(SYSTEM_PROMPT_MACRO_14B) - 1200 - 64) * CHARS_PER_TOKEN
    assert len(macro_prompt) <= budget
    assert "Pass 1 Module-Level Qualitative Audit Notes" in macro_prompt  # most important sections kept
    assert not any("input trimmed" in i for i in report.issues_encountered)


def test_creativity_is_scored_on_every_batch():
    from letsvibe.evaluator.prompts import LOCALIZED_FACTORS, micro_schema

    assert "creativity" not in LOCALIZED_FACTORS
    assert "relevant" not in micro_schema()["properties"]["detailed_findings"]["properties"]["creativity"]["properties"]


def test_placeholder_findings_are_dropped_but_real_ones_kept():
    from letsvibe.evaluator.consensus import _real_findings

    junk = ["None applicable as this module does not contain any UI/UX code", "N/A", "None detected in this batch",
            "No UI/UX considerations evident from the given files", "No stubbed or incomplete functionality found"]
    real = ["No test files provided", "No explicit bounds checks or timeouts for I/O operations",
            "No implementation of OBD data parsing"]
    assert _real_findings(junk + real) == real


def test_clamped_weights_note_it_in_the_rationale():
    report = _run(_Client([(60.0, True)] * 3))
    assert "adjusted to the rubric" in report.weights.justification


def test_size_is_never_a_quality_signal():
    from letsvibe.evaluator.checklist import CHECKLIST
    from letsvibe.evaluator.prompts import SYSTEM_PROMPT_ARCHETYPE, SYSTEM_PROMPT_MACRO_14B, SYSTEM_PROMPT_MICRO_7B

    for prompt in (SYSTEM_PROMPT_ARCHETYPE, SYSTEM_PROMPT_MICRO_7B, SYSTEM_PROMPT_MACRO_14B):
        assert "SIZE IS NEVER A QUALITY SIGNAL" in prompt and "story" in prompt
    assert "CONTENT CHUNKS" in SYSTEM_PROMPT_MICRO_7B
    questions = " ".join(q for qs in CHECKLIST.values() for _, q in qs).lower()
    for size_word in ("lines of code", "number of files", "too long", "too large", "file size"):
        assert size_word not in questions
