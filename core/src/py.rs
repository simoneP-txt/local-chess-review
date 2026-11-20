/// Python's round(x, ndigits): rounds the exact binary value, exact ties to even, so
/// round(2.675, 2) is 2.67. Rust's fixed-precision formatting does the same.
pub fn py_round(x: f64, ndigits: usize) -> f64 {
    format!("{x:.ndigits$}").parse().unwrap()
}

/// Python's int(round(x)): ties to even, unlike f64::round.
pub fn py_round_int(x: f64) -> i64 {
    x.round_ties_even() as i64
}
