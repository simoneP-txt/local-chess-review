mod common;

use common::{analyze, scripted, scripted_with};
use review_core::accuracy::pstdev;
use review_core::analysis::{classify, evaluate};
use review_core::engine::{Line, Score};
use review_core::game::{Game, parse_elo, read_game};
use review_core::openings::{Book, epd};
use review_core::py::{py_round, py_round_int};
use review_core::see::static_exchange_evaluation;
use review_core::special::make_comment;
use review_core::stats::{PhaseStats, compute_phases, estimate_performance_elo};
use review_core::{ByColor, analyze_pgn};
use serde_json::{Value, json};
use shakmaty::fen::Fen;
use shakmaty::san::{San, SanPlus};
use shakmaty::uci::UciMove;
use shakmaty::{CastlingMode, Chess, Move, Position};

fn pos(fen: &str) -> Chess {
    fen.parse::<Fen>()
        .unwrap()
        .into_position(CastlingMode::Standard)
        .unwrap()
}

/// A legal move of `board`.
fn mv(board: &Chess, uci: &str) -> Move {
    uci.parse::<UciMove>().unwrap().to_move(board).unwrap()
}

fn see(fen: &str, uci: &str) -> i32 {
    let board = pos(fen);
    static_exchange_evaluation(&board, mv(&board, uci))
}

fn epd_after(sans: &[&str]) -> String {
    let mut board = Chess::default();
    for san in sans {
        let m = san.parse::<San>().unwrap().to_move(&board).unwrap();
        board.play_unchecked(m);
    }
    epd(&board)
}

fn read(pgn: &str) -> Game {
    read_game(pgn, &Book::load()).unwrap_or_else(|e| panic!("{pgn:?}: {e}"))
}

fn read_error(pgn: &str) -> Option<String> {
    read_game(pgn, &Book::load()).err()
}

fn ucis(game: &Game) -> Vec<&str> {
    game.plies.iter().map(|p| p.uci.as_str()).collect()
}

#[test]
fn test_see_tie_break_follows_python_chess() {
    // 5.8.13: after Bxg6 both queens can retake: python-chess tries h6 first, f6 keeps blocking
    // the d6 rook and the SEE is 2; with f6 first the rook joins in and it would be 5
    assert_eq!(see("8/8/3r1QRQ/8/4b3/1k6/8/5K2 b - - 0 1", "e4g6"), 2);
}

#[test]
fn test_see_of_castling_is_zero() {
    // Review Focus 1
    assert_eq!(see("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1"), 0);
    // with the rook square a1 as the target, a2 would promote on the emptied a1 and give -8
    assert_eq!(see("4k3/8/8/8/8/8/p7/R3K3 w Q - 0 1", "e1c1"), 0);
}

/// (classification, (fen, played), pv_after_uci, expected)
type CommentCase = (
    &'static str,
    (&'static str, &'static str),
    &'static [&'static str],
    Option<Value>,
);

