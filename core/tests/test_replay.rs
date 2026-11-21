use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use review_core::compare::first_difference;
use review_core::engine::{Engine, EngineRequest, Line, Score};
use review_core::replay::ReplayEngine;
use serde_json::{Value, json};

const START: &str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const FOOLS_MATE: &str = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3";

fn replay(engine_json: Value) -> ReplayEngine {
    ReplayEngine::from_json(&engine_json.to_string()).unwrap().0
}

fn ask(
    engine: &mut ReplayEngine,
    fen: &str,
    multipv: u32,
    depth: u32,
) -> Result<Vec<Line>, String> {
    engine.analyse(&EngineRequest {
        fen,
        start_fen: fen,
        moves: &[],
        depth,
        multipv,
    })
}

fn line(score: Score, pv: &[&str]) -> Line {
    Line {
        score,
        pv: pv.iter().map(|m| m.to_string()).collect(),
    }
}

fn info(cp: i32, pv: &[&str]) -> Value {
    json!({"score": {"cp": cp}, "pv": pv})
}

#[test]
fn test_replay_answers_the_recorded_search() {
    let text = json!({
        "meta": {"depth": 15, "engine": "Stockfish"},
        "calls": [{"fen": START, "multipv": 2, "depth": 15, "infos": [
            {"score": {"cp": 36}, "pv": ["e2e4", "e7e5"], "depth": 15, "multipv": 1,
             "nodes": 412345, "wdl": [52, 921, 27]},
            {"score": {"cp": 20}, "pv": ["d2d4", "d7d5"], "depth": 15, "multipv": 2,
             "nodes": 412345, "wdl": [40, 930, 30]},
        ]}],
    })
    .to_string();
    let (mut engine, depth) = ReplayEngine::from_json(&text).unwrap();
    assert_eq!(depth, 15);
    assert_eq!(
        ask(&mut engine, START, 2, 15).unwrap(),
        vec![
            line(Score::Cp(36), &["e2e4", "e7e5"]),
            line(Score::Cp(20), &["d2d4", "d7d5"]),
        ]
    );
}

#[test]
fn test_replay_reads_a_mate_and_an_empty_pv() {
    let mut engine = replay(json!({
        "meta": {"depth": 15},
        "calls": [{"fen": FOOLS_MATE, "multipv": 2, "depth": 15,
                   "infos": [{"score": {"mate": 0}, "pv": []}]}],
    }));
    assert_eq!(
        ask(&mut engine, FOOLS_MATE, 2, 15).unwrap(),
        vec![line(Score::Mate(0), &[])]
    );
}

#[test]
fn test_replay_multipv_null_counts_as_one() {
    // Python's `multipv or 1`, on the recorded call and on the request
    let null = json!({"fen": START, "multipv": null, "depth": 15, "infos": [info(30, &["e2e4"])]});
    let absent = json!({"fen": START, "depth": 15, "infos": [info(30, &["e2e4"])]});
    let zero = json!({"fen": START, "multipv": 0, "depth": 15, "infos": [info(30, &["e2e4"])]});
    for call in [null, absent, zero] {
        let mut engine = replay(json!({"meta": {"depth": 15}, "calls": [call]}));
        for multipv in [1, 0] {
            assert_eq!(
                ask(&mut engine, START, multipv, 15),
                Ok(vec![line(Score::Cp(30), &["e2e4"])]),
                "{call} multipv={multipv}"
            );
        }
    }
    let mut engine = replay(json!({"meta": {"depth": 15}, "calls": []}));
    assert_eq!(
        ask(&mut engine, START, 0, 15),
        Err(format!("no recorded search: {START} multipv=1 depth=15"))
    );
}

#[test]
fn test_replay_malformed_json_is_an_error() {
    assert_eq!(
        ReplayEngine::from_json("{").err().as_deref(),
        Some("EOF while parsing an object at line 1 column 1")
    );
}

#[test]
fn test_replay_last_recorded_search_wins() {
    let mut engine = replay(json!({
        "meta": {"depth": 15},
        "calls": [
            {"fen": START, "multipv": 2, "depth": 15, "infos": [info(10, &["g1f3"])]},
            {"fen": START, "multipv": 2, "depth": 15, "infos": [info(25, &["e2e4"])]},
        ],
    }));
    assert_eq!(
        ask(&mut engine, START, 2, 15).unwrap(),
        vec![line(Score::Cp(25), &["e2e4"])]
    );
}

#[test]
fn test_replay_unknown_position_names_fen_multipv_depth() {
    // the same position with another multipv or depth is another search
    let mut engine = replay(json!({
        "meta": {"depth": 15},
        "calls": [
            {"fen": START, "multipv": 1, "depth": 15, "infos": [info(30, &["e2e4"])]},
            {"fen": START, "multipv": 2, "depth": 14, "infos": [info(30, &["e2e4"])]},
        ],
    }));
    assert_eq!(
        ask(&mut engine, START, 2, 15),
        Err(format!("no recorded search: {START} multipv=2 depth=15"))
    );
}

