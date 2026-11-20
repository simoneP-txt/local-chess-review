use review_core::engine::Score;
use review_core::scoring::*;
use review_core::see::*;
use review_core::special::*;
use shakmaty::fen::Fen;
use shakmaty::uci::UciMove;
use shakmaty::{CastlingMode, Chess, Color, Move};

const W: Color = Color::White;
const B: Color = Color::Black;

fn pos(fen: &str) -> Chess {
    fen.parse::<Fen>()
        .unwrap()
        .into_position(CastlingMode::Standard)
        .unwrap()
}

/// A legal move of `board`.
fn mv(board: &Chess, uci: &str) -> Move {
    uci.parse::<UciMove>().unwrap().to_move(board).unwrap()
}

fn assert_approx(actual: f64, expected: f64, case: &str) {
    assert!(
        (actual - expected).abs() <= 1e-6,
        "{case}: {actual} != {expected}"
    );
}

#[test]
fn test_cp_to_wp() {
    // the code clamps cp to ±2000
    let cases = [
        (0, 0.5),
        (100, 0.591026),
        (-100, 0.408974),
        (300, 0.751126),
        (2000, 0.999367),
        (5000, 0.999367),
        (-5000, 0.000633),
    ];
    for (cp, wp) in cases {
        assert_approx(cp_to_wp(cp), wp, &format!("cp {cp}"));
    }
}

#[test]
fn test_score_cp_from_both_sides() {
    let s = Score::Cp(100);
    assert_approx(score_to_wp(s, W, W), 0.591026, "white");
    assert_approx(score_to_wp(s, W, B), 0.408974, "black");
    assert_eq!((score_to_cp(s, W, W), score_to_cp(s, W, B)), (100, -100));
}

#[test]
fn test_score_mate_in_n() {
    let s = Score::Mate(3);
    assert_eq!((score_to_wp(s, W, W), score_to_wp(s, W, B)), (1.0, 0.0));
    assert_eq!((score_to_cp(s, W, W), score_to_cp(s, W, B)), (9980, -9980));
    assert_eq!(score_to_cp(Score::Mate(-2), W, W), -9990);
}

#[test]
fn test_score_side_to_move_is_mated() {
    let s = Score::Mate(0); // what Stockfish returns when White is checkmated
    assert_eq!(score_to_wp(s, W, W), 0.0);
    assert_eq!(score_to_cp(s, W, W), -10000);
}

#[test]
fn test_score_mate_given_wp_is_a_win() {
    assert_eq!(score_to_wp(Score::Mate(0), W, B), 1.0);
}

#[test]
fn test_score_mate_given_cp_is_a_win() {
    assert_eq!(score_to_cp(Score::Mate(0), W, B), 10000);
}

#[test]
fn test_classify_standard() {
    let cases = [
        (0.5, true, "best"),
        (0.0, false, "best"),
        (0.0001, false, "excellent"),
        (0.0199, false, "excellent"),
        (0.02, false, "good"),
        (0.0499, false, "good"),
        (0.05, false, "inaccuracy"),
        (0.0999, false, "inaccuracy"),
        (0.10, false, "mistake"),
        (0.1999, false, "mistake"),
        (0.20, false, "blunder"),
        (0.9, false, "blunder"),
    ];
    for (drop, is_best, expected) in cases {
        assert_eq!(
            classify_standard(drop, is_best),
            expected,
            "drop {drop}, is_best {is_best}"
        );
    }
}

#[test]
fn test_blunder_implies_drop_at_least_mistake_max() {
    // Miss relies on this: it needs no drop check of its own on the opponent's blunder
    for i in 0..=1000 {
        let drop = i as f64 / 1000.0;
        if classify_standard(drop, false) == "blunder" {
            assert!(drop >= MISTAKE_MAX, "drop {drop}");
        }
    }
}

#[test]
fn test_detect_phase() {
    let start = Chess::default();
    assert_eq!(
        [1, 20, 21].map(|p| detect_phase(&start, p)),
        ["opening", "opening", "middlegame"]
    );
    // non-pawn material 24, then 27
    assert_eq!(
        detect_phase(&pos("r3k2r/8/8/8/8/8/8/Q3K2R w - - 0 1"), 5),
        "endgame"
    );
    assert_eq!(
        detect_phase(&pos("r3k2r/8/8/8/8/8/8/QN2K2R w - - 0 1"), 25),
        "middlegame"
    );
}