#[test]
fn test_make_comment() {
    // 5.8.15
    const SAC: (&str, &str) = ("4k3/8/3p4/8/8/5N2/8/4K3 w - - 0 1", "f3e5");
    const QUEEN: (&str, &str) = ("4k3/8/8/2p5/3Q4/8/8/1N2K3 w - - 0 1", "b1c3");
    let cases: [CommentCase; 12] = [
        ("best", SAC, &[], None),
        (
            "miss",
            SAC,
            &[],
            Some(json!({"key": "miss_default", "params": {}})),
        ),
        (
            "blunder",
            SAC,
            &[],
            Some(json!({"key": "hanging_severe", "params": {"piece": "knight"}})),
        ),
        (
            "mistake",
            ("4k3/8/3p4/8/3P4/5N2/8/4K3 w - - 0 1", "f3e5"),
            &[],
            Some(json!({"key": "exchanges_lose_piece", "params": {"piece": "knight"}})),
        ),
        (
            "inaccuracy",
            ("4k3/8/3p4/8/4P3/8/8/4K3 w - - 0 1", "e4e5"),
            &[],
            Some(json!({"key": "exchanges_lose_minor", "params": {}})),
        ),
        (
            "blunder",
            QUEEN,
            &["c5d4"],
            Some(json!({"key": "punishment_capture", "params": {"piece": "queen"}})),
        ),
        (
            "blunder",
            QUEEN,
            &["a1d4"], // illegal: only the arrival square counts
            Some(json!({"key": "punishment_capture", "params": {"piece": "queen"}})),
        ),
        (
            "mistake",
            QUEEN,
            &["zz"],
            Some(json!({"key": "mistake_default", "params": {}})),
        ),
        (
            "blunder",
            QUEEN,
            &[],
            Some(json!({"key": "blunder_default", "params": {}})),
        ),
        (
            "inaccuracy",
            QUEEN,
            &["e8e7"],
            Some(json!({"key": "inaccuracy_default", "params": {}})),
        ),
        (
            "blunder",
            QUEEN,
            &["d4c5"], // c5 holds a black pawn: only a piece of the mover counts
            Some(json!({"key": "blunder_default", "params": {}})),
        ),
        (
            "blunder",
            ("4k3/8/8/8/8/8/8/R3K3 w - - 0 1", "e1d1"),
            &["0000"], // python-chess null move: its to_square is a1
            Some(json!({"key": "punishment_capture", "params": {"piece": "rook"}})),
        ),
    ];
    for (classification, (fen, uci), pv, expected) in cases {
        let board = pos(fen);
        let played = mv(&board, uci);
        let pv: Vec<String> = pv.iter().map(|s| s.to_string()).collect();
        assert_eq!(
            make_comment(classification, &board, played, see(fen, uci), &pv),
            expected,
            "{classification} {fen} {uci} {pv:?}"
        );
    }
}

#[test]
fn test_py_round_rounds_the_binary_value() {
    // 5.8.1, 5.8.2
    assert_eq!(py_round(0.125, 2), 0.12);
    assert_eq!(py_round(2.675, 2), 2.67);
    assert_eq!(py_round(0.375, 2), 0.38);
    assert_eq!(py_round(62.345, 2), 62.34);
    assert_eq!(
        (py_round_int(2.5), py_round_int(3.5), py_round_int(-2.5)),
        (2, 4, -2)
    );
}

#[test]
fn test_sums_run_left_to_right() {
    // 5.8.3: in Python 3.11 0.1 + 0.2 + 0.3 is 0.6000000000000001
    let p = compute_phases(&[
        ("opening", "white", 0.1),
        ("opening", "white", 0.2),
        ("opening", "white", 0.3),
    ]);
    // a compensated sum gives 0.6, a mean below 0.20 and "mistake"
    assert_eq!(
        p.opening.white,
        PhaseStats {
            label: Some("blunder"),
            mean_drop: Some(0.2),
            moves: 3
        }
    );
}

#[test]
fn test_pstdev_shortcut() {
    // 5.8.4: the expected value is Python 3.11 statistics.pstdev on the same list
    assert_eq!(pstdev(&[50.0, 50.0]), 0.0);
    assert!((pstdev(&[50.0, 40.89741028083871, 50.0]) - 4.291001944518598).abs() < 1e-12);
}

#[test]
fn test_exp_matches_python() {
    // 5.8.5: EXP_CASES from Appendix A, (input bits, Python math.exp bits)
    for (x, e) in EXP_CASES {
        assert_eq!(
            f64::from_bits(x).exp().to_bits(),
            e,
            "exp({})",
            f64::from_bits(x)
        );
    }
}

#[test]
fn test_epd_writes_en_passant_only_when_legal() {
    // 5.8.7
    // after 1. e4 no black pawn can take en passant; after 1. e4 d5 2. e5 f5, exf6 is legal
    assert_eq!(
        epd_after(&["e4"]),
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq -"
    );
    assert_eq!(
        epd_after(&["e4", "d5", "e5", "f5"]),
        "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6"
    );
}

#[test]
fn test_fen_writes_en_passant_only_when_legal() {
    // 5.8.6
    let game = read("1. e4 d5 2. e5 f5 *");
    assert_eq!(
        game.plies[0].fen_after,
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    );
    assert_eq!(
        game.plies[3].fen_after,
        "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3"
    );
}

