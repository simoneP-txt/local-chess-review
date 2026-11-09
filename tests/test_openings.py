"""Book from the Lichess openings database: the data, and what analyze_pgn does with it."""
import chess
import pytest

import analyzer as A
import openings
from tests.engine_replay import ScriptedEngine, _analyze

AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def _epd(*sans):
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board.epd()


def _row(pgn):
    """The TSV row with this exact pgn, from the files: the expected values skip openings.book()."""
    for path in openings.DATA_DIR.glob("*.tsv"):
        for line in path.read_text(encoding="utf-8").splitlines():
            eco, name, moves = line.split("\t")
            if moves == pgn:
                return {"eco": eco, "name": name}
    raise AssertionError(f"no TSV row has the pgn {pgn}")


def _classes(result):
    return [m["classification"] for m in result["moves"]]


def _from_fen(fen, movetext):
    return f'[SetUp "1"]\n[FEN "{fen}"]\n\n{movetext} *\n'


def test_book_holds_every_position_along_the_rows():
    positions, named = openings.book()
    assert _epd() in positions  # the starting position lies along every row
    assert _epd("e4") in positions
    inside = _epd("f3", "e5", "g4")  # along the row 1. f3 e5 2. g4 Qh4#, and no row ends here
    assert inside in positions and inside not in named
    assert _epd("h4", "h5", "a4", "a5", "Rh3") not in positions


def test_book_names_the_last_position_of_a_row():
    row = _row("1. e4 e5 2. Nf3 Nc6 3. Bb5")
    assert openings.book()[1][_epd("e4", "e5", "Nf3", "Nc6", "Bb5")] == (row["eco"], row["name"])


def test_analyze_book_ends_at_the_first_move_out_of_theory():
    result = _analyze("1. e4 e5 2. Nf3 Nc6 3. Bb5 h5 4. Nc3 *\n", ScriptedEngine())
    assert _classes(result) == ["book"] * 5 + ["excellent", "excellent"]
    assert result["opening"] == _row("1. e4 e5 2. Nf3 Nc6 3. Bb5")


def test_analyze_transposition_back_into_theory_is_not_book():
    # 2. Ng1 leaves theory; 2... Nb8 returns to the start and 3. e4 reaches a named position
    assert _epd("e4") in openings.book()[0]
    result = _analyze("1. Nf3 Nc6 2. Ng1 Nb8 3. e4 *\n", ScriptedEngine())
    assert _classes(result) == ["book", "book", "excellent", "excellent", "excellent"]
    assert result["opening"] == _row("1. Nf3 Nc6")


@pytest.mark.parametrize("fen, movetext", [
    ("4k3/8/8/8/8/8/4P3/4K3 w - - 0 1", "1. e4 Kd7 2. e5"),
    # the position after 1. Nf3 Nc6 2. Ng1: its moves reach theory, from outside it
    ("r1bqkbnr/pppppppp/2n5/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1", "1... Nb8 2. e4"),
], ids=["no-theory-in-reach", "reaches-theory"])
def test_analyze_from_a_fen_out_of_theory_has_no_book(fen, movetext):
    result = _analyze(_from_fen(fen, movetext), ScriptedEngine())
    assert "book" not in _classes(result)
    assert result["opening"] is None


def test_analyze_from_a_theory_fen_with_black_to_move():
    result = _analyze(_from_fen(AFTER_E4, "1... e5 2. Nf3 Nc6"), ScriptedEngine())
    assert [(m["color"], m["classification"]) for m in result["moves"]] == [
        ("black", "book"), ("white", "book"), ("black", "book")]
    assert result["opening"] == _row("1. e4 e5 2. Nf3 Nc6")


def test_analyze_named_first_position_is_not_the_opening():
    # the FEN is the row 1. e4, but no Book move reaches it: 1... b5 leaves theory at once
    result = _analyze(_from_fen(AFTER_E4, "1... b5"), ScriptedEngine())
    assert _classes(result) == ["excellent"]
    assert result["opening"] is None


