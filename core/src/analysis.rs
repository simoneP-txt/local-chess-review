//! Steps 2 and 3 of the analysis: ask the engine for every position, then classify every move
//! as GameAnalyzer.analyze_pgn does (analyzer.py:650-848).
use serde_json::{Map, Value};
use shakmaty::san::SanPlus;
use shakmaty::uci::UciMove;
use shakmaty::{Color, Position};

use crate::ByColor;
use crate::accuracy::game_accuracy_lichess;
use crate::engine::{Engine, EngineRequest, Line};
use crate::game::{Game, read_game};
use crate::openings::Book;
use crate::py::{py_round, py_round_int};
use crate::scoring::{
    ACPL_CP_LOSS_CAP, PV_PLIES_TO_KEEP, classify_standard, score_to_cp, score_to_wp,
};
use crate::see::static_exchange_evaluation;
use crate::special::{is_brilliant, is_great_move, make_comment};
use crate::stats::{Phases, compute_phases, estimate_performance_elo};

#[derive(Debug, serde::Serialize)]
pub struct MoveAnalysis {
    pub ply: u32,
    pub move_number: u32,
    pub color: &'static str,
    pub san: String,
    pub uci: String,
    pub fen_before: String,
    pub fen_after: String,
    pub best_move_san: String,
    pub best_move_uci: String,
    pub eval_before_cp: i32,
    pub eval_after_cp: i32,
    pub eval_white_cp: i32,
    pub wp_before: f64,
    pub wp_after: f64,
    pub wp_white_after: f64,
    pub wp_drop: f64,
    pub cp_loss: i32,
    pub classification: &'static str,
    pub is_best: bool,
    pub phase: &'static str,
    pub see_played: i32,
    pub comment: Option<Value>,
    pub pv_uci: Vec<String>,
    pub pv_after_uci: Vec<String>,
    pub clock_after: Option<f64>,
}

#[derive(Debug, serde::Serialize)]
pub struct WpPoint {
    pub ply: u32,
    pub wp_white: f64,
    pub classification: &'static str,
    pub color: &'static str,
}

#[derive(Debug, serde::Serialize)]
pub struct Opening {
    pub eco: String,
    pub name: String,
}

#[derive(Debug, serde::Serialize)]
pub struct Analysis {
    pub headers: Map<String, Value>,
    pub moves: Vec<MoveAnalysis>,
    pub accuracy: ByColor<f64>,
    pub acpl: ByColor<i64>,
    pub performance_elo: ByColor<i64>,
    pub wp_series: Vec<WpPoint>,
    pub move_counts: ByColor<Map<String, Value>>,
    pub phases: Phases,
    pub real_elo: ByColor<Option<i64>>,
    pub time_control: String,
    pub opening: Option<Opening>,
    pub start_fen: String,
}

/// What ply i needs from the plies before it (Great (a) and Miss read the previous move).
#[derive(Clone, Copy, Default)]
pub struct Carry {
    pub wp_before_opponent_move: Option<f64>,
    pub previous_blunder: bool,
}

/// The UCI moves of the game, the slices the requests borrow.
pub fn game_ucis(game: &Game) -> Vec<String> {
    game.plies.iter().map(|p| p.uci.clone()).collect()
}

/// n + 1 requests in game order, multipv 2: request i has fen of position i, the game's
/// start_fen and moves = &ucis[..i].
pub fn requests<'a>(game: &'a Game, ucis: &'a [String], depth: u32) -> Vec<EngineRequest<'a>> {
    let fens = std::iter::once(&game.start_fen).chain(game.plies.iter().map(|p| &p.fen_after));
    fens.enumerate()
        .map(|(i, fen)| EngineRequest {
            fen,
            start_fen: &game.start_fen,
            moves: &ucis[..i],
            depth,
            multipv: 2,
        })
        .collect()
}

/// Step 2: one search per position, in game order: the first position, then one after each ply.
pub fn evaluate(
    game: &Game,
    engine: &mut dyn Engine,
    depth: u32,
) -> Result<Vec<Vec<Line>>, String> {
    let ucis = game_ucis(game);
    requests(game, &ucis, depth)
        .iter()
        .map(|request| {
            let lines = engine.analyse(request)?;
            if lines.is_empty() {
                return Err(format!("no lines in the engine answer: {}", request.fen));
            }
            Ok(lines)
        })
        .collect()
}

