"""
PHASE 2 + PHASE 3 (strict refactor) - Stockfish-based analysis engine using
the "Expected Points Model".

Per-move pipeline:
  1. Stockfish analyses the BEFORE position with multipv=2 -> best + 2nd best
  2. The eval (cp/mate) is converted to Win Probability [0..1] via Lichess sigmoid
  3. The played move is pushed, Stockfish re-evaluates -> WP AFTER
  4. Drop = WP_best - WP_played; classification via standard thresholds
  5. Special move detection with hierarchy: Brilliant > Great > Book > Miss > base

Accuracy: Lichess-style. Per-move accuracy via the sigmoid formula; game-level
accuracy is the average between harmonic mean and volatility-weighted mean.

Performance Elo: direct accuracy->Elo curve, with an upper cap based on
real_elo (real_elo + 1500) so that a 500-Elo player playing flawlessly does
not produce a 3250 game score. No lower floor.

Tactical comments: returned as STRUCTURED DATA (key + params), not localized
strings. The frontend renders them through its i18n layer in the chosen
language. Keys are documented in `_make_comment` below.
"""

from __future__ import annotations

import io
import math
import statistics
from dataclasses import dataclass, asdict
from typing import Any, Optional

import chess
import chess.engine
import chess.pgn

# -------- Classification thresholds (Win Probability drop, range 0..1) --------
# Aligned with the well-known "Expected Points Model" thresholds.
WP_THRESHOLDS = {
    "excellent_max": 0.02,
    "good_max":      0.05,
    "inaccuracy_max":0.10,
    "mistake_max":   0.20,
    # > 0.20 -> blunder
}

# Standard piece values (knight/bishop = 3, rook = 5, queen = 9).
PIECE_VALUE = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

# Piece-type -> string key used in tactical comment params. The frontend maps
# these to localized names ("the rook" / "la torre", etc.).
PIECE_KEY = {
    chess.PAWN:   "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK:   "rook",
    chess.QUEEN:  "queen",
    chess.KING:   "king",
}

# Lichess sigmoid coefficient cp -> WP.
WP_K = 0.00368208

# Book/theory heuristic.
BOOK_PLY = 16
BOOK_CP_RANGE = 80

# "Great move" thresholds.
GREAT_TURNAROUND_FROM = 0.40   # WP before below this (player was losing)
GREAT_TURNAROUND_TO   = 0.50   # WP after above this (now equal or winning)
GREAT_ONLY_2ND_DROP   = 0.20   # 2nd-best move would drop WP by at least this

# "Miss" threshold.
MISS_PREV_BLUNDER_DROP = 0.15  # opponent lost at least this much WP

# How many plies of Principal Variation we save for the UI "continuation".
PV_PLIES_TO_KEEP = 6

# Per-move cap on cp loss (used for ACPL). Avoids a single mate or catastrophic
# blunder dominating the average. Lichess uses a similar cap.
ACPL_CP_LOSS_CAP = 1000

# Brilliant: STRICT tolerances.
BRILLIANT_WP_FLOOR = 0.55      # after the move the position is still playable/winning
BRILLIANT_WP_CEILING = 0.85    # before the move the position was NOT already crushing
BRILLIANT_TOLERANCE = 0.02     # WP must not drop by more than this
BRILLIANT_SEE_MAX = -2         # SEE must be <= -2 (real sacrifice, not a trade)
BRILLIANT_2ND_GAP = 0.10       # min WP gap between best and 2nd best (unique idea)

# Game phase thresholds.
ENDGAME_NONPAWN_MATERIAL = 24  # sum of non-pawn values across both sides
OPENING_MAX_PLY = 20

# Performance Elo: how much a player can "overperform" their real rating in
# a single game. Empirically: even playing perfectly, a 500-Elo player will
# not automatically produce 3000-Elo quality (long-term planning, deep
# calculation, etc. don't show up in every position). The "true" rating their
# game deserves is at most real + MAX_OVERPERFORMANCE.
PERFORMANCE_ELO_OVERPERFORMANCE_CAP = 1500