#[test]
fn test_first_difference() {
    let cases = [
        (
            json!({"a": 1, "b": [1, 2]}),
            json!({"b": [1, 2], "a": 1}),
            None,
        ),
        (json!({"a": 1}), json!({"a": 1.0}), Some("a: 1 != 1.0")),
        (
            json!({"moves": [{}, {"wp_drop": 0.1}]}),
            json!({"moves": [{}, {"wp_drop": 0.2}]}),
            Some("moves[1].wp_drop: 0.1 != 0.2"),
        ),
        (json!({"a": 1, "b": 2}), json!({"a": 1}), Some("b: missing")),
        (
            json!({"a": 1}),
            json!({"a": 1, "c": 3}),
            Some("c: unexpected"),
        ),
        (
            json!({"moves": [1, 2, 3]}),
            json!({"moves": [1, 2, 3, 4]}),
            Some("moves: length 3 != 4"),
        ),
        // the expected object's key order decides which difference comes first
        (
            json!({"x": 1, "y": 2}),
            json!({"y": 3, "x": 4}),
            Some("x: 1 != 4"),
        ),
    ];
    for (expected, actual, want) in cases {
        assert_eq!(
            first_difference(&expected, &actual).as_deref(),
            want,
            "{expected} vs {actual}"
        );
    }
}

/// A fresh folder in the system temp directory, named after the test and this process.
fn temp_dir(test: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("{test}-{}", std::process::id()));
    fs::create_dir_all(&dir).unwrap();
    dir
}

/// The exit code, stdout and stderr of the replay binary.
fn run_replay(args: &[&Path]) -> (Option<i32>, String, String) {
    let output = Command::new(env!("CARGO_BIN_EXE_replay"))
        .args(args)
        .output()
        .unwrap();
    let text = |bytes| String::from_utf8(bytes).unwrap();
    (
        output.status.code(),
        text(output.stdout),
        text(output.stderr),
    )
}

/// Copies the three mini files into `dir`.
fn copy_mini(dir: &Path) {
    for name in ["mini.pgn", "mini.engine.json", "mini.analysis.json"] {
        fs::copy(data_dir().join(name), dir.join(name)).unwrap();
    }
}

fn data_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("data")
}

fn read_json(path: &Path) -> Value {
    serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn test_replay_command_matches_python_on_the_mini_game() {
    let out = temp_dir("test_replay_command_matches_python_on_the_mini_game");
    let (code, stdout, _) = run_replay(&[&data_dir(), &out]);
    assert_eq!(code, Some(0), "{stdout}");
    assert_eq!(stdout, "ok mini\n1/1 equal\n");
    let written = read_json(&out.join("mini.analysis.json"));
    let stored = read_json(&data_dir().join("mini.analysis.json"));
    assert_eq!(first_difference(&stored, &written), None);
    fs::remove_dir_all(&out).unwrap();
}

#[test]
fn test_replay_command_reports_the_first_difference() {
    let dir = temp_dir("test_replay_command_reports_the_first_difference");
    copy_mini(&dir);
    let path = dir.join("mini.analysis.json");
    let mut analysis = read_json(&path);
    analysis["moves"][0]["wp_drop"] = json!(0.5);
    fs::write(&path, analysis.to_string()).unwrap();

    let (code, stdout, _) = run_replay(&[&dir, &dir.join("out")]);
    assert_eq!(code, Some(1), "{stdout}");
    let lines: Vec<&str> = stdout.lines().collect();
    assert!(
        lines[0].starts_with("diff mini moves[0].wp_drop: 0.5 != "),
        "{stdout}"
    );
    assert_eq!(lines[1..], ["0/1 equal"]);
    fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn test_replay_command_needs_two_folders() {
    let (code, stdout, stderr) = run_replay(&[&data_dir()]);
    assert_eq!(code, Some(2), "{stdout}");
    assert_eq!(stderr, "usage: replay <fixture dir> <out dir> [id ...]\n");
}

#[test]
fn test_replay_command_refuses_the_fixture_folder_as_output() {
    // writing there would replace the stored analysis, then compare it with itself
    let dir = temp_dir("test_replay_command_refuses_the_fixture_folder_as_output");
    copy_mini(&dir);
    let path = dir.join("mini.analysis.json");
    let before = fs::read(&path).unwrap();
    let (code, stdout, _) = run_replay(&[&dir, &dir]);
    assert_eq!(code, Some(2), "{stdout}");
    assert_eq!(fs::read(&path).unwrap(), before);
    fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn test_replay_command_rejects_a_folder_without_engine_files() {
    let dir = temp_dir("test_replay_command_rejects_a_folder_without_engine_files");
    let (code, stdout, stderr) = run_replay(&[&dir, &dir.join("out")]);
    assert_eq!(code, Some(1), "{stdout}");
    assert_eq!(
        stderr,
        format!("error: no *.engine.json in {}\n", dir.display())
    );
    fs::remove_dir_all(&dir).unwrap();
}
