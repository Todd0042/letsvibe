"""Triage of files by role, content checks, naming-interpretation stats and routed chunks."""

from letsvibe.evaluator.prompts import micro_schema
from letsvibe.evaluator.triage import (
    ROLE_FACTORS, build_routed_batches, compatible, content_role, name_role, run_triage,
)

STORY = "\n".join(f"The lighthouse keeper whispered about the storm that swallowed the harbor, line {i}." for i in range(40))
FILES = {
    "main.py": "import pygame\nfrom game import run\nrun()\n",
    "game/engine.py": "def step(state):\n    return state\n",
    "ui/hud.py": "def draw(screen):\n    screen.blit(img, (0,0))\n    pygame.draw.rect(screen)\n    screen.blit(a, b)\n",
    "net/client.py": "import requests\nr = requests.get(url, timeout=5)\nrequests.post(u)\nsocket.socket()\n",
    "setup_wizard.py": "def first_run():\n    print('Welcome')\n",
    "story/chapter1.json": STORY,
    "tests/test_engine.py": "def test_step():\n    assert step(1) == 1\n    assert True\n    assert 2\n",
    "pyproject.toml": "[project]\nname='x'\n",
    "README.md": "# Game\n",
    "helpers.py": "import requests\nrequests.get(a)\nrequests.get(b)\nopen('f')\n",   # named generically, contents say I/O
}


def test_name_rules_and_content_markers():
    assert name_role("ui/hud.py") == "ui" and name_role("net/client.py") == "io"
    assert name_role("setup_wizard.py") == "journey" and name_role("story/chapter1.json") == "content"
    assert name_role("tests/test_engine.py") == "tests" and name_role("pyproject.toml") == "config"
    assert name_role("README.md") == "docs" and name_role("game/engine.py") == "core"
    assert content_role("story/chapter1.json", STORY) == "content"
    assert content_role("tests/test_engine.py", FILES["tests/test_engine.py"]) == "tests"
    assert content_role("helpers.py", FILES["helpers.py"]) == "io"
    assert compatible("journey", "ui") and not compatible("content", "io")


class _Client:
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail = answer, fail

    def generate_json(self, **kw):
        if self.fail:
            raise RuntimeError("model offline")
        return self.answer


def test_triage_rates_naming_and_lets_contents_win():
    paths = sorted(FILES)
    idx = {p: i + 1 for i, p in enumerate(paths)}
    answer = {r: [] for r in ("ui", "journey", "core", "io", "content", "tests", "config", "docs")}
    for p, role in [("main.py", "core"), ("game/engine.py", "core"), ("ui/hud.py", "ui"), ("net/client.py", "io"),
                    ("setup_wizard.py", "journey"), ("story/chapter1.json", "content"), ("tests/test_engine.py", "tests"),
                    ("pyproject.toml", "config"), ("README.md", "docs"), ("helpers.py", "content")]:  # one wrong guess
        answer[role].append(idx[p])
    t = run_triage(_Client(answer), "m", FILES)
    assert t.model_ok and t.roles["ui/hud.py"] == "ui"
    assert t.roles["helpers.py"] == "io"                          # contents overrode the model's name-based guess
    assert ("helpers.py", "content", "io") in t.disagreements
    st = t.stats()
    assert st["files"] == 10 and st["overridden_by_contents"] == 1
    assert 0 < st["model_vs_contents"] < 1 and st["name_rules_vs_contents"] is not None


def test_triage_survives_a_failed_model_call():
    t = run_triage(_Client(fail=True), "m", FILES)
    assert not t.model_ok and t.roles["story/chapter1.json"] == "content" and len(t.roles) == 10


def test_routed_batches_ask_only_role_factors_and_sample_content():
    t = run_triage(_Client(fail=True), "m", FILES)
    batches = build_routed_batches(FILES, FILES, t)
    by_role = {b[3]: b for b in batches}
    assert set(by_role) <= set(ROLE_FACTORS) and "config" not in by_role and "docs" not in by_role
    assert by_role["ui"][2] == ("ui_ux", "user_journey", "functionality")
    assert by_role["content"][2] == ("resonance", "creativity") and "(sample)" in by_role["content"][1]
    assert by_role["tests"][2] == ("code_health",)
    schema = micro_schema(by_role["content"][2], include_malware=False)
    assert set(schema["properties"]["factor_scores"]["required"]) == {"resonance", "creativity"}
    assert "qualifier_scores" not in schema["properties"]


