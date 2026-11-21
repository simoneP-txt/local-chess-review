//! replay <fixture dir> <out dir> [id ...]: analyzes each game on its recorded engine answers,
//! writes <out dir>/<id>.analysis.json and compares it with <fixture dir>/<id>.analysis.json.
use std::path::Path;
use std::process::ExitCode;
use std::{env, fs};

use review_core::analyze_pgn;
use review_core::compare::first_difference;
use review_core::openings::Book;
use review_core::replay::ReplayEngine;
use serde_json::Value;

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    let [fixtures, out, ids @ ..] = &args[..] else {
        eprintln!("usage: replay <fixture dir> <out dir> [id ...]");
        return ExitCode::from(2);
    };
    let (fixtures, out) = (Path::new(fixtures), Path::new(out));
    // the output would replace the stored analyses, then pass as them
    if let (Ok(a), Ok(b)) = (fs::canonicalize(fixtures), fs::canonicalize(out))
        && a == b
    {
        eprintln!("error: the output folder is the fixture folder");
        return ExitCode::from(2);
    }
    let ids = if ids.is_empty() {
        match recorded_ids(fixtures) {
            Ok(ids) => ids,
            Err(e) => {
                eprintln!("error: {e}");
                return ExitCode::FAILURE;
            }
        }
    } else {
        ids.to_vec()
    };
    let book = Book::load();
    let mut equal = 0;
    for id in &ids {
        match replay(fixtures, out, id, &book) {
            Ok(None) => {
                equal += 1;
                println!("ok {id}");
            }
            Ok(Some(difference)) => println!("diff {id} {difference}"),
            Err(e) => println!("error {id}: {e}"),
        }
    }
    println!("{equal}/{} equal", ids.len());
    if equal == ids.len() {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

/// The id of every `<id>.engine.json` in `dir`, sorted; none is an error.
fn recorded_ids(dir: &Path) -> Result<Vec<String>, String> {
    let entries = fs::read_dir(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let mut ids = Vec::new();
    for entry in entries {
        let name = entry.map_err(|e| e.to_string())?.file_name();
        // shortcut: a file name that is not UTF-8 is skipped, read it as OsStr if an id ever needs it
        if let Some(id) = name.to_str().and_then(|n| n.strip_suffix(".engine.json")) {
            ids.push(id.to_string());
        }
    }
    if ids.is_empty() {
        return Err(format!("no *.engine.json in {}", dir.display()));
    }
    ids.sort();
    Ok(ids)
}

/// Writes the Rust analysis of game `id`, then gives its first difference from the stored one.
fn replay(fixtures: &Path, out: &Path, id: &str, book: &Book) -> Result<Option<String>, String> {
    let read =
        |name: String| fs::read_to_string(fixtures.join(&name)).map_err(|e| format!("{name}: {e}"));
    let pgn = read(format!("{id}.pgn"))?;
    let (mut engine, depth) = ReplayEngine::from_json(&read(format!("{id}.engine.json"))?)
        .map_err(|e| format!("{id}.engine.json: {e}"))?;
    let analysis = analyze_pgn(&pgn, None, None, &mut engine, book, depth)?;
    let written = out.join(format!("{id}.analysis.json"));
    let text = serde_json::to_string_pretty(&analysis).map_err(|e| e.to_string())?;
    fs::create_dir_all(out)
        .and_then(|()| fs::write(&written, text))
        .map_err(|e| format!("{}: {e}", written.display()))?;
    let expected: Value = serde_json::from_str(&read(format!("{id}.analysis.json"))?)
        .map_err(|e| format!("{id}.analysis.json: {e}"))?;
    let actual = serde_json::to_value(&analysis).map_err(|e| e.to_string())?;
    Ok(first_difference(&expected, &actual))
}
