use serde_json::{Value, json};
use shakmaty::uci::UciMove;
use shakmaty::{Chess, Move, Position, Square};

use crate::scoring::{
    BRILLIANT_2ND_GAP, BRILLIANT_SEE_MAX, BRILLIANT_TOLERANCE, BRILLIANT_WP_CEILING,
    BRILLIANT_WP_FLOOR, GREAT_ONLY_2ND_DROP, GREAT_TURNAROUND_FROM, GREAT_TURNAROUND_TO,
    INACCURACY_MAX, piece_key,
};
use crate::see::sacrifice_balance;

/// A structured comment `{"key", "params"}` for an inaccuracy, mistake, blunder or miss, else
/// None; the frontend localizes the key (static/js/i18n.js, "comment.*").
pub fn make_comment(
    classification: &str,
    board_before: &Chess,
    played: Move,
    see_played: i32,
    pv_after_uci: &[String],
) -> Option<Value> {
    if !matches!(
        classification,
        "inaccuracy" | "mistake" | "blunder" | "miss"
    ) {
        return None;
    }
    let piece = piece_key(played.role());

    if classification == "miss" {
        return Some(json!({"key": "miss_default", "params": {}}));
    }
    // The piece lands on a square where the exchanges lose.
    if see_played <= -3 {
        return Some(json!({"key": "hanging_severe", "params": {"piece": piece}}));
    }
    if see_played == -2 {
        return Some(json!({"key": "exchanges_lose_piece", "params": {"piece": piece}}));
    }
    if see_played == -1 {
        return Some(json!({"key": "exchanges_lose_minor", "params": {}}));
    }

    // The error is elsewhere: what the first move of the punishment line lands on. Like Python,
    // no legality check; unreadable text means no capture, 0000 lands on a1 (python-chess null).
    let mut after = board_before.clone();
    after.play_unchecked(played);
    // shortcut: UciMove accepts e2e2 (same from and to) and an uppercase promotion (e7e8Q), which
    // python-chess Move.from_uci rejects (no capture), and Stockfish never sends either; add the
    // two checks if a source other than Stockfish fills pv_after_uci.
    let captured = pv_after_uci
        .first()
        .and_then(|uci| uci.parse::<UciMove>().ok())
        .and_then(|uci| after.board().piece_at(uci.to().unwrap_or(Square::A1)))
        .filter(|p| p.color == board_before.turn());
    if let Some(p) = captured {
        return Some(json!({"key": "punishment_capture", "params": {"piece": piece_key(p.role)}}));
    }

    Some(match classification {
        "blunder" => json!({"key": "blunder_default", "params": {}}),
        "mistake" => json!({"key": "mistake_default", "params": {}}),
        _ => json!({"key": "inaccuracy_default", "params": {}}),
    })
}

/// Brilliant: the best move, a real sacrifice of a piece worth at least 3, the advantage kept in a
/// position not already won, no forced mate after, and a unique idea (the 2nd line clearly worse).
pub fn is_brilliant(
    board_before: &Chess,
    played: Move,
    wp_before: f64,
    wp_after: f64,
    eval_after_cp: i32,
    second_best_wp: Option<f64>,
    is_best: bool,
) -> bool {
    if !is_best
        || wp_after < wp_before - BRILLIANT_TOLERANCE
        || wp_after < BRILLIANT_WP_FLOOR
        || wp_before > BRILLIANT_WP_CEILING
        // an |eval_after_cp| of 9000 or more is a forced mate
        || eval_after_cp.abs() >= 9000
        // no 2nd line (an only legal move, no choice to praise) or one too close to the best
        || second_best_wp.is_none_or(|second| wp_before - second < BRILLIANT_2ND_GAP)
    {
        return false;
    }
    sacrifice_balance(board_before, played) <= BRILLIANT_SEE_MAX
}

/// Great, checked only when the move is not Brilliant: (a) a turnaround after an opponent's
/// mistake, or (b) the only saving move.
pub fn is_great_move(
    is_best: bool,
    base_class: &str,
    wp_before: f64,
    wp_after: f64,
    second_best_wp: Option<f64>,
    wp_before_opponent_move: Option<f64>,
) -> bool {
    if let Some(before_opponent) = wp_before_opponent_move
        && second_best_wp.is_some()
        && before_opponent < GREAT_TURNAROUND_FROM
        // without an opponent mistake the jump over 0.50 is search noise
        && wp_before - before_opponent >= INACCURACY_MAX
        && wp_after > GREAT_TURNAROUND_TO
        && matches!(base_class, "best" | "excellent")
    {
        return true;
    }
    // wp_before is the best line, from the same search as second_best_wp
    is_best && second_best_wp.is_some_and(|second| wp_before - second >= GREAT_ONLY_2ND_DROP)
}
