"""
Unit tests for deterministic static analyzers and whole-repo ingestion.
"""

from pathlib import Path
from letsvibe.analyzers.complexity import ComplexityAnalyzer
from letsvibe.analyzers.functionality import FunctionalityAnalyzer
from letsvibe.analyzers.security import SecurityAnalyzer
from letsvibe.core.ingest import RepositoryIngestor
from letsvibe.core.models import CORE_FACTORS, DynamicWeights


def test_dynamic_weights_normalization():
    weights = DynamicWeights(
        maintainability=20.0,
        functionality=20.0,
        creativity=20.0,
        ui_ux=0.0,
        architecture=20.0,
        operational_safety=10.0,
        resonance=30.0,
    )
    normalized = weights.normalize()
    total = sum(getattr(normalized, f) for f in CORE_FACTORS)
    assert round(total) == 100
    assert round(sum(getattr(DynamicWeights(), f) for f in CORE_FACTORS)) == 100
    d = DynamicWeights()
    assert d.resonance == max(getattr(d, f) for f in CORE_FACTORS)


def test_security_scanner_flags_secrets():
    mock_files = {
        # Fake secrets are assembled at run time so the source holds no secret-looking literal for repo scanners
        "src/config.py": "api_key = '" + "abcdef1234567890" * 2 + "'\n" + "pass" + "word = '" + "super_secret_" + "password_123'",
        "src/clean.py": "def add(a, b):\n    return a + b\n",
    }
    count, findings = SecurityAnalyzer.scan(mock_files)
    assert count >= 1
    assert any("API Key" in f or "Password" in f for f in findings)
    # Public reports never reveal where a secret is or any of its characters
    assert not any("config.py" in f or "abcd" in f or "supe" in f for f in findings)


def test_functionality_analyzer_finds_tests_and_infra():
    mock_files = {
        "tests/test_core.py": "def test_ok():\n    assert True\n",
        ".github/workflows/ci.yml": "name: CI\non: [push]\n",
        "Dockerfile": "FROM python:3.11\n",
        "LICENSE": "MIT License\n",
        "README.md": "# Sample Project\n",
    }
    (
        test_count,
        ratio,
        has_ci,
        has_docker,
        has_desktop,
        has_license,
        has_readme,
        readme_excerpt,
    ) = FunctionalityAnalyzer.evaluate(mock_files, total_loc=100)
    assert test_count == 1
    assert has_ci is True
    assert has_docker is True
    assert has_license is True
    assert has_readme is True
    assert has_readme is True


def test_functionality_analyzer_recognizes_android_packaging():
    mock_files = {
        "app/src/main/AndroidManifest.xml": "<manifest package='com.example.app'></manifest>\n",
        "app/build.gradle.kts": "plugins { alias(libs.plugins.android.application) }\n",
    }
    (
        _,
        _,
        _,
        _,
        has_packaging,
        _,
        _,
        _,
    ) = FunctionalityAnalyzer.evaluate(mock_files, total_loc=50)
    assert has_packaging is True


def test_repo_ingestor_on_self():
    repo_root = Path(__file__).parent.parent
    packed = RepositoryIngestor.ingest(str(repo_root))
    assert packed.repo_name == "letsvibe"
    assert len(packed.files) > 0
    assert packed.total_loc > 100
    assert "pyproject.toml" in packed.files or any("cli.py" in p for p in packed.files)
    assert not any(".venv" in p or "venv" in p for p in packed.files)


def test_distill_code_reduces_size_preserving_structure():
    from letsvibe.core.ingest import distill_code

    # Python test
    py_code = """
def long_function(a, b):
    \"\"\"Docstring preserved.\"\"\"
    x = 1
""" + "".join(f"    x += {i}\n" for i in range(50)) + """    return x
"""
    distilled_py = distill_code("sample.py", py_code, max_body_lines=15)
    assert "def long_function(a, b):" in distilled_py
    assert '"""Docstring preserved."""' in distilled_py
    assert "condensed for structural analysis" in distilled_py
    assert "return x" in distilled_py
    assert len(distilled_py.splitlines()) < len(py_code.splitlines())

    # Kotlin/Brace test
    kt_code = """
class TestService {
    fun processItems(items: List<String>): Int {
        var count = 0
""" + "".join(f"        count += {i}\n" for i in range(50)) + """        return count
    }
}
"""
    distilled_kt = distill_code("TestService.kt", kt_code, max_body_lines=15)
    assert "fun processItems(items: List<String>): Int {" in distilled_kt
    assert "condensed for structural analysis" in distilled_kt
    assert "return count" in distilled_kt
    assert len(distilled_kt.splitlines()) < len(kt_code.splitlines())



