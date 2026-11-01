"""Shared setup for the browser tests: a live Flask server on a fake Stockfish.

Chromium (Playwright) opens the real pages against that server. The real
/api/analyze and GameAnalyzer run on a fake Stockfish that waits until the test
releases it, so the test decides when the analysis ends. The Lichess puzzle comes
from a stub. Needs `python -m playwright install chromium` and internet (CDN
scripts, piece images); the browser tests skip when Playwright is not installed.
"""
import json
import threading

import chess
import pytest
from chess.engine import Cp, PovScore
from werkzeug.serving import make_server

import app
from tests.engine_replay import patch_popen

PHONE, DESKTOP = (390, 844), (1440, 900)
GAME = {
    "white": "alpha", "black": "beta", "date": "2026.01.01", "user_color": "white",
    "white_rating": 1200, "black_rating": 1210, "white_country": "IT", "black_country": "FR",
    "pgn": '[Event "Test"]\n[White "alpha"]\n[Black "beta"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 *\n',
}
PUZZLE = {"id": "t1", "fen": chess.STARTING_FEN, "moves": ["e2e4", "e7e5", "g1f3", "b8c6"],
          "rating": 1500, "themes": ["opening"]}


def pretend_host(monkeypatch, cores):
    """Fixes what GET /api/engine reports, whatever this machine and shell are."""
    monkeypatch.delenv("REVIEW_DEPTH", raising=False)
    monkeypatch.setattr(app, "_host_cores", lambda: cores)


def seed_local_storage(page, items):
    """Writes localStorage keys before any page script runs, on every load of the page.

    A str is stored as it is, anything else as JSON text."""
    page.add_init_script("".join(
        f"localStorage.setItem({json.dumps(key)}, "
        f"{json.dumps(value if isinstance(value, str) else json.dumps(value))});"
        for key, value in items.items()))


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
    sync_api = pytest.importorskip("playwright.sync_api")
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
