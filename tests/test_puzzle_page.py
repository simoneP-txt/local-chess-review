"""Browser tests for the rules of the mini-puzzle on the loading screen.

Chromium (Playwright) opens the real page against the live server of conftest.py. Each
test serves its own puzzle through page.route and plays by click-to-move or by
drag-and-drop. Needs `python -m playwright install chromium` and internet (CDN scripts,
piece images); skipped when Playwright is not installed.
"""
import pytest

from tests.conftest import PUZZLE

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

SOLVED = "Puzzle risolto!"
WRONG = "Sbagliato!"
PROMOTION_FEN = "k7/4P3/8/8/8/7r/8/K7 b - - 0 1"
TWO_MATES_FEN = "7k/6pp/8/3n4/8/8/1R6/R5K1 b - - 0 1"  # after d5-c3, a1-a8 and b2-b8 both mate

both_inputs = pytest.mark.parametrize("how", ["click", "drag"])


def square(name):
    return f'#puzzle-board [data-square="{name}"]'


def start_puzzle(page, base_url, fen, moves, ready="puzzleStep === 1"):
    page.route("**/api/puzzle*", lambda route: route.fulfill(json={**PUZZLE, "fen": fen, "moves": moves}))
    page.goto(f"{base_url}/review")
    page.wait_for_function(ready)


def play(page, how, source, target):
    if how == "click":
        page.click(square(source))
        page.click(square(target))
    else:
        page.drag_and_drop(square(source), square(target))


def expect_status(page, text):
    expect(page.locator("#puzzle-status")).to_contain_text(text)


def expect_piece(page, name, piece):
    expect(page.locator(f'{square(name)} img[data-piece="{piece}"]')).to_have_count(1)


@both_inputs
def test_underpromotion_gets_the_expected_piece(page, base_url, how):
    start_puzzle(page, base_url, PROMOTION_FEN, ["h3h4", "e7e8n"])
    play(page, how, "e7", "e8")
    expect_status(page, SOLVED)
    expect_piece(page, "e8", "wN")
    assert page.evaluate("puzzleChess.get('e8').type") == "n"


@both_inputs
def test_another_mate_solves_the_puzzle(page, base_url, how):
    start_puzzle(page, base_url, TWO_MATES_FEN, ["d5c3", "a1a8"])
    play(page, how, "b2", "b8")
    expect_status(page, SOLVED)
    expect_piece(page, "b8", "wR")


@both_inputs
def test_a_mate_on_other_squares_promotes_to_a_queen(page, base_url, how):
    # The expected move underpromotes on purpose: with a plain one, this passes even if
    # the code applies the expected piece on every square.
    start_puzzle(page, base_url, "k7/pp2P1P1/8/8/8/7r/5P1P/R5K1 b - - 0 1", ["h3h4", "g7g8n"])
    play(page, how, "e7", "e8")
    expect_status(page, SOLVED)
    expect_piece(page, "e8", "wQ")


def test_a_mate_ends_a_longer_puzzle_without_a_reply(page, base_url):
    start_puzzle(page, base_url, TWO_MATES_FEN, ["d5c3", "g1g2", "h7h6", "a1a8"])
    play(page, "click", "b2", "b8")
    expect_status(page, SOLVED)
    page.clock.run_for(2_000)  # a reply timer would fire here
    expect_status(page, SOLVED)
    assert page.evaluate("puzzleStep") == 2


@both_inputs
def test_a_wrong_move_goes_back_in_a_promotion_puzzle(page, base_url, how):
    start_puzzle(page, base_url, PROMOTION_FEN, ["h3h4", "e7e8n"])
    play(page, how, "a1", "a2")
    expect_status(page, WRONG)
    expect_piece(page, "a1", "wK")
    expect_piece(page, "e7", "wP")
    assert page.evaluate("puzzleStep") == 1
    assert page.evaluate("puzzleSolved") is False


