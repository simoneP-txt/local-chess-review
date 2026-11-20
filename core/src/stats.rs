use crate::ByColor;
use crate::py::{py_round, py_round_int};
use crate::scoring::{
    EXCELLENT_MAX, GOOD_MAX, INACCURACY_MAX, MISTAKE_MAX, PERFORMANCE_ELO_OVERPERFORMANCE_CAP,
};

/// Maps accuracy -> Elo, piecewise linear. The curve is calibrated so that low accuracy means
/// a low score (50% -> 350, not 1400).
pub fn accuracy_to_elo(accuracy: f64) -> i64 {
    const POINTS: [(i64, i64); 12] = [
        (0, 100),
        (30, 150),
        (45, 250),
        (55, 450),
        (65, 800),
        (70, 1100),
        (75, 1400),
        (80, 1700),
        (85, 2000),
        (90, 2300),
        (95, 2600),
        (100, 2900),
    ];
    if accuracy <= POINTS[0].0 as f64 {
        return POINTS[0].1;
    }
    if accuracy >= POINTS[11].0 as f64 {
        return POINTS[11].1;
    }
    for pair in POINTS.windows(2) {
        let ((x0, y0), (x1, y1)) = (pair[0], pair[1]);
        if accuracy <= x1 as f64 {
            let t = (accuracy - x0 as f64) / (x1 - x0) as f64;
            return py_round_int(y0 as f64 + t * (y1 - y0) as f64);
        }
    }
    POINTS[11].1
}

/// Performance Elo for the game: the accuracy curve, capped at real_elo + 1500 when real_elo is
/// known (a 500-Elo player cannot perform 2780 in one game), no lower floor, then clamped into
/// [100, 2900].
pub fn estimate_performance_elo(accuracy: f64, real_elo: Option<i64>) -> i64 {
    let mut pure = accuracy_to_elo(accuracy);
    if let Some(real_elo) = real_elo.filter(|&elo| elo > 0) {
        pure = pure.min(real_elo + PERFORMANCE_ELO_OVERPERFORMANCE_CAP);
    }
    pure.clamp(100, 2900)
}

/// Map a phase's mean drop to a classification label.
fn phase_label_from_drop(mean_drop: f64) -> &'static str {
    if mean_drop < EXCELLENT_MAX {
        "excellent"
    } else if mean_drop < GOOD_MAX {
        "good"
    } else if mean_drop < INACCURACY_MAX {
        "inaccuracy"
    } else if mean_drop < MISTAKE_MAX {
        "mistake"
    } else {
        "blunder"
    }
}

#[derive(Clone, Copy, Debug, PartialEq, serde::Serialize)]
pub struct PhaseStats {
    pub label: Option<&'static str>,
    pub mean_drop: Option<f64>,
    pub moves: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, serde::Serialize)]
pub struct Phases {
    pub opening: ByColor<PhaseStats>,
    pub middlegame: ByColor<PhaseStats>,
    pub endgame: ByColor<PhaseStats>,
}

/// For each phase and each color: mean WP drop and mean classification.
/// `moves` holds (phase, color, wp_drop already rounded) per move.
pub fn compute_phases(moves: &[(&str, &str, f64)]) -> Phases {
    let stats = |phase: &str, color: &str| {
        let drops: Vec<f64> = moves
            .iter()
            .filter(|m| m.0 == phase && m.1 == color)
            .map(|m| m.2)
            .collect();
        if drops.is_empty() {
            return PhaseStats {
                label: None,
                mean_drop: None,
                moves: 0,
            };
        }
        let mean_drop = drops.iter().sum::<f64>() / drops.len() as f64;
        PhaseStats {
            label: Some(phase_label_from_drop(mean_drop)),
            mean_drop: Some(py_round(mean_drop, 4)),
            moves: drops.len() as u32,
        }
    };
    let by_color = |phase: &str| ByColor {
        white: stats(phase, "white"),
        black: stats(phase, "black"),
    };
    Phases {
        opening: by_color("opening"),
        middlegame: by_color("middlegame"),
        endgame: by_color("endgame"),
    }
}
