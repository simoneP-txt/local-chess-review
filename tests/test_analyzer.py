import itertools
import threading

import chess
import pytest
from chess.engine import Cp, Mate, PovScore

import analyzer as A
from tests.conftest import FakeEngine
from tests.engine_replay import ScriptedEngine, _analyze

W, B = chess.WHITE, chess.BLACK


@pytest.mark.parametrize("cp, wp", [
    (0, 0.5), (100, 0.591026), (-100, 0.408974), (300, 0.751126),
    (2000, 0.999367), (5000, 0.999367), (-5000, 0.000633),  # code clamps cp to ±2000
])
def test_cp_to_wp(cp, wp):
    assert A.cp_to_wp(cp) == pytest.approx(wp, abs=1e-6)


def test_score_cp_from_both_sides():
    s = PovScore(Cp(100), W)
    assert A.score_to_wp(s, W) == pytest.approx(0.591026, abs=1e-6)
    assert A.score_to_wp(s, B) == pytest.approx(0.408974, abs=1e-6)
    assert (A.score_to_cp(s, W), A.score_to_cp(s, B)) == (100, -100)


def test_score_mate_in_n():
    s = PovScore(Mate(3), W)
    assert (A.score_to_wp(s, W), A.score_to_wp(s, B)) == (1.0, 0.0)
    assert (A.score_to_cp(s, W), A.score_to_cp(s, B)) == (9980, -9980)
    assert A.score_to_cp(PovScore(Mate(-2), W), W) == -9990


def test_score_side_to_move_is_mated():
    s = PovScore(Mate(0), W)  # what Stockfish returns when White is checkmated
    assert A.score_to_wp(s, W) == 0.0
    assert A.score_to_cp(s, W) == -10000


def test_score_mate_given_wp_is_a_win():
    s = PovScore(Mate(0), W)
    assert A.score_to_wp(s, B) == 1.0


def test_score_mate_given_cp_is_a_win():
    s = PovScore(Mate(0), W)
    assert A.score_to_cp(s, B) == 10000


SEE_OK = [
    ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "e2e4", 0),
    ("4k3/8/8/8/8/5N2/8/4K3 w - - 0 1", "f3e5", 0),       # safe square
    ("4k3/8/3p4/8/8/5N2/8/4K3 w - - 0 1", "f3e5", -3),    # knight hangs to a pawn
    ("4k3/8/3p4/8/4P3/8/8/4K3 w - - 0 1", "e4e5", -1),    # pawn hangs to a pawn
    ("4k3/8/2p5/3r4/8/8/3Q4/4K3 w - - 0 1", "d2d5", -4),  # QxR, pawn recaptures
    ("4k3/8/4n3/8/3p4/8/1B6/4RK2 w - - 0 1", "b2d4", 1),  # only defender is pinned
    ("3R4/2P5/7k/8/8/8/3r4/6K1 b - - 0 1", "d2d8", -8),   # cxd8=Q recaptures and promotes
    ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "e5d6", 1),     # free en passant
    ("4k3/2p5/8/3pP3/8/8/8/4K3 w - d6 0 1", "e5d6", 0),   # en passant, c7 retakes
    ("3rk3/8/4p3/3n4/5N2/8/8/4RK2 w - - 0 1", "f4d5", 0),  # e6 is pinned, the d8 rook retakes
    ("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e7e8q", 8),       # free promotion
    ("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e7e8n", 2),       # free underpromotion
    ("8/8/3p1k2/4p3/8/5N2/1B6/4R1K1 w - - 0 1", "f3e5", -1),  # dxe5 Bxe5, Kxe5 never legal
    ("7k/1P1K4/8/8/8/8/2r5/8 b - - 0 1", "c2c8", -13),    # bxc8=Q, the king captures last
    ("3rk3/8/8/3p4/8/8/3R4/3RK3 w - - 0 1", "d2d5", 1),   # Rd1 recaptures through d2
    ("r7/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e7e8n", -1),     # Rxe8 takes the new knight
    ("3r3k/8/8/8/8/5Q2/2p4K/1R1n4 w - - 0 1", "b1d1", -10),  # cxd1=Q Qxd1 Rxd1
    ("4k3/2p5/3p4/8/4N3/8/8/4R1K1 w - - 0 1", "e4d6", 1),  # double check: cxd6 is illegal
]
SEE_LOSS_LIMITING = [  # the recapture loses less than stopping: 2 and -2, not 3 and -3
    ("4k3/8/4p3/3n4/4P3/8/8/4K3 w - - 0 1", "e4d5", 2),
    ("4k3/8/3p4/8/3P4/5N2/8/4K3 w - - 0 1", "f3e5", -2),
]