/// Step 3, a pure function: ply i reads evals[i] as its BEFORE and evals[i + 1] as its AFTER.
pub fn classify(
    game: &Game,
    evals: &[Vec<Line>],
    real_elo: ByColor<Option<i64>>,
) -> Result<Analysis, String> {
    if evals.len() != game.plies.len() + 1 {
        return Err(format!(
            "expected {} engine answers, got {}",
            game.plies.len() + 1,
            evals.len()
        ));
    }
    if let Some(i) = evals.iter().position(Vec::is_empty) {
        let fen = match i {
            0 => &game.start_fen,
            _ => &game.plies[i - 1].fen_after,
        };
        return Err(format!("no lines in the engine answer: {fen}"));
    }

    let mut moves = Vec::new();
    let mut carry = Carry::default();
    for (i, pair) in evals.windows(2).enumerate() {
        let (analysis, next) = classify_ply(game, i, &pair[0], &pair[1], carry)?;
        moves.push(analysis);
        carry = next;
    }
    Ok(summarize(game, moves, real_elo))
}

/// Ply i of the game from its BEFORE (evals[i]) and AFTER (evals[i + 1]) answers.
pub fn classify_ply(
    game: &Game,
    i: usize,
    before: &[Line],
    after: &[Line],
    carry: Carry,
) -> Result<(MoveAnalysis, Carry), String> {
    let ply = &game.plies[i];
    let color_to_move = ply.before.turn();

    // 1) BEFORE: the search of the current position (line 1 best, line 2 2nd best)
    let Some(best_info) = before.first() else {
        return Err(format!("no lines in the engine answer: {}", ply.fen_before));
    };
    let second_info = before.get(1);

    let best_pv = &best_info.pv;
    // shortcut: a 0000 or illegal first move is an error, while python-chess writes "" for 0000
    // and a SAN for an illegal move from an occupied square (e2e5 is e5); Stockfish sends
    // neither, upgrade if another source fills the lines.
    let best_move = best_pv
        .first()
        .map(|uci| {
            uci.parse::<UciMove>()
                .ok()
                .and_then(|m| m.to_move(&ply.before).ok())
                .ok_or_else(|| format!("unreadable best move {uci} in {}", ply.fen_before))
        })
        .transpose()?;
    let pv_uci: Vec<String> = best_pv.iter().take(PV_PLIES_TO_KEEP).cloned().collect();

    let score_best = best_info.score;
    let wp_before_best = score_to_wp(score_best, color_to_move, color_to_move);
    let eval_before_cp = score_to_cp(score_best, color_to_move, color_to_move);

    let second_best_wp =
        second_info.map(|line| score_to_wp(line.score, color_to_move, color_to_move));

    let best_move_san = best_move.map_or(String::new(), |m| {
        SanPlus::from_move(ply.before.clone(), m).to_string()
    });
    let best_move_uci = best_pv.first().cloned().unwrap_or_default();

    // 2) AFTER: the search of the new position -> residual WP + "punishment" PV
    //    (the line the opponent uses to exploit the just-played move)
    let Some(info_after) = after.first() else {
        return Err(format!("no lines in the engine answer: {}", ply.fen_after));
    };
    let score_after = info_after.score;
    let after_turn = !color_to_move;
    let wp_after = score_to_wp(score_after, after_turn, color_to_move);
    let eval_after_cp = score_to_cp(score_after, after_turn, color_to_move);
    let eval_white_cp = score_to_cp(score_after, after_turn, Color::White);
    let wp_white_after = score_to_wp(score_after, after_turn, Color::White);
    let pv_after_uci: Vec<String> = info_after
        .pv
        .iter()
        .take(PV_PLIES_TO_KEEP)
        .cloned()
        .collect();

    let wp_drop = (wp_before_best - wp_after).max(0.0);
    let cp_loss = ACPL_CP_LOSS_CAP.min((eval_before_cp - eval_after_cp).max(0));
    // 5.8.14: the played move against the first move of line 1, castling as e1g1
    let is_best = best_pv.first() == Some(&ply.uci);
    let see_played = static_exchange_evaluation(&ply.before, ply.played);

    let base_class = classify_standard(wp_drop, is_best);

    // ===== ASSIGNMENT HIERARCHY =====
    // Brilliant > Great > Book (except a base mistake or blunder: the database holds trap
    // lines) > Miss (the opponent's previous move was a blunder and ours does not take
    // advantage) > base class
    let final_class = if is_brilliant(
        &ply.before,
        ply.played,
        wp_before_best,
        wp_after,
        eval_after_cp,
        second_best_wp,
        is_best,
    ) {
        "brilliant"
    } else if is_great_move(
        is_best,
        base_class,
        wp_before_best,
        wp_after,
        second_best_wp,
        carry.wp_before_opponent_move,
    ) {
        "great"
    } else if ply.in_book && !matches!(base_class, "mistake" | "blunder") {
        "book"
    } else if !matches!(base_class, "best" | "excellent") && carry.previous_blunder {
        "miss"
    } else {
        base_class
    };
    let carry = Carry {
        // The next mover's WP in this BEFORE position, unrounded, for its Great (a).
        // A line-1 move is no error, even if the next search sees it worse (horizon effect).
        wp_before_opponent_move: if is_best {
            None
        } else {
            Some(1.0 - wp_before_best)
        },
        previous_blunder: final_class == "blunder",
    };

    let comment = make_comment(
        final_class,
        &ply.before,
        ply.played,
        see_played,
        &pv_after_uci,
    );

    let analysis = MoveAnalysis {
        ply: i as u32 + 1,
        move_number: ply.move_number,
        color: ply.color,
        san: ply.san.clone(),
        uci: ply.uci.clone(),
        fen_before: ply.fen_before.clone(),
        fen_after: ply.fen_after.clone(),
        best_move_san,
        best_move_uci,
        eval_before_cp,
        eval_after_cp,
        eval_white_cp,
        wp_before: py_round(wp_before_best, 4),
        wp_after: py_round(wp_after, 4),
        wp_white_after: py_round(wp_white_after, 4),
        wp_drop: py_round(wp_drop, 4),
        cp_loss,
        classification: final_class,
        is_best,
        phase: ply.phase,
        see_played,
        comment,
        pv_uci,
        pv_after_uci,
        clock_after: ply.clock.map(|clock| py_round(clock, 2)),
    };
    Ok((analysis, carry))
}

