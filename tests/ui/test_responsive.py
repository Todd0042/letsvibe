"""Layout checks for the leaderboard and project page across phone, foldable, tablet and desktop sizes."""

import pytest

from pathlib import Path

SCREENS = Path(__file__).resolve().parents[2] / "test-results" / "screens"

VIEWPORTS = {
    "mobile-390x844": (390, 844),
    "mobile-small-360x780": (360, 780),
    "foldable-portrait-673x841": (673, 841),
    "foldable-square-717x717": (717, 717),
    "foldable-landscape-841x701": (841, 701),
    "tablet-portrait-820x1180": (820, 1180),
    "tablet-landscape-1180x820": (1180, 820),
    "desktop-1440x900": (1440, 900),
}
TOUCH = {"mobile-390x844", "mobile-small-360x780", "foldable-portrait-673x841", "foldable-square-717x717",
         "foldable-landscape-841x701", "tablet-portrait-820x1180", "tablet-landscape-1180x820"}


@pytest.fixture(params=list(VIEWPORTS), ids=list(VIEWPORTS))
def page(request, browser):
    name = request.param
    w, h = VIEWPORTS[name]
    ctx = browser.new_context(viewport={"width": w, "height": h}, has_touch=name in TOUCH, device_scale_factor=2 if name in TOUCH else 1)
    pg = ctx.new_page()
    pg.viewport_name = name
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.js_errors = errors
    yield pg
    ctx.close()


def assert_no_horizontal_scroll(page):
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if overflow > 1:
        culprits = page.evaluate(
            """() => [...document.querySelectorAll('body *')]
                .filter(el => el.offsetParent !== null && el.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
                .filter(el => !el.closest('[data-scroll-x]'))
                .slice(0, 8).map(el => `${el.tagName.toLowerCase()}#${el.id}.${[...el.classList].join('.')} right=${Math.round(el.getBoundingClientRect().right)}`)"""
        )
        pytest.fail(f"page scrolls sideways by {overflow}px; widest elements: {culprits}")


def assert_inside_viewport(page, locator, what):
    box = locator.bounding_box()
    width = page.viewport_size["width"]
    assert box is not None, f"{what} is not visible"
    assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1, f"{what} spills off screen: {box} (viewport {width})"


def test_leaderboard_layout(page, live_server):
    page.goto(live_server + "/", wait_until="networkidle")
    page.locator("#rankings-tbody .repo-title").first.wait_for()
    page.wait_for_load_state("networkidle")  # all leaderboard/taxonomy fetches finished re-rendering
    page.screenshot(path=str(SCREENS / f"leaderboard-{page.viewport_name}.png"), full_page=True)

    assert_no_horizontal_scroll(page)
    assert_inside_viewport(page, page.locator("#rankings-tbody .repo-title").first, "first project title")
    assert_inside_viewport(page, page.locator("#sort-select"), "sort dropdown")
    # Every column of a ranking row must be on screen, not clipped by the card's overflow:hidden
    first_row = page.locator("#rankings-tbody tr").first
    for i in range(first_row.locator("td").count()):
        assert_inside_viewport(page, first_row.locator("td").nth(i), f"ranking row cell {i + 1}")
    assert_inside_viewport(page, first_row.locator("button, a").last, "row action button")

    for group in ("tier", "category", "platform"):
        page.locator(f'[data-toggle="{group}"]').click()
        panel = page.locator(f'.filter-dd[data-filter="{group}"] .filter-dd-panel')
        panel.wait_for(state="visible")
        assert_inside_viewport(page, panel, f"{group} filter panel")
        if group == "platform":
            page.screenshot(path=str(SCREENS / f"filters-open-{page.viewport_name}.png"))
        page.keyboard.press("Escape")
        panel.wait_for(state="hidden")
    assert not page.js_errors, page.js_errors


def test_project_page_layout(page, live_server):
    page.goto(live_server + "/project/todd-demo/mx5-dash", wait_until="networkidle")
    page.locator("#project-verdict-summary").wait_for()
    page.wait_for_load_state("networkidle")
    page.screenshot(path=str(SCREENS / f"project-{page.viewport_name}.png"), full_page=True)

    assert_no_horizontal_scroll(page)
    assert_inside_viewport(page, page.locator("#projectRadarCanvas"), "radar chart")
    assert "ESP32-S3" in page.locator("#project-device-profile").inner_text()
    # rubric v9: code complexity is described publicly and marked as not graded
    assert page.locator("#project-complexity-block").is_visible()
    assert "4.2 paths per function" in page.locator("#project-complexity-summary").inner_text()
    assert "render_dash in display.cpp" in page.locator("#project-complexity-details").inner_text()
    assert not page.js_errors, page.js_errors


def test_filters_combine_in_browser(browser, live_server):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    from playwright.sync_api import expect

    page.goto(live_server + "/", wait_until="networkidle")
    titles = page.locator("#rankings-tbody .repo-title")
    podium = page.locator("#podium-cards .podium-repo")  # DOM order: 2nd, 1st, 3rd
    expect(podium).to_have_count(3)
    expect(titles).to_have_count(3)  # the top 3 are on the podium, not repeated in the table

    def pick(group, selector):
        page.locator(f'[data-toggle="{group}"]').click()
        page.locator(f'.filter-dd[data-filter="{group}"] {selector}').check()
        page.keyboard.press("Escape")

    pick("category", 'input[data-id="game"]')
    # expect() retries until the re-render lands, so there is no race with the fetch
    expect(podium).to_have_text(["retro-arcade", "neon-racer"])
    expect(titles).to_have_count(0)
    expect(page.locator("#rankings-tbody")).to_contain_text("All matching projects are shown in the top 3 above.")

    # Ticking the "Mobile" parent selects Android + iOS; combined with Game -> only the mobile game
    pick("platform", 'input[data-parent="mobile"]')
    expect(podium).to_have_text(["neon-racer"])
    expect(page.locator(".filter-chip")).to_have_count(3)  # Game, Android, iOS

    page.locator('#active-filters [data-clear="all"]').click()
    expect(podium).to_have_count(3)
    expect(titles).to_have_count(3)

    # Sorting by one factor shows that factor's score, with the overall grade kept as context
    page.select_option("#sort-select", "ui_ux")
    expect(page.locator("#score-col-header")).to_have_text("UI / UX Score")
    expect(page.locator("#podium-cards .standing-note").first).to_contain_text("UI / UX")
    page.select_option("#sort-select", "overall")
    expect(page.locator("#score-col-header")).to_have_text("Score & Grade")
    ctx.close()