#[test]
fn test_see() {
    let cases = [
        (
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "e2e4",
            0,
        ),
        ("4k3/8/8/8/8/5N2/8/4K3 w - - 0 1", "f3e5", 0), // safe square
        ("4k3/8/3p4/8/8/5N2/8/4K3 w - - 0 1", "f3e5", -3), // knight hangs to a pawn
        ("4k3/8/3p4/8/4P3/8/8/4K3 w - - 0 1", "e4e5", -1), // pawn hangs to a pawn
        ("4k3/8/2p5/3r4/8/8/3Q4/4K3 w - - 0 1", "d2d5", -4), // QxR, pawn recaptures
        ("4k3/8/4n3/8/3p4/8/1B6/4RK2 w - - 0 1", "b2d4", 1), // only defender is pinned
        ("3R4/2P5/7k/8/8/8/3r4/6K1 b - - 0 1", "d2d8", -8), // cxd8=Q recaptures and promotes
        ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "e5d6", 1), // free en passant
        ("4k3/2p5/8/3pP3/8/8/8/4K3 w - d6 0 1", "e5d6", 0), // en passant, c7 retakes
        ("3rk3/8/4p3/3n4/5N2/8/8/4RK2 w - - 0 1", "f4d5", 0), // e6 is pinned, the d8 rook retakes
        ("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e7e8q", 8), // free promotion
        ("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e7e8n", 2), // free underpromotion
        ("8/8/3p1k2/4p3/8/5N2/1B6/4R1K1 w - - 0 1", "f3e5", -1), // dxe5 Bxe5, Kxe5 never legal
        ("7k/1P1K4/8/8/8/8/2r5/8 b - - 0 1", "c2c8", -13), // bxc8=Q, the king captures last
        ("3rk3/8/8/3p4/8/8/3R4/3RK3 w - - 0 1", "d2d5", 1), // Rd1 recaptures through d2
        ("r7/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e7e8n", -1), // Rxe8 takes the new knight
        ("3r3k/8/8/8/8/5Q2/2p4K/1R1n4 w - - 0 1", "b1d1", -10), // cxd1=Q Qxd1 Rxd1
        ("4k3/2p5/3p4/8/4N3/8/8/4R1K1 w - - 0 1", "e4d6", 1), // double check: cxd6 is illegal
    ];
    for (fen, uci, expected) in cases {
        let board = pos(fen);
        let see = static_exchange_evaluation(&board, mv(&board, uci));
        assert_eq!(see, expected, "{fen} {uci}");
    }
}

#[test]
fn test_see_keeps_loss_limiting_recapture() {
    // the recapture loses less than stopping: 2 and -2, not 3 and -3
    let cases = [
        ("4k3/8/4p3/3n4/4P3/8/8/4K3 w - - 0 1", "e4d5", 2),
        ("4k3/8/3p4/8/3P4/5N2/8/4K3 w - - 0 1", "f3e5", -2),
    ];
    for (fen, uci, expected) in cases {
        let board = pos(fen);
        let see = static_exchange_evaluation(&board, mv(&board, uci));
        assert_eq!(see, expected, "{fen} {uci}");
    }
}

#[test]
fn test_is_great_move() {
    // (is_best, base, before, after, second, before_opponent, expected)
    let cases = [
        // (a): before_opponent is the mover's WP before the opponent's last move
        (false, "best", 0.6, 0.6, Some(0.55), Some(0.35), true),
        (false, "excellent", 0.61, 0.6, Some(0.55), Some(0.35), true),
        (false, "best", 0.6, 0.6, Some(0.55), Some(0.45), false),
        (false, "best", 0.6, 0.6, Some(0.55), Some(0.40), false),
        (false, "best", 0.5, 0.5, Some(0.45), Some(0.35), false),
        (false, "good", 0.63, 0.6, Some(0.55), Some(0.35), false),
        (false, "best", 0.6, 0.6, Some(0.55), None, false), // first move of the game
        // the opponent did not err: 0.51 is search noise
        (false, "best", 0.39, 0.51, Some(0.35), Some(0.39), false),
        // the opponent gave away 0.09: an inaccuracy, not a mistake
        (false, "best", 0.44, 0.51, Some(0.40), Some(0.35), false),
        // just above 0.10: 0.45 - 0.35 is 0.10000000000000003, so this row does not tell >= from >
        (false, "best", 0.45, 0.51, Some(0.40), Some(0.35), true),
        // only legal move, always line 1: no choice to praise
        (true, "best", 0.6, 0.6, None, Some(0.35), false),
        // (b): the only saving move
        (true, "best", 0.6, 0.6, Some(0.39), None, true),
        (true, "best", 0.6, 0.6, Some(0.41), None, false),
        (false, "best", 0.6, 0.6, Some(0.30), None, false),
        (true, "best", 0.6, 0.6, None, None, false),
        // 2nd line 0.10 below the best line; wp_after plays no part
        (true, "best", 0.5, 0.75, Some(0.4), None, false),
        (true, "best", 0.65, 0.6, Some(0.44), None, true), // 2nd line 0.21 below the best line
    ];
    for (is_best, base, before, after, second, before_opponent, expected) in cases {
        assert_eq!(
            is_great_move(is_best, base, before, after, second, before_opponent),
            expected,
            "{is_best} {base} {before} {after} {second:?} {before_opponent:?}"
        );
    }
}