#[test]
fn test_san_matches_python_chess() {
    // 5.8.8, Review Focus 2
    let cases = [
        ("4k3/8/8/8/8/5N2/8/1N2K3 w - - 0 1", "b1d2", "Nbd2"),
        ("4k3/8/8/R7/8/8/8/R3K3 w - - 0 1", "a1a3", "R1a3"),
        // the black king on g6, not e8: with e8 Qe1 checks it on White's move, which shakmaty rejects
        ("8/8/6k1/8/7Q/8/8/4Q1KQ w - - 0 1", "h1e4", "Qh1e4+"),
        ("3r3k/2P5/8/8/8/8/8/4K3 w - - 0 1", "c7d8n", "cxd8=N"),
        ("7k/2P5/8/8/8/8/8/4K3 w - - 0 1", "c7c8q", "c8=Q+"),
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1", "O-O"),
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1c1", "O-O-O"),
        ("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "a1a8", "Ra8#"),
        ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "e5d6", "exd6"),
    ];
    for (fen, uci, san) in cases {
        let board = pos(fen);
        let m = mv(&board, uci);
        assert_eq!(SanPlus::from_move(board, m).to_string(), san, "{fen} {uci}");
    }
}

#[test]
fn test_castling_uci_is_the_king_move() {
    // Review Focus 1
    let game = read("1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. O-O *");
    let last = game.plies.last().unwrap();
    assert_eq!((last.san.as_str(), last.uci.as_str()), ("O-O", "e1g1"));
}

#[test]
fn test_headers_follow_python_chess() {
    // 5.8.9: the seven roster tags first, then the others in file order; values as in the file
    let game = read(
        r#"[White "A"]
[Foo "x"]
[Event "E"]
[White "B"]
[Black "Gioè"]
[Site "A \"quoted\" \\ site"]

1. e4 *
"#,
    );
    let pairs: Vec<(&str, &str)> = game
        .headers
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_str().unwrap()))
        .collect();
    assert_eq!(
        pairs,
        [
            ("Event", "E"),
            ("Site", r#"A \"quoted\" \\ site"#),
            ("Date", "????.??.??"),
            ("Round", "?"),
            ("White", "B"),
            ("Black", "Gioè"),
            ("Result", "*"),
            ("Foo", "x"),
        ]
    );
}

#[test]
fn test_elo_like_python_int() {
    let cases = [
        (Some("  1500 "), Some(1500)),
        (Some("+1500"), Some(1500)),
        (Some("-5"), Some(-5)),
        (Some("0"), None),
        (Some("?"), None),
        (Some(""), None),
        (Some("1500.0"), None),
        (None, None),
    ];
    for (value, elo) in cases {
        assert_eq!(parse_elo(value), elo, "{value:?}");
    }
    // shortcut: Python int() reads 15_00 as 1500
    assert_eq!(parse_elo(Some("15_00")), None);
}

#[test]
fn test_clock_like_python_chess() {
    // Review Focus 3
    let game = read(
        "1. e4 {[%clk 0:09:57.3]} e5 {[%clk 1:00:00]} 2. Nf3 {[%clk 0:00:05.]} Nc6 3. Bb5 {text} \
         {[%clk 0:01:02.345]} a6 {[%clk 1:2] [%clk 0:00:07.25] [%clk 0:00:09]} 4. Ba4 *",
    );
    let clocks: Vec<Option<f64>> = game.plies.iter().map(|p| p.clock).collect();
    assert_eq!(
        clocks,
        [
            Some(597.3),
            Some(3600.0),
            Some(5.0),
            None,
            Some(62.345),
            Some(7.25),
            None
        ]
    );
}

#[test]
fn test_clock_in_a_comment_across_a_buffer_refill() {
    // pgn-reader 0.29.2 reads 16384 bytes at a time and hands a comment that crosses a refill in
    // chunks: here the refill falls inside the clock, right after "[%cl"
    let x = "x".repeat(16_384 - "1. e4 {[%cl".len());
    let game = read(&format!("1. e4 {{{x}[%clk 0:00:07]}} *"));
    assert_eq!(game.plies[0].clock, Some(7.0));
}

