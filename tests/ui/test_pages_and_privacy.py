"""Content pages, footer, no third-party requests, and the My Data deletion flow in a real browser."""

from pathlib import Path
from urllib.parse import urlparse

import pytest

from ui_helpers import UI_STATE, session_cookie_for
from test_responsive import VIEWPORTS, assert_inside_viewport, assert_no_horizontal_scroll

SCREENS = Path(__file__).resolve().parents[2] / "test-results" / "screens"
PAGES = ["how-it-works", "faq", "privacy", "cookies", "terms", "remove", "support", "changelog", "my-data"]


@pytest.mark.parametrize("vp", ["mobile-small-360x780", "foldable-square-717x717", "desktop-1440x900"])
@pytest.mark.parametrize("slug", PAGES)
def test_content_pages_render_and_fit(browser, live_server, slug, vp):
    w, h = VIEWPORTS[vp]
    ctx = browser.new_context(viewport={"width": w, "height": h})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    resp = page.goto(f"{live_server}/{slug}")
    assert resp.status == 200
    page.locator("main h1").first.wait_for()
    assert_no_horizontal_scroll(page)
    footer = page.locator("footer.site-footer")
    assert footer.count() == 1 and "Chat / Support" in footer.inner_text()
    if vp != "desktop-1440x900" or slug in ("how-it-works", "my-data", "privacy"):
        page.screenshot(path=str(SCREENS / f"page-{slug}-{vp}.png"), full_page=True)
    assert not errors, errors
    ctx.close()


def test_unknown_page_is_404(browser, live_server):
    ctx = browser.new_context()
    assert ctx.new_page().goto(f"{live_server}/definitely-not-a-page").status == 404
    ctx.close()


@pytest.mark.parametrize("path", ["/", "/project/todd-demo/mx5-dash", "/privacy", "/my-data"])
def test_browser_never_contacts_third_parties(browser, live_server, path):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    hosts = set()
    page.on("request", lambda r: hosts.add(urlparse(r.url).netloc))
    page.goto(live_server + path, wait_until="networkidle")
    page.mouse.wheel(0, 20000)  # trigger lazy-loaded footer images
    page.wait_for_timeout(500)
    own = urlparse(live_server).netloc
    assert hosts == {own}, f"third-party requests from {path}: {sorted(hosts - {own})}"
    ctx.close()


def test_dashboard_has_footer_support_and_notice(browser, live_server):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    page = ctx.new_page()
    page.goto(live_server + "/")
    page.locator("#rankings-tbody .repo-title").first.wait_for()
    assert page.locator("footer.site-footer").count() == 1
    assert page.locator("a.support-link").first.get_attribute("href") == "https://ko-fi.com/todd0042"
    notice = page.locator("#cookie-consent-banner")
    notice.wait_for(state="visible")
    assert "No tracking" in notice.inner_text()
    assert notice.get_by_role("button", name="Accept").is_visible()
    assert notice.get_by_role("button", name="Decline").is_visible()
    notice.get_by_role("button", name="Decline").click()
    notice.wait_for(state="hidden")
    assert page.evaluate("() => localStorage.getItem('letsvibe_cookie_consent')") is None
    page.goto(live_server + "/project/todd-demo/mx5-dash")
    page.locator("aside.support-card").wait_for()
    assert_inside_viewport(page, page.locator("aside.support-card .kofi-button"), "report Ko-fi button")
    assert "rubric" in page.locator("#project-rubric-note").inner_text().lower()
    ctx.close()


def test_my_data_signed_out_prompts_sign_in(browser, live_server):
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(live_server + "/my-data")
    page.locator("#signed-out").wait_for(state="visible")
    page.locator("#signed-out a[href='/sign-in']").click()
    # Signing in creates an account: essential cookies and terms (18+) must be accepted before GitHub is contacted
    page.wait_for_url("**/sign-in")
    go = page.locator("#signin-go")
    assert go.is_disabled()
    assert page.locator("#signin-accept").is_disabled()
    page.locator("#signin-cookie-accept").click()
    assert page.locator("#signin-accept").is_enabled()
    page.locator("#signin-accept").check()
    assert go.is_enabled()
    ctx.close()


