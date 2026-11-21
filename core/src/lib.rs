pub mod accuracy;
pub mod analysis;
pub mod compare;
pub mod engine;
pub mod game;
pub mod openings;
pub mod py;
pub mod replay;
pub mod scoring;
pub mod see;
pub mod special;
pub mod stats;

pub use analysis::analyze_pgn;

#[derive(Clone, Copy, Debug, PartialEq, serde::Serialize)]
pub struct ByColor<T> {
    pub white: T,
    pub black: T,
}
