//! UciEngine over ProcessIo with the real Stockfish; the tests that run it skip without
//! engine/stockfish.exe.
#![cfg(not(any(target_os = "android", target_os = "ios")))]

use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use review_core::analysis::{game_ucis, requests};
use review_core::engine::{Engine, EngineRequest};
use review_core::game::read_game;
use review_core::openings::Book;
use review_core::process::ProcessIo;
use review_core::uci::{EngineIo, UciEngine};
use serde_json::Value;

/// The first 12 plies of the Opera Game (Morphy, Paris 1858), public domain.
const OPERA: &str = "1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 *\n";

fn engine_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("engine")
}

/// Stockfish with Hash 128, or None (and a message) without the exe.
fn stockfish() -> Option<UciEngine<ProcessIo>> {
    let exe = engine_dir().join(format!("stockfish{}", std::env::consts::EXE_SUFFIX));
    if !exe.exists() {
        eprintln!("skipped: {} is missing", exe.display());
        return None;
    }
    Some(UciEngine::start(ProcessIo::spawn(&exe).unwrap(), 128).unwrap())
}

/// time and nps are measured, so they change from run to run.
fn without_times(mut infos: Vec<Value>) -> Vec<Value> {
    for info in &mut infos {
        let info = info.as_object_mut().unwrap();
        info.remove("time");
        info.remove("nps");
    }
    infos
}

#[test]
fn test_missing_exe_is_an_error() {
    let exe = engine_dir().join("no-such-engine.exe");
    let err = ProcessIo::spawn(&exe).err().unwrap();
    assert!(err.contains(&exe.display().to_string()), "{err}");
}

#[test]
fn test_order_does_not_change_answers() {
    let Some(mut engine) = stockfish() else {
        return;
    };
    assert!(
        engine.id_name().starts_with("Stockfish"),
        "{}",
        engine.id_name()
    );
    let game = read_game(OPERA, &Book::load()).unwrap();
    let ucis = game_ucis(&game);
    let requests = requests(&game, &ucis, 10);
    assert_eq!(requests.len(), 13);
    let mut answer = |request: &EngineRequest| {
        engine.analyse(request).unwrap();
        without_times(engine.last_infos().unwrap())
    };
    let forward: Vec<_> = requests.iter().map(&mut answer).collect();
    let mut backward: Vec<_> = requests.iter().rev().map(&mut answer).collect();
    backward.reverse();
    for infos in &forward {
        assert_eq!(infos.len(), 2, "{infos:?}");
        assert!(infos.iter().all(|info| info["nodes"].is_u64()), "{infos:?}");
    }
    assert_eq!(forward, backward);
}

#[test]
fn test_close_ends_the_process() {
    let Some(mut engine) = stockfish() else {
        return;
    };
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let closed = engine.io_mut().close();
        sender.send((closed, engine.io_mut().read_line())).unwrap();
    });
    let (closed, read) = receiver
        .recv_timeout(Duration::from_secs(3))
        .expect("close and read_line took more than 3 s");
    assert_eq!(closed, Ok(()));
    assert!(read.is_err(), "{read:?}");
}
