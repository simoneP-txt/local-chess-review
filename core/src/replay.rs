//! Recorded Stockfish answers, as tests/engine_replay.py ReplayEngine.
use std::collections::HashMap;

use serde::Deserialize;

use crate::engine::{Engine, EngineRequest, Line, Score};

/// `<id>.engine.json`; serde skips the fields the analysis does not use (nodes, wdl, ...).
#[derive(Deserialize)]
struct EngineJson {
    meta: Meta,
    calls: Vec<Call>,
}

#[derive(Deserialize)]
struct Meta {
    depth: u32,
}

#[derive(Deserialize)]
struct Call {
    fen: String,
    /// null when python-chess was asked for one line
    multipv: Option<u32>,
    depth: u32,
    infos: Vec<Info>,
}

#[derive(Deserialize)]
struct Info {
    score: JsonScore,
    pv: Vec<String>,
}

/// {"cp": n} or {"mate": n}
#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum JsonScore {
    Cp(i32),
    Mate(i32),
}

/// (fen, multipv, depth) -> lines
pub struct ReplayEngine {
    index: HashMap<(String, u32, u32), Vec<Line>>,
}

impl ReplayEngine {
    /// The engine and meta.depth of an `<id>.engine.json`. A later call with the same key replaces
    /// an earlier one, as Python's dict.
    pub fn from_json(text: &str) -> Result<(ReplayEngine, u32), String> {
        let data: EngineJson = serde_json::from_str(text).map_err(|e| e.to_string())?;
        let index = data
            .calls
            .into_iter()
            .map(|call| {
                let lines = call
                    .infos
                    .into_iter()
                    .map(|info| Line {
                        score: match info.score {
                            JsonScore::Cp(cp) => Score::Cp(cp),
                            JsonScore::Mate(n) => Score::Mate(n),
                        },
                        pv: info.pv,
                    })
                    .collect();
                // Python's `multipv or 1`: null and 0 are 1
                let multipv = call.multipv.filter(|&n| n != 0).unwrap_or(1);
                ((call.fen, multipv, call.depth), lines)
            })
            .collect();
        Ok((ReplayEngine { index }, data.meta.depth))
    }
}

impl Engine for ReplayEngine {
    fn analyse(&mut self, request: &EngineRequest) -> Result<Vec<Line>, String> {
        let multipv = request.multipv.max(1);
        let key = (request.fen.to_string(), multipv, request.depth);
        self.index.get(&key).cloned().ok_or_else(|| {
            format!(
                "no recorded search: {} multipv={multipv} depth={}",
                request.fen, request.depth
            )
        })
    }
}
