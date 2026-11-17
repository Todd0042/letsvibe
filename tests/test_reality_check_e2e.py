"""E2E tests for LetsVibe: Reality Check (LetsVibeRC) and shared round-robin LLM locking."""

from __future__ import annotations

import threading
import time
from typing import List

from fastapi.testclient import TestClient

from letsvibe.reality_check import evaluator as rc_eval
from letsvibe.reality_check import routes as rc_routes
from letsvibe.web.llama_supervisor import LlamaSupervisor
from letsvibe.web.server import app


def test_supervisor_round_robin_and_mutual_exclusion(tmp_path) -> None:
    sup = LlamaSupervisor(llama_bin=str(tmp_path / "missing"), idle_minutes=0.0)
    try:
        # Hold initial lock on letsvibe so both queues build up while busy
        first = sup.acquire_lock("letsvibe", job_id="lv-0", ttl_sec=30.0, wait_timeout_sec=5.0)
        assert first["acquired"] is True

        order: List[str] = []
        active_count = 0
        overlap_detected = False
        mu = threading.Lock()

        def worker(app_id: str, job_id: str) -> None:
            nonlocal active_count, overlap_detected
            lease = sup.acquire_lock(app_id, job_id=job_id, ttl_sec=30.0, wait_timeout_sec=10.0)
            assert lease["acquired"] is True
            try:
                with mu:
                    active_count += 1
                    if active_count > 1:
                        overlap_detected = True
                    order.append(f"{app_id}:{job_id}")
                time.sleep(0.03)
            finally:
                with mu:
                    active_count -= 1
                sup.release_lock(lease["lease_id"])

        threads = [
            threading.Thread(target=worker, args=("letsvibe", "lv-1")),
            threading.Thread(target=worker, args=("letsvibe", "lv-2")),
            threading.Thread(target=worker, args=("letsvibe_rc", "rc-1")),
            threading.Thread(target=worker, args=("letsvibe_rc", "rc-2")),
        ]
        for t in threads:
            t.start()
        time.sleep(0.08)
        sup.release_lock(first["lease_id"])
        for t in threads:
            t.join(timeout=5.0)

        assert not overlap_detected
        assert len(order) == 4
        # Strictly alternates letsvibe_rc -> letsvibe -> letsvibe_rc -> letsvibe after lv-0
        apps = [x.split(":")[0] for x in order]
        assert apps == ["letsvibe_rc", "letsvibe", "letsvibe_rc", "letsvibe"]
    finally:
        sup.shutdown()


def test_reality_check_e2e_flow_host_routing_and_xss(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(rc_routes, "_store", rc_routes.RealityCheckStore(str(tmp_path / "rc.db")))
    monkeypatch.setattr(rc_routes, "_RATE_MAX_HITS", 20)

    def fake_grounding(pitch: str, platform: str = "Web", twist: str = ""):
        return {
            "keywords": ["todo", "task"],
            "platform": platform,
            "github_total_count": 48000,
            "github_top_repos": [{"full_name": "example/todo", "stars": 12000, "language": "TS", "description": "Todo app", "url": ""}],
            "npm_total_count": 5000,
            "npm_top_packages": [],
        }

    def fake_llama(system_prompt: str, user_prompt: str, max_tokens: int = 2000):
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
