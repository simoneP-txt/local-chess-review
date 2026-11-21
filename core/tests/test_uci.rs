//! UciEngine against a fake pipe: the commands it sends and how it reads a search.
use std::collections::VecDeque;

use review_core::engine::{Engine, EngineRequest, Line, Score};
use review_core::uci::{EngineIo, UciEngine};
use serde_json::json;

const START: &str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const AFTER_E4_E5: &str = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2";
const PAWN_ENDING: &str = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1";
const PAWN_ENDING_E4: &str = "4k3/8/8/8/4P3/8/8/4K3 b - - 0 1";

/// Answers uci, isready and each go (with the next scripted search); records every command.
struct FakeIo {
    sent: Vec<String>,
    pending: VecDeque<String>,
    searches: VecDeque<Vec<String>>,
}

impl EngineIo for FakeIo {
    fn send(&mut self, line: &str) -> Result<(), String> {
        self.sent.push(line.to_string());
        if line == "uci" {
            let answer = [
                "Fake by nobody",
                "id name Fake 1",
                "id author nobody",
                "uciok",
            ];
            self.pending.extend(answer.map(String::from));
        } else if line == "isready" {
            self.pending.push_back("readyok".to_string());
        } else if line.starts_with("go ") {
            let search = self.searches.pop_front().unwrap_or_default();
            self.pending.extend(search);
        }
        Ok(())
    }

    fn read_line(&mut self) -> Result<String, String> {
        self.pending
            .pop_front()
            .ok_or_else(|| "the fake engine is gone".to_string())
    }
}

fn engine(searches: &[&[&str]]) -> UciEngine<FakeIo> {
    let io = FakeIo {
        sent: Vec::new(),
        pending: VecDeque::new(),
        searches: searches
            .iter()
            .map(|lines| lines.iter().map(|l| l.to_string()).collect())
            .collect(),
    };
    UciEngine::start(io, 128).unwrap()
}

fn request<'a>(start_fen: &'a str, fen: &'a str, moves: &'a [String]) -> EngineRequest<'a> {
    EngineRequest {
        fen,
        start_fen,
        moves,
        depth: 15,
        multipv: 2,
    }
}

/// One search of the start position, multipv 2, depth 15, answered by `lines`.
fn search(lines: &[&str]) -> Result<Vec<Line>, String> {
    engine(&[lines]).analyse(&request(START, START, &[]))
}

fn line(score: Score, pv: &[&str]) -> Line {
    Line {
        score,
        pv: pv.iter().map(|m| m.to_string()).collect(),
    }
}

fn ucis(moves: &[&str]) -> Vec<String> {
    moves.iter().map(|m| m.to_string()).collect()
}

#[test]
fn test_search_sends_newgame_position_and_go() {
    let mut engine = engine(&[&["bestmove g1f3"], &["bestmove e8d7"], &["bestmove e2e4"]]);
    assert_eq!(engine.id_name(), "Fake 1");
    assert_eq!(
        engine.io_mut().sent,
        [
            "uci",
            "setoption name Threads value 1",
            "setoption name Hash value 128",
            "isready",
        ]
    );
    let e4_e5 = ucis(&["e2e4", "e7e5"]);
    engine
        .analyse(&request(START, AFTER_E4_E5, &e4_e5))
        .unwrap();
    let e4 = ucis(&["e2e4"]);
    engine
        .analyse(&request(PAWN_ENDING, PAWN_ENDING_E4, &e4))
        .unwrap();
    let mut first = request(PAWN_ENDING, PAWN_ENDING, &[]);
    first.multipv = 1;
    engine.analyse(&first).unwrap();
    assert_eq!(
        engine.io_mut().sent[4..],
        [
            "ucinewgame",
            "isready",
            "setoption name MultiPV value 2",
            "position startpos moves e2e4 e7e5",
            "go depth 15",
            "ucinewgame",
            "isready",
            "position fen 4k3/8/8/8/8/8/4P3/4K3 w - - 0 1 moves e2e4",
            "go depth 15",
            "ucinewgame",
            "isready",
            "setoption name MultiPV value 1",
            "position fen 4k3/8/8/8/8/8/4P3/4K3 w - - 0 1",
            "go depth 15",
        ]
    );
}