#[test]
fn test_start_fen_cleans_castling_and_move_zero() {
    // 5.8.11
    let game = read("[SetUp \"1\"]\n[FEN \"4k3/8/8/8/8/8/8/4K3 w KQkq - 0 0\"]\n\n*\n");
    assert_eq!(game.start_fen, "4k3/8/8/8/8/8/8/4K3 w - - 0 1");
    let game = read("[FEN \"7k/8/6K1/5Q2/8/8/8/8 b - - 3 40\"]\n\n*\n");
    assert_eq!(game.start_fen, "7k/8/6K1/5Q2/8/8/8/8 b - - 3 40");
}

#[test]
fn test_chess960_and_invalid_positions_are_errors() {
    // 5.9
    for pgn in [
        "[FEN \"4k3/8/8/8/8/8/8/1R2K2R w KQ - 0 1\"]\n\n*\n",
        "[Variant \"Chess960\"]\n\n*\n",
    ] {
        assert_eq!(
            read_error(pgn).as_deref(),
            Some("Chess960 is not supported"),
            "{pgn:?}"
        );
    }
    let error = read_error("[FEN \"8/8/8/8/8/8/8/4K3 w - - 0 1\"]\n\n*\n").unwrap();
    assert!(error.starts_with("invalid FEN"), "{error}");
}

#[test]
fn test_illegal_move_ends_the_game() {
    assert_eq!(ucis(&read("1. e4 e5 2. Ke3 Nc6 *")), ["e2e4", "e7e5"]);
}

#[test]
fn test_reads_the_first_game_and_its_main_line() {
    assert_eq!(
        ucis(&read("1. e4 (1. d4 d5) e5 *\n\n1. d4 *\n")),
        ["e2e4", "e7e5"]
    );
}

#[test]
fn test_empty_pgn() {
    for pgn in ["", "   \n"] {
        assert_eq!(
            read_error(pgn).as_deref(),
            Some("invalid or empty PGN"),
            "{pgn:?}"
        );
    }
    assert!(read("*\n").plies.is_empty());
}

#[test]
fn test_performance_elo_cap_does_not_overflow() {
    // Python ints do not overflow: real_elo 2**63 - 1 leaves the pure curve
    assert_eq!(estimate_performance_elo(50.0, Some(i64::MAX)), 350);
}

#[test]
fn test_elo_argument_wins_over_the_header() {
    // venv, GameAnalyzer.analyze_pgn with ScriptedEngine(lambda board: 0): accuracy 100 and pure
    // Elo 2900, so each cap binds; from the headers alone White would get 2000
    let pgn = "[WhiteElo \"500\"]\n[BlackElo \"900\"]\n\n1. e4 e5 *\n";
    let mut engine = scripted_with(|_| 0);
    let analysis = analyze_pgn(pgn, Some(1200), None, &mut engine, &Book::load(), 15).unwrap();
    let real_elo = ByColor {
        white: Some(1200),
        black: Some(900),
    };
    assert_eq!(analysis.real_elo, real_elo);
    let performance_elo = ByColor {
        white: 2700,
        black: 2400,
    };
    assert_eq!(analysis.performance_elo, performance_elo);
}

const START_FEN: &str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

#[test]
fn test_empty_engine_answer_is_an_error() {
    let mut engine = scripted().with_answer(START_FEN, vec![]);
    let error = analyze_pgn("1. e4 *", None, None, &mut engine, &Book::load(), 15).unwrap_err();
    assert_eq!(error, format!("no lines in the engine answer: {START_FEN}"));
}

#[test]
fn test_classify_checks_the_engine_answers() {
    let after_e4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
    let game = read("1. e4 *");
    let evals = evaluate(&game, &mut scripted(), 15).unwrap();
    let no_elo = ByColor {
        white: None,
        black: None,
    };
    let error = classify(&game, &evals[..1], no_elo).unwrap_err();
    assert_eq!(error, "expected 2 engine answers, got 1");
    for (i, fen) in [(0, START_FEN), (1, after_e4)] {
        let mut evals = evals.clone();
        evals[i].clear();
        let error = classify(&game, &evals, no_elo).unwrap_err();
        assert_eq!(error, format!("no lines in the engine answer: {fen}"));
    }
}