def _see(fen, uci):
    board = chess.Board(fen)
    see = A.static_exchange_evaluation(board, chess.Move.from_uci(uci))
    assert board == chess.Board(fen)  # the caller's board is left alone
    return see


@pytest.mark.parametrize("fen, uci, expected", SEE_OK)
def test_see(fen, uci, expected):
    assert _see(fen, uci) == expected


@pytest.mark.parametrize("fen, uci, expected", SEE_LOSS_LIMITING)
def test_see_keeps_loss_limiting_recapture(fen, uci, expected):
    assert _see(fen, uci) == expected


@pytest.mark.parametrize("drop, is_best, expected", [
    (0.5, True, "best"), (0.0, False, "best"), (0.0001, False, "excellent"),
    (0.0199, False, "excellent"), (0.02, False, "good"), (0.0499, False, "good"),
    (0.05, False, "inaccuracy"), (0.0999, False, "inaccuracy"),
    (0.10, False, "mistake"), (0.1999, False, "mistake"),
    (0.20, False, "blunder"), (0.9, False, "blunder"),
])
def test_classify_standard(drop, is_best, expected):
    assert A.classify_standard(drop, is_best) == expected


def test_blunder_implies_drop_at_least_mistake_max():
    # Miss relies on this: it needs no drop check of its own on the opponent's blunder
    for drop in (i / 1000 for i in range(1001)):
        if A.classify_standard(drop, False) == "blunder":
            assert drop >= A.WP_THRESHOLDS["mistake_max"]


@pytest.mark.parametrize("is_best, base, before, after, second, before_opponent, expected", [
    # (a): before_opponent is the mover's WP before the opponent's last move
    (False, "best", 0.6, 0.6, 0.55, 0.35, True), (False, "excellent", 0.61, 0.6, 0.55, 0.35, True),
    (False, "best", 0.6, 0.6, 0.55, 0.45, False), (False, "best", 0.6, 0.6, 0.55, 0.40, False),
    (False, "best", 0.5, 0.5, 0.45, 0.35, False), (False, "good", 0.63, 0.6, 0.55, 0.35, False),
    (False, "best", 0.6, 0.6, 0.55, None, False),  # first move of the game
    (False, "best", 0.39, 0.51, 0.35, 0.39, False),  # the opponent did not err: 0.51 is search noise
    (False, "best", 0.44, 0.51, 0.40, 0.35, False),  # the opponent gave away 0.09: an inaccuracy, not a mistake
    # just above 0.10: 0.45 - 0.35 is 0.10000000000000003, so this row does not tell >= from >
    (False, "best", 0.45, 0.51, 0.40, 0.35, True),
    (True, "best", 0.6, 0.6, None, 0.35, False),  # only legal move, always line 1: no choice to praise
    # (b): the only saving move
    (True, "best", 0.6, 0.6, 0.39, None, True), (True, "best", 0.6, 0.6, 0.41, None, False),
    (False, "best", 0.6, 0.6, 0.30, None, False), (True, "best", 0.6, 0.6, None, None, False),
    (True, "best", 0.5, 0.75, 0.4, None, False),  # 2nd line 0.10 below the best line; wp_after plays no part
    (True, "best", 0.65, 0.6, 0.44, None, True),  # 2nd line 0.21 below the best line
])
def test_is_great_move(is_best, base, before, after, second, before_opponent, expected):
    assert A.is_great_move(is_best, base, before, after, second, before_opponent) is expected


