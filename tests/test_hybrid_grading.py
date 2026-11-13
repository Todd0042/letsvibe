"""Rubric v9 (hybrid grading): per-factor code evidence, quote checks against real files, item-by-item comparison with
the dossier, conflict checks, the time limit, the public complexity description, and a full run with a fake model."""
import re
import subprocess
import time

import pytest

from letsvibe.core.models import FunctionComplexity
from letsvibe.evaluator.experience import TimeLimitExceeded, _CallLog, code_evidence, complexity, reconcile
from letsvibe.evaluator.ollama import CpuSpilloverError

FILES = {
    "app.py": "from fastapi import FastAPI\napp = FastAPI()\n\n@app.post('/api/save')\ndef save(item):\n"
              "    db.write(item)\n    return {'message': 'Saved! Your list is safe.'}\n",
    "templates/index.html": "<html><body>\n<button aria-label=\"Add a task\">Add</button>\n"
                            "<div class=\"spinner\">Loading your tasks...</div>\n</body></html>\n",
    "engine/rules.py": "def score(cards):\n    return sum(c.value for c in cards if c.face_up)\n",
}


def _chunk(*paths):
    return "".join(f"\n--- START FILE: {p} ---\n{FILES[p]}\n--- END FILE: {p} ---\n" for p in paths)


# ---- routing -----------------------------------------------------------------------------------

def test_each_chunk_goes_only_to_the_factors_it_can_inform():
    batches = [("ui", _chunk("templates/index.html")), ("logic", _chunk("engine/rules.py")), ("entry", _chunk("app.py"))]
    r = code_evidence.route(batches, FILES)
    assert 0 in r["ui_ux"] and 0 in r["journey"]
    assert 1 not in r["ui_ux"] and 1 not in r["journey"]           # pure logic: no screens, no setup
    assert 1 in r["functionality"] and 1 in r["creativity"]
    assert 2 in r["journey"]                                        # the entry point is where launch happens
    assert "value" not in r                                         # real-world value is never judged chunk by chunk


def test_one_factor_per_session_and_schemas_hold_only_that_factor():
    seen = []

    def fake(**kw):
        seen.append(set(kw["format_schema"]["properties"]))
        return {}

    batches = [("all", _chunk("app.py", "templates/index.html", "engine/rules.py"))]
    res = code_evidence.run(fake, batches, FILES)
    assert len(seen) == 4 and res.calls == 4
    for props in seen:
        assert props in [set(v) for v in code_evidence.CODE_FIELDS.values()]
    assert all(m == code_evidence.MODEL for m in [code_evidence.MODEL])


# ---- quote checks ------------------------------------------------------------------------------

def test_quotes_must_be_real_lines_of_the_real_file():
    cands = ["app.py", "templates/index.html"]
    hit = code_evidence.locate("return {'message': 'Saved! Your list is safe.'}", "app.py", FILES, cands)
    assert hit == ("app.py", 7, "return {'message': 'Saved! Your list is safe.'}")
    assert code_evidence.locate("show_toast('Saved successfully')", "app.py", FILES, cands) is None   # invented
    assert code_evidence.locate("app", "app.py", FILES, cands) is None                                 # too short
    # the right line is found even when the model names the wrong file
    assert code_evidence.locate('aria-label="Add a task"', "app.py", FILES, cands)[0] == "templates/index.html"


def test_answers_without_real_quotes_are_dropped():
    raw = {"confirms_success": {"answer": "CLEARLY", "evidence": [{"file": "app.py", "quote": "'Saved! Your list is safe.'"}]},
           "tells_user_about_problems": {"answer": "CLEARLY", "evidence": [{"file": "app.py", "quote": "alert('Oops, try again')"}]},
           "core_flow": {"answer": "NOT_IN_THIS_CODE", "evidence": []}}
    out = code_evidence.check(raw, "functionality", FILES, ["app.py"])
    assert out["confirms_success"]["verified"] and out["confirms_success"]["evidence"][0]["line"] == 7
    assert not out["tells_user_about_problems"]["verified"]
    assert "core_flow" not in out


def test_combining_chunks_uses_the_majority_of_checked_answers():
    ev = [{"file": "a", "line": 1, "quote": "x"}]
    chunks = [{"clarity": {"answer": "CLEAR", "verified": True, "evidence": ev}},
              {"clarity": {"answer": "CLEAR", "verified": True, "evidence": ev}},
              {"clarity": {"answer": "MIXED", "verified": True, "evidence": ev + ev}},
              {"clarity": {"answer": "CLUTTERED_OR_TECHNICAL", "verified": False, "evidence": []}}]
    out = code_evidence.combine("ui_ux", chunks)
    assert out["clarity"]["answer"] == "CLEAR" and out["clarity"]["chunks"] == 3


