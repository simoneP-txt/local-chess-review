use std::cmp::Reverse;

use shakmaty::{CastlingMode, Chess, Move, Position, Role, Square};

use crate::scoring::piece_value;

/// python-chess `to_square`: the king's destination for castling, where `Move::to()` is the rook.
fn to_square(m: Move) -> Square {
    m.to_uci(CastlingMode::Standard).to().unwrap()
}

/// What a move wins at once: the captured piece, a pawn en passant, plus the promoted piece minus
/// the pawn.
pub fn material_taken(m: Move) -> i32 {
    m.capture().map_or(0, piece_value) + m.promotion().map_or(0, |p| piece_value(p) - 1)
}

/// The net material change for the mover after the optimal capture sequence on the arrival
/// square: each side captures with its cheapest legal capturer, the king last, recaptures promote
/// to a queen. gain[d] is what the side capturing at step d has won if the sequence stops there.
pub fn static_exchange_evaluation(board: &Chess, m: Move) -> i32 {
    let target = to_square(m);
    let mut b = board.clone();
    let mut gain: Vec<i32> = Vec::new();
    let mut next = Some(m);
    while let Some(m) = next {
        gain.push(material_taken(m) - gain.last().copied().unwrap_or(0));
        b.play_unchecked(m);
        // Python's min() keeps the first of equal capturers in python-chess generation order,
        // from h8 down to a1: the highest from square wins the tie (5.8.13).
        next = b
            .legal_moves()
            .into_iter()
            .filter(|c| c.to() == target && matches!(c.promotion(), None | Some(Role::Queen)))
            .min_by_key(|c| (c.role(), Reverse(c.from())));
    }
    // Negamax from the end: each side stops when going on loses more.
    gain.into_iter()
        .rev()
        .reduce(|later, g| -(-g).max(later))
        .unwrap()
}

/// The mover's worst material balance after the move, over its pieces worth at least 3: on the
/// arrival square the SEE of the move, if the moved piece is worth at least 3 (a promoted pawn is
/// not); on any other square the opponent can capture legally, the material the move took minus
/// the opponent's best SEE there. 0 if neither case exists.
// shortcut: static, so intermediate checks and counter-threats do not count; add a search if calibration asks for it.
pub fn sacrifice_balance(board_before: &Chess, played: Move) -> i32 {
    let mut balances = Vec::new();
    if piece_value(played.role()) >= 3 {
        balances.push(static_exchange_evaluation(board_before, played));
    }
    let taken = material_taken(played);
    let arrival = to_square(played);
    let mut after = board_before.clone();
    after.play_unchecked(played);
    // every promotion piece counts here: python-chess does not filter these captures
    let captures = after.legal_moves();
    for square in after.board().by_color(board_before.turn()) {
        let role = after.board().role_at(square).unwrap();
        if square != arrival && piece_value(role) >= 3 {
            let best = captures
                .iter()
                .filter(|c| c.to() == square)
                .map(|&c| static_exchange_evaluation(&after, c))
                .max();
            if let Some(best) = best {
                balances.push(taken - best);
            }
        }
    }
    balances.into_iter().min().unwrap_or(0)
}
