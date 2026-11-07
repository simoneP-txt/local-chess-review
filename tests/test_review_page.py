"""Browser tests for the review page while the analysis runs.

Chromium (Playwright) opens the real page against the live server of conftest.py:
the real /api/analyze and GameAnalyzer run on a fake Stockfish that waits until the
test releases it, so the test decides when the analysis ends. Needs
`python -m playwright install chromium` and internet (CDN scripts, piece images);
skipped when Playwright is not installed.
"""
import re

import pytest

import app
from tests.conftest import DESKTOP, FakeEngine, GAME, PHONE, pretend_host, seed_local_storage

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

ACTIVE = "document.activeElement.tagName + '#' + document.activeElement.id"
# 20 full moves = 40 plies (the knights walk out and back), so 600 ms per ply is above the 20 s floor.
LONG_GAME = {**GAME, "pgn": GAME["pgn"].replace(
    "1. e4 e5 2. Nf3 Nc6",
    " ".join(f"{n}. {'Nf3 Nf6' if n % 2 else 'Ng1 Ng8'}" for n in range(1, 21)))}


def box(page, selector):
    """[x, y, width, height] in document coordinates, so scrolling does not change it."""
    return page.evaluate(
        """selector => {
            const r = document.querySelector(selector).getBoundingClientRect();
            return [r.x + scrollX, r.y + scrollY, r.width, r.height];
        }""",
        selector,
    )


def overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


@pytest.mark.parametrize("page", [PHONE, DESKTOP], indirect=True, ids=["390x844", "1440x900"])
@pytest.mark.parametrize("dismiss_first", [False, True], ids=["open", "dismiss-then-open"])
def test_ready_notice_leaves_the_puzzle_alone(page, base_url, release, dismiss_first):
    page.goto(f"{base_url}/review")
    # Half-solve the puzzle: the setup move plays itself, the test plays e7-e5 by
    # click-to-move, the reply plays itself.
    page.wait_for_function("puzzleStep === 1")
    page.click('#puzzle-board [data-square="e7"]')
    page.click('#puzzle-board [data-square="e5"]')
    page.wait_for_function("puzzleStep === 3")
    fen = page.evaluate("puzzleChess.fen()")
    board_box = box(page, "#puzzle-board")
    focused = page.evaluate(ACTIVE)

    release.set()  # the analysis ends now
    expect(page.locator("#review-ready")).to_be_visible()
    # Fire every pending timer: none may switch the screen or hide the notice.
    page.clock.run_for(60_000)
    expect(page.locator("#review-ready")).to_be_visible()
    expect(page.get_by_text("Revisione pronta")).to_be_visible()
    expect(page.locator("#loading-status")).to_be_hidden()
    expect(page.locator("#summary-screen")).to_be_hidden()
    expect(page.locator("#loading")).to_be_visible()
    assert page.evaluate("puzzleChess.fen()") == fen
    assert page.evaluate("puzzleStep") == 3
    assert box(page, "#puzzle-board") == board_box
    assert not overlaps(box(page, "#review-ready"), box(page, ".puzzle-card"))
    assert page.evaluate(ACTIVE) == focused
    row = page.locator("#loading-row")
    expect(row).to_have_attribute("aria-live", "polite")
    expect(row.locator("#btn-open-review")).to_be_visible()
    expect(page.get_by_role("button", name="Chiudi")).to_have_attribute("id", "btn-dismiss-ready")
    assert "progress-bar-animated" not in page.get_attribute("#loading-progress-bar", "class")

    if dismiss_first:
        page.click("#btn-dismiss-ready")
        expect(page.get_by_text("Revisione pronta")).to_be_hidden()
        expect(page.locator("#btn-dismiss-ready")).to_be_hidden()
        expect(page.locator("#btn-open-review")).to_be_visible()
        expect(page.locator("#summary-screen")).to_be_hidden()
        assert box(page, "#puzzle-board") == board_box

    page.click("#btn-open-review")
    expect(page.locator("#summary-screen")).to_be_visible()
    expect(page.locator("#loading")).to_be_hidden()


def test_analysis_error_shows_in_the_status_row_and_keeps_the_puzzle(page, base_url, monkeypatch):
    page.goto(f"{base_url}/review")  # the analysis is pending: the estimate text shows
    card_top = box(page, ".puzzle-card")[1]

    monkeypatch.setattr(app, "ENGINE_PATH", None)  # /api/analyze now answers 500
    page.reload()
    expect(page.locator("#loading-row")).to_contain_text("Errore: Stockfish not found")
    expect(page.locator("#loading-status")).to_have_class(re.compile("loading-error"))
    bar = page.locator("#loading-progress-bar")
    expect(bar).to_have_class(re.compile("bg-danger"))
    expect(bar).not_to_have_class(re.compile("progress-bar-animated|bg-primary"))
    page.wait_for_function("puzzleStep === 1")
    expect(page.locator("#puzzle-board")).to_be_visible()
    assert box(page, ".puzzle-card")[1] == card_top


