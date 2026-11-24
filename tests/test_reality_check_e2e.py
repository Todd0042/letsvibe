"""E2E tests for LetsVibe: Reality Check (LetsVibeRC).

Round-robin GPU turns are covered by gpu-scheduler/tests/e2e_rotation.py.
"""

from __future__ import annotations

import time
from typing import List

from fastapi.testclient import TestClient

from letsvibe.reality_check import evaluator as rc_eval
from letsvibe.reality_check import routes as rc_routes
from letsvibe.web.server import app


def test_reality_check_e2e_flow_host_routing_and_xss(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(rc_routes, "_store", rc_routes.RealityCheckStore(str(tmp_path / "rc.db")))
    monkeypatch.setattr(rc_routes, "_RATE_MAX_HITS", 20)

    def fake_grounding(pitch: str, platform: str = "Web", twist: str = ""):
        return {
            "keywords": ["todo", "task"],
            "platform": platform,
            "github_total_count": 48000,
            "github_top_repos": [{"full_name": "example/todo", "stars": 12000, "language": "TS", "description": "Todo app", "url": ""}],
            }

    captured_prompts: List[str] = []

    def fake_llama(system_prompt: str, user_prompt: str, max_tokens: int = 2000, **_options):
        captured_prompts.append(user_prompt)
        return {
            "verdict": "DO NOT BUILD",
            "headline": "Crowded TODO Wrapper With Zero Switching Incentive",
            "viability_score": 1.8,
            "saturation_pct": 97.0,
            "demand_tier": "Wrapper Slop",
            "wasted_hours_estimate": "40-80 hrs",
            "wasted_tokens_estimate": "~250k tokens",
            "executive_summary": "Thousands of free TODO apps already exist.",
            "dimensions": {
                "market_saturation": {"label": "Market Saturation & Crowding", "score": 0.5, "weight": 25, "reasoning": "48k GitHub repos already exist."},
                "switching_friction": {"label": "Switching Motivation", "score": 1.0, "weight": 20, "reasoning": "Nobody migrates tasks for dark mode."},
                "niche_moat": {"label": "Niche Twist & Moat", "score": 1.5, "weight": 20, "reasoning": "Trivial feature."},
                "real_demand": {"label": "Real Demand & Distribution", "score": 2.5, "weight": 15, "reasoning": "No distribution edge."},
                "effort_vs_payoff": {"label": "Build Effort vs. Payoff", "score": 3.0, "weight": 10, "reasoning": "Low payoff."},
                "wrapper_risk": {"label": "Anti-Slop / Originality", "score": 2.0, "weight": 10, "reasoning": "Standard CRUD."},
            },
            "existing_competitors": [{"name": "Todoist", "type": "Paid SaaS", "why_it_wins": "15 years of polish."}],
            "fatal_flaws": ["Extreme saturation", "<script>alert(1)</script>"],
            "redeeming_angles": ["Good weekend learning exercise"],
            "pivots": [{"title": "Target a regulated industry checklist", "impact": "high", "effort": "medium", "why_it_matters": "Compliance creates real demand", "how": "Build offline audit trails"}],
            "pricing_recommendation": {"suggested_price": "Do Not Monetize", "model": "Do Not Monetize", "reasoning": "Free incumbents already cover this."},
            "marketing_playbook": {
                "difficulty": "Brutal",
                "where_buyers_hang_out": ["r/productivity"],
                "biggest_distribution_trap": "Posting to dev forums instead of actual buyers.",
                "distribution_channels": [{"channel": "None", "why": "Saturated", "first_step": "Skip building."}],
            },
            "liability_and_entity": {
                "recommendation": "Personal / Indie",
                "risk_level": "Low",
                "reasoning": "Harmless local app if not monetized.",
                "must_have_protections": ["AS-IS Terms of Service disclaimer"],
            },
        }

    monkeypatch.setattr(rc_eval, "gather_market_grounding", fake_grounding)
    monkeypatch.setattr(rc_eval, "ensure_llama_awake", lambda **_: {"ok": True, "state": "awake"})
    monkeypatch.setattr(rc_eval, "_call_shared_llama", fake_llama)

    client = TestClient(app)

    # Host-routed letsviberc.todd0042.dev serves Reality Check at "/"
    home_res = client.get("/", headers={"host": "letsviberc.todd0042.dev"})
    assert home_res.status_code == 200
    assert "LetsVibe: Reality Check" in home_res.text
    assert "https://letsvibe.todd0042.dev" in home_res.text
    assert 'value="whisper">Whisper Sweet Nothings<' in home_res.text

    # Main letsvibe dashboard links to Reality Check in nav and footer
    main_res = client.get("/", headers={"host": "letsvibe.todd0042.dev"})
    assert main_res.status_code == 200
    assert "https://letsviberc.todd0042.dev" in main_res.text

    # Submit realistic idea pitch
    sub = client.post(
        "/api/rc/evaluate",
        json={
            "pitch": "An AI-powered daily task manager with dark mode <script>alert(1)</script>",
            "platform": "Web",
            "twist": "Dark mode + AI reminders",
            "tone": "brutal",
        },
    )
    assert sub.status_code == 200
    job_id = sub.json()["job_id"]

    report_id = None
    for _ in range(30):
        jr = client.get(f"/api/rc/job/{job_id}").json()
        if jr["status"] == "completed":
            report_id = jr["report_id"]
            assert jr["report"]["verdict"] == "DO NOT BUILD"
            assert jr["report"]["saturation_pct"] == 97.0
            break
        time.sleep(0.05)
    assert report_id

    # Shareable page and standalone HTML export escape XSS
    share = client.get(f"/r/{report_id}")
    assert share.status_code == 200
    assert "Crowded TODO Wrapper" in share.text

    dl = client.get(f"/api/rc/report/{report_id}/html")
    assert dl.status_code == 200
    assert "<script>alert(1)</script>" not in dl.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in dl.text

    # Whisper Sweet Nothing tone routes through evaluator and stores tone="whisper"
    whisper_sub = client.post(
        "/api/rc/evaluate",
        json={
            "pitch": "An AI-powered daily task manager with dark mode",
            "platform": "Web",
            "twist": "Encouraging vibes",
            "tone": "whisper",
        },
    )
    assert whisper_sub.status_code == 200
    whisper_job_id = whisper_sub.json()["job_id"]
    whisper_report = None
    for _ in range(30):
        jr = client.get(f"/api/rc/job/{whisper_job_id}").json()
        if jr["status"] == "completed":
            whisper_report = jr["report"]
            break
        time.sleep(0.05)
    assert whisper_report is not None
    assert whisper_report["tone"] == "whisper"
    assert any("WHISPER SWEET NOTHINGS" in p for p in captured_prompts)


def test_idea_finder_recommends_the_first_sellable_idea(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LETSVIBE_RC_IDEA_HISTORY_PATH", str(tmp_path / "idea_hist.json"))
    monkeypatch.setattr(rc_routes, "_store", rc_routes.RealityCheckStore(str(tmp_path / "rc_if.db")))
    monkeypatch.setattr(rc_routes, "_RATE_MAX_HITS", 20)
    monkeypatch.setattr(rc_eval, "gather_market_grounding", lambda pitch, platform="Web", twist="": {"query": "test", "github_top_repos": [], "app_store_hits": []})
    monkeypatch.setattr(rc_eval, "ensure_llama_awake", lambda **_: {"ok": True, "state": "awake"})

    from letsvibe.reality_check import routes as r_mod

    monkeypatch.setattr(
        r_mod,
        "generate_idea_batch_llama",
        lambda sp, up, job_id="": {
            "candidates": [
                {"title": "Cliché Wrapper", "pitch": "A generic AI note taker for everyone online.", "twist": "Dark mode", "target_buyer": "Anyone", "why_monetizable": "Subs"},
                {"title": "Offline Track Telemetry", "pitch": "An offline laptop pit-lane lap & tire-temp logger for Miata track-day drivers.", "twist": "Local Wi-Fi OBD2", "target_buyer": "Track-day drivers", "why_monetizable": "Replaces $49/mo cloud"},
                {"title": "Backup Idea", "pitch": "Backup niche tool for local woodworking shops.", "twist": "Offline", "target_buyer": "Woodworkers", "why_monetizable": "One-time"},
            ]
        },
    )

    gate_calls = {"n": 0}

    def fake_brutal(system_prompt: str, user_prompt: str, max_tokens: int = 4096, **_options):
        gate_calls["n"] += 1
        if gate_calls["n"] == 1:
            return {
                "verdict": "DO NOT BUILD",
                "headline": "Generic wrapper slop",
                "viability_score": 2.0,
                "saturation_pct": 90.0,
                "demand_tier": "Wrapper Slop",
                "dimensions": {"market_saturation": {"label": "Sat", "score": 2.0, "weight": 100, "reasoning": "Bad"}},
                "pricing_recommendation": {"suggested_price": "Do Not Monetize", "model": "Do Not Monetize", "reasoning": "No"},
            }
        return {
            "verdict": "NICHE ONLY",
            "headline": "Focused offline pit-lane logger",
            "viability_score": 6.4,
            "saturation_pct": 20.0,
            "demand_tier": "Underserved Pain",
            "dimensions": {"market_saturation": {"label": "Sat", "score": 6.4, "weight": 100, "reasoning": "Good"}},
            "pricing_recommendation": {"suggested_price": "$49 one-time", "model": "One-Time License", "reasoning": "Fair"},
        }

    monkeypatch.setattr(rc_eval, "_call_shared_llama", fake_brutal)
    client = TestClient(app)
    sub = client.post("/api/rc/find-idea", json={"interests": "", "platform": "Desktop"})
    assert sub.status_code == 200
    job_id = sub.json()["job_id"]

    res = None
    for _ in range(30):
        jr = client.get(f"/api/rc/job/{job_id}").json()
        if jr["status"] == "completed":
            res = jr
            break
        time.sleep(0.05)
    assert res is not None
    fr = res["finder_result"]
    assert fr["passed_reality_check"] is True
    assert fr["cycles_used"] == 2
    assert len(fr["attempts"]) == 2
    assert fr["attempts"][0]["passed"] is False
    assert fr["attempts"][1]["passed"] is True
    assert fr["selected_idea"]["title"] == "Offline Track Telemetry"
    # NICHE ONLY at $49 one-time: sellable, labelled as niche, in the report the UI shows
    assert fr["attempts"][1]["sellability"]["label"] == "Sellable: niche only"
    assert res["report"]["sellability"]["sellable"] is True
    assert (tmp_path / "idea_hist.json").exists()


def test_reality_check_e2e_github_rate_limit_graceful_fallback(monkeypatch, tmp_path) -> None:
    from letsvibe.core.github_client import GitHubResponse, clear_github_cache
    from letsvibe.reality_check import grounding as rc_grounding

    clear_github_cache()
    monkeypatch.setattr(rc_routes, "_store", rc_routes.RealityCheckStore(str(tmp_path / "rc_rl.db")))
    monkeypatch.setattr(rc_routes, "_RATE_MAX_HITS", 20)
    monkeypatch.setattr(rc_eval, "ensure_llama_awake", lambda **_: {"ok": True, "state": "awake"})
    monkeypatch.setattr(
        rc_grounding,
        "github_get_json",
        lambda url, timeout=8.0, cache_ttl=900.0: GitHubResponse(
            ok=False,
            status_code=403,
            data=None,
            rate_limited=True,
            reset_in_sec=45,
            error="GitHub API rate limit exceeded (resets in ~45s).",
        ),
    )
    monkeypatch.setattr(rc_grounding, "_fetch_app_store", lambda short_q, platform, keywords: {"app_store_hits": []})

    captured_prompts: List[str] = []

    def fake_llama(system_prompt: str, user_prompt: str, max_tokens: int = 4096, **_options):
        captured_prompts.append(user_prompt)
        return {
            "verdict": "NICHE ONLY",
            "headline": "Rate-limit resilient check",
            "viability_score": 5.5,
            "saturation_pct": 45.0,
            "demand_tier": "Underserved Pain",
            "dimensions": {"market_saturation": {"label": "Sat", "score": 5.5, "weight": 100, "reasoning": "Fallback"}},
            "pricing_recommendation": {"suggested_price": "$19", "model": "One-Time License", "reasoning": "Fair"},
        }

    monkeypatch.setattr(rc_eval, "_call_shared_llama", fake_llama)
    client = TestClient(app)
    sub = client.post("/api/rc/evaluate", json={"pitch": "Offline track telemetry logger for Miata drivers", "platform": "Desktop"})
    assert sub.status_code == 200
    job_id = sub.json()["job_id"]

    res = None
    for _ in range(30):
        jr = client.get(f"/api/rc/job/{job_id}").json()
        if jr["status"] == "completed":
            res = jr
            break
        time.sleep(0.05)
    assert res is not None
    assert len(captured_prompts) == 1
    assert "rate-limited" in captured_prompts[0].lower()
    assert "no widely-known >=500-star github" not in captured_prompts[0].lower()


def test_idea_finder_and_reality_check_agree_daily_bark(monkeypatch, tmp_path) -> None:
    """The approvers' case: Idea Finder recommended "Daily Bark", then Reality Check scored the same idea 3.5 (Do
    Not Build). Now Idea Finder's gate IS Reality Check (same prompt, temperature 0, same text): an idea that isn't
    sellable is never recommended, and checking it in the Reality Check tab returns that same report."""
    monkeypatch.setenv("LETSVIBE_RC_IDEA_HISTORY_PATH", str(tmp_path / "idea_hist.json"))
    monkeypatch.setattr(rc_routes, "_store", rc_routes.RealityCheckStore(str(tmp_path / "rc_bark.db")))
    monkeypatch.setattr(rc_routes, "_RATE_MAX_HITS", 20)
    monkeypatch.setattr(rc_eval, "gather_market_grounding", lambda pitch, platform="Web", twist="": {"query": "dog", "github_top_repos": [], "app_store_hits": []})
    monkeypatch.setattr(rc_eval, "ensure_llama_awake", lambda **_: {"ok": True, "state": "awake"})
    monkeypatch.setattr(rc_routes, "generate_idea_batch_llama", lambda sp, up, job_id="": {"candidates": [
        {"title": "Daily Bark", "pitch": "A daily dog-walk diary that reminds owners to walk their dog.", "twist": "Streaks", "target_buyer": "Dog owners", "why_monetizable": "Subs"},
        {"title": "Leash Log", "pitch": "Another generic pet habit tracker for busy dog owners.", "twist": "Dark mode", "target_buyer": "Dog owners", "why_monetizable": "Subs"},
        {"title": "Kennel Board", "pitch": "A free open-source kennel whiteboard for volunteer shelters.", "twist": "Offline", "target_buyer": "Shelters", "why_monetizable": "Donations"},
    ]})
    calls = []

    def model(system_prompt: str, user_prompt: str, max_tokens: int = 4096, temperature: float = 0.25):
        calls.append((user_prompt, temperature))
        if "Kennel Board" in user_prompt:  # a fine idea nobody pays for
            return {"verdict": "NICHE ONLY", "headline": "Useful but free", "viability_score": 5.0,
                    "dimensions": {"d": {"label": "D", "score": 5.0, "weight": 100, "reasoning": "ok"}},
                    "pricing_recommendation": {"suggested_price": "$0 (Free / OSS)", "model": "Free / OSS", "buyer": "none"}}
        return {"verdict": "DO NOT BUILD", "headline": "Saturated habit tracker", "viability_score": 3.5,
                "dimensions": {"d": {"label": "D", "score": 3.5, "weight": 100, "reasoning": "crowded"}},
                "pricing_recommendation": {"suggested_price": "Do Not Monetize", "model": "Do Not Monetize"}}

    monkeypatch.setattr(rc_eval, "_call_shared_llama", model)
    client = TestClient(app)

    def wait(job_id):
        for _ in range(60):
            jr = client.get(f"/api/rc/job/{job_id}").json()
            if jr["status"] in ("completed", "failed"):
                return jr
            time.sleep(0.05)
        raise AssertionError("job did not finish")

    res = wait(client.post("/api/rc/find-idea", json={"interests": "dogs", "platform": "Android"}).json()["job_id"])
    fr = res["finder_result"]
    assert fr["passed_reality_check"] is False and fr["selected_idea"] is None and fr["winning_report"] is None
    assert [a["passed"] for a in fr["attempts"]] == [False, False, False]
    assert fr["attempts"][2]["sellability"]["why"].startswith("Reality Check found no price")
    assert all(t == 0.0 for _, t in calls)  # deterministic: temperature 0 for every check
    assert "Daily Bark: A daily dog-walk diary" in calls[0][0]  # the gate graded title + pitch, as the tab will

    # The user checks Daily Bark in the Reality Check tab with the text Idea Finder showed: same report, no new call
    bark = fr["attempts"][0]
    n = len(calls)
    rc = wait(client.post("/api/rc/evaluate", json={"pitch": bark["pitch"], "platform": "Android", "twist": bark["twist"], "tone": "realistic"}).json()["job_id"])
    assert len(calls) == n
    assert rc["report"]["id"] == bark["report_id"] and rc["report"]["viability_score"] == 3.5 and rc["report"]["reused"] is True
