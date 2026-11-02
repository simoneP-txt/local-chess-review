"""Game accuracy ported from lila: modules/analyse AccuracyPercent.scala (lichess-org/lila 2e3e641)."""
import pytest

import analyzer as A

# lila's AccuracyPercentTest.scala, same names and tolerances. lila starts every series at cp 15.
# Row: White moves first, cp of the positions after the start (White's side), (White, tolerance), (Black, tolerance).
LILA_CASES = {
    "two good moves": (True, [15, 15], (100, 1), (100, 1)),
    "white blunders on first move": (True, [-900, -900], (10, 5), (100, 1)),
    "black blunders on first move": (True, [15, 900], (100, 1), (10, 5)),
    "both blunder on first move": (True, [-900, 0], (10, 5), (10, 5)),
    "20 perfect moves": (True, [15] * 20, (100, 1), (100, 1)),
    "20 perfect moves and a white blunder": (True, [15] * 20 + [-900], (50, 5), (100, 1)),
    "21 perfect moves and a black blunder": (True, [15] * 21 + [900], (100, 1), (50, 5)),
    "5 average moves (65 cpl) on each side": (True, [-50, 15] * 5, (76, 8), (76, 8)),
    "50 average moves (65 cpl) on each side": (True, [-50, 15] * 50, (76, 8), (76, 8)),
    "50 mediocre moves (150 cpl) on each side": (True, [-135, 15] * 50, (54, 8), (54, 8)),
    "50 terrible moves (500 cpl) on each side": (True, [-435, 15] * 50, (20, 8), (20, 8)),
    "black moves first, two good moves": (False, [15, 15], (100, 1), (100, 1)),
    "black moves first, black blunders on first move": (False, [900, 900], (100, 1), (10, 5)),
    "black moves first, white blunders on first move": (False, [15, -900], (10, 5), (100, 1)),
    "black moves first, both blunder on first move": (False, [900, 0], (10, 5), (10, 5)),
}


@pytest.mark.parametrize("white_starts, cps, white, black", LILA_CASES.values(), ids=list(LILA_CASES))
def test_game_accuracy_passes_lila_tests(white_starts, cps, white, black):
    acc = A._game_accuracy(white_starts, [15] + cps)  # unrounded, as lila's isCloseTo sees it
    assert acc["white"] == pytest.approx(white[0], abs=white[1])
    assert acc["black"] == pytest.approx(black[0], abs=black[1])


@pytest.mark.parametrize("white_starts, cps, expected", [
    (True, [15], {"white": 0.0, "black": 0.0}),  # lila gives no accuracy for these four
    (True, [15, 15], {"white": 100.0, "black": 0.0}),
    (False, [15], {"white": 0.0, "black": 0.0}),
    (False, [15, 15], {"white": 0.0, "black": 100.0}),
])
def test_game_accuracy_of_a_colour_without_moves(white_starts, cps, expected):
    assert A._game_accuracy(white_starts, cps) == expected


def test_lichess_win_pct():
    assert A.lichess_win_pct(0) == 50.0
    # exp(-0.00368208 * 1000) = 0.0251705656, 50 + 50 * (2 / 1.0251705656 - 1) = 97.5447436341
    assert A.lichess_win_pct(1000) == pytest.approx(97.5447436341, abs=1e-9)
    assert A.lichess_win_pct(5000) == A.lichess_win_pct(10000) == A.lichess_win_pct(1000)  # cap; 10000 is a mate
    assert A.lichess_win_pct(-1000) == pytest.approx(100 - A.lichess_win_pct(1000), abs=1e-9)
    assert A.lichess_win_pct(-10000) == A.lichess_win_pct(-1000)


@pytest.mark.parametrize("before, after, expected", [
    (50, 50, 100.0), (40, 60, 100.0),
    # 103.1668100711649 * exp(-0.04354415386753951 * 0.1) - 3.166924740191411 + 1 = 100.5516, capped
    (50, 49.9, 100.0),
    # drop 10: exp(-0.4354415386753951) = 0.6469789378, * 103.1668100711649 = 66.7467531939,
    # - 3.166924740191411 + 1 = 64.5798284537
    (60, 50, 64.5798284537),
    # drop 30: exp(-1.3063246160261853) = 0.2708135734, * 103.1668100711649 = 27.9389724871,
    # - 3.166924740191411 + 1 = 25.7720477469
    (80, 50, 25.7720477469),
    # drop 100: 103.1668100711649 * 0.0128499496 - 2.166924740191411 = -0.8412364263, floored
    (100, 0, 0.0),
])
def test_move_accuracy_lichess(before, after, expected):
    assert A.move_accuracy_lichess(before, after) == pytest.approx(expected, abs=1e-6)