def test_my_data_delete_flow(browser, live_server):
    from letsvibe.storage.db import get_leaderboard, get_user_by_github_id, list_projects_owned_by, save_report_to_db
    from letsvibe.storage.identity import parse_repo_identity

    db = UI_STATE["db"]
    owner, gid = "ui-deleter", "880088"
    for i in range(3):
        rep = {"final_vibe_score": 60.0 + i, "letter_grade": "C", "archetype": {"name": f"r{i}"}, "factor_evaluations": {}}
        save_report_to_db(rep, parse_repo_identity(f"https://github.com/{owner}/repo-{i}"), db_path=db)
    others_before = [i["full_slug"] for i in get_leaderboard(db_path=db) if not i["full_slug"].startswith(owner)]

    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    ctx.add_cookies([session_cookie_for(live_server, owner, gid)])
    page = ctx.new_page()
    page.goto(live_server + "/my-data")
    items = page.locator("#proj-list li")
    items.first.wait_for()
    assert items.count() == 3
    # Confirmation prompts stay hidden until asked for
    assert page.locator("[data-confirm]:visible").count() == 0
    assert not page.locator("#delete-all-confirm").is_visible()
    page.screenshot(path=str(SCREENS / "my-data-signed-in-mobile.png"), full_page=True)
    assert_no_horizontal_scroll(page)

    # Delete one project with the inline confirmation
    row = page.locator(f"#proj-list li[data-slug='{owner}/repo-0']")
    row.get_by_role("button", name="Delete").first.click()
    row.locator("[data-yes]").click()
    page.locator("#proj-status.ok").wait_for()
    from playwright.sync_api import expect
    expect(items).to_have_count(2)

    # Delete the rest as a batched job with a progress bar
    page.locator("#delete-all-btn").click()
    page.locator("#delete-all-yes").click()
    expect(page.locator("#proj-status")).to_have_text("Deleted 2 projects.", timeout=20000)
    assert list_projects_owned_by(owner, db_path=db) == []

    # Account deletion stays disabled until the username is typed exactly
    btn = page.locator("#acct-delete-btn")
    page.locator("#confirm-name").fill("not-me")
    assert btn.is_disabled()
    page.locator("#confirm-name").fill(owner)
    btn.click()
    page.locator("#account-gone").wait_for(state="visible", timeout=20000)
    page.screenshot(path=str(SCREENS / "my-data-account-deleted-mobile.png"), full_page=True)
    assert get_user_by_github_id(gid, db_path=db) is None

    # Signed out everywhere, other people's projects untouched
    page.goto(live_server + "/my-data")
    page.locator("#signed-out").wait_for(state="visible")
    others_after = [i["full_slug"] for i in get_leaderboard(db_path=db) if not i["full_slug"].startswith(owner)]
    assert others_after == others_before
    ctx.close()


def test_my_projects_shows_a_vibe_review_hold_without_asking_anything(browser, live_server):
    """A graded project the vibe-coded check could not confirm: the owner is told it waits for review, nothing more."""
    from letsvibe.storage.integrity import hold_report

    owner, gid = "ui-vibe-review", "880099"
    hold_report({"repo_name": "chat-pasted"}, owner, "chat-pasted", f"{owner}/chat-pasted", f"https://github.com/{owner}/chat-pasted",
                [{"file": "What the vibe-coded check found", "start_line": None, "end_line": None, "code": "- <script>x</script>"}],
                db_path=UI_STATE["db"], kind="vibe_review")
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    ctx.add_cookies([session_cookie_for(live_server, owner, gid)])
    page = ctx.new_page()
    page.goto(live_server + "/my-projects")
    card = page.locator("[data-held]")
    card.first.wait_for()
    text = card.first.inner_text()
    assert "Waiting for review" in text and "Nothing is needed from you" in text
    assert card.first.locator("textarea").count() == 0          # no explanation is asked for
    assert "<script>" not in text                                # what the check found is for the admin only
    assert_no_horizontal_scroll(page)
    ctx.close()