#[test]
fn test_unreadable_best_move_is_an_error() {
    // e2e5 is no legal move in the starting position
    let line = Line {
        score: Score::Cp(10),
        pv: vec!["e2e5".into()],
    };
    let mut engine = scripted().with_answer(START_FEN, vec![line]);
    let error = analyze_pgn("1. e4 *", None, None, &mut engine, &Book::load(), 15).unwrap_err();
    assert_eq!(error, format!("unreadable best move e2e5 in {START_FEN}"));
}

#[test]
fn test_is_best_reads_castling_as_uci() {
    // 5.8.14, Review Focus 1
    let mut engine = scripted().with_lines(&[(
        "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
        &["e1g1", "d2d3"],
    )]);
    let moves = analyze("1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. O-O *", &mut engine).moves;
    assert!(moves[6].is_best);
    assert_eq!(
        (
            moves[6].best_move_uci.as_str(),
            moves[6].best_move_san.as_str()
        ),
        ("e1g1", "O-O")
    );
}

#[test]
fn test_clock_after_is_rounded() {
    // Review Focus 3: Python's round(62.345, 2) is 62.34
    let moves = analyze("1. e4 {[%clk 0:01:02.345]} e5 *", &mut scripted()).moves;
    let clocks: Vec<Option<f64>> = moves.iter().map(|m| m.clock_after).collect();
    assert_eq!(clocks, [Some(62.34), None]);
}

fn keys(value: &Value) -> Vec<&str> {
    value
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect()
}

#[test]
fn test_json_types_match_python() {
    // Review Focus 4: integer and decimal as Python's json.dumps writes them, null for None
    let v = serde_json::to_value(analyze("*\n", &mut scripted())).unwrap();
    assert_eq!(
        keys(&v),
        [
            "headers",
            "moves",
            "accuracy",
            "acpl",
            "performance_elo",
            "wp_series",
            "move_counts",
            "phases",
            "real_elo",
            "time_control",
            "opening",
            "start_fen"
        ]
    );
    assert!(v["accuracy"]["white"].is_f64() && v["accuracy"]["white"] == 0.0);
    assert!(v["acpl"]["white"].is_i64() && v["acpl"]["white"] == 0);
    assert!(v["performance_elo"]["white"].is_i64() && v["performance_elo"]["white"] == 100);
    assert_eq!(v["real_elo"], json!({"white": null, "black": null}));
    assert_eq!(
        v["phases"]["opening"]["white"],
        json!({"label": null, "mean_drop": null, "moves": 0})
    );
    assert!(v["phases"]["opening"]["white"]["moves"].is_i64());
    assert!(v["opening"].is_null());
    assert_eq!(v["time_control"], "");
    assert_eq!(v["move_counts"]["white"], json!({}));
    assert_eq!(
        keys(&v["headers"]),
        ["Event", "Site", "Date", "Round", "White", "Black", "Result"]
    );

    // 1. f3 e5 2. g4 Qh4#
    let v = serde_json::to_value(analyze("1. f3 e5 2. g4 Qh4# 0-1\n", &mut scripted())).unwrap();
    let m = &v["moves"][3];
    assert_eq!(
        keys(m),
        [
            "ply",
            "move_number",
            "color",
            "san",
            "uci",
            "fen_before",
            "fen_after",
            "best_move_san",
            "best_move_uci",
            "eval_before_cp",
            "eval_after_cp",
            "eval_white_cp",
            "wp_before",
            "wp_after",
            "wp_white_after",
            "wp_drop",
            "cp_loss",
            "classification",
            "is_best",
            "phase",
            "see_played",
            "comment",
            "pv_uci",
            "pv_after_uci",
            "clock_after"
        ]
    );
    assert!(m["wp_after"].is_f64() && m["wp_after"] == 1.0);
    assert!(m["wp_drop"].is_f64() && m["wp_drop"] == 0.0);
    assert!(m["eval_after_cp"].is_i64() && m["eval_after_cp"] == 10000);
    assert!(m["comment"].is_null() && m["clock_after"].is_null());
    assert!(v["wp_series"][3]["wp_white"].is_f64());
}