@dataclass
class MoveAnalysis:
    ply: int                  # 1-based
    move_number: int
    color: str                # 'white' | 'black'
    san: str
    uci: str
    fen_before: str
    fen_after: str
    best_move_san: str
    best_move_uci: str
    eval_before_cp: int
    eval_after_cp: int
    eval_white_cp: int
    wp_before: float
    wp_after: float
    wp_white_after: float
    wp_drop: float
    cp_loss: int              # max(0, eval_before - eval_after) from mover POV, capped
    classification: str
    is_best: bool
    phase: str                # 'opening' | 'middlegame' | 'endgame'
    see_played: int           # Static Exchange Evaluation of the played move
    comment: Optional[dict]   # structured comment {key, params} or None
    pv_uci: list              # PV from the BEFORE position: best move + responses
    pv_after_uci: list        # PV from the AFTER position: punishment/refutation line
    clock_after: Optional[float] = None  # seconds remaining for the mover, parsed from PGN [%clk]


# ============================================================
# Conversions cp <-> WP (Lichess)
# ============================================================

def cp_to_wp(cp: int) -> float:
    cp_clamped = max(-2000, min(2000, cp))
    return 1.0 / (1.0 + math.exp(-WP_K * cp_clamped))


def score_to_wp(score: chess.engine.PovScore, pov_color: chess.Color) -> float:
    pov = score.pov(pov_color)
    if pov.is_mate():
        m = pov.mate()
        if m is None:
            return 0.5
        return 1.0 if m > 0 else 0.0
    cp = pov.score()
    return 0.5 if cp is None else cp_to_wp(cp)


def score_to_cp(score: chess.engine.PovScore, pov_color: chess.Color) -> int:
    pov = score.pov(pov_color)
    if pov.is_mate():
        m = pov.mate()
        if m is None:
            return 0
        magnitude = 10000 - max(0, abs(m) - 1) * 10
        return magnitude if m > 0 else -magnitude
    cp = pov.score()
    return 0 if cp is None else cp


# ============================================================
# Material and phases
# ============================================================

def material_value(board: chess.Board, color: chess.Color) -> int:
    return sum(len(board.pieces(pt, color)) * v for pt, v in PIECE_VALUE.items())


def non_pawn_material_total(board: chess.Board) -> int:
    """Total non-pawn material on both sides (used for endgame detection)."""
    total = 0
    for color in (chess.WHITE, chess.BLACK):
        for pt, v in PIECE_VALUE.items():
            if pt in (chess.PAWN, chess.KING):
                continue
            total += len(board.pieces(pt, color)) * v
    return total


def detect_phase(board_before: chess.Board, ply: int) -> str:
    """
    Game phase evaluated on the BEFORE position:
      - opening: first OPENING_MAX_PLY plies (typically 10 moves) and high material
      - endgame: total non-pawn material on both sides <= ENDGAME_NONPAWN_MATERIAL
      - middlegame: everything else
    """
    npm = non_pawn_material_total(board_before)
    if npm <= ENDGAME_NONPAWN_MATERIAL:
        return "endgame"
    if ply <= OPENING_MAX_PLY:
        return "opening"
    return "middlegame"


# ============================================================
# Base classifications
# ============================================================

def classify_standard(wp_drop: float, is_best: bool) -> str:
    if is_best or wp_drop <= 0.0:
        return "best"
    if wp_drop < WP_THRESHOLDS["excellent_max"]:
        return "excellent"
    if wp_drop < WP_THRESHOLDS["good_max"]:
        return "good"
    if wp_drop < WP_THRESHOLDS["inaccuracy_max"]:
        return "inaccuracy"
    if wp_drop < WP_THRESHOLDS["mistake_max"]:
        return "mistake"
    return "blunder"