def test_analyze_book_has_no_ply_limit():
    line = "1. e4 c5 2. Nf3 Nc6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 d6 6. Bg5 e6 7. Qd2 Be7 8. O-O-O O-O 9. f4 e5"
    result = _analyze(line + " *\n", ScriptedEngine())
    assert _classes(result) == ["book"] * 18
    assert result["opening"] == _row(line)


@pytest.mark.parametrize("black_cp, expected", [
    (-55, "book"),     # drop 0.0994, an inaccuracy
    (-54, "mistake"),  # drop 0.1004
    (54, "mistake"),   # drop 0.1994
    (55, "blunder"),   # drop 0.2004
])
def test_analyze_theory_move_is_book_up_to_an_inaccuracy(black_cp, expected):
    scores = iter([168, black_cp])  # side to move, P0-P1: 1. e4 falls from 0.65, beyond any cp range for Book
    result = _analyze("1. e4 *\n", ScriptedEngine(lambda board: next(scores)))
    assert _classes(result) == [expected]
    assert result["opening"] == _row("1. e4")  # the opening stays on the positions, whatever the class


def test_analyze_line_one_theory_move_is_book_whatever_the_drop():
    assert list(chess.Board().legal_moves)[0] == chess.Move.from_uci("g1h3")  # Nh3 is ScriptedEngine's line 1
    scores = iter([168, 55])  # side to move, P0-P1: the drop of 0.2004 of the blunder row above
    result = _analyze("1. Nh3 *\n", ScriptedEngine(lambda board: next(scores)))
    assert _classes(result) == ["book"]  # a line-1 move has the base class best, whatever the drop


def test_analyze_theory_goes_on_after_a_theory_blunder():
    # side to move, P0-P7: 3. Nxe5 drops 0.25; the inaccuracy 3... Nxe5 stays Book, above Miss; 4. d4 reaches a name
    scores = iter([0, 0, 0, 0, 0, 300, -205, 205])
    result = _analyze("1. e4 e5 2. Nf3 Nc6 3. Nxe5 Nxe5 4. d4 *\n", ScriptedEngine(lambda board: next(scores)))
    assert _classes(result) == ["book", "book", "book", "book", "blunder", "book", "book"]
    assert result["opening"] == _row("1. e4 e5 2. Nf3 Nc6 3. Nxe5 Nxe5 4. d4")


@pytest.mark.parametrize("reply", ["a6", "Nxe5"], ids=["out-of-theory", "theory"])  # 3... Nxe5 lies along a row
def test_analyze_miss_after_a_theory_blunder(reply):
    scores = iter([0, 0, 0, 0, 0, 300, 0])  # side to move, P0-P6: 3. Nxe5 drops 0.25, the reply gives it all back
    result = _analyze(f"1. e4 e5 2. Nf3 Nc6 3. Nxe5 {reply} *\n", ScriptedEngine(lambda board: next(scores)))
    assert _classes(result) == ["book", "book", "book", "book", "blunder", "miss"]
    assert result["opening"] == _row("1. e4 e5 2. Nf3 Nc6 3. Nxe5")


@pytest.mark.parametrize("line, classes", [
    ("1. e4 e5", ["book", "great"]),  # the opening follows the position, whatever the class of its move
    ("1. e4 e5 2. Nf3", ["book", "great", "book"]),  # a Great does not end the sequence
])
def test_analyze_great_in_theory_keeps_the_book_going(monkeypatch, line, classes):
    greats = iter([False, True])  # stubbed: which rule makes the Great plays no part here
    monkeypatch.setattr(A, "is_great_move", lambda *args: next(greats, False))
    result = _analyze(line + " *\n", ScriptedEngine())
    assert _classes(result) == classes
    assert result["opening"] == _row(line)