def test_score_validation_helpers_fail_closed():
    from letsvibe.core.models import CORE_FACTORS
    from letsvibe.evaluator.consensus import _has_all_scores
    from letsvibe.evaluator.prompts import macro_schema, micro_schema

    full = {"factor_scores": {f: 70 for f in CORE_FACTORS}}
    assert _has_all_scores(full)
    assert not _has_all_scores({})
    assert not _has_all_scores({"factor_scores": {"maintainability": 70}})
    assert not _has_all_scores({"factor_scores": {**full["factor_scores"], "ui_ux": "n/a"}})
    for schema in (micro_schema(), macro_schema()):
        assert set(schema["properties"]["factor_scores"]["required"]) == set(CORE_FACTORS)
    # A free-form {"response": "..."} refusal must never count as a scored result
    assert not _has_all_scores({"response": "I'm sorry, but I can't assist with that request."})


def test_letter_grades_are_fixed_bands():
    from letsvibe.evaluator.consensus import calculate_letter_grade as g

    # No curve: the standard US bands (90s A, 80s B, 70s C, 60s D, below 60 F), the same for every project
    assert g(97) == "A+" and g(93) == "A" and g(90) == "A-" and g(89.9) == "B+" and g(83) == "B" and g(80) == "B-"
    assert g(77) == "C+" and g(73) == "C" and g(70) == "C-" and g(67) == "D+" and g(63) == "D" and g(60) == "D-"
    assert g(59.9) == "F" and g(0) == "F"
    grades = [g(x) for x in range(0, 101)]
    order = ["F", "D-", "D", "D+", "C-", "C", "C+", "B-", "B", "B+", "A-", "A", "A+"]
    assert [order.index(x) for x in grades] == sorted(order.index(x) for x in grades)  # monotonic


def test_checklist_scoring_is_anchored_and_explainable():
    from letsvibe.evaluator.checklist import CHECKLIST, checklist_schema, score_checklist

    all_yes = {f: {q: "yes" for q, _ in qs} for f, qs in CHECKLIST.items()}
    all_no = {f: {q: "no" for q, _ in qs} for f, qs in CHECKLIST.items()}
    s_yes, f_yes = score_checklist(all_yes)
    s_no, f_no = score_checklist(all_no)
    assert set(s_yes) == set(CHECKLIST) and all(v == 95.0 for v in s_yes.values())
    assert all(v == 35.0 for v in s_no.values())
    assert all(f_yes[f] == [] for f in CHECKLIST)
    assert all(len(f_no[f]) == len(CHECKLIST[f]) for f in CHECKLIST)

    mixed = {f: {q: ("yes" if i % 2 == 0 else "no") for i, (q, _) in enumerate(qs)} for f, qs in CHECKLIST.items()}
    s_mixed, _ = score_checklist(mixed)
    assert all(35.0 < v < 95.0 for v in s_mixed.values())

    # too few answerable questions -> factor omitted; garbage -> empty
    assert score_checklist({"ui_ux": {"has_interface": "yes"}})[0] == {}
    assert score_checklist("nonsense") == ({}, {})
    assert set(checklist_schema()["required"]) == set(CHECKLIST)


def test_fit_prompt_never_drops_system_prompt():
    from letsvibe.evaluator.ollama import TRIM_MARKER, estimate_tokens, fit_prompt

    system = "S" * 6000  # ~2000 tokens
    user = "u" * 60000  # ~20000 tokens: far larger than the window
    fitted, num_ctx, trimmed = fit_prompt(system, user, max_ctx=6144, max_output_tokens=1200)
    assert trimmed and fitted.endswith(TRIM_MARKER) and fitted.startswith("u")
    assert num_ctx <= 6144
    assert estimate_tokens(system) + estimate_tokens(fitted) + 1200 <= num_ctx

    small, small_ctx, small_trimmed = fit_prompt("sys", "hello", max_ctx=12288, max_output_tokens=500)
    assert small == "hello" and not small_trimmed and small_ctx == 12288  # fixed window per model (no reloads)