SAC = ("4k3/8/3p4/8/8/5N2/8/4K3 w - - 0 1", "f3e5")  # knight sacrifice, SEE -3
QUEEN_EN_PRISE = ("4k3/8/8/2p5/3Q4/8/8/1N2K3 w - - 0 1", "b1c3")  # the queen stays en prise to c5
ROOK_DEFENDED = ("3rk3/8/8/8/3R4/8/8/1N1RK3 w - - 0 1", "b1c3")  # rook en prise but defended: even trade
PASSANT_LEAVES_KNIGHT = ("4k3/8/8/2ppP3/3N4/8/8/4K3 w - d6 0 1", "e5d6")  # exd6 takes 1, cxd4 takes 3
PROMOTION_LEAVES_QUEEN = ("8/k3P3/8/2p5/3Q4/8/8/4K3 w - - 0 1", "e7e8q")  # e8=Q gains 8, cxd4 takes 9
SOUND = dict(wp_before=0.6, wp_after=0.6, eval_after_cp=100, second_best_wp=0.45, is_best=True)


def _brilliant(fen=SAC[0], uci=SAC[1], **override):
    return A.is_brilliant(chess.Board(fen), chess.Move.from_uci(uci), **{**SOUND, **override})


@pytest.mark.parametrize("fen, uci, expected", [
    (*QUEEN_EN_PRISE, -9),
    (*ROOK_DEFENDED, 0),
    ("4k3/8/3p4/8/4P3/8/8/4K3 w - - 0 1", "e4e5", 0),      # only a pawn
    (*SAC, -3),
    ("4k3/8/8/2p1p3/3Q4/5N2/8/4K3 w - - 0 1", "f3e5", -8),  # Nxe5 takes 1, cxd4 takes 9
    (*PASSANT_LEAVES_KNIGHT, -2),
    (*PROMOTION_LEAVES_QUEEN, -1),
    ("4k3/3N1p2/6Q1/8/8/8/8/R5K1 w - - 0 1", "a1e1", -3),  # check: Kxd7 parries it, fxg6 is illegal
    ("4k3/8/8/4p3/3Q4/8/8/1N2RK2 w - - 0 1", "b1c3", 0),   # exd4 is illegal: Re1 pins e5
    ("1n2k3/8/8/3q4/2P5/8/8/4K3 b - - 0 1", "b8c6", -9),   # Black to move: cxd5 takes the queen
    ("r7/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e7e8q", 0),       # the moved piece is a pawn: Rxe8 does not count
    ("4k3/8/8/2p5/3P4/8/8/1N2K3 w - - 0 1", "b1c3", 0),    # a pawn en prise does not count
    ("3qk3/8/8/2p5/3R4/8/8/1N1RK3 w - - 0 1", "b1c3", -5),  # the opponent's best: cxd4 (5), not Qxd4 (1)
])
def test_sacrifice_balance(fen, uci, expected):
    board = chess.Board(fen)
    move = chess.Move.from_uci(uci)
    assert board.is_valid() and board.is_legal(move)
    assert A.sacrifice_balance(board, move) == expected
    assert board == chess.Board(fen)  # the caller's board is left alone


def test_brilliant_accepts_sound_sacrifice():
    assert _brilliant()


def test_brilliant_accepts_sacrifice_of_another_piece():
    assert _brilliant(*QUEEN_EN_PRISE)
    assert not _brilliant(*ROOK_DEFENDED)


def test_brilliant_requires_the_best_move():
    assert not _brilliant(is_best=False)


@pytest.mark.parametrize("override", [
    {"wp_after": 0.57},                     # WP drops 0.03 > 0.02
    {"wp_before": 0.55, "wp_after": 0.54},  # after below 0.55
    {"wp_before": 0.86, "wp_after": 0.86},  # before above 0.85
    {"eval_after_cp": 9500},                # forced mate after the move
    {"second_best_wp": 0.55},               # second best only 0.05 worse
    {"second_best_wp": 0.55, "wp_after": 0.66},  # still 0.05 below the best line
    {"second_best_wp": None},               # only legal move: no choice to praise
])
def test_brilliant_rejects(override):
    assert not _brilliant(**override)


def test_brilliant_needs_a_piece_sacrifice():
    assert not _brilliant("4k3/8/3p4/8/4P3/8/8/4K3 w - - 0 1", "e4e5")  # pawn, SEE -1
    assert not _brilliant("4k3/8/8/8/8/5N2/8/4K3 w - - 0 1", "f3e5")    # knight, SEE 0
    assert _brilliant(*PASSANT_LEAVES_KNIGHT)        # balance -2, the limit
    assert not _brilliant(*PROMOTION_LEAVES_QUEEN)   # balance -1


