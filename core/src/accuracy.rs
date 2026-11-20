//! Accuracy (lila's AccuracyPercent, ported one to one).
use crate::ByColor;
use crate::py::py_round;
use crate::scoring::WP_K;

/// lila's WinPercent 0-100: the Lichess sigmoid on cp capped at ±1000, so a mate counts 97.54%.
pub fn lichess_win_pct(cp: i32) -> f64 {
    let cp = cp.clamp(-1000, 1000);
    50.0 + 50.0 * (2.0 / (1.0 + (-WP_K * cp as f64).exp()) - 1.0)
}

/// Accuracy 0-100 of a move from the mover's win% before and after it.
pub fn move_accuracy_lichess(before_pct: f64, after_pct: f64) -> f64 {
    if after_pct >= before_pct {
        return 100.0;
    }
    let raw = 103.1668100711649 * (-0.04354415386753951 * (before_pct - after_pct)).exp()
        - 3.166924740191411;
    // lila's +1 "uncertainty bonus" for the imperfect analysis
    (raw + 1.0).clamp(0.0, 100.0)
}

/// Robust harmonic mean: protects against division by zero (1.0 floor for safety).
pub fn harmonic_mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.len() as f64 / values.iter().map(|v| 1.0 / v.max(1.0)).sum::<f64>()
}

// shortcut: plain f64, while Python 3.11 statistics.pstdev is exact and rounds once; on the 46
// fixtures (2026-11-20) it moves the last bits of some window weights and no rounded accuracy;
// port _ss and _float_sqrt_of_frac if a parity gate shows an accuracy difference.
pub fn pstdev(values: &[f64]) -> f64 {
    let n = values.len() as f64;
    let mean = values.iter().sum::<f64>() / n;
    (values.iter().map(|x| (x - mean) * (x - mean)).sum::<f64>() / n).sqrt()
}

/// lila's gameAccuracy, unrounded. cps_white holds P0 ... Pn from White's side.
/// Per colour: mean of the volatility-weighted and the harmonic mean of its
/// move accuracies; 0.0 for a colour without moves.
pub fn game_accuracy(white_starts: bool, cps_white: &[i32]) -> ByColor<f64> {
    let pcts: Vec<f64> = cps_white.iter().map(|&cp| lichess_win_pct(cp)).collect();
    // saturating_sub: Python's (0 - 1) // 10 is -1, and max(2, ...) gives 2 all the same
    let window = (pcts.len().saturating_sub(1) / 10).clamp(2, 8);
    // Move i weighs by the window that ends on P(i+1); the first moves reuse the first window.
    // lila's guards for a series shorter than the window are left out: such a series has no moves.
    let first = &pcts[..window.min(pcts.len())];
    let windows = std::iter::repeat_n(first, window - 2).chain(pcts.windows(window));
    let (mut white_pairs, mut black_pairs) = (vec![], vec![]);
    for (i, ((&prev, &nxt), win)) in pcts
        .iter()
        .zip(pcts.iter().skip(1))
        .zip(windows)
        .enumerate()
    {
        let white = (i % 2 == 0) == white_starts;
        // lila swaps the pair for Black: the same drop as Black's own 100 - pct
        let acc = if white {
            move_accuracy_lichess(prev, nxt)
        } else {
            move_accuracy_lichess(nxt, prev)
        };
        let pairs = if white {
            &mut white_pairs
        } else {
            &mut black_pairs
        };
        pairs.push((acc, pstdev(win).clamp(0.5, 12.0)));
    }
    let color_accuracy = |pairs: &[(f64, f64)]| {
        if pairs.is_empty() {
            return 0.0;
        }
        let weighted = pairs.iter().map(|(a, w)| a * w).sum::<f64>()
            / pairs.iter().map(|(_, w)| w).sum::<f64>();
        let accs: Vec<f64> = pairs.iter().map(|&(a, _)| a).collect();
        (weighted + harmonic_mean(&accs)) / 2.0
    };
    ByColor {
        white: color_accuracy(&white_pairs),
        black: color_accuracy(&black_pairs),
    }
}

/// Game accuracy per colour, one decimal (see game_accuracy).
pub fn game_accuracy_lichess(white_starts: bool, cps_white: &[i32]) -> ByColor<f64> {
    let acc = game_accuracy(white_starts, cps_white);
    ByColor {
        white: py_round(acc.white, 1),
        black: py_round(acc.black, 1),
    }
}