def test_booleans_come_back_as_booleans():
    ev = [{"file": "a", "line": 1, "quote": "x"}]
    out = code_evidence.combine("creativity", [{"has_distinct_voice": {"answer": True, "verified": True, "evidence": ev}}])
    assert out["has_distinct_voice"]["answer"] is True


# ---- comparing with the dossier ----------------------------------------------------------------

DOSSIER = "# App dossier\n## Screens\n- 1 screen: index.html\n## Feedback\n- Success messages found: 0\n- Plain wording\n"
CODE_EV = [{"file": "app.py", "line": 7, "quote": "return {'message': 'Saved! Your list is safe.'}"}]


def _results(**func):
    base = {"value": {}, "creativity": {}, "journey": {}, "ui_ux": {}, "functionality": {}}
    base["functionality"].update(func)
    return base


def test_code_evidence_fills_what_the_dossier_could_not_show():
    results = _results(confirms_success={"answer": "NOT_SHOWN_IN_DOSSIER", "evidence": [], "verified": False})
    code = {"functionality": {"confirms_success": {"answer": "CLEARLY", "evidence": CODE_EV}}}
    merged, decisions = reconcile.merge(results, code, DOSSIER, FILES)
    assert merged["functionality"]["confirms_success"]["answer"] == "CLEARLY"
    assert "app.py line 7" in merged["functionality"]["confirms_success"]["evidence"][0]
    assert decisions[0]["result"] == "code fills a gap"


def test_agreement_keeps_the_answer_and_adds_the_code_location():
    results = _results(confirms_success={"answer": "CLEARLY", "evidence": ["Success messages found"], "verified": True})
    code = {"functionality": {"confirms_success": {"answer": "CLEARLY", "evidence": CODE_EV}}}
    merged, decisions = reconcile.merge(results, code, DOSSIER, FILES)
    assert merged["functionality"]["confirms_success"]["source"] == "both"
    assert decisions[0]["result"] == "agree"


def _conflict(reply):
    results = _results(confirms_success={"answer": "SOMETIMES", "evidence": ["Success messages found: 0"], "verified": True})
    code = {"functionality": {"confirms_success": {"answer": "CLEARLY", "evidence": CODE_EV}}}
    prompts = []

    def fake(**kw):
        prompts.append(kw["user_prompt"])
        return reply

    merged, decisions = reconcile.merge(results, code, DOSSIER, FILES, generate=fake, model="qwen2.5-coder:14b")
    return merged["functionality"]["confirms_success"], decisions[0], prompts


def test_conflicts_are_settled_by_rereading_the_quoted_code():
    ans, decision, prompts = _conflict({"supported": "B", "deciding_quote": "return {'message': 'Saved! Your list is safe.'}",
                                        "reason": "The save route returns a success message."})
    assert ans["answer"] == "CLEARLY" and ans["source"] == "conflict: code"
    assert decision["result"] == "conflict -> code" and "Saved!" in decision["deciding_quote"]
    # the judge saw the actual lines around the quote, with line numbers, and both claims
    assert "    7  " in prompts[0] and "CLAIM A" in prompts[0] and "CLAIM B" in prompts[0]


def test_an_invented_deciding_quote_leaves_the_dossier_answer():
    ans, decision, _ = _conflict({"supported": "B", "deciding_quote": "toast.success('All done')", "reason": "x"})
    assert ans["answer"] == "SOMETIMES" and decision["result"] == "conflict -> undecided"


def test_neither_claim_holding_up_counts_as_not_shown():
    ans, decision, _ = _conflict({"supported": "NEITHER", "deciding_quote": "", "reason": "No message is shown on screen."})
    assert ans["answer"] == "NOT_SHOWN_IN_DOSSIER"


# ---- time limit --------------------------------------------------------------------------------

def test_the_time_limit_stops_everything_and_is_never_swallowed():
    log = _CallLog(lambda **kw: {"ok": 1}, deadline=time.monotonic() - 1)
    with pytest.raises(TimeLimitExceeded):
        log(model="qwen2.5-coder:7b")
    assert issubclass(TimeLimitExceeded, CpuSpilloverError)   # every stage re-raises these
    ok = _CallLog(lambda **kw: {"ok": 1}, deadline=time.monotonic() + 60)
    ok(model="qwen2.5-coder:7b"), ok(model="qwen2.5-coder:7b"), ok(model="qwen2.5-coder:14b")
    s = ok.summary()
    assert s["calls_7b"] == 2 and s["calls_14b"] == 1 and "avg_call_7b_sec" in s


