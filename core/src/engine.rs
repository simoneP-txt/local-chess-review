/// Relative to the side to move, as Stockfish reports it; Mate(0): the side to move is mated.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Score {
    Cp(i32),
    Mate(i32),
}

#[derive(Clone, Debug, PartialEq)]
pub struct Line {
    pub score: Score,
    /// UCI moves, castling as e1g1.
    pub pv: Vec<String>,
}

pub struct EngineRequest<'a> {
    /// python-chess board.fen()
    pub fen: &'a str,
    /// The game's first position. With `moves`, it lets a real engine see repetitions, as
    /// python-chess sends `position startpos moves ...`; a replay only needs `fen`.
    pub start_fen: &'a str,
    /// UCI moves from start_fen to fen.
    pub moves: &'a [String],
    pub depth: u32,
    pub multipv: u32,
}

pub trait Engine {
    fn analyse(&mut self, request: &EngineRequest) -> Result<Vec<Line>, String>;

    /// The last answer as the "infos" of an engine.json call (score, pv, depth, seldepth, nodes,
    /// time, multipv, ...): a live engine fills it, a replay gives None.
    fn last_infos(&self) -> Option<Vec<serde_json::Value>> {
        None
    }
}