#[test]
fn test_last_line_per_multipv_wins() {
    let lines = search(&[
        "info string NNUE evaluation using nn-fake.nnue",
        "info depth 14 seldepth 18 multipv 1 score cp 30 nodes 9000 nps 90000 tbhits 0 time 100 pv e2e4 e7e5",
        "info depth 14 seldepth 17 multipv 2 score cp 20 nodes 9000 nps 90000 tbhits 0 time 100 pv d2d4 d7d5",
        "info depth 15 seldepth 19 multipv 1 score cp 40 lowerbound nodes 12000 nps 100000 tbhits 0 time 120 pv e2e4",
        "info depth 15 seldepth 21 multipv 2 score cp 25 nodes 15000 nps 100000 tbhits 0 time 150 pv d2d4 g8f6",
        "info depth 15 seldepth 21 multipv 1 score cp 33 nodes 15000 nps 100000 tbhits 0 time 150 pv e2e4 c7c5",
        "info depth 15 currmove g1f3 currmovenumber 3 score cp 999 pv g1f3",
        "info string score cp 999 pv a2a3",
        "bestmove e2e4 ponder c7c5",
    ])
    .unwrap();
    assert_eq!(
        lines,
        [
            line(Score::Cp(33), &["e2e4", "c7c5"]),
            line(Score::Cp(25), &["d2d4", "g8f6"]),
        ]
    );
}

#[test]
fn test_mated_position_gives_one_empty_line() {
    let mut engine = engine(&[&["info depth 0 score mate 0", "bestmove (none)"]]);
    let lines = engine.analyse(&request(START, START, &[])).unwrap();
    assert_eq!(lines, [line(Score::Mate(0), &[])]);
    assert_eq!(
        engine.last_infos(),
        Some(vec![json!({"score": {"mate": 0}, "pv": [], "depth": 0})])
    );
}

#[test]
fn test_stalemate_gives_cp_zero() {
    let lines = search(&["info depth 0 score cp 0", "bestmove (none)"]).unwrap();
    assert_eq!(lines, [line(Score::Cp(0), &[])]);
}

#[test]
fn test_out_of_range_score_is_an_error() {
    for score in [
        "cp 100001",
        "mate -1001",
        "cp 3000000000",
        "cp -99999999999999999999",
    ] {
        let text = format!("info depth 1 multipv 1 score {score} pv e2e4");
        let err = search(&[&text, "bestmove e2e4"]).unwrap_err();
        assert_eq!(err, format!("score out of range: {text}"));
    }
    for (score, expected) in [
        ("cp 100000", Score::Cp(100000)),
        ("cp -100000", Score::Cp(-100000)),
        ("mate -1000", Score::Mate(-1000)),
    ] {
        let text = format!("info depth 1 multipv 1 score {score} pv e2e4");
        let lines = search(&[&text, "bestmove e2e4"]).unwrap();
        assert_eq!(lines, [line(expected, &["e2e4"])]);
    }
    // a score that is no number is no range error
    let text = "info depth 1 multipv 1 score cp ten pv e2e4";
    let err = search(&[text, "bestmove e2e4"]).unwrap_err();
    assert_eq!(err, format!("bad info line: {text}"));
}

#[test]
fn test_engine_gone_is_an_error() {
    let err = search(&["info depth 1 multipv 1 score cp 10 pv e2e4"]).unwrap_err();
    assert_eq!(err, "the fake engine is gone");
}

#[test]
fn test_unreadable_bestmove_is_an_error() {
    let info = "info depth 1 multipv 1 score cp 10 pv e2e4";
    // e7e5 is legal for Black, not in the start position
    for text in ["bestmove xyz", "bestmove e2e5", "bestmove e7e5 ponder e2e4"] {
        let err = search(&[info, text]).unwrap_err();
        assert_eq!(err, format!("bad bestmove line: {text}"));
    }
    // as python-chess: no move, (none), NULL, a null move, capitals, a bad ponder move
    for text in [
        "bestmove",
        "bestmove (none)",
        "bestmove NULL",
        "bestmove 0000",
        "bestmove G1F3 ponder xyz",
    ] {
        assert_eq!(
            search(&[info, text]).unwrap(),
            [line(Score::Cp(10), &["e2e4"])]
        );
    }
}

#[test]
fn test_last_infos_has_nodes_and_time_in_seconds() {
    let mut engine = engine(&[&[
        "info depth 15 seldepth 20 multipv 1 score cp 33 wdl 52 921 27 nodes 158239 nps 653880 hashfull 51 tbhits 0 time 242 pv e2e4 e7e5",
        "bestmove e2e4 ponder e7e5",
    ]]);
    assert_eq!(engine.last_infos(), None);
    engine.analyse(&request(START, START, &[])).unwrap();
    assert_eq!(
        engine.last_infos(),
        Some(vec![json!({
            "score": {"cp": 33}, "pv": ["e2e4", "e7e5"], "depth": 15, "seldepth": 20,
            "nodes": 158239, "time": 0.242, "nps": 653880, "hashfull": 51, "tbhits": 0,
            "multipv": 1, "wdl": [52, 921, 27],
        })])
    );
}
