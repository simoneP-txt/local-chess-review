//! The protocol of spike/bench/uci.py over engines in the app: the same commands, the same RawRun.

use std::thread;
use std::time::{Duration, Instant};

use serde::Deserialize;
use serde_json::{json, Value};

use crate::engine::EngineIo;

#[derive(Deserialize)]
struct Opera {
    moves: Vec<String>,
    depth: u32,
    multipv: u32,
    hash_mb: u32,
}

/// Analyses the Opera Game and returns the RawRun. One engine searches every position with
/// `threads`; several engines run one thread each, engine i searching the plies k with
/// k mod N = i. Err, and no RawRun, when an engine fails before its last bestmove.
pub fn run(
    mut engines: Vec<Box<dyn EngineIo>>,
    threads: u32,
    label: &str,
    on_line: &(dyn Fn(&str) + Sync),
) -> Result<Value, String> {
    if engines.is_empty() || threads == 0 || (engines.len() > 1 && threads != 1) {
        return Err(format!(
            "{} engines with {threads} threads: one engine with 1 thread or more, or several with 1 each",
            engines.len()
        ));
    }
    let opera: Opera = serde_json::from_str(include_str!("../../../opera/opera.json"))
        .map_err(|e| e.to_string())?;
    let result = analyse(&mut engines, &opera, threads, label, on_line);
    for engine in &mut engines {
        let _ = engine.send("quit"); // as in uci.py: a complete run survives an engine gone after it
    }
    result
}

fn analyse(
    engines: &mut [Box<dyn EngineIo>],
    opera: &Opera,
    threads: u32,
    label: &str,
    on_line: &(dyn Fn(&str) + Sync),
) -> Result<Value, String> {
    let setup = [
        format!("setoption name Threads value {threads}"),
        format!("setoption name Hash value {}", opera.hash_mb),
        format!("setoption name MultiPV value {}", opera.multipv),
        "ucinewgame".to_owned(),
    ];
    let mut ids = Vec::new();
    for engine in engines.iter_mut() {
        engine.send("uci")?;
        let lines = read_until(engine.as_mut(), "uciok", on_line)?;
        ids.push(
            lines
                .iter()
                .find_map(|line| line.strip_prefix("id name ").map(str::to_owned)),
        );
        for command in &setup {
            engine.send(command)?;
        }
        engine.send("isready")?;
        read_until(engine.as_mut(), "readyok", on_line)?;
    }
    // With N engines the clock starts once every one is ready and stops at the last bestmove of any.
    let n = engines.len();
    let start = Instant::now();
    let searched = thread::scope(|scope| {
        let searches: Vec<_> = engines
            .iter_mut()
            .enumerate()
            .map(|(i, engine)| scope.spawn(move || search(engine.as_mut(), opera, i, n, on_line)))
            .collect();
        searches
            .into_iter()
            .map(|search| {
                search
                    .join()
                    .map_err(|_| "a search thread panicked".to_owned())?
            })
            .collect::<Result<Vec<_>, String>>()
    })?;
    let end = searched.iter().map(|(_, end)| *end).max().unwrap_or(start);
    let mut positions: Vec<Value> = searched
        .into_iter()
        .flat_map(|(positions, _)| positions)
        .collect();
    positions.sort_by_key(|position| position["ply"].as_u64());
    Ok(
        json!({"label": label, "engine_id": ids[0], "threads": threads, "instances": n,
              "total_wall_ms": ms(end - start), "meta": {}, "positions": positions}),
    )
}

/// Searches the plies first, first + step, ... and returns their positions and the time of the last bestmove.
fn search(
    engine: &mut dyn EngineIo,
    opera: &Opera,
    first: usize,
    step: usize,
    on_line: &(dyn Fn(&str) + Sync),
) -> Result<(Vec<Value>, Instant), String> {
    let mut positions = Vec::new();
    for ply in (first..=opera.moves.len()).step_by(step) {
        engine.send(&match ply {
            0 => "position startpos".to_owned(),
            _ => format!("position startpos moves {}", opera.moves[..ply].join(" ")),
        })?;
        let go = Instant::now();
        engine.send(&format!("go depth {}", opera.depth))?;
        let lines = read_until(engine, "bestmove", on_line)?;
        positions.push(json!({"ply": ply, "wall_ms": ms(go.elapsed()), "lines": lines}));
    }
    Ok((positions, Instant::now()))
}

/// Every line up to the first that starts with `prefix`, that one included.
fn read_until(
    engine: &mut dyn EngineIo,
    prefix: &str,
    on_line: &(dyn Fn(&str) + Sync),
) -> Result<Vec<String>, String> {
    let mut lines = Vec::new();
    loop {
        let line = engine
            .read_line()
            .map_err(|e| format!("{e} before {prefix}"))?;
        on_line(&line);
        let last = line.starts_with(prefix);
        lines.push(line);
        if last {
            return Ok(lines);
        }
    }
}