// Python 3.11.9 math.exp on Windows, on the arguments cp_to_wp and lichess_win_pct pass for cp
// -2000..=2000 step 50, then those of move_accuracy_lichess for drops 0..=100 step 1.3.
const EXP_CASES: [(u64, u64); 158] = [
    (0x401d74e65bea0ba2, 0x4098a98e5bf93d94),
    (0x401cb860999dcb57, 0x409483ed589104b3),
    (0x401bfbdad7518b0d, 0x409110cfd4cab615),
    (0x401b3f5515054ac2, 0x408c6462d2d397e1),
    (0x401a82cf52b90a78, 0x40879e33a4fcce6a),
    (0x4019c649906cca2e, 0x4083a587528d0dc3),
    (0x401909c3ce2089e3, 0x408057cf4deae018),
    (0x40184d3e0bd44999, 0x407b30992ebbb6fe),
    (0x401790b84988094e, 0x40769e2b35dd1df0),
    (0x4016d432873bc904, 0x4072d08c3b5163da),
    (0x401617acc4ef88b9, 0x406f4d489ed208a6),
    (0x40155b2702a3486f, 0x406a09d824b7c1a9),
    (0x40149ea140570824, 0x4065a8fa538299c9),
    (0x4013e21b7e0ac7da, 0x40620495faeafede),
    (0x40132595bbbe8790, 0x405df9f3d832cf19),
    (0x4012690ff9724745, 0x4058ef926a0676c7),
    (0x4011ac8a372606fb, 0x4054be2b754f63a3),
    (0x4010f00474d9c6b0, 0x40514142cc281f66),
    (0x4010337eb28d8666, 0x404cb4fd9eef2398),
    (0x400eedf1e0828c37, 0x4047e140af972566),
    (0x400d74e65bea0ba2, 0x4043dd4e0cc82b76),
    (0x400bfbdad7518b0d, 0x404086350dba6c41),
    (0x400a82cf52b90a78, 0x403b7dca2d53bfc6),
    (0x400909c3ce2089e3, 0x4036de61612d3e93),
    (0x400790b84988094e, 0x403305f64f9fcd81),
    (0x400617acc4ef88b9, 0x402fa6262aaa4061),
    (0x40049ea140570824, 0x402a53c456576d55),
    (0x40032595bbbe8790, 0x4025e67867430940),
    (0x4001ac8a372606fb, 0x402237bd040bd683),
    (0x4000337eb28d8666, 0x401e4f0e0963887e),
    (0x3ffd74e65bea0ba2, 0x4019365d3e18e524),
    (0x3ffa82cf52b90a78, 0x4014f90eeb8db185),
    (0x3ff790b84988094e, 0x4011723f4f4929e3),
    (0x3ff49ea140570824, 0x400d067d40cdb7f1),
    (0x3ff1ac8a372606fb, 0x4008250c1563dfa3),
    (0x3fed74e65bea0ba2, 0x400415b32006300b),
    (0x3fe790b84988094e, 0x4000b51e8639117e),
    (0x3fe1ac8a372606fb, 0x3ffbcbd650f9916d),
    (0x3fd790b84988094e, 0x3ff71f4dd81c8323),
    (0x3fc790b84988094e, 0x3ff33bf8085bbabe),
    (0x8000000000000000, 0x3ff0000000000000),
    (0xbfc790b84988094e, 0x3fea9e82655bfce8),
    (0xbfd790b84988094e, 0x3fe624a50e71c027),
    (0xbfe1ac8a372606fb, 0x3fe26b7545b5989e),
    (0xbfe790b84988094e, 0x3fdea519d5208a42),
    (0xbfed74e65bea0ba2, 0x3fd97df10c80e410),
    (0xbff1ac8a372606fb, 0x3fd5349990b880ec),
    (0xbff49ea140570824, 0x3fd1a3c6e4ab4d88),
    (0xbff790b84988094e, 0x3fcd58e44217bd5b),
    (0xbffa82cf52b90a78, 0x3fc86997f2cdd8bc),
    (0xbffd74e65bea0ba2, 0x3fc44eb84dd2d7a9),
    (0xc000337eb28d8666, 0x3fc0e48d2d5b227b),
    (0xc001ac8a372606fb, 0x3fbc1ac007d2e697),
    (0xc0032595bbbe8790, 0x3fb760f2a03354be),
    (0xc0049ea140570824, 0x3fb3729314078f60),
    (0xc00617acc4ef88b9, 0x3fb02d6c758812ed),
    (0xc00790b84988094e, 0x3faaea14a592b822),
    (0xc00909c3ce2089e3, 0x3fa6638238acca38),
    (0xc00a82cf52b90a78, 0x3fa29fc05c2faea1),
    (0xc00bfbdad7518b0d, 0x3f9efc19e95240e2),
    (0xc00d74e65bea0ba2, 0x3f99c6500fd0e24a),
    (0xc00eedf1e0828c37, 0x3f9570cd3f7113d4),
    (0xc010337eb28d8666, 0x3f91d5db17209a34),
    (0xc010f00474d9c6b0, 0x3f8dac3533a9f9af),
    (0xc011ac8a372606fb, 0x3f88aee66a3e2823),
    (0xc012690ff9724745, 0x3f84885f5cb61ed4),
    (0xc0132595bbbe8790, 0x3f8114827d3a97f9),
    (0xc013e21b7e0ac7da, 0x3f7c6a89c6ebbd67),
    (0xc0149ea140570824, 0x3f77a351c4b76064),
    (0xc0155b2702a3486f, 0x3f73a9c925ebe43a),
    (0xc01617acc4ef88b9, 0x3f705b59e0047951),
    (0xc016d432873bc904, 0x3f6b367d71645ee3),
    (0xc01790b84988094e, 0x3f66a311db113f4c),
    (0xc0184d3e0bd44999, 0x3f62d49fe853f588),
    (0xc01909c3ce2089e3, 0x3f5f5410fb7c6021),
    (0xc019c649906cca2e, 0x3f5a0f7c88ef261e),
    (0xc01a82cf52b90a78, 0x3f55adabd79c2324),
    (0xc01b3f5515054ac2, 0x3f52087d75dc02fd),
    (0xc01bfbdad7518b0d, 0x3f4e0072adaa045c),
    (0xc01cb860999dcb57, 0x3f48f4f9a42d24dd),
    (0xc01d74e65bea0ba2, 0x3f44c2aa184269a0),
    (0x8000000000000000, 0x3ff0000000000000),
    (0xbfacfba527a9785a, 0x3fee3d2737dee0bc),
    (0xbfbcfba527a9785a, 0x3fec931e6676f7ab),
    (0xbfc5bcbbddbf1a43, 0x3feb0087f733c96b),
    (0xbfccfba527a9785a, 0x3fe9841992ba77d6),
    (0xbfd21d4738c9eb38, 0x3fe81c9b0fda7239),
    (0xbfd5bcbbddbf1a43, 0x3fe6c8e57369191f),
    (0xbfd95c3082b4494e, 0x3fe587e1fe3628a8),
    (0xbfdcfba527a9785a, 0x3fe4588948524d7f),
    (0xbfe04d8ce64f53b2, 0x3fe339e268ec36c0),
    (0xbfe21d4738c9eb38, 0x3fe22b022a12cb39),
    (0xbfe3ed018b4482be, 0x3fe12b0a47b4eb4f),
    (0xbfe5bcbbddbf1a43, 0x3fe03928b94061b5),
    (0xbfe78c763039b1c8, 0x3fdea92e0a94b926),
    (0xbfe95c3082b4494e, 0x3fdcf9333d680a20),
    (0xbfeb2bead52ee0d4, 0x3fdb60fe958b2708),
    (0xbfecfba527a9785a, 0x3fd9df411f8332db),
    (0xbfeecb5f7a240fe0, 0x3fd872be56f25aa3),
    (0xbff04d8ce64f53b2, 0x3fd71a4b22e06bcf),
    (0xbff1356a0f8c9f75, 0x3fd5d4cce04e8d2e),
    (0xbff21d4738c9eb38, 0x3fd4a1387a4cb939),
    (0xbff30524620736fb, 0x3fd37e918ed2ad60),
    (0xbff3ed018b4482be, 0x3fd26be99fa87b5d),
    (0xbff4d4deb481ce80, 0x3fd1685f4eb4cef8),
    (0xbff5bcbbddbf1a43, 0x3fd0731da51054c4),
    (0xbff6a49906fc6606, 0x3fcf16b6c88d09bb),
    (0xbff78c763039b1c8, 0x3fcd60b4c268dc7a),
    (0xbff874535976fd8c, 0x3fcbc2cdcffc076b),
    (0xbff95c3082b4494e, 0x3fca3bae5133c107),
    (0xbffa440dabf19512, 0x3fc8ca1556f50c75),
    (0xbffb2bead52ee0d4, 0x3fc76cd39bc57d83),
    (0xbffc13c7fe6c2c97, 0x3fc622ca8af22f2e),
    (0xbffcfba527a9785a, 0x3fc4eaeb5568bc2e),
    (0xbffde38250e6c41c, 0x3fc3c4361381454e),
    (0xbffecb5f7a240fe0, 0x3fc2adb8f3033024),
    (0xbfffb33ca3615ba2, 0x3fc1a68f70b851a5),
    (0xc0004d8ce64f53b2, 0x3fc0ade19cebb34b),
    (0xc000c17b7aedf994, 0x3fbf85c6d4742fb0),
    (0xc001356a0f8c9f75, 0x3fbdc9a80c45b852),
    (0xc001a958a42b4557, 0x3fbc25fa759fa1ad),
    (0xc0021d4738c9eb38, 0x3fba9965b32472de),
    (0xc0029135cd689119, 0x3fb922a45b34854c),
    (0xc0030524620736fb, 0x3fb7c082ecea0393),
    (0xc0037912f6a5dcdc, 0x3fb671ded3c6e192),
    (0xc003ed018b4482be, 0x3fb535a57945c347),
    (0xc00460f01fe3289f, 0x3fb40ad3638a2d66),
    (0xc004d4deb481ce80, 0x3fb2f07360771e77),
    (0xc00548cd49207462, 0x3fb1e59dbc7d5ca2),
    (0xc005bcbbddbf1a43, 0x3fb0e977847c614d),
    (0xc00630aa725dc025, 0x3faff663a433c34e),
    (0xc006a49906fc6606, 0x3fae341243f912f5),
    (0xc00718879b9b0be7, 0x3fac8a8966bd342d),
    (0xc0078c763039b1c8, 0x3faaf86be0e99481),
    (0xc0080064c4d857ab, 0x3fa97c6fbe5a04d4),
    (0xc00874535976fd8c, 0x3fa8155d339ed273),
    (0xc008e841ee15a36d, 0x3fa6c20d9e255882),
    (0xc0095c3082b4494e, 0x3fa5816a92751c64),
    (0xc009d01f1752ef30, 0x3fa4526cf7bb1461),
    (0xc00a440dabf19512, 0x3fa3341c2fe7a37b),
    (0xc00ab7fc40903af3, 0x3fa2258d4bae260a),
    (0xc00b2bead52ee0d4, 0x3fa125e249beaaaf),
    (0xc00b9fd969cd86b5, 0x3fa03449609ba989),
    (0xc00c13c7fe6c2c97, 0x3f9e9ff8a4ec8022),
    (0xc00c87b6930ad279, 0x3f9cf07f9507642f),
    (0xc00cfba527a9785a, 0x3f9b58c5868d6ca0),
    (0xc00d6f93bc481e3b, 0x3f99d77bea9cdad3),
    (0xc00de38250e6c41c, 0x3f986b669be7a77b),
    (0xc00e5770e58569fe, 0x3f97135adb4a198f),
    (0xc00ecb5f7a240fe0, 0x3f95ce3e5aa834f6),
    (0xc00f3f4e0ec2b5c1, 0x3f949b065548dbea),
    (0xc00fb33ca3615ba2, 0x3f9378b6b4f09016),
    (0xc01013959c0000c2, 0x3f92666143083665),
    (0xc0104d8ce64f53b2, 0x3f916324e5262302),
    (0xc0108784309ea6a4, 0x3f906e2ce4590a1c),
    (0xc010c17b7aedf994, 0x3f8f0d607d3a8cd2),
    (0xc010fb72c53d4c85, 0x3f8d57e203da78f8),
    (0xc011356a0f8c9f75, 0x3f8bba7760cb0ad1),
];