def test_harmonic_mean():
    assert A._harmonic_mean([50, 100]) == pytest.approx(66.666667, abs=1e-6)
    assert A._harmonic_mean([0, 100]) == pytest.approx(1.980198, abs=1e-6)  # 0 floored to 1
    assert A._harmonic_mean([]) == 0.0


@pytest.mark.parametrize("acc, elo", [
    (-5, 100), (0, 100), (30, 150), (50, 350), (65, 800), (72.5, 1250),
    (75, 1400), (85, 2000), (95, 2600), (98, 2780), (100, 2900), (110, 2900),
])
def test_accuracy_to_elo(acc, elo):
    assert A._accuracy_to_elo(acc) == elo


@pytest.mark.parametrize("acc, real, elo", [
    (98, 500, 2000),   # capped at real_elo + 1500
    (50, None, 350), (50, 0, 350),
    (20, 2500, 133),   # no floor
])
def test_estimate_performance_elo(acc, real, elo):
    assert A.estimate_performance_elo(acc, real) == elo


def test_detect_phase():
    start = chess.Board()
    assert [A.detect_phase(start, p) for p in (1, 20, 21)] == ["opening", "opening", "middlegame"]
    assert A.detect_phase(chess.Board("r3k2r/8/8/8/8/8/8/Q3K2R w - - 0 1"), 5) == "endgame"      # 24
    assert A.detect_phase(chess.Board("r3k2r/8/8/8/8/8/8/QN2K2R w - - 0 1"), 25) == "middlegame"  # 27


def test_compute_phases():
    moves = [
        {"phase": "opening", "color": "white", "wp_drop": 0.0},
        {"phase": "opening", "color": "white", "wp_drop": 0.04},
        {"phase": "endgame", "color": "white", "wp_drop": 0.3},
        {"phase": "middlegame", "color": "black", "wp_drop": 0.06},
    ]
    out = A.compute_phases(moves)
    assert out["opening"]["white"] == {"label": "good", "mean_drop": 0.02, "moves": 2}
    assert out["opening"]["black"] == {"label": None, "mean_drop": None, "moves": 0}
    assert out["middlegame"]["black"] == {"label": "inaccuracy", "mean_drop": 0.06, "moves": 1}
    assert out["endgame"]["white"] == {"label": "blunder", "mean_drop": 0.3, "moves": 1}


OPENING = "1. e4 e5 2. Nf3 Nc6 *\n"
BLACK_MATES = "1. f3 e5 2. g4 Qh4# 0-1\n"
WHITE_MATES = "1. e4 f6 2. d4 g5 3. Qh5# 1-0\n"
ONE_MOVE_THEN_STALEMATE = '[SetUp "1"]\n[FEN "7k/8/6K1/5Q2/8/8/8/8 b - - 0 1"]\n\n1... Kg8 2. Qf6 1/2-1/2\n'
OFF_THEORY = '[SetUp "1"]\n[FEN "7k/2P5/8/8/8/6q1/8/7K w - - 0 1"]\n\n'  # the king cannot move: line 1 is c8=Q, line 2 c8=R


def _first_legal(fen):
    return [move.uci() for move in chess.Board(fen).legal_moves][:1]


def test_analyze_searches_each_position_once():
    engine = ScriptedEngine()
    moves = _analyze(OPENING, engine)["moves"]
    assert len(engine.calls) == 5
    assert all(multipv == 2 for _, multipv in engine.calls)
    assert len({fen for fen, _ in engine.calls}) == 5
    for m in moves:  # line 1 plays the first legal move: reading line 2 instead fails here
        assert [m["best_move_uci"]] == _first_legal(m["fen_before"])
        assert m["pv_after_uci"][:1] == _first_legal(m["fen_after"])


def test_analyze_reuses_after_eval_as_next_before():
    scores = itertools.count(10, 10)  # every search answers a new score
    moves = _analyze(OPENING, ScriptedEngine(lambda board: next(scores)))["moves"]
    for k in range(len(moves) - 1):
        # one search of the position between the two moves, seen by its two sides
        assert moves[k]["eval_after_cp"] == -moves[k + 1]["eval_before_cp"]


