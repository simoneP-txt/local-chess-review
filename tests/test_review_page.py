"""Browser tests for the review page: the loading screen, then the move-by-move board.

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
FEN_GAME = {**GAME, "pgn": GAME["pgn"].replace(
    '[Result "*"]', '[Result "*"]\n[SetUp "1"]\n[FEN "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"]').replace(
    "1. e4 e5 2. Nf3 Nc6", "1. e4 Kd7 2. e5")}
UNDERPROMOTION_GAME = {**GAME, "pgn": GAME["pgn"].replace(
    "1. e4 e5 2. Nf3 Nc6", "1. h4 g5 2. hxg5 Nf6 3. g6 Rg8 4. gxh7 a6 5. hxg8=N")}


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


def start_review(page, base_url, release, monkeypatch, game):
    """Opens the move-by-move review of this game, its analysis already done."""
    monkeypatch.setitem(app._games_cache, "tester", [game])
    release.set()
    page.goto(f"{base_url}/review")
    page.click("#btn-open-review")
    page.click("#btn-start-review")


def piece_on(page, square):
    """data-piece of the piece on this square of the main board, None when the square is empty."""
    return page.evaluate("sel => document.querySelector(sel)?.dataset.piece ?? null",
                         f'#board [data-square="{square}"] img')


def play(page, how, source, target):
    source, target = (f'#board [data-square="{name}"]' for name in (source, target))
    if how == "click":
        page.click(source)
        page.click(target)
    else:
        page.drag_and_drop(source, target)


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


def test_review_starts_from_the_game_fen(page, base_url, release, monkeypatch):
    start_review(page, base_url, release, monkeypatch, FEN_GAME)
    fen_squares, fen_pieces = ("e1", "a1", "e2", "e8"), ["wK", None, "wP", "bK"]
    assert [piece_on(page, name) for name in fen_squares] == fen_pieces
    page.click("#btn-next")
    page.click("#btn-next")
    assert [piece_on(page, name) for name in ("d7", "e4", "e8")] == ["bK", "wP", None]
    page.click("#btn-start")
    assert [piece_on(page, name) for name in fen_squares] == fen_pieces
    # The continuation plays its first move at once: Kf8, the first legal move, so the fake engine's line.
    status = page.evaluate(
        "goToPly(1); startContinuation(); document.getElementById('continuation-status').textContent")
    assert status == "Mossa 1/1: Kf8"
    expect(page.locator('#board [data-square="f8"] img[data-piece="bK"]')).to_have_count(1)  # the move is animated


@pytest.mark.parametrize("how", ["click", "drag"])
def test_replaying_an_underpromotion_moves_forward(page, base_url, release, monkeypatch, how):
    start_review(page, base_url, release, monkeypatch, UNDERPROMOTION_GAME)
    page.evaluate("goToPly(8)")
    play(page, how, "h7", "g8")  # 5. hxg8=N, the game move
    assert page.evaluate("currentPly") == 9
    expect(page.locator("#variation-banner")).to_be_hidden()
    assert piece_on(page, "g8") == "wN"


def test_a_promotion_on_other_squares_takes_a_queen(page, base_url, release, monkeypatch):
    page.route("**/api/eval", lambda route: route.abort())  # the eval of the variation plays no part here
    start_review(page, base_url, release, monkeypatch, UNDERPROMOTION_GAME)
    page.evaluate("goToPly(8)")
    play(page, "click", "h7", "h8")
    expect(page.locator("#variation-banner")).to_be_visible()
    assert piece_on(page, "h8") == "wQ"


def test_a_promotion_inside_a_variation_takes_a_queen(page, base_url, release, monkeypatch):
    page.route("**/api/eval", lambda route: route.abort())  # the eval of the variation plays no part here
    start_review(page, base_url, release, monkeypatch, UNDERPROMOTION_GAME)
    page.evaluate("goToPly(8)")
    play(page, "click", "a2", "a3")  # out of the game: the variation starts
    play(page, "click", "a6", "a5")
    play(page, "click", "h7", "g8")  # the squares of the game move 5. hxg8=N
    assert piece_on(page, "g8") == "wQ"


def test_a_drop_after_the_last_move_starts_a_variation(page, base_url, release, monkeypatch):
    errors = []
    page.on("pageerror", lambda err: errors.append(err))
    page.route("**/api/eval", lambda route: route.abort())  # the eval of the variation plays no part here
    start_review(page, base_url, release, monkeypatch, UNDERPROMOTION_GAME)
    page.evaluate("goToPly(9)")
    play(page, "click", "a6", "a5")
    expect(page.locator("#variation-banner")).to_be_visible()
    assert errors == []


def test_a_resize_drops_the_board_selection(page, base_url, release, monkeypatch):
    start_review(page, base_url, release, monkeypatch, GAME)
    page.click('#board [data-square="e2"]')
    page.set_viewport_size({"width": 844, "height": 390})  # the redraw removes the highlight
    page.wait_for_timeout(300)
    page.click('#board [data-square="e4"]')  # e2-e4 is the game move: a kept selection would play it
    assert page.evaluate("currentPly") == 0
    assert [piece_on(page, name) for name in ("e2", "e4")] == ["wP", None]
    expect(page.locator("#variation-banner")).to_be_hidden()