class _ScriptedClient:
    """Fake Ollama client: answers by system prompt, refusing like a truncated model if told to."""

    def __init__(self, refuse_micro=False):
        self.refuse_micro = refuse_micro
        self.last_call = {}

    def list_models(self):
        return ["qwen2.5-coder:7b", "qwen2.5-coder:14b"]

    def unload_all_models(self):
        return 0

    def generate_json(self, model, system_prompt, user_prompt, **kw):
        from letsvibe.core.models import CORE_FACTORS
        from letsvibe.evaluator.checklist import CHECKLIST
        from letsvibe.evaluator.prompts import SYSTEM_PROMPT_ARCHETYPE, SYSTEM_PROMPT_MACRO_14B, SYSTEM_PROMPT_MICRO_7B

        scores = {f: 70.0 for f in CORE_FACTORS}
        if system_prompt == SYSTEM_PROMPT_ARCHETYPE:
            return {"archetype": {"name": "Demo", "category": "Embedded Dash"}, "weights": {}, "qualifiers": {"malware_risk": 95}}
        if system_prompt == SYSTEM_PROMPT_MICRO_7B:
            if self.refuse_micro:
                return {"response": "I'm sorry, but I can't assist with that request."}
            return {"detailed_findings": {}, "factor_scores": scores, "qualifier_scores": {"malware_risk": 95}, "micro_critique_summary": "ok"}
        if system_prompt == SYSTEM_PROMPT_MACRO_14B:
            return {
                "findings": {f: {"reasoning": f"{f} reasoning", "weakness": "None"} for f in CORE_FACTORS},
                "factor_scores": scores, "qualifier_scores": {"malware_risk": 95},
                "macro_critique_summary": "A real summary.", "verdict_title": "Solid Dash",
                "top_solid_features": ["X: works"], "top_features_needing_work": ["Y: tests"], "priority_improvements": [],
            }
        return {f: {q: "yes" for q, _ in qs} for f, qs in CHECKLIST.items()}


def _run_engine(client):
    from letsvibe.core.models import DeterministicMetrics
    from letsvibe.evaluator.consensus import ConsensusEngine

    batches = [(f"Batch {i}", "# REPOSITORY DIRECTORY LAYOUT:\nsrc/a.cpp\n--- START FILE: src/a.cpp ---\nint main(){}") for i in range(4)]
    metrics = DeterministicMetrics(total_loc=10, total_files=1, languages={"C++": 10})
    return ConsensusEngine(client=client).evaluate_pipeline("demo", "local", batches, metrics, mode="blend")


def test_pipeline_fails_closed_when_micro_batches_refuse():
    import pytest
    from letsvibe.evaluator.consensus import IncompleteEvaluationError

    with pytest.raises(IncompleteEvaluationError, match="0/4 code batches"):
        _run_engine(_ScriptedClient(refuse_micro=True))


def test_pipeline_surfaces_macro_descriptions():
    report = _run_engine(_ScriptedClient())
    assert report.verdict_summary == "A real summary."
    assert report.factor_evaluations["user_journey"].reasoning == "user_journey reasoning"
    assert not any("returned no usable scores" in i for i in report.issues_encountered)


def test_pipeline_uses_given_classification():
    from letsvibe.evaluator.classifier import Classification

    seen = []

    class Spy(_ScriptedClient):
        def generate_json(self, model, system_prompt, user_prompt, **kw):
            seen.append(user_prompt)
            return super().generate_json(model, system_prompt, user_prompt, **kw)

    from letsvibe.core.models import DeterministicMetrics
    from letsvibe.evaluator.consensus import ConsensusEngine

    batches = [("Batch 1", "# REPOSITORY DIRECTORY LAYOUT:\nsrc/main.cpp\n--- START FILE: src/main.cpp ---\nint main(){}")]
    cls = Classification(general_category="embedded", platforms=["embedded"], device_profile="ESP32-S3, 480x480 display")
    report = ConsensusEngine(client=Spy()).evaluate_pipeline(
        "dash", "local", batches, DeterministicMetrics(total_loc=10, total_files=1), mode="blend", classification=cls,
    )
    assert report.archetype.general_category == "embedded" and report.archetype.platforms == ["embedded"]
    # Stage 1 and every grader saw the device profile
    assert all("ESP32-S3" in p for p in seen[:3])
