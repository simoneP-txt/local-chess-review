"""Browser tests for the engine settings panel on the dashboard.

Chromium (Playwright) opens the dashboard against the live server of conftest.py.
Needs `python -m playwright install chromium` and internet (Bootstrap from the
CDN); skipped when Playwright is not installed.
"""
import json

import pytest

from tests.conftest import DESKTOP, PHONE, pretend_host, seed_local_storage

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

KEY = "chessreview.engine_settings"
TOUCH = 44  # smallest comfortable touch target, in CSS pixels
LIMITS = {"cores": 8, "threads": {"min": 1, "max": 8, "default": 1},
          "depth": {"min": 10, "max": 22, "default": 15}}
# What is in localStorage (None = nothing) -> what loadEngineSettings(LIMITS) returns.
STORED = {
    None: (15, 1),
    "not json": (15, 1),
    "null": (15, 1),
    "7": (15, 1),
    "[18, 2]": (15, 1),
    '{"depth": 18, "threads": 2}': (18, 2),
    '{"depth": 99, "threads": 99}': (22, 8),
    '{"depth": 1, "threads": 0}': (10, 1),
    '{"depth": 18}': (18, 1),
    '{"depth": "18", "threads": 2.5}': (15, 1),
}


@pytest.fixture(autouse=True)
def eight_cores(monkeypatch):
    pretend_host(monkeypatch, 8)


def bounds(page, selector):
    box = page.locator(selector).bounding_box()
    return box["x"], box["y"], box["width"], box["height"]


@pytest.mark.parametrize("page", [PHONE, DESKTOP], indirect=True, ids=["390x844", "1440x900"])
def test_engine_panel_opens_inside_the_viewport_and_suits_touch(page, base_url):
    page.goto(base_url)
    button = page.locator("#btn-engine")
    expect(button).to_have_text("Motore")
    expect(button).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")  # the navbar fits
    x, _, width, _ = bounds(page, "#btn-engine")
    assert x + width <= bounds(page, "#lang-select")[0]  # left of the language selector

    button.click()
    expect(page.locator("#engine-dialog")).to_be_visible()
    for selector in ("#engine-dialog", "#engine-depth", "#engine-threads", "#btn-engine-close"):
        x, y, width, height = bounds(page, selector)
        assert 0 <= x and 0 <= y, selector
        assert x + width <= page.viewport_size["width"], selector
        assert y + height <= page.viewport_size["height"], selector
    for selector in ("#engine-depth", "#engine-threads", "#btn-engine-close"):
        assert bounds(page, selector)[3] >= TOUCH, selector


def test_engine_panel_closes_with_escape_and_with_the_close_button(page, base_url):
    page.goto(base_url)
    page.evaluate("window.sameDocument = true")
    dialog = page.locator("#engine-dialog")
    page.click("#btn-engine")
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    page.click("#btn-engine")
    expect(dialog).to_be_visible()
    page.click("#btn-engine-close")
    expect(dialog).to_be_hidden()
    assert page.evaluate("window.sameDocument")  # closing did not reload the page (and the games list)


@pytest.mark.parametrize("cores, depth", [(8, "15"), (2, "12")])
def test_engine_panel_offers_what_the_host_allows(page, base_url, monkeypatch, cores, depth):
    pretend_host(monkeypatch, cores)
    page.goto(base_url)
    page.click("#btn-engine")
    threads = page.locator("#engine-threads")
    expect(threads.locator("option")).to_have_count(cores)
    expect(threads.locator("option").last).to_have_text(f"{cores} di {cores} core")
    expect(threads).to_have_value("1")
    expect(page.locator("#engine-depth option")).to_have_count(13)  # 10 to 22
    expect(page.locator("#engine-depth")).to_have_value(depth)


@pytest.mark.parametrize("lang, texts", [
    ("it", ["Motore", "Impostazioni del motore", "Profondità", "Threads", "Chiudi", "4 di 8 core"]),
    ("en", ["Engine", "Engine settings", "Depth", "Threads", "Close", "4 of 8 cores"]),
])
def test_engine_panel_speaks_the_chosen_language(page, base_url, lang, texts):
    seed_local_storage(page, {"chessreview.lang": lang})
    page.goto(base_url)
    page.click("#btn-engine")
    button, title, depth, threads, close, option = texts
    expect(page.locator("#btn-engine")).to_have_text(button)
    expect(page.locator("#engine-dialog h5")).to_have_text(title)
    expect(page.locator("label[for=engine-depth]")).to_have_text(depth)
    expect(page.locator("label[for=engine-threads]")).to_have_text(threads)
    expect(page.locator("#btn-engine-close")).to_have_text(close)
    expect(page.locator("#engine-threads option").nth(3)).to_have_text(option)


def test_engine_choice_survives_a_reload(page, base_url):
    page.goto(base_url)
    page.click("#btn-engine")
    page.select_option("#engine-depth", "18")
    page.select_option("#engine-threads", "3")
    page.reload()
    page.click("#btn-engine")
    expect(page.locator("#engine-depth")).to_have_value("18")
    expect(page.locator("#engine-threads")).to_have_value("3")
    assert json.loads(page.evaluate(f"localStorage.getItem('{KEY}')")) == {"depth": 18, "threads": 3}


def test_stored_values_outside_the_host_range_load_clamped(page, base_url):
    seed_local_storage(page, {KEY: {"depth": 99, "threads": 99}})
    page.goto(base_url)
    page.click("#btn-engine")
    expect(page.locator("#engine-depth")).to_have_value("22")
    expect(page.locator("#engine-threads")).to_have_value("8")


def test_load_engine_settings_clamps_and_falls_back_to_the_defaults(page, base_url):
    page.goto(base_url)
    got = {
        raw: tuple(page.evaluate(
            """([raw, limits]) => {
                if (raw === null) localStorage.removeItem(ENGINE_SETTINGS_KEY);
                else localStorage.setItem(ENGINE_SETTINGS_KEY, raw);
                const { depth, threads } = loadEngineSettings(limits);
                return [depth, threads];
            }""",
            [raw, LIMITS]))
        for raw in STORED
    }
    assert got == STORED


def test_engine_button_stays_disabled_without_the_limits(page, base_url):
    page.route("**/api/engine", lambda route: route.fulfill(status=500, json={"error": "boom"}))
    page.goto(base_url, wait_until="networkidle")  # the failed fetch has been handled by now
    expect(page.locator("#btn-engine")).to_be_disabled()
