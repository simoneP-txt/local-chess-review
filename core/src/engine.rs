// shortcut: scores are unchecked i32, so -cp and n.abs() in score_to_cp overflow on i32::MIN, and
// eval_before_cp - eval_after_cp in classify overflows on scores near the i32 limits: a debug
// build panics and a release build wraps, while Python's ints give the right value. Stockfish
// never sends such scores. Redo it in the Phase 2 UCI parser that reads Stockfish's answers:
// reject out-of-range scores there as an error, for example |cp| > 100000 or |mate| > 1000.
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
}
