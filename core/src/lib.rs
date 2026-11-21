pub mod accuracy;
pub mod engine;
pub mod game;
pub mod openings;
pub mod py;
pub mod scoring;
pub mod see;
pub mod special;
pub mod stats;

#[derive(Clone, Copy, Debug, PartialEq, serde::Serialize)]
pub struct ByColor<T> {
    pub white: T,
    pub black: T,
}