def test_game_accuracy_of_a_short_series_by_hand():
    # Win% of P0-P4 (cp from White's side, capped at 1000): 0 -> 50, 0 -> 50, 200 -> 67.6211637485,
    # 100 -> 59.1025897192, 10000 -> 97.5447436341.
    # 5 positions: window max(2, min(8, 4 // 10)) = 2, no copy of the first window, move i uses [P(i), P(i+1)].
    # Weights, population standard deviation clamped to [0.5, 12]:
    #   move 0 (White) [50, 50] -> 0 -> 0.5            move 1 (Black) [50, 67.62] -> 8.8105818743
    #   move 2 (White) [67.62, 59.10] -> 4.2592870147  move 3 (Black) [59.10, 97.54] -> 19.22 -> 12
    # Accuracy from the mover's win% (Black's is 100 - White's):
    #   move 0: 50 -> 50, no drop -> 100
    #   move 1: 50 -> 32.3788362515, drop 17.6211637485 -> 45.7298671917
    #   move 2: 67.6211637485 -> 59.1025897192, drop 8.5185740293 -> 69.0273991942
    #   move 3: 40.8974102808 -> 2.4552563659, drop 38.4421539149 -> 17.1777297662
    # White: weighted (100 * 0.5 + 69.0273991942 * 4.2592870147) / 4.7592870147 = 72.2813110417,
    #   harmonic 2 / (1/100 + 1/69.0273991942) = 81.6759880626, mean 76.9786495522
    # Black: weighted (45.7298671917 * 8.8105818743 + 17.1777297662 * 12) / 20.8105818743 = 29.2658561815,
    #   harmonic 2 / (1/45.7298671917 + 1/17.1777297662) = 24.9742587175, mean 27.1200574495
    cps = [0, 0, 200, 100, 10000]
    assert A._game_accuracy(True, cps) == pytest.approx({"white": 76.9786495522, "black": 27.1200574495}, abs=1e-6)
    assert A.game_accuracy_lichess(True, cps) == {"white": 77.0, "black": 27.1}


def test_game_accuracy_with_a_window_of_three_by_hand():
    # 31 positions at cp 0 (win% 50) but P1 at -100 (40.8974102808): window 30 // 10 = 3.
    # Windows: 3 - 2 = 1 copy of the first one, then sliding: moves 0 and 1 use [P0, P1, P2],
    # move i >= 1 uses [P(i-1), P(i), P(i+1)], which ends on the position after the move.
    # Weights: moves 0, 1, 2 -> pstdev([50, 40.90, 50]) = 4.2910019445; moves 3-29 -> 0 -> 0.5.
    # Accuracy: move 0 (White 50 -> 40.90) and move 1 (Black 59.10 -> 50) drop 9.1025897192 -> 67.2397212280,
    # every other move 100.
    # White, moves 0, 2, ..., 28: weighted (67.2397212280 * 4.2910019445 + 100 * 4.2910019445 + 13 * 100 * 0.5)
    #   / (2 * 4.2910019445 + 13 * 0.5) = 90.6793274324, harmonic 15 / (1/67.2397212280 + 14/100) = 96.8540752908,
    #   mean 93.7667013616
    # Black, moves 1, 3, ..., 29: weighted (67.2397212280 * 4.2910019445 + 14 * 100 * 0.5) / (4.2910019445 + 7)
    #   = 87.5498719596, same harmonic, mean 92.2019736252
    cps = [0, -100] + [0] * 29
    assert A._game_accuracy(True, cps) == pytest.approx({"white": 93.7667013616, "black": 92.2019736252}, abs=1e-6)
    assert A.game_accuracy_lichess(True, cps) == {"white": 93.8, "black": 92.2}