def is_book_move(ply: int, eval_before_cp: int, eval_after_cp: int, base_class: str) -> bool:
    if ply > BOOK_PLY:
        return False
    if base_class not in {"best", "excellent", "good"}:
        return False
    return abs(eval_before_cp) <= BOOK_CP_RANGE and abs(eval_after_cp) <= BOOK_CP_RANGE


# ============================================================
# SEE (Static Exchange Evaluation)
# ============================================================

def static_exchange_evaluation(board: chess.Board, move: chess.Move) -> int:
    """
    Simulates the optimal capture sequence on the destination square (each side
    alternates using the lowest-valued attacker) and returns the net material
    change for the side making the initial move.

    SEE > 0  -> winning capture (gains material)
    SEE == 0 -> even trade
    SEE < 0  -> net loss (real sacrifice; the more negative, the riskier)

    The pruning step `max(-gain[d-1], gain[d])` reflects each side's option to
    NOT continue the capture sequence if the running balance is unfavourable.
    """
    target = move.to_square
    captured = board.piece_at(target)
    initial_gain = PIECE_VALUE[captured.piece_type] if captured else 0

    moving_piece = board.piece_at(move.from_square)
    if moving_piece is None:
        return 0

    b = board.copy(stack=False)
    try:
        b.push(move)
    except Exception:  # pragma: no cover
        return initial_gain

    gain = [initial_gain]
    on_square = PIECE_VALUE[moving_piece.piece_type]
    side = b.turn  # opponent of the original mover

    while True:
        attackers = b.attackers(side, target)
        if not attackers:
            break
        atk_sq = min(attackers, key=lambda sq: PIECE_VALUE[b.piece_at(sq).piece_type])
        atk_piece = b.piece_at(atk_sq)
        atk_value = PIECE_VALUE[atk_piece.piece_type]

        gain.append(on_square - gain[-1])
        # Pruning: if the previous side wouldn't continue, stop.
        if max(-gain[-2], gain[-1]) < 0:
            gain.pop()
            break

        on_square = atk_value

        recap = chess.Move(atk_sq, target)
        if recap not in b.legal_moves:
            # The smallest attacker can't legally capture (pinned, or king moving
            # into an attacked square). Roll back the speculative gain we just
            # appended so this would-be recapture doesn't count.
            if atk_piece.piece_type == chess.PAWN and chess.square_rank(target) in (0, 7):
                recap = chess.Move(atk_sq, target, promotion=chess.QUEEN)
                if recap not in b.legal_moves:
                    gain.pop()
                    break
            else:
                gain.pop()
                break
        try:
            b.push(recap)
        except Exception:  # pragma: no cover
            break
        side = not side

    # Negamax over the gain array.
    while len(gain) > 1:
        gain[-2] = -max(-gain[-2], gain[-1])
        gain.pop()
    return gain[0]


# ============================================================
# Tactical comment generation (structured, language-agnostic)
# ============================================================