def test_analyze_great_turnaround_after_the_opponent_blunder():
    scores = iter([200, -200, 200, 200, -200])  # side to move, P0-P4: White +200 until 2. Nd6?? hands it to Black
    moves = _analyze(OFF_THEORY + "1. c8=N Qg4 2. Nd6 Kg8 *\n", ScriptedEngine(lambda board: next(scores)))["moves"]
    # 1. c8=N keeps 0.68 but has no move before it; 1... Qg4 stays at 0.32; 2... Kg8 goes from 0.32 to 0.68
    assert [m["classification"] for m in moves] == ["best", "best", "blunder", "great"]


def test_analyze_no_great_turnaround_after_an_opponent_line_one():
    def classes(movetext):
        scores = iter([168, -33, -54])  # side to move, P0-P2: Black goes 0.35, 0.47, then 0.55 after its reply
        moves = _analyze(OFF_THEORY + movetext, ScriptedEngine(lambda board: next(scores)))["moves"]
        return [m["classification"] for m in moves]

    # c8=Q is the first legal move, so line 1: only the next search gives Black the 0.12 (horizon effect)
    assert classes("1. c8=Q+ Kh7 *\n") == ["best", "best"]
    # same scores, but c8=N is not line 1: a real mistake, and the reply punishes it
    assert classes("1. c8=N Kg8 *\n") == ["mistake", "great"]


def test_analyze_miss_after_the_opponent_blunder():
    scores = iter([0, 900, 0])  # side to move, P0-P2: 1. c8=N?? hands Black 0.96, 1... Kh7 gives it back
    moves = _analyze(OFF_THEORY + "1. c8=N Kh7 *\n", ScriptedEngine(lambda board: next(scores)))["moves"]
    assert [m["classification"] for m in moves] == ["blunder", "miss"]  # Kh7 is not line 1: Kg8 is


def test_analyze_handles_mate_and_single_move():
    assert len(_analyze(BLACK_MATES, ScriptedEngine())["moves"]) == 4
    moves = _analyze(ONE_MOVE_THEN_STALEMATE, ScriptedEngine())["moves"]  # Kg8 is the only legal move
    assert len(moves) == 2 and moves[0]["color"] == "black"


@pytest.mark.parametrize("pgn, white_cp, white_wp, classification", [
    (BLACK_MATES, -10000, 0.0, "book"),  # 1. f3 e5 2. g4 Qh4# is a row of the openings database
    (WHITE_MATES, 10000, 1.0, "best"),
], ids=["black-mates", "white-mates"])
def test_analyze_scores_a_given_mate_as_a_win(pgn, white_cp, white_wp, classification):
    last = _analyze(pgn, ScriptedEngine())["moves"][-1]  # answers Mate(0) for the mated side
    assert (last["eval_after_cp"], last["wp_after"], last["cp_loss"]) == (10000, 1.0, 0)
    assert (last["eval_white_cp"], last["wp_white_after"]) == (white_cp, white_wp)
    assert (last["classification"], last["wp_drop"]) == (classification, 0.0)


def test_analyze_move_number_from_fen():
    moves = _analyze(ONE_MOVE_THEN_STALEMATE, ScriptedEngine())["moves"]
    assert [(m["san"], m["move_number"]) for m in moves] == [("Kg8", 1), ("Qf6", 2)]


@pytest.mark.parametrize("pgn, white_starts, cps_white", [
    (BLACK_MATES, True, [10, -10, 10, -10, -10000]),
    (WHITE_MATES, True, [10, -10, 10, -10, 10, 10000]),
    (ONE_MOVE_THEN_STALEMATE, False, [-10, 10, 0]),  # P0 is Black's +10 seen from White
    ("1. e4 *\n", True, [10, -10]),
], ids=["black-mates", "white-mates", "black-moves-first", "one-ply"])
def test_analyze_accuracy_series(pgn, white_starts, cps_white):
    # ScriptedEngine scores 10 cp for the side to move, Mate(0) for the mated side, 0 for stalemate
    assert _analyze(pgn, ScriptedEngine())["accuracy"] == A.game_accuracy_lichess(white_starts, cps_white)


def test_analyze_accuracy_without_moves():
    assert _analyze("*\n", ScriptedEngine())["accuracy"] == {"white": 0.0, "black": 0.0}


def test_browser_fake_engine_answers_a_mated_position():
    release = threading.Event()
    release.set()  # its first search waits for this
    assert len(_analyze(BLACK_MATES, FakeEngine(release))["moves"]) == 4