def test_long_analysis_error_does_not_move_the_puzzle(page, base_url, monkeypatch):
    page.goto(f"{base_url}/review")
    card = box(page, ".puzzle-card")

    def boom(self, *args, **kwargs):
        raise RuntimeError("engine crashed " * 25)

    monkeypatch.setattr(FakeEngine, "analyse", boom)
    page.reload()
    status = page.locator("#loading-status")
    expect(status).to_contain_text("engine crashed engine crashed")
    assert len(status.get_attribute("title")) >= 300
    assert box(page, ".puzzle-card") == card


def test_ready_notice_in_english(page, base_url, release):
    seed_local_storage(page, {"chessreview.lang": "en"})
    page.goto(f"{base_url}/review")
    release.set()
    expect(page.get_by_text("Review ready")).to_be_visible()
    expect(page.get_by_role("button", name="Open review")).to_be_visible()


def test_arrow_keys_on_the_loading_screen_raise_nothing(page, base_url, release):
    errors = []
    page.on("pageerror", lambda err: errors.append(err))
    page.goto(f"{base_url}/review")
    release.set()
    expect(page.locator("#review-ready")).to_be_visible()
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowRight")
    assert errors == []


def test_failing_engine_endpoint_shows_in_the_status_row(page, base_url):
    page.route("**/api/engine", lambda route: route.fulfill(status=500, json={"error": "no engine info"}))
    page.goto(f"{base_url}/review")
    expect(page.locator("#loading-row")).to_contain_text("Errore: no engine info")


def test_missing_game_shows_the_error_and_the_puzzle_still_starts(page, base_url, monkeypatch):
    monkeypatch.setitem(app._games_cache, "tester", [])  # /api/game answers 404
    page.goto(f"{base_url}/review")
    expect(page.locator("#loading-row")).to_contain_text("Errore: game not found")
    page.wait_for_function("puzzleStep === 1")


@pytest.mark.parametrize("stored, sent", [
    ({"depth": 18, "threads": 2}, {"depth": 18, "threads": 2}),
    (None, {"depth": 15, "threads": 1}),
], ids=["stored", "defaults"])
def test_review_sends_the_engine_settings_and_shows_the_depth(page, base_url, monkeypatch, stored, sent):
    pretend_host(monkeypatch, 4)
    if stored:
        seed_local_storage(page, {"chessreview.engine_settings": stored})
    with page.expect_request("**/api/analyze") as request:
        page.goto(f"{base_url}/review")
    body = request.value.post_data_json
    assert {name: body[name] for name in sent} == sent
    expect(page.locator("#loading-status")).to_contain_text(f"profondità {sent['depth']}")


@pytest.mark.parametrize("stored, estimate_ms", [
    ({"15x1": 1000}, 40_000),  # same settings: 40 plies x the stored 1000 ms
    ({"18x2": 1000}, 24_000),  # other settings: 40 plies x the 600 ms default
], ids=["same-settings", "other-settings"])
def test_progress_estimate_uses_the_last_time_for_the_same_settings(
        page, base_url, monkeypatch, stored, estimate_ms):
    pretend_host(monkeypatch, 4)
    monkeypatch.setitem(app._games_cache, "tester", [LONG_GAME])
    seed_local_storage(page, {"chessreview.review_ms_per_ply": stored})
    page.goto(f"{base_url}/review")
    page.wait_for_function(f"progressEstimateMs === {estimate_ms}")


def test_review_keeps_the_time_per_ply_of_each_setting(page, base_url, release, monkeypatch):
    pretend_host(monkeypatch, 4)
    seed_local_storage(page, {"chessreview.engine_settings": {"depth": 12, "threads": 2},
                              "chessreview.review_ms_per_ply": {"15x1": 5000}})
    page.goto(f"{base_url}/review")
    release.set()
    expect(page.locator("#review-ready")).to_be_visible()
    times = page.evaluate("JSON.parse(localStorage.getItem('chessreview.review_ms_per_ply'))")
    assert set(times) == {"15x1", "12x2"}
    assert times["15x1"] == 5000  # another setting keeps its last time
    assert 0 < times["12x2"] < 5000  # this review: elapsed ms / 4 plies, in the same map


@pytest.mark.parametrize("lang, labels", [
    ("it", ["Principiante (400-800)", "Facile (800-1200)", "Intermedio (1200-1600)",
            "Avanzato (1600-2000)", "Esperto (2000-2400)", "Maestro (2400-2800)"]),
    ("en", ["Beginner (400-800)", "Easy (800-1200)", "Intermediate (1200-1600)",
            "Advanced (1600-2000)", "Expert (2000-2400)", "Master (2400-2800)"]),
], ids=["it", "en"])
def test_puzzle_difficulty_selector(page, base_url, lang, labels):
    seed_local_storage(page, {"chessreview.lang": lang})
    with page.expect_request("**/api/puzzle*") as first:
        page.goto(f"{base_url}/review")
    assert first.value.url.endswith("?difficulty=intermediate")
    page.wait_for_function("puzzleStep === 1")
    assert page.input_value("#puzzle-difficulty") == "intermediate"

    options = page.locator("#puzzle-difficulty option")
    expect(options).to_have_text(labels)
    assert options.evaluate_all("els => els.map(el => el.value)") == [
        "beginner", "easy", "intermediate", "advanced", "expert", "master"]

    with page.expect_request("**/api/puzzle*") as chosen:
        page.select_option("#puzzle-difficulty", "master")
    assert chosen.value.url.endswith("?difficulty=master")