const SAC: (&str, &str) = ("4k3/8/3p4/8/8/5N2/8/4K3 w - - 0 1", "f3e5"); // knight sacrifice, SEE -3
// the queen stays en prise to c5
const QUEEN_EN_PRISE: (&str, &str) = ("4k3/8/8/2p5/3Q4/8/8/1N2K3 w - - 0 1", "b1c3");
// rook en prise but defended: even trade
const ROOK_DEFENDED: (&str, &str) = ("3rk3/8/8/8/3R4/8/8/1N1RK3 w - - 0 1", "b1c3");
// exd6 takes 1, cxd4 takes 3
const PASSANT_LEAVES_KNIGHT: (&str, &str) = ("4k3/8/8/2ppP3/3N4/8/8/4K3 w - d6 0 1", "e5d6");
// e8=Q gains 8, cxd4 takes 9
const PROMOTION_LEAVES_QUEEN: (&str, &str) = ("8/k3P3/8/2p5/3Q4/8/8/4K3 w - - 0 1", "e7e8q");

/// (wp_before, wp_after, eval_after_cp, second_best_wp, is_best)
type BrilliantInputs = (f64, f64, i32, Option<f64>, bool);
const SOUND: BrilliantInputs = (0.6, 0.6, 100, Some(0.45), true);

fn brilliant((fen, uci): (&str, &str), inputs: BrilliantInputs) -> bool {
    let (wp_before, wp_after, eval_after_cp, second_best_wp, is_best) = inputs;
    let board = pos(fen);
    is_brilliant(
        &board,
        mv(&board, uci),
        wp_before,
        wp_after,
        eval_after_cp,
        second_best_wp,
        is_best,
    )
}

#[test]
fn test_sacrifice_balance() {
    let cases = [
        (QUEEN_EN_PRISE, -9),
        (ROOK_DEFENDED, 0),
        (("4k3/8/3p4/8/4P3/8/8/4K3 w - - 0 1", "e4e5"), 0), // only a pawn
        (SAC, -3),
        (("4k3/8/8/2p1p3/3Q4/5N2/8/4K3 w - - 0 1", "f3e5"), -8), // Nxe5 takes 1, cxd4 takes 9
        (PASSANT_LEAVES_KNIGHT, -2),
        (PROMOTION_LEAVES_QUEEN, -1),
        // check: Kxd7 parries it, fxg6 is illegal
        (("4k3/3N1p2/6Q1/8/8/8/8/R5K1 w - - 0 1", "a1e1"), -3),
        // exd4 is illegal: Re1 pins e5
        (("4k3/8/8/4p3/3Q4/8/8/1N2RK2 w - - 0 1", "b1c3"), 0),
        // Black to move: cxd5 takes the queen
        (("1n2k3/8/8/3q4/2P5/8/8/4K3 b - - 0 1", "b8c6"), -9),
        // the moved piece is a pawn: Rxe8 does not count
        (("r7/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e7e8q"), 0),
        // a pawn en prise does not count
        (("4k3/8/8/2p5/3P4/8/8/1N2K3 w - - 0 1", "b1c3"), 0),
        // the opponent's best: cxd4 (5), not Qxd4 (1)
        (("3qk3/8/8/2p5/3R4/8/8/1N1RK3 w - - 0 1", "b1c3"), -5),
    ];
    for ((fen, uci), expected) in cases {
        let board = pos(fen);
        assert_eq!(
            sacrifice_balance(&board, mv(&board, uci)),
            expected,
            "{fen} {uci}"
        );
    }
}

#[test]
fn test_brilliant_accepts_sound_sacrifice() {
    assert!(brilliant(SAC, SOUND));
}

#[test]
fn test_brilliant_accepts_sacrifice_of_another_piece() {
    assert!(brilliant(QUEEN_EN_PRISE, SOUND));
    assert!(!brilliant(ROOK_DEFENDED, SOUND));
}

#[test]
fn test_brilliant_requires_the_best_move() {
    assert!(!brilliant(SAC, (0.6, 0.6, 100, Some(0.45), false)));
}

#[test]
fn test_brilliant_rejects() {
    let cases = [
        ("WP drops 0.03 > 0.02", (0.6, 0.57, 100, Some(0.45), true)),
        ("after below 0.55", (0.55, 0.54, 100, Some(0.45), true)),
        ("before above 0.85", (0.86, 0.86, 100, Some(0.45), true)),
        (
            "forced mate after the move",
            (0.6, 0.6, 9500, Some(0.45), true),
        ),
        (
            "second best only 0.05 worse",
            (0.6, 0.6, 100, Some(0.55), true),
        ),
        (
            "still 0.05 below the best line",
            (0.6, 0.66, 100, Some(0.55), true),
        ),
        (
            "only legal move: no choice to praise",
            (0.6, 0.6, 100, None, true),
        ),
    ];
    for (case, inputs) in cases {
        assert!(!brilliant(SAC, inputs), "{case}");
    }
}

#[test]
fn test_brilliant_needs_a_piece_sacrifice() {
    // pawn, SEE -1
    assert!(!brilliant(
        ("4k3/8/3p4/8/4P3/8/8/4K3 w - - 0 1", "e4e5"),
        SOUND
    ));
    // knight, SEE 0
    assert!(!brilliant(
        ("4k3/8/8/8/8/5N2/8/4K3 w - - 0 1", "f3e5"),
        SOUND
    ));
    assert!(brilliant(PASSANT_LEAVES_KNIGHT, SOUND)); // balance -2, the limit
    assert!(!brilliant(PROMOTION_LEAVES_QUEEN, SOUND)); // balance -1
}