def _make_comment(
    classification: str,
    board_before: chess.Board,
    played: chess.Move,
    see_played: int,
    pv_after_uci: list,
) -> Optional[dict]:
    """
    Returns a structured comment object {"key": ..., "params": {...}} or None
    for non-error classifications. The frontend i18n layer renders the actual
    text in the chosen language.

    Comment keys (defined in static/js/i18n.js under "comment.*"):
      - miss_default            (no params)
      - hanging_severe          {piece}
      - exchanges_lose_piece    {piece}
      - exchanges_lose_minor    (no params)
      - punishment_capture      {piece}
      - blunder_default         (no params)
      - mistake_default         (no params)
      - inaccuracy_default      (no params)
    """
    if classification not in {"inaccuracy", "mistake", "blunder", "miss"}:
        return None

    moved = board_before.piece_at(played.from_square)
    piece_key = PIECE_KEY.get(moved.piece_type, "generic") if moved else "generic"

    if classification == "miss":
        return {"key": "miss_default", "params": {}}

    # Classic tactical error: piece lands on a square where exchanges are losing.
    if see_played <= -3:
        return {"key": "hanging_severe", "params": {"piece": piece_key}}
    if see_played == -2:
        return {"key": "exchanges_lose_piece", "params": {"piece": piece_key}}
    if see_played == -1:
        return {"key": "exchanges_lose_minor", "params": {}}

    # SEE >= 0 on the played square: error is elsewhere. Look at the first
    # punishment move from pv_after.
    captured_elsewhere_key = None
    if pv_after_uci:
        try:
            after_board = board_before.copy(stack=False)
            after_board.push(played)
            mv = chess.Move.from_uci(pv_after_uci[0])
            target_piece = after_board.piece_at(mv.to_square)
            if target_piece and target_piece.color == board_before.turn:
                captured_elsewhere_key = PIECE_KEY.get(target_piece.piece_type, "generic")
        except Exception:
            pass

    if captured_elsewhere_key is not None:
        return {"key": "punishment_capture", "params": {"piece": captured_elsewhere_key}}

    if classification == "blunder":
        return {"key": "blunder_default", "params": {}}
    if classification == "mistake":
        return {"key": "mistake_default", "params": {}}
    return {"key": "inaccuracy_default", "params": {}}


# ============================================================
# Brilliant !! and Great !
# ============================================================

def is_brilliant(
    board_before: chess.Board,
    played: chess.Move,
    wp_before: float,
    wp_after: float,
    eval_after_cp: int,
    second_best_wp: Optional[float],
    base_class: str,
) -> bool:
    """
    Brilliant !! - VERY STRICT rules. A move is brilliant when:

      1. base = best or excellent (move is strong per Stockfish)
      2. WP doesn't drop more than BRILLIANT_TOLERANCE (advantage maintained)
      3. AFTER the move position is still playable/winning (WP >= BRILLIANT_WP_FLOOR)
      4. BEFORE the move position was not already completely won
         (WP <= BRILLIANT_WP_CEILING) - if you're already up +6, sacrificing
         a piece isn't "brilliant", just luxurious
      5. NO forced mate after the move (a sacrifice that leads to mate-in-3
         isn't "positional brilliance": the engine just sees it)
      6. The move is a REAL sacrifice: SEE <= BRILLIANT_SEE_MAX (-2 default).
         SEE >= 0 = favourable trade or "free" capture, not a sacrifice.
         SEE = -1 = exchange sacrifice (minor), too trivial for brilliant.
      7. Sacrificed piece must be of value >= 3 (no pawns)
      8. 2nd-best move must be substantially worse: the brilliant idea is
         unique, not one of many good options (gap WP >= BRILLIANT_2ND_GAP)
    """
    if base_class not in {"best", "excellent"}:
        return False
    if wp_after < wp_before - BRILLIANT_TOLERANCE:
        return False
    if wp_after < BRILLIANT_WP_FLOOR:
        return False
    if wp_before > BRILLIANT_WP_CEILING:
        return False
    # No forced mate after: |eval_after_cp| above ~9000 indicates a mate score.
    if abs(eval_after_cp) >= 9000:
        return False

    moved_piece = board_before.piece_at(played.from_square)
    moved_value = PIECE_VALUE.get(moved_piece.piece_type, 0) if moved_piece else 0
    if moved_value < 3:
        return False

    see = static_exchange_evaluation(board_before, played)
    if see > BRILLIANT_SEE_MAX:
        return False

    # 2nd-best must be visibly worse (unique brilliant move, not one of many).
    if second_best_wp is not None and (wp_after - second_best_wp) < BRILLIANT_2ND_GAP:
        return False

    return True