def test_plain_mode_models_get_json_extracted():
    from letsvibe.evaluator.ollama import extract_json, supports_format

    assert not supports_format("gpt-oss:20b") and supports_format("qwen3:8b")
    assert extract_json('Here you go:\n{"ui": [1, 2]}\nHope that helps') == {"ui": [1, 2]}


def test_routed_chunks_are_accepted_when_they_score_only_their_factors():
    from letsvibe.core.models import CORE_FACTORS, DeterministicMetrics
    from letsvibe.evaluator.checklist import CHECKLIST
    from letsvibe.evaluator.consensus import ConsensusEngine
    from letsvibe.evaluator.prompts import SYSTEM_PROMPT_ARCHETYPE, SYSTEM_PROMPT_MACRO_14B, SYSTEM_PROMPT_MICRO_7B

    class Fake:
        last_call = {}
        def list_models(self): return ["m"]
        def unload_all_models(self): return 0
        def generate_json(self, model, system_prompt, user_prompt, format_schema=None, **kw):
            if system_prompt == SYSTEM_PROMPT_ARCHETYPE:
                return {"archetype": {"name": "x"}, "weights": {}, "qualifiers": {"malware_risk": 95}}
            if system_prompt == SYSTEM_PROMPT_MICRO_7B:
                asked = format_schema["properties"]["factor_scores"]["required"]
                return {"detailed_findings": {}, "factor_scores": {f: 70 for f in asked}, "micro_critique_summary": "ok"}
            if system_prompt == SYSTEM_PROMPT_MACRO_14B:
                return {"findings": {}, "factor_scores": {f: 65 for f in CORE_FACTORS}, "qualifier_scores": {"malware_risk": 95},
                        "macro_critique_summary": "fine", "verdict_title": "t"}
            return {f: {q: "yes" for q, _ in qs} for f, qs in CHECKLIST.items()}

    batches = [("UI 1", "--- START FILE: a.html ---\n<div/>", ("ui_ux", "user_journey", "functionality"), "ui"),
               ("Content", "--- START FILE: s.json ---\nstory", ("resonance", "creativity"), "content")]
    rep = ConsensusEngine(client=Fake(), micro_model="m", macro_model="m").evaluate_pipeline(
        "x", "local", batches, DeterministicMetrics(total_loc=10, total_files=2), mode="blend", file_tree="a.html\ns.json")
    micro = rep.perspectives[0].factor_scores
    assert micro["ui_ux"] == 70 and micro["resonance"] == 70 and "operational_safety" not in micro
    assert not any("no usable scores" in i for i in rep.issues_encountered)


def test_licenses_and_readmes_are_never_story_content():
    files = dict(FILES)
    files["LICENSE"] = STORY
    files["packaging/windows/THIRD_PARTY_LICENSES.txt"] = STORY
    files["install.sh"] = "#!/bin/sh\n" + STORY
    t = run_triage(_Client(fail=True), "m", files)
    assert t.roles["LICENSE"] == "docs" and t.roles["packaging/windows/THIRD_PARTY_LICENSES.txt"] == "docs"
    assert t.roles["install.sh"] == "journey" and t.roles["story/chapter1.json"] == "content"


def test_name_filter_skips_docs_config_tests_and_samples_content():
    from letsvibe.evaluator.checklist import CHECKLIST
    from letsvibe.evaluator.triage import select_for_review

    files = dict(FILES)
    files["story/chapter2.txt"] = STORY * 3
    keep, coverage, roles = select_for_review(files)
    assert "README.md" not in keep and "pyproject.toml" not in keep and "tests/test_engine.py" not in keep
    assert keep["ui/hud.py"] == FILES["ui/hud.py"] and "game/engine.py" in keep
    assert "story/chapter2.txt" in keep and "sample only" in keep["story/chapter2.txt"]
    assert coverage["tests"] == (0, 1) and coverage["content"] == (2, 2)
    assert "code_health" not in CHECKLIST and sum(len(q) for q in CHECKLIST.values()) == 23


def test_setup_screens_are_journey_not_packaging():
    assert name_role("setup.py") == "config"
    assert name_role("routes/setup.py") == "journey" and name_role("app/screens/setup_wizard.py") == "journey"