/// Accuracy, ACPL, performance Elo, series, counts, phases and headers from the classified plies.
pub fn summarize(
    game: &Game,
    moves: Vec<MoveAnalysis>,
    real_elo: ByColor<Option<i64>>,
) -> Analysis {
    let accuracy = compute_accuracy(&moves);
    let acpl = compute_acpl(&moves);
    let performance_elo = ByColor {
        white: estimate_performance_elo(accuracy.white, real_elo.white),
        black: estimate_performance_elo(accuracy.black, real_elo.black),
    };
    let wp_series = moves
        .iter()
        .map(|m| WpPoint {
            ply: m.ply,
            wp_white: py_round(m.wp_white_after * 100.0, 1),
            classification: m.classification,
            color: m.color,
        })
        .collect();
    let move_counts = count_classifications(&moves);
    let drops: Vec<(&str, &str, f64)> = moves
        .iter()
        .map(|m| (m.phase, m.color, m.wp_drop))
        .collect();
    let phases = compute_phases(&drops);

    Analysis {
        headers: game.headers.clone(),
        accuracy,
        acpl,
        performance_elo,
        wp_series,
        move_counts,
        phases,
        real_elo,
        time_control: game
            .headers
            .get("TimeControl")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        opening: game
            .opening
            .clone()
            .map(|(eco, name)| Opening { eco, name }),
        start_fen: game.start_fen.clone(),
        moves,
    }
}

/// The analysis of the PGN's first game: the Elo arguments, or else the header values, cap the
/// performance Elo.
pub fn analyze_pgn(
    pgn: &str,
    white_elo: Option<i64>,
    black_elo: Option<i64>,
    engine: &mut dyn Engine,
    book: &Book,
    depth: u32,
) -> Result<Analysis, String> {
    let game = read_game(pgn, book)?;
    let real_elo = ByColor {
        white: white_elo.or(game.white_elo),
        black: black_elo.or(game.black_elo),
    };
    let evals = evaluate(&game, engine, depth)?;
    classify(&game, &evals, real_elo)
}

/// Per colour, in order of first appearance, as Python's dict.
fn count_classifications(moves: &[MoveAnalysis]) -> ByColor<Map<String, Value>> {
    let mut out = ByColor {
        white: Map::new(),
        black: Map::new(),
    };
    for m in moves {
        let counts = if m.color == "white" {
            &mut out.white
        } else {
            &mut out.black
        };
        let n = counts
            .get(m.classification)
            .and_then(Value::as_i64)
            .unwrap_or(0);
        counts.insert(m.classification.to_string(), Value::from(n + 1));
    }
    out
}

/// ACPL (Average Centipawn Loss) per player: the mean of cp_loss, already capped per move.
fn compute_acpl(moves: &[MoveAnalysis]) -> ByColor<i64> {
    let acpl = |color: &str| {
        let losses: Vec<i64> = moves
            .iter()
            .filter(|m| m.color == color)
            .map(|m| m.cp_loss as i64)
            .collect();
        if losses.is_empty() {
            return 0;
        }
        py_round_int(losses.iter().sum::<i64>() as f64 / losses.len() as f64)
    };
    ByColor {
        white: acpl("white"),
        black: acpl("black"),
    }
}

/// Per-player game accuracy (lila's) on every position of the game, P0 included, from White's side.
fn compute_accuracy(moves: &[MoveAnalysis]) -> ByColor<f64> {
    let Some(first) = moves.first() else {
        return ByColor {
            white: 0.0,
            black: 0.0,
        };
    };
    let white_starts = first.color == "white";
    let p0 = if white_starts {
        first.eval_before_cp
    } else {
        -first.eval_before_cp
    };
    let cps_white: Vec<i32> = std::iter::once(p0)
        .chain(moves.iter().map(|m| m.eval_white_cp))
        .collect();
    game_accuracy_lichess(white_starts, &cps_white)
}
