"""Browser tests for the review page while the analysis runs.

Chromium (Playwright) opens the real page against a live Flask server. The real
/api/analyze and GameAnalyzer run on a fake Stockfish that waits until the test
releases it, so the test decides when the analysis ends. The Lichess puzzle comes
from a stub. Needs `python -m playwright install chromium` and internet (CDN
scripts, piece images); skipped when Playwright is not installed.
"""
import threading

import chess
import pytest
from chess.engine import Cp, PovScore
from werkzeug.serving import make_server

import app
from tests.engine_replay import patch_popen

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

PHONE, DESKTOP = (390, 844), (1440, 900)
GAME = {
    "white": "alpha", "black": "beta", "date": "2026.01.01", "user_color": "white",
    "white_rating": 1200, "black_rating": 1210, "white_country": "IT", "black_country": "FR",
    "pgn": '[Event "Test"]\n[White "alpha"]\n[Black "beta"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 *\n',
}
PUZZLE = {"id": "t1", "fen": chess.STARTING_FEN, "moves": ["e2e4", "e7e5", "g1f3", "b8c6"],
          "rating": 1500, "themes": ["opening"]}
ACTIVE = "document.activeElement.tagName + '#' + document.activeElement.id"


class FakeEngine:
    """Scores every position 0 cp. Its first search waits until `release` is set."""

    def __init__(self, release):
        self.release, self.started = release, False

    def configure(self, options):
        pass

    def analyse(self, board, limit, multipv=None):
        if not self.started:
            self.started = True
            self.release.wait()
        moves = list(board.legal_moves)[: multipv or 1]
        infos = [{"score": PovScore(Cp(0), board.turn), "pv": [m], "depth": limit.depth}
                 for m in moves]
        return infos if multipv is not None else infos[0]

    def quit(self):
        pass


@pytest.fixture
def release():
    return threading.Event()


@pytest.fixture
def base_url(monkeypatch, release):
    monkeypatch.setitem(app._games_cache, "tester", [GAME])
    monkeypatch.setattr(app, "ENGINE_PATH", "fake")
    server = make_server("127.0.0.1", 0, app.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with patch_popen(lambda path: FakeEngine(release)):
        yield f"http://127.0.0.1:{server.port}"
        release.set()  # a request blocked in the engine would hang the shutdown
        server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser, request):
    width, height = getattr(request, "param", PHONE)
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    page.set_default_timeout(15_000)
    page.add_init_script('sessionStorage.setItem("review_username", "tester");'
                         'sessionStorage.setItem("review_game_id", "0");')
    page.route("**/api/puzzle*", lambda route: route.fulfill(json=PUZZLE))
    page.clock.install()  # time runs normally until a test calls clock.run_for()
    yield page
    context.close()


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
    page.wait_for_function("puzzleStep === 1")
    expect(page.locator("#puzzle-board")).to_be_visible()
    assert box(page, ".puzzle-card")[1] == card_top


def test_missing_game_shows_the_error_and_the_puzzle_still_starts(page, base_url, monkeypatch):
    monkeypatch.setitem(app._games_cache, "tester", [])  # /api/game answers 404
    page.goto(f"{base_url}/review")
    expect(page.locator("#loading-row")).to_contain_text("Errore: game not found")
    page.wait_for_function("puzzleStep === 1")
