import chess
import pytest
from chess.engine import Cp, Mate, PovScore

import analyzer as A

W, B = chess.WHITE, chess.BLACK
PHASE1 = "Phase 1 bug, see docs/phase-0/notes.md"


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


@pytest.mark.xfail(strict=True, reason=PHASE1)
def test_score_mate_given_wp_is_a_win():
    s = PovScore(Mate(0), W)
    assert A.score_to_wp(s, B) == 1.0


@pytest.mark.xfail(strict=True, reason=PHASE1)
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
    ("3R4/2P5/7k/8/8/8/3r4/6K1 b - - 0 1", "d2d8", 0),    # promotion ignored, notes.md
]
SEE_BUGS = [  # pruning drops a recapture that limits the loss: code gives 3 and -3
    ("4k3/8/4p3/3n4/4P3/8/8/4K3 w - - 0 1", "e4d5", 2),
    ("4k3/8/3p4/8/3P4/5N2/8/4K3 w - - 0 1", "f3e5", -2),
]


def _see(fen, uci):
    return A.static_exchange_evaluation(chess.Board(fen), chess.Move.from_uci(uci))


@pytest.mark.parametrize("fen, uci, expected", SEE_OK)
def test_see(fen, uci, expected):
    assert _see(fen, uci) == expected


@pytest.mark.xfail(strict=True, reason=PHASE1)
@pytest.mark.parametrize("fen, uci, expected", SEE_BUGS)
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


@pytest.mark.parametrize("ply, before, after, base, expected", [
    (16, 80, -80, "good", True), (1, 0, 0, "excellent", True),
    (17, 0, 0, "best", False), (10, 81, 0, "best", False),
    (10, 0, -81, "best", False), (10, 0, 0, "inaccuracy", False),
])
def test_is_book_move(ply, before, after, base, expected):
    assert A.is_book_move(ply, before, after, base) is expected


@pytest.mark.parametrize("is_best, before, after, second, expected", [
    (False, 0.39, 0.51, None, True), (False, 0.40, 0.51, None, False),
    (False, 0.39, 0.50, None, False), (True, 0.6, 0.6, 0.39, True),
    (True, 0.6, 0.6, 0.41, False), (False, 0.6, 0.6, 0.30, False),
    (True, 0.6, 0.6, None, False),
])
def test_is_great_move(is_best, before, after, second, expected):
    assert A.is_great_move(is_best, before, after, second) is expected


SAC = ("4k3/8/3p4/8/8/5N2/8/4K3 w - - 0 1", "f3e5")  # knight sacrifice, SEE -3
SOUND = dict(wp_before=0.6, wp_after=0.6, eval_after_cp=100,
             second_best_wp=0.45, base_class="best")


def _brilliant(fen=SAC[0], uci=SAC[1], **override):
    return A.is_brilliant(chess.Board(fen), chess.Move.from_uci(uci), **{**SOUND, **override})


def test_brilliant_accepts_sound_sacrifice():
    assert _brilliant()
    assert _brilliant(second_best_wp=None)


@pytest.mark.parametrize("override", [
    {"base_class": "good"},
    {"wp_after": 0.57},                     # WP drops 0.03 > 0.02
    {"wp_before": 0.55, "wp_after": 0.54},  # after below 0.55
    {"wp_before": 0.86, "wp_after": 0.86},  # before above 0.85
    {"eval_after_cp": 9500},                # forced mate after the move
    {"second_best_wp": 0.55},               # second best only 0.05 worse
])
def test_brilliant_rejects(override):
    assert not _brilliant(**override)


def test_brilliant_needs_a_piece_sacrifice():
    assert not _brilliant("4k3/8/3p4/8/4P3/8/8/4K3 w - - 0 1", "e4e5")  # pawn, SEE -1
    assert not _brilliant("4k3/8/8/8/8/5N2/8/4K3 w - - 0 1", "f3e5")    # knight, SEE 0


@pytest.mark.parametrize("before, after, acc", [
    (0.5, 0.5, 100.0), (0.6, 0.7, 100.0), (0.6, 0.5, 64.582619),
    (0.55, 0.5, 80.817040), (1.0, 0.0, 0.0),
])
def test_move_accuracy(before, after, acc):
    assert A.move_accuracy_lichess(before, after) == pytest.approx(acc, abs=1e-6)


def test_harmonic_mean():
    assert A._harmonic_mean([50, 100]) == pytest.approx(66.666667, abs=1e-6)
    assert A._harmonic_mean([0, 100]) == pytest.approx(1.980198, abs=1e-6)  # 0 floored to 1
    assert A._harmonic_mean([]) == 0.0


def test_volatility_weighted_mean():
    flat, swing = [50, 50, 50], [50, 50, 80]  # weights 0.5,0.5,0.5 and 0.5,12,12
    assert A._volatility_weighted_mean([100, 50, 100], flat) == pytest.approx(83.333333, abs=1e-6)
    assert A._volatility_weighted_mean([100, 50, 100], swing) == pytest.approx(75.510204, abs=1e-6)
    assert A._volatility_weighted_mean([42.0], [50.0]) == 42.0
    assert A._volatility_weighted_mean([], []) == 0.0


def test_game_accuracy():
    assert A.game_accuracy_lichess([100, 50, 100], [50, 50, 80]) == 75.3  # (75.0 + 75.51) / 2
    assert A.game_accuracy_lichess([], []) == 0.0


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