def test_a_wrong_move_from_the_expected_square_goes_back(page, base_url):
    start_puzzle(page, base_url, TWO_MATES_FEN, ["d5c3", "a1a8"])
    play(page, "click", "a1", "a7")
    expect_status(page, WRONG)
    expect_piece(page, "a1", "wR")


def test_the_setup_side_cannot_solve_the_puzzle(page, base_url):
    # The stopped clock holds the setup timer: White has the move and a1-a8 mates, but
    # White only plays the setup.
    page.clock.pause_at(page.evaluate("Date.now()") / 1000 + 3600)
    start_puzzle(page, base_url, "7k/6pp/8/8/8/8/1R6/R5K1 w - - 0 1", ["g1g2", "h7h6"],
                 ready="puzzleBoard !== null && puzzleStep === 0")
    play(page, "click", "a1", "a8")
    assert page.evaluate("puzzleSolved") is False
    expect_piece(page, "a1", "wR")
    page.clock.run_for(800)
    assert page.evaluate("puzzleStep") == 1
    assert page.evaluate("puzzleChess.get('g2').type") == "k"


def test_the_opponent_cannot_play_its_own_reply(page, base_url):
    page.clock.pause_at(page.evaluate("Date.now()") / 1000 + 3600)
    start_puzzle(page, base_url, PUZZLE["fen"], PUZZLE["moves"], ready="puzzleBoard !== null")
    page.clock.run_for(800)
    play(page, "click", "e7", "e5")
    play(page, "click", "g1", "f3")  # the reply, before its timer
    assert page.evaluate("puzzleStep") == 2
    page.clock.run_for(700)
    assert page.evaluate("puzzleStep") == 3


def test_the_puzzle_board_follows_a_resize(page, base_url):
    page.set_viewport_size({"width": 844, "height": 390})  # a phone on its side
    start_puzzle(page, base_url, PUZZLE["fen"], PUZZLE["moves"])
    page.set_viewport_size({"width": 390, "height": 844})  # turned upright during the analysis
    page.wait_for_timeout(300)
    board, wrapper, page_width = page.evaluate("""[
        document.querySelector('#puzzle-board .board-b72b1').offsetWidth,
        document.getElementById('puzzle-board').clientWidth,
        document.documentElement.scrollWidth]""")
    assert board <= wrapper and page_width <= 390


def test_a_resize_drops_the_puzzle_selection(page, base_url):
    page.set_viewport_size({"width": 844, "height": 390})
    start_puzzle(page, base_url, PUZZLE["fen"], PUZZLE["moves"])
    page.click(square("e7"))
    page.set_viewport_size({"width": 390, "height": 844})  # the redraw removes the highlight
    page.wait_for_timeout(300)
    page.click(square("e5"))  # e7-e5 is the solution: a kept selection would play it
    assert page.evaluate("puzzleStep") == 1
    expect_piece(page, "e7", "bP")


@pytest.mark.parametrize("pending", ["setup", "reply"])
def test_new_cancels_the_pending_move_of_the_old_puzzle(page, base_url, pending):
    puzzles = iter([PUZZLE, {**PUZZLE, "id": "t2", "moves": ["d2d4", "d7d5"]}])
    page.route("**/api/puzzle*", lambda route: route.fulfill(json=next(puzzles)))
    page.clock.pause_at(page.evaluate("Date.now()") / 1000 + 3600)
    page.goto(f"{base_url}/review")
    page.wait_for_function("puzzleBoard !== null && puzzleStep === 0")
    if pending == "reply":
        page.clock.run_for(800)
        play(page, "click", "e7", "e5")
        assert page.evaluate("puzzleStep") == 2
    page.click("#btn-puzzle-new")
    page.wait_for_function("puzzleData.id === 't2' && puzzleStep === 0")
    page.clock.run_for(650)  # past the old reply timer (600 ms), short of the setup timers (700 ms)
    assert page.evaluate("puzzleStep") == 0
    page.clock.run_for(150)
    assert page.evaluate("puzzleStep") == 1
    assert page.evaluate("puzzleChess.fen()") == "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq d3 0 1"