def is_great_move(
    is_best: bool,
    wp_before: float,
    wp_after: float,
    second_best_wp: Optional[float],
) -> bool:
    """
    Great ! - strict rules, evaluated only if the move is NOT brilliant:
      A) Swing turnaround: wp_before < 0.40 (losing) and wp_after > 0.50
         (equal/winning). Recognises the move that punishes an opponent's
         mistake by flipping the game.
      B) Only saving move: the move is the best, and the second-best move
         would drop WP by at least GREAT_ONLY_2ND_DROP relative to the best.
    """
    if wp_before < GREAT_TURNAROUND_FROM and wp_after > GREAT_TURNAROUND_TO:
        return True

    # Only-move: played move is best AND every alternative is significantly worse.
    if is_best and second_best_wp is not None:
        if (wp_after - second_best_wp) >= GREAT_ONLY_2ND_DROP:
            return True

    return False


# ============================================================
# Accuracy (Lichess: harmonic + volatility-weighted)
# ============================================================

def move_accuracy_lichess(wp_before: float, wp_after: float) -> float:
    """
    Per-move accuracy, official Lichess formula:
      acc = 103.1668 * exp(-0.04354 * wp_loss_percent) - 3.1669 + 1
    Clamped to [0, 100]. The "+1" is documented as the analysis "uncertainty bonus".
    """
    wp_loss_pct = max(0.0, (wp_before - wp_after) * 100.0)
    raw = 103.1668 * math.exp(-0.04354 * wp_loss_pct) - 3.1669 + 1.0
    return max(0.0, min(100.0, raw))


def _harmonic_mean(values: list[float]) -> float:
    """Robust harmonic mean: protects against division by zero (1.0 floor for safety)."""
    if not values:
        return 0.0
    safe = [max(v, 1.0) for v in values]
    return len(safe) / sum(1.0 / v for v in safe)