# ---- complexity --------------------------------------------------------------------------------

def test_complexity_is_described_but_never_graded():
    class M:
        avg_cyclomatic_complexity = 4.99
        max_cyclomatic_complexity = 69
        top_complex_functions = [FunctionComplexity(name="get_next_track", filename="database.py", nloc=117, cyclomatic_complexity=69)]

    out = complexity.describe(M(), [{"kind": "oversized file"}, {"kind": "duplicated code"}, {"kind": "swallowed error"}], 39866)
    assert out["graded"] is False
    assert "5.0 paths per function" in out["summary"] and "69 paths" in out["summary"]
    assert "get_next_track in database.py: 69 paths over 117 lines" in out["details"]
    assert any("1 very large file in" in d for d in out["details"])
    assert not any("swallowed" in d for d in out["details"])     # other code-health notes stay owner-only


# ---- a full run with a fake model ----------------------------------------------------------------

class FakeClient:
    host = "http://127.0.0.1:9"   # nothing listens: the optional app-type check is skipped

    def __init__(self):
        self.models = []

    def unload_all_models(self):
        return 0

    def generate_json(self, model, system_prompt, user_prompt, format_schema=None, max_output_tokens=1024, temperature=0.2, **kw):
        self.models.append(model)
        props = (format_schema or {}).get("properties", {})
        if "text" in props:
            return {"text": "It saves your list and tells you when it worked."}
        if "supported" in props:
            return {"supported": "NEITHER", "deciding_quote": "", "reason": "unclear"}
        code_lines = [l for l in re.findall(r"^(?!--- )(.{12,})$", user_prompt, re.M) if "def " in l or "return" in l or "<" in l]
        out = {}
        for k, spec in props.items():
            ans = spec.get("properties", {}).get("answer", {}) if spec.get("type") == "object" else {}
            choices = ans.get("enum") or ([True] if ans.get("type") == "boolean" else ["x"])
            ev_spec = spec.get("properties", {}).get("evidence", {}).get("items", {})
            if ev_spec.get("type") == "object":       # code evidence: quote a real line
                ev = [{"file": "app.py", "quote": code_lines[0].strip()}] if code_lines else []
            else:
                ev = ["App dossier"]
            if spec.get("type") == "array":
                out[k] = []
            else:
                out[k] = {"answer": choices[0], "evidence": ev}
        return out


@pytest.fixture()
def small_app(tmp_path):
    repo = tmp_path / "small-app"
    (repo / "templates").mkdir(parents=True)
    for p, c in FILES.items():
        (repo / p).parent.mkdir(parents=True, exist_ok=True)
        (repo / p).write_text(c)
    (repo / "README.md").write_text("# Small App\nA tiny to-do list that saves your tasks.\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    return repo


def test_full_v9_run_with_a_fake_model(small_app):
    from letsvibe.core.ingest import RepositoryIngestor
    from letsvibe.evaluator.classifier import Classification
    from letsvibe.evaluator.pipeline import PIPELINE_ID, evaluate, gather_metrics

    packed = RepositoryIngestor.ingest(str(small_app))
    metrics, _ = gather_metrics(packed)
    client = FakeClient()
    report = evaluate(client, packed, str(small_app), metrics, Classification(general_category="productivity", platforms=["web"]))
    assert PIPELINE_ID == "v9-hybrid" and report.rubric_version == "v9"
    assert report.experience["rubric_id"] == "v9-hybrid"
    assert client.models.index("qwen2.5-coder:7b") < client.models.index("qwen2.5-coder:14b")   # all 7B work first
    ce = report.experience["code_evidence"]
    assert ce["calls"] >= 4 and ce["chunks"] >= 1
    assert report.experience["decisions"]                    # every comparison is recorded
    assert report.complexity.get("graded") is False and report.complexity.get("summary")
    assert set(report.factor_evaluations) == {"resonance", "creativity", "user_journey", "functionality", "ui_ux"}
    assert 0 <= report.final_vibe_score <= 100
    assert report.timing_breakdown["calls_7b"] == ce["calls"] and "total_sec" in report.timing_breakdown
