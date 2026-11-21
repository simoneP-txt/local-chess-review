#![allow(dead_code)]
//! Fake engine for the analysis tests, as tests/engine_replay.py ScriptedEngine.
use std::collections::HashMap;

use review_core::analysis::{Analysis, analyze_pgn};
use review_core::engine::{Engine, EngineRequest, Line, Score};
use review_core::openings::Book;
use shakmaty::fen::Fen;
use shakmaty::{CastlingMode, Chess, Position};

/// Line i plays the i-th legal move of shakmaty (or of `lines`), every line scores cp_for(board).
pub struct ScriptedEngine<F: FnMut(&Chess) -> i32> {
    pub cp_for: F,
    /// FEN -> UCI moves of lines 1, 2, ... (python-chess order)
    pub lines: HashMap<String, Vec<String>>,
    /// FEN -> whole answer; cp_for is not called
    pub answers: HashMap<String, Vec<Line>>,
    /// (fen, multipv, start_fen, moves) of every request
    pub calls: Vec<(String, u32, String, Vec<String>)>,
}

/// 10 cp everywhere.
pub fn scripted() -> ScriptedEngine<impl FnMut(&Chess) -> i32> {
    scripted_with(|_| 10)
}

pub fn scripted_with<F: FnMut(&Chess) -> i32>(cp_for: F) -> ScriptedEngine<F> {
    ScriptedEngine {
        cp_for,
        lines: HashMap::new(),
        answers: HashMap::new(),
        calls: Vec::new(),
    }
}

impl<F: FnMut(&Chess) -> i32> ScriptedEngine<F> {
    pub fn with_lines(mut self, rows: &[(&str, &[&str])]) -> Self {
        for (fen, moves) in rows {
            let moves = moves.iter().map(|m| m.to_string()).collect();
            self.lines.insert(fen.to_string(), moves);
        }
        self
    }

    pub fn with_answer(mut self, fen: &str, lines: Vec<Line>) -> Self {
        self.answers.insert(fen.to_string(), lines);
        self
    }
}

impl<F: FnMut(&Chess) -> i32> Engine for ScriptedEngine<F> {
    fn analyse(&mut self, request: &EngineRequest) -> Result<Vec<Line>, String> {
        self.calls.push((
            request.fen.to_string(),
            request.multipv,
            request.start_fen.to_string(),
            request.moves.to_vec(),
        ));
        if let Some(lines) = self.answers.get(request.fen) {
            return Ok(lines.clone());
        }
        let board: Chess = request
            .fen
            .parse::<Fen>()
            .map_err(|e| e.to_string())?
            .into_position(CastlingMode::Standard)
            .map_err(|e| e.to_string())?;
        let cp = (self.cp_for)(&board);
        let ucis: Vec<String> = match self.lines.get(request.fen) {
            Some(ucis) => ucis.clone(),
            None => board
                .legal_moves()
                .iter()
                .map(|m| m.to_uci(CastlingMode::Standard).to_string())
                .collect(),
        };
        let infos: Vec<Line> = ucis
            .into_iter()
            .take(request.multipv.max(1) as usize)
            .map(|uci| Line {
                score: Score::Cp(cp),
                pv: vec![uci],
            })
            .collect();
        if infos.is_empty() {
            // Stockfish gives one line without moves: mated, or stalemate
            let score = if board.is_checkmate() {
                Score::Mate(0)
            } else {
                Score::Cp(0)
            };
            return Ok(vec![Line { score, pv: vec![] }]);
        }
        Ok(infos)
    }
}

/// analyze_pgn with the whole book, depth 15 and no Elo.
pub fn analyze(pgn: &str, engine: &mut dyn Engine) -> Analysis {
    analyze_pgn(pgn, None, None, engine, &Book::load(), 15).unwrap()
}
