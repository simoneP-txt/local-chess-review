use shakmaty::{Chess, Color, Position, Role};

use crate::engine::Score;

// Classification thresholds on the Win Probability drop (0..1), Expected Points Model;
// a drop of MISTAKE_MAX or more is a blunder.
pub const EXCELLENT_MAX: f64 = 0.02;
pub const GOOD_MAX: f64 = 0.05;
pub const INACCURACY_MAX: f64 = 0.10;
pub const MISTAKE_MAX: f64 = 0.20;

/// Lichess sigmoid coefficient cp -> WP.
pub const WP_K: f64 = 0.00368208;

/// The mover's WP before the opponent's last move below this: it was losing.
pub const GREAT_TURNAROUND_FROM: f64 = 0.40;
pub const GREAT_TURNAROUND_TO: f64 = 0.50;
/// The 2nd-best move would drop WP by at least this: the only saving move.
pub const GREAT_ONLY_2ND_DROP: f64 = 0.20;

pub const PV_PLIES_TO_KEEP: usize = 6;

/// Per-move cap on cp loss, so one mate does not dominate the ACPL.
pub const ACPL_CP_LOSS_CAP: i32 = 1000;

pub const BRILLIANT_WP_FLOOR: f64 = 0.55;
pub const BRILLIANT_WP_CEILING: f64 = 0.85;
pub const BRILLIANT_TOLERANCE: f64 = 0.02;
pub const BRILLIANT_SEE_MAX: i32 = -2;
pub const BRILLIANT_2ND_GAP: f64 = 0.10;

pub const ENDGAME_NONPAWN_MATERIAL: i32 = 24;
pub const OPENING_MAX_PLY: u32 = 20;

/// Performance Elo is capped at real_elo + this.
pub const PERFORMANCE_ELO_OVERPERFORMANCE_CAP: i64 = 1500;

pub fn piece_value(role: Role) -> i32 {
    match role {
        Role::Pawn => 1,
        Role::Knight | Role::Bishop => 3,
        Role::Rook => 5,
        Role::Queen => 9,
        Role::King => 0,
    }
}

/// The piece name the frontend localizes in comment params.
pub fn piece_key(role: Role) -> &'static str {
    match role {
        Role::Pawn => "pawn",
        Role::Knight => "knight",
        Role::Bishop => "bishop",
        Role::Rook => "rook",
        Role::Queen => "queen",
        Role::King => "king",
    }
}

pub fn cp_to_wp(cp: i32) -> f64 {
    let cp_clamped = cp.clamp(-2000, 2000);
    1.0 / (1.0 + (-WP_K * cp_clamped as f64).exp())
}

/// python-chess PovScore(Mate(n), turn).pov(pov) > Cp(0): Mate(0) is the side to move mated,
/// so from the other side it is MateGiven, a win.
fn mate_is_win(n: i32, turn: Color, pov: Color) -> bool {
    if pov == turn { n > 0 } else { n <= 0 }
}

pub fn score_to_wp(score: Score, turn: Color, pov: Color) -> f64 {
    match score {
        Score::Cp(cp) => cp_to_wp(if pov == turn { cp } else { -cp }),
        Score::Mate(n) => {
            if mate_is_win(n, turn, pov) {
                1.0
            } else {
                0.0
            }
        }
    }
}

pub fn score_to_cp(score: Score, turn: Color, pov: Color) -> i32 {
    match score {
        Score::Cp(cp) => {
            if pov == turn {
                cp
            } else {
                -cp
            }
        }
        Score::Mate(n) => {
            let magnitude = 10000 - (n.abs() - 1).max(0) * 10;
            if mate_is_win(n, turn, pov) {
                magnitude
            } else {
                -magnitude
            }
        }
    }
}

/// Non-pawn material of both sides, for endgame detection.
pub fn non_pawn_material_total(pos: &Chess) -> i32 {
    [Role::Knight, Role::Bishop, Role::Rook, Role::Queen]
        .into_iter()
        .map(|role| pos.board().by_role(role).count() as i32 * piece_value(role))
        .sum()
}

/// The phase of the position before the move.
pub fn detect_phase(board_before: &Chess, ply: u32) -> &'static str {
    if non_pawn_material_total(board_before) <= ENDGAME_NONPAWN_MATERIAL {
        "endgame"
    } else if ply <= OPENING_MAX_PLY {
        "opening"
    } else {
        "middlegame"
    }
}

pub fn classify_standard(wp_drop: f64, is_best: bool) -> &'static str {
    if is_best || wp_drop <= 0.0 {
        "best"
    } else if wp_drop < EXCELLENT_MAX {
        "excellent"
    } else if wp_drop < GOOD_MAX {
        "good"
    } else if wp_drop < INACCURACY_MAX {
        "inaccuracy"
    } else if wp_drop < MISTAKE_MAX {
        "mistake"
    } else {
        "blunder"
    }
}