def _volatility_weighted_mean(
    accuracies: list[float],
    win_pcts: list[float],
) -> float:
    """
    Lichess-style volatility-weighted mean: volatile moments weigh more.

      windowSize = clamp(N/10, 2, 8)
      weights[i] = std_dev(win_pcts[i..i+W]) clamped to [0.5, 12]

    Returns the weighted mean of per-move accuracies.
    """
    n = len(accuracies)
    if n == 0:
        return 0.0
    if n == 1:
        return accuracies[0]

    window = max(2, min(8, n // 10))
    weights = []
    for i in range(n):
        end = min(n, i + window)
        chunk = win_pcts[i:end]
        if len(chunk) < 2:
            chunk = win_pcts[max(0, n - window):]
        try:
            w = statistics.pstdev(chunk)
        except statistics.StatisticsError:
            w = 0.5
        weights.append(max(0.5, min(12.0, w)))

    total_w = sum(weights)
    if total_w <= 0:
        return sum(accuracies) / n
    return sum(a * w for a, w in zip(accuracies, weights)) / total_w


def game_accuracy_lichess(per_move: list[float], win_pcts: list[float]) -> float:
    """Game accuracy = (harmonic_mean + volatility_weighted_mean) / 2."""
    if not per_move:
        return 0.0
    h = _harmonic_mean(per_move)
    v = _volatility_weighted_mean(per_move, win_pcts)
    return round(max(0.0, min(100.0, (h + v) / 2.0)), 1)


# ============================================================
# Performance Elo (direct curve, capped by real_elo)
# ============================================================

def _accuracy_to_elo(accuracy: float) -> int:
    """
    Maps accuracy -> Elo. The curve is calibrated so that low accuracy means
    a low score (50% -> ~330, not 1400). Piecewise linear interpolation:

       0% -> 100
      30% -> 150
      45% -> 250
      55% -> 450
      65% -> 800
      70% -> 1100
      75% -> 1400
      80% -> 1700
      85% -> 2000
      90% -> 2300
      95% -> 2600
     100% -> 2900
    """
    points = [
        (0,   100),
        (30,  150),
        (45,  250),
        (55,  450),
        (65,  800),
        (70,  1100),
        (75,  1400),
        (80,  1700),
        (85,  2000),
        (90,  2300),
        (95,  2600),
        (100, 2900),
    ]
    if accuracy <= points[0][0]:
        return points[0][1]
    if accuracy >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        if accuracy <= x1:
            t_ = (accuracy - x0) / (x1 - x0)
            return int(round(y0 + t_ * (y1 - y0)))
    return points[-1][1]


def estimate_performance_elo(accuracy: float, real_elo: Optional[int] = None) -> int:
    """
    Performance Elo for the game.

    Computation:
      1. Pure curve accuracy -> Elo (see _accuracy_to_elo): the starting point
         is the playing quality reflected by the precision.
      2. If real_elo is known, apply an UPPER CAP = real_elo + 1500.
         Example: real_elo=500, accuracy=98% -> pure=2700, cap=2000 -> returns 2000.
         A 500-Elo player cannot "perform" at 3250 in a single game: their true
         playing limit is at most a meaningful jump above their rating, not an
         astronomical value.
      3. No lower floor: a bad game stays a bad game regardless of real rating
         (a 2500 can perform 800 on a bad day).
      4. Final clamp in [100, 2900].
    """
    pure = _accuracy_to_elo(accuracy)
    if real_elo and real_elo > 0:
        ceiling = real_elo + PERFORMANCE_ELO_OVERPERFORMANCE_CAP
        pure = min(pure, ceiling)
    return max(100, min(2900, pure))


# ============================================================
# Per-phase statistics
# ============================================================

def _phase_label_from_drop(mean_drop: float) -> str:
    """Map a phase's mean drop to a classification label."""
    if mean_drop < WP_THRESHOLDS["excellent_max"]:
        return "excellent"
    if mean_drop < WP_THRESHOLDS["good_max"]:
        return "good"
    if mean_drop < WP_THRESHOLDS["inaccuracy_max"]:
        return "inaccuracy"
    if mean_drop < WP_THRESHOLDS["mistake_max"]:
        return "mistake"
    return "blunder"


def compute_phases(moves: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    For each phase (opening/middlegame/endgame) and each color: mean WP drop
    and mean classification.
    """
    out: dict[str, dict[str, Any]] = {}
    for phase in ("opening", "middlegame", "endgame"):
        out[phase] = {}
        for color in ("white", "black"):
            drops = [m["wp_drop"] for m in moves if m["phase"] == phase and m["color"] == color]
            if not drops:
                out[phase][color] = {"label": None, "mean_drop": None, "moves": 0}
                continue
            mean_drop = sum(drops) / len(drops)
            out[phase][color] = {
                "label": _phase_label_from_drop(mean_drop),
                "mean_drop": round(mean_drop, 4),
                "moves": len(drops),
            }
    return out


# ============================================================
# GameAnalyzer
# ============================================================

class GameAnalyzer:
    def __init__(self, engine_path: str, depth: int = 15, threads: int = 2, hash_mb: int = 128):
        self.engine_path = engine_path
        self.limit = chess.engine.Limit(depth=depth)
        self.threads = threads
        self.hash_mb = hash_mb

    def analyze_pgn(
        self,
        pgn_string: str,
        white_elo: Optional[int] = None,
        black_elo: Optional[int] = None,
    ) -> dict[str, Any]:
        game = chess.pgn.read_game(io.StringIO(pgn_string))
        if game is None:
            raise ValueError("invalid or empty PGN")

        headers = dict(game.headers)
        if white_elo is None:
            try: white_elo = int(headers.get("WhiteElo", 0)) or None
            except ValueError: white_elo = None
        if black_elo is None:
            try: black_elo = int(headers.get("BlackElo", 0)) or None
            except ValueError: black_elo = None

        moves_out: list[dict[str, Any]] = []

        engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
        try:
            engine.configure({"Threads": self.threads, "Hash": self.hash_mb})

            board = game.board()
            ply_index = 0

            # Iterate nodes (not just moves) to read the [%clk ...] PGN annotation
            # via node.clock() — returns float seconds remaining, or None if absent.
            for node in game.mainline():
                played = node.move
                if played is None:
                    continue
                clock_after = node.clock()
                ply_index += 1
                color_to_move = board.turn
                color_str = "white" if color_to_move == chess.WHITE else "black"
                move_number = (ply_index + 1) // 2
                phase = detect_phase(board, ply_index)

                # 1) BEFORE analysis with multipv=2
                infos_before = engine.analyse(board, self.limit, multipv=2)
                best_info = infos_before[0]
                second_info = infos_before[1] if len(infos_before) > 1 else None

                best_pv = best_info.get("pv", []) or []
                best_move = best_pv[0] if best_pv else None
                pv_uci = [mv.uci() for mv in best_pv[:PV_PLIES_TO_KEEP]]

                score_best = best_info.get("score")
                wp_before_best = score_to_wp(score_best, color_to_move) if score_best else 0.5
                eval_before_cp = score_to_cp(score_best, color_to_move) if score_best else 0

                second_best_wp: Optional[float] = None
                if second_info is not None:
                    score_2nd = second_info.get("score")
                    if score_2nd is not None:
                        second_best_wp = score_to_wp(score_2nd, color_to_move)

                best_move_san = board.san(best_move) if best_move else ""
                best_move_uci = best_move.uci() if best_move else ""

                played_san = board.san(played)
                played_uci = played.uci()
                fen_before = board.fen()
                board_before_snap = board.copy(stack=False)

                board.push(played)
                fen_after = board.fen()

                # 2) AFTER analysis -> residual WP + "punishment" PV
                #    (the line the opponent uses to exploit the just-played move)
                info_after = engine.analyse(board, self.limit)
                score_after = info_after.get("score")
                wp_after = score_to_wp(score_after, color_to_move) if score_after else 0.5
                eval_after_cp = score_to_cp(score_after, color_to_move) if score_after else 0
                eval_white_cp = score_to_cp(score_after, chess.WHITE) if score_after else 0
                wp_white_after = score_to_wp(score_after, chess.WHITE) if score_after else 0.5
                pv_after_raw = info_after.get("pv", []) or []
                pv_after_uci = [mv.uci() for mv in pv_after_raw[:PV_PLIES_TO_KEEP]]

                wp_drop = max(0.0, wp_before_best - wp_after)
                cp_loss = min(ACPL_CP_LOSS_CAP, max(0, eval_before_cp - eval_after_cp))
                is_best = best_move is not None and played == best_move
                see_played = static_exchange_evaluation(board_before_snap, played)

                base_class = classify_standard(wp_drop, is_best)
                final_class = base_class

                # ===== ASSIGNMENT HIERARCHY =====
                # 1) Brilliant (requires sacrifice + strong base + WP doesn't crash)
                # 2) Great   (turnaround or only saving move) - only if NOT brilliant
                # 3) Book    (heuristic on first plies) - only replaces "good" classes
                # 4) Miss    (opponent blundered and we didn't punish) - only replaces
                #            classes below "best/excellent"
                # 5) Standard (already set in base_class)

                if is_brilliant(
                    board_before_snap, played,
                    wp_before_best, wp_after, eval_after_cp,
                    second_best_wp, base_class,
                ):
                    final_class = "brilliant"
                elif is_great_move(is_best, wp_before_best, wp_after, second_best_wp):
                    final_class = "great"
                elif is_book_move(ply_index, eval_before_cp, eval_after_cp, base_class):
                    final_class = "book"
                else:
                    # MISS: the opponent's previous move was a blunder and ours
                    # doesn't take advantage (regression to neutral/worse).
                    if (final_class not in {"best", "excellent"}
                            and len(moves_out) >= 1
                            and moves_out[-1]["classification"] == "blunder"
                            and moves_out[-1]["wp_drop"] >= MISS_PREV_BLUNDER_DROP):
                        final_class = "miss"

                comment = _make_comment(
                    final_class, board_before_snap, played, see_played, pv_after_uci,
                )

                moves_out.append(asdict(MoveAnalysis(
                    ply=ply_index,
                    move_number=move_number,
                    color=color_str,
                    san=played_san,
                    uci=played_uci,
                    fen_before=fen_before,
                    fen_after=fen_after,
                    best_move_san=best_move_san,
                    best_move_uci=best_move_uci,
                    eval_before_cp=eval_before_cp,
                    eval_after_cp=eval_after_cp,
                    eval_white_cp=eval_white_cp,
                    wp_before=round(wp_before_best, 4),
                    wp_after=round(wp_after, 4),
                    wp_white_after=round(wp_white_after, 4),
                    wp_drop=round(wp_drop, 4),
                    cp_loss=cp_loss,
                    classification=final_class,
                    is_best=is_best,
                    phase=phase,
                    see_played=see_played,
                    comment=comment,
                    pv_uci=pv_uci,
                    pv_after_uci=pv_after_uci,
                    clock_after=round(clock_after, 2) if clock_after is not None else None,
                )))
        finally:
            engine.quit()

        accuracy = self._compute_accuracy(moves_out)
        acpl = self._compute_acpl(moves_out)
        performance = {
            "white": estimate_performance_elo(accuracy["white"], white_elo),
            "black": estimate_performance_elo(accuracy["black"], black_elo),
        }
        wp_series = [{
            "ply": m["ply"],
            "wp_white": round(m["wp_white_after"] * 100, 1),
            "classification": m["classification"],
            "color": m["color"],
        } for m in moves_out]
        move_counts = self._count_classifications(moves_out)
        phases = compute_phases(moves_out)

        return {
            "headers": headers,
            "moves": moves_out,
            "accuracy": accuracy,
            "acpl": acpl,
            "performance_elo": performance,
            "wp_series": wp_series,
            "move_counts": move_counts,
            "phases": phases,
            "real_elo": {"white": white_elo, "black": black_elo},
            "time_control": headers.get("TimeControl", ""),
        }

    @staticmethod
    def _count_classifications(moves: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {"white": {}, "black": {}}
        for m in moves:
            out[m["color"]][m["classification"]] = out[m["color"]].get(m["classification"], 0) + 1
        return out

    @staticmethod
    def _compute_acpl(moves: list[dict[str, Any]]) -> dict[str, int]:
        """
        ACPL (Average Centipawn Loss) per player - Lichess's signature metric.
        Arithmetic mean of cp_loss per move, already capped at ACPL_CP_LOSS_CAP
        during analysis (so a single mate or catastrophic blunder doesn't dominate).
        """
        out: dict[str, int] = {}
        for color in ("white", "black"):
            losses = [m["cp_loss"] for m in moves if m["color"] == color]
            out[color] = int(round(sum(losses) / len(losses))) if losses else 0
        return out

    @staticmethod
    def _compute_accuracy(moves: list[dict[str, Any]]) -> dict[str, float]:
        """
        Per-player game accuracy (Lichess style):
        average between harmonic mean and volatility-weighted mean of per-move accuracies.
        """
        out: dict[str, float] = {}
        for color in ("white", "black"):
            mvs = [m for m in moves if m["color"] == color]
            if not mvs:
                out[color] = 0.0
                continue
            per_move = [move_accuracy_lichess(m["wp_before"], m["wp_after"]) for m in mvs]
            win_pcts = [m["wp_before"] * 100.0 for m in mvs]
            out[color] = game_accuracy_lichess(per_move, win_pcts)
        return out


if __name__ == "__main__":
    import json
    import sys
    a = GameAnalyzer(sys.argv[1], depth=12)
    with open(sys.argv[2], "r", encoding="utf-8") as f:
        result = a.analyze_pgn(f.read())
    print(json.dumps(result, indent=2, ensure_ascii=False)[:3000])
