use review_core::engine::Score;
use review_core::scoring::*;
use shakmaty::fen::Fen;
use shakmaty::{CastlingMode, Chess, Color};

const W: Color = Color::White;
const B: Color = Color::Black;

fn pos(fen: &str) -> Chess {
    fen.parse::<Fen>()
        .unwrap()
        .into_position(CastlingMode::Standard)
        .unwrap()
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
