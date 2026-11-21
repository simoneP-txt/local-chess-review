//! A UCI engine behind a line pipe, read as python-chess 1.11.2 reads Stockfish (spec 6.1).
use std::collections::BTreeMap;
use std::num::IntErrorKind;

use serde_json::{Map, Value, json};
use shakmaty::fen::Fen;
use shakmaty::uci::UciMove;
use shakmaty::{CastlingMode, Chess};

use crate::engine::{Engine, EngineRequest, Line, Score};

const STARTING_FEN: &str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

/// One command line in, one answer line out.
pub trait EngineIo: Send {
    fn send(&mut self, line: &str) -> Result<(), String>;
    /// Blocks for the next line, without its line end; Err once the engine is gone.
    fn read_line(&mut self) -> Result<String, String>;
}

pub struct UciEngine<I: EngineIo> {
    io: I,
    id_name: String,
    /// The MultiPV the engine holds; 0 before the first search.
    multipv: u32,
    last_infos: Option<Vec<Value>>,
}

impl<I: EngineIo> UciEngine<I> {
    /// uci, id name, setoption Threads 1, Hash hash_mb, isready.
    pub fn start(mut io: I, hash_mb: u32) -> Result<Self, String> {
        io.send("uci")?;
        let mut id_name = String::new();
        loop {
            let line = io.read_line()?;
            if line.trim() == "uciok" {
                break;
            }
            if let Some(name) = line.strip_prefix("id name ") {
                id_name = name.trim().to_string();
            }
        }
        io.send("setoption name Threads value 1")?;
        io.send(&format!("setoption name Hash value {hash_mb}"))?;
        let mut engine = UciEngine {
            io,
            id_name,
            multipv: 0,
            last_infos: None,
        };
        engine.ready()?;
        Ok(engine)
    }

    pub fn id_name(&self) -> &str {
        &self.id_name
    }

    pub fn io_mut(&mut self) -> &mut I {
        &mut self.io
    }

    /// isready, then the lines up to readyok.
    fn ready(&mut self) -> Result<(), String> {
        self.io.send("isready")?;
        while self.io.read_line()?.trim() != "readyok" {}
        Ok(())
    }
}

impl<I: EngineIo> Engine for UciEngine<I> {
    fn analyse(&mut self, request: &EngineRequest) -> Result<Vec<Line>, String> {
        self.last_infos = None;
        self.io.send("ucinewgame")?;
        self.ready()?;
        let multipv = request.multipv.max(1);
        if self.multipv != multipv {
            self.io
                .send(&format!("setoption name MultiPV value {multipv}"))?;
            self.multipv = multipv;
        }
        let mut position = if request.start_fen == STARTING_FEN {
            "position startpos".to_string()
        } else {
            format!("position fen {}", request.start_fen)
        };
        if !request.moves.is_empty() {
            position = format!("{position} moves {}", request.moves.join(" "));
        }
        self.io.send(&position)?;
        self.io.send(&format!("go depth {}", request.depth))?;
        // multipv -> its last info line with a score
        let mut answers = BTreeMap::new();
        loop {
            let text = self.io.read_line()?;
            match text.split_whitespace().next() {
                Some("bestmove") => {
                    check_bestmove(&text, request.fen)?;
                    break;
                }
                Some("info") => {
                    if let Some((multipv, line, info)) = parse_info(&text)? {
                        answers.insert(multipv, (line, info));
                    }
                }
                _ => {}
            }
        }
        let (lines, infos) = answers.into_values().unzip();
        self.last_infos = Some(infos);
        Ok(lines)
    }

    fn last_infos(&self) -> Option<Vec<Value>> {
        self.last_infos.clone()
    }
}

/// As python-chess: no move, (none), NULL, a null move, or a move legal in `fen`; the ponder move
/// does not count.
fn check_bestmove(text: &str, fen: &str) -> Result<(), String> {
    let Some(token) = text.split_whitespace().nth(1) else {
        return Ok(());
    };
    if matches!(token, "(none)" | "NULL" | "0000") {
        return Ok(());
    }
    let pos: Chess = fen
        .parse::<Fen>()
        .ok()
        .and_then(|setup| setup.into_position(CastlingMode::Standard).ok())
        .ok_or_else(|| format!("invalid FEN: {fen}"))?;
    // python-chess lowers it: some engines write the promotion piece in capitals
    token
        .to_ascii_lowercase()
        .parse::<UciMove>()
        .ok()
        .and_then(|m| m.to_move(&pos).ok())
        .map(|_| ())
        .ok_or_else(|| format!("bad bestmove line: {text}"))
}

/// An info line with a score as (multipv, line, engine.json info); None for the lines that do not
/// change the answer: info string, currmove, no score.
fn parse_info(text: &str) -> Result<Option<(i64, Line, Value)>, String> {
    let bad = || format!("bad info line: {text}");
    let int = |token: Option<&str>| token.and_then(|t| t.parse::<i64>().ok()).ok_or_else(bad);
    let (mut multipv, mut score, mut pv) = (1, None, Vec::new());
    let mut info = Map::new();
    let mut tokens = text.split_whitespace().skip(1);
    while let Some(key) = tokens.next() {
        match key {
            "string" => break,
            "currmove" => return Ok(None),
            "depth" | "seldepth" | "nodes" | "nps" | "hashfull" | "tbhits" => {
                info.insert(key.into(), int(tokens.next())?.into());
            }
            "multipv" => {
                multipv = int(tokens.next())?;
                info.insert(key.into(), multipv.into());
            }
            // milliseconds in UCI, seconds in python-chess
            "time" => {
                info.insert(key.into(), (int(tokens.next())? as f64 / 1000.0).into());
            }
            "wdl" => {
                let wdl = [
                    int(tokens.next())?,
                    int(tokens.next())?,
                    int(tokens.next())?,
                ];
                info.insert(key.into(), json!(wdl));
            }
            "score" => {
                let kind = tokens.next();
                let limit = match kind {
                    Some("cp") => 100_000,
                    Some("mate") => 1000,
                    _ => return Err(bad()),
                };
                // a number past i32 is out of range too, not a bad line
                let n = match tokens.next().unwrap_or("").parse::<i32>() {
                    Ok(n) if n.unsigned_abs() <= limit => n,
                    Err(e)
                        if matches!(e.kind(), IntErrorKind::Empty | IntErrorKind::InvalidDigit) =>
                    {
                        return Err(bad());
                    }
                    _ => return Err(format!("score out of range: {text}")),
                };
                score = Some(if kind == Some("cp") {
                    Score::Cp(n)
                } else {
                    Score::Mate(n)
                });
            }
            // shortcut: pv takes the rest of the line unchecked, as Stockfish writes it last and
            // legal; python-chess stops at the first token that is no UCI move and drops a pv with
            // an illegal move. Redo it if another engine fills the lines.
            "pv" => pv = tokens.by_ref().map(str::to_string).collect(),
            _ => {}
        }
    }
    let Some(score) = score else {
        return Ok(None);
    };
    let json_score = match score {
        Score::Cp(n) => json!({"cp": n}),
        Score::Mate(n) => json!({"mate": n}),
    };
    info.insert("score".into(), json_score);
    info.insert("pv".into(), json!(pv));
    Ok(Some((multipv, Line { score, pv }, Value::Object(info))))
}