fn ms(duration: Duration) -> f64 {
    (duration.as_secs_f64() * 10_000.0).round() / 10.0
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;
    use std::sync::{Arc, Mutex};

    use super::*;

    type Log = Arc<Mutex<Vec<String>>>;

    /// Answers like Stockfish and logs every command; with `dies_at` = Some(n) it goes silent at its n-th go.
    struct Fake {
        log: Log,
        out: VecDeque<String>,
        gos: usize,
        dies_at: Option<usize>,
    }

    impl EngineIo for Fake {
        fn send(&mut self, line: &str) -> Result<(), String> {
            self.log.lock().unwrap().push(line.to_owned());
            match line.split(' ').next() {
                Some("uci") => self.out.extend(["id name Fake 1".into(), "uciok".into()]),
                Some("isready") => self.out.push_back("readyok".into()),
                Some("go") => {
                    self.gos += 1;
                    if self.dies_at != Some(self.gos) {
                        self.out.push_back(
                            "info depth 1 multipv 1 score cp 10 nodes 20 time 3 pv e2e4".into(),
                        );
                        self.out.push_back("bestmove e2e4".into());
                    }
                }
                _ => {}
            }
            Ok(())
        }

        fn read_line(&mut self) -> Result<String, String> {
            self.out
                .pop_front()
                .ok_or_else(|| "the engine exited".to_owned())
        }
    }

    fn fakes(n: usize, dies_at: Option<usize>) -> (Vec<Box<dyn EngineIo>>, Vec<Log>) {
        let logs: Vec<Log> = (0..n).map(|_| Log::default()).collect();
        let engines = logs
            .iter()
            .map(|log| {
                Box::new(Fake {
                    log: log.clone(),
                    out: VecDeque::new(),
                    gos: 0,
                    dies_at,
                }) as Box<dyn EngineIo>
            })
            .collect();
        (engines, logs)
    }

    /// The commands of the Protocol for the given plies, with the moves of opera.json.
    fn protocol(threads: u32, plies: impl Iterator<Item = usize>) -> Vec<String> {
        let opera: Value = serde_json::from_str(include_str!("../../../opera/opera.json")).unwrap();
        let moves: Vec<&str> = opera["moves"]
            .as_array()
            .unwrap()
            .iter()
            .map(|m| m.as_str().unwrap())
            .collect();
        let mut commands: Vec<String> = vec![
            "uci".into(),
            format!("setoption name Threads value {threads}"),
            "setoption name Hash value 128".into(),
            "setoption name MultiPV value 2".into(),
            "ucinewgame".into(),
            "isready".into(),
        ];
        for k in plies {
            commands.push(match k {
                0 => "position startpos".into(),
                _ => format!("position startpos moves {}", moves[..k].join(" ")),
            });
            commands.push("go depth 15".into());
        }
        commands.push("quit".into());
        commands
    }

    fn plies(raw: &Value) -> Vec<u64> {
        raw["positions"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| p["ply"].as_u64().unwrap())
            .collect()
    }

    #[test]
    fn run_follows_protocol() {
        let (engines, logs) = fakes(1, None);
        let seen = Mutex::new(Vec::new());
        let raw = run(engines, 1, "fake-t1-1", &|line| {
            seen.lock().unwrap().push(line.to_owned())
        })
        .unwrap();
        assert_eq!(*logs[0].lock().unwrap(), protocol(1, 0..34));
        assert_eq!(
            (
                &raw["label"],
                &raw["engine_id"],
                &raw["threads"],
                &raw["instances"],
                &raw["meta"]
            ),
            (
                &"fake-t1-1".into(),
                &"Fake 1".into(),
                &1.into(),
                &1.into(),
                &serde_json::json!({})
            )
        );
        assert_eq!(plies(&raw), (0..34).collect::<Vec<_>>());
        for position in raw["positions"].as_array().unwrap() {
            let lines = position["lines"].as_array().unwrap();
            assert_eq!(lines.len(), 2);
            assert_eq!(lines[1], "bestmove e2e4");
            assert!(position["wall_ms"].is_f64());
        }
        assert!(raw["total_wall_ms"].is_f64());
        assert_eq!(seen.lock().unwrap().len(), 3 + 34 * 2); // id name, uciok, readyok, then two lines a go

        let (engines, logs) = fakes(3, None);
        let raw = run(engines, 1, "fake-i3-1", &|_| {}).unwrap();
        for (i, log) in logs.iter().enumerate() {
            assert_eq!(*log.lock().unwrap(), protocol(1, (i..34).step_by(3)));
        }
        assert_eq!((&raw["threads"], &raw["instances"]), (&1.into(), &3.into()));
        assert_eq!(plies(&raw), (0..34).collect::<Vec<_>>());
    }

    #[test]
    fn run_gives_no_raw_run_on_errors() {
        let (engines, logs) = fakes(1, Some(5));
        assert!(run(engines, 1, "dies-t1-1", &|_| {}).is_err());
        assert_eq!(logs[0].lock().unwrap().last().unwrap(), "quit");
        let (engines, _) = fakes(3, Some(2));
        assert!(run(engines, 1, "dies-i3-1", &|_| {}).is_err());
        let (engines, _) = fakes(3, None);
        assert!(run(engines, 2, "bad-i3-1", &|_| {}).is_err()); // instances run one thread each
        assert!(run(Vec::new(), 1, "bad-t1-1", &|_| {}).is_err());
    }
}
