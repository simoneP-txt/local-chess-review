mod common;

use common::{analyze, scripted, scripted_with};
use review_core::analysis::Analysis;
use review_core::engine::{Line, Score};
use review_core::openings::{Book, TSV, epd};
use shakmaty::san::San;
use shakmaty::{Chess, Position};

fn epd_after(sans: &[&str]) -> String {
    let mut board = Chess::default();
    for san in sans {
        let m = san.parse::<San>().unwrap().to_move(&board).unwrap();
        board.play_unchecked(m);
    }
    epd(&board)
}

/// The TSV row with this exact pgn, from the files: the expected values skip Book::load().
fn row(pgn: &str) -> (String, String) {
    for tsv in TSV {
        for line in tsv.lines() {
            let [eco, name, moves] = line.split('\t').collect::<Vec<_>>()[..] else {
                panic!("not an eco, name, pgn row: {line}");
            };
            if moves == pgn {
                return (eco.to_string(), name.to_string());
            }
        }
    }
    panic!("no TSV row has the pgn {pgn}")
}

#[test]
fn test_book_holds_every_position_along_the_rows() {
    let book = Book::load();
    assert!(book.positions.contains(&epd_after(&[]))); // the starting position lies along every row
    assert!(book.positions.contains(&epd_after(&["e4"])));
    let inside = epd_after(&["f3", "e5", "g4"]); // along the row 1. f3 e5 2. g4 Qh4#, and no row ends here
    assert!(book.positions.contains(&inside) && !book.named.contains_key(&inside));
    assert!(
        !book
            .positions
            .contains(&epd_after(&["h4", "h5", "a4", "a5", "Rh3"]))
    );
}

#[test]
fn test_book_names_the_last_position_of_a_row() {
    let row = row("1. e4 e5 2. Nf3 Nc6 3. Bb5");
    assert_eq!(
        Book::load().named[&epd_after(&["e4", "e5", "Nf3", "Nc6", "Bb5"])],
        row
    );
}

const AFTER_E4: &str = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";

fn classes(result: &Analysis) -> Vec<&'static str> {
    result.moves.iter().map(|m| m.classification).collect()
}

fn opening(result: &Analysis) -> Option<(String, String)> {
    let o = result.opening.as_ref()?;
    Some((o.eco.clone(), o.name.clone()))
}

fn from_fen(fen: &str, movetext: &str) -> String {
    format!("[SetUp \"1\"]\n[FEN \"{fen}\"]\n\n{movetext} *\n")
}

#[test]
fn test_analyze_book_ends_at_the_first_move_out_of_theory() {
    let result = analyze("1. e4 e5 2. Nf3 Nc6 3. Bb5 h5 4. Nc3 *\n", &mut scripted());
    assert_eq!(
        classes(&result),
        [
            "book",
            "book",
            "book",
            "book",
            "book",
            "excellent",
            "excellent"
        ]
    );
    assert_eq!(opening(&result), Some(row("1. e4 e5 2. Nf3 Nc6 3. Bb5")));
}

#[test]
fn test_analyze_transposition_back_into_theory_is_not_book() {
    // 2. Ng1 leaves theory; 2... Nb8 returns to the start and 3. e4 reaches a named position
    assert!(Book::load().positions.contains(&epd_after(&["e4"])));
    let result = analyze("1. Nf3 Nc6 2. Ng1 Nb8 3. e4 *\n", &mut scripted());
    assert_eq!(
        classes(&result),
        ["book", "book", "excellent", "excellent", "excellent"]
    );
    assert_eq!(opening(&result), Some(row("1. Nf3 Nc6")));
}

#[test]
fn test_analyze_from_a_fen_out_of_theory_has_no_book() {
    let cases = [
        (
            "no-theory-in-reach",
            "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1",
            "1. e4 Kd7 2. e5",
        ),
        // the position after 1. Nf3 Nc6 2. Ng1: its moves reach theory, from outside it
        (
            "reaches-theory",
            "r1bqkbnr/pppppppp/2n5/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1",
            "1... Nb8 2. e4",
        ),
    ];
    for (case, fen, movetext) in cases {
        let result = analyze(&from_fen(fen, movetext), &mut scripted());
        assert!(!classes(&result).contains(&"book"), "{case}");
        assert!(result.opening.is_none(), "{case}");
    }
}

#[test]
fn test_analyze_from_a_theory_fen_with_black_to_move() {
    let result = analyze(&from_fen(AFTER_E4, "1... e5 2. Nf3 Nc6"), &mut scripted());
    let pairs: Vec<(&str, &str)> = result
        .moves
        .iter()
        .map(|m| (m.color, m.classification))
        .collect();
    assert_eq!(
        pairs,
        [("black", "book"), ("white", "book"), ("black", "book")]
    );
    assert_eq!(opening(&result), Some(row("1. e4 e5 2. Nf3 Nc6")));
}

#[test]
fn test_analyze_named_first_position_is_not_the_opening() {
    // the FEN is the row 1. e4, but no Book move reaches it: 1... b5 leaves theory at once
    let result = analyze(&from_fen(AFTER_E4, "1... b5"), &mut scripted());
    assert_eq!(classes(&result), ["excellent"]);
    assert!(result.opening.is_none());
}

#[test]
fn test_analyze_book_has_no_ply_limit() {
    let line = "1. e4 c5 2. Nf3 Nc6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 d6 6. Bg5 e6 7. Qd2 Be7 8. O-O-O O-O 9. f4 e5";
    let result = analyze(&format!("{line} *\n"), &mut scripted());
    assert_eq!(classes(&result), ["book"; 18]);
    assert_eq!(opening(&result), Some(row(line)));
}

#[test]
fn test_analyze_theory_move_is_book_up_to_an_inaccuracy() {
    let cases = [
        (-55, "book"),    // drop 0.0994, an inaccuracy
        (-54, "mistake"), // drop 0.1004
        (54, "mistake"),  // drop 0.1994
        (55, "blunder"),  // drop 0.2004
    ];
    for (black_cp, expected) in cases {
        // side to move, P0-P1: 1. e4 falls from 0.65, beyond any cp range for Book
        let mut scores = [168, black_cp].into_iter();
        let result = analyze("1. e4 *\n", &mut scripted_with(|_| scores.next().unwrap()));
        assert_eq!(classes(&result), [expected], "black cp {black_cp}");
        // the opening stays on the positions, whatever the class
        assert_eq!(opening(&result), Some(row("1. e4")), "black cp {black_cp}");
    }
}

#[test]
fn test_analyze_line_one_theory_move_is_book_whatever_the_drop() {
    // Nh3 is python-chess's first legal move, so ScriptedEngine's line 1: the lines come from python-chess
    let mut scores = [168, 55].into_iter(); // side to move, P0-P1: the drop of 0.2004 of the blunder row above
    let mut engine = scripted_with(|_| scores.next().unwrap()).with_lines(&[
        (
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            &["g1h3", "g1f3"],
        ),
        (
            "rnbqkbnr/pppppppp/8/8/8/7N/PPPPPPPP/RNBQKB1R b KQkq - 1 1",
            &["g8h6", "g8f6"],
        ),
    ]);
    let result = analyze("1. Nh3 *\n", &mut engine);
    assert_eq!(classes(&result), ["book"]); // a line-1 move has the base class best, whatever the drop
}

#[test]
fn test_analyze_theory_goes_on_after_a_theory_blunder() {
    // side to move, P0-P7: 3. Nxe5 drops 0.25; the inaccuracy 3... Nxe5 stays Book, above Miss; 4. d4 reaches a name
    let mut scores = [0, 0, 0, 0, 0, 300, -205, 205].into_iter();
    let result = analyze(
        "1. e4 e5 2. Nf3 Nc6 3. Nxe5 Nxe5 4. d4 *\n",
        &mut scripted_with(|_| scores.next().unwrap()),
    );
    assert_eq!(
        classes(&result),
        ["book", "book", "book", "book", "blunder", "book", "book"]
    );
    assert_eq!(
        opening(&result),
        Some(row("1. e4 e5 2. Nf3 Nc6 3. Nxe5 Nxe5 4. d4"))
    );
}

#[test]
fn test_analyze_miss_after_a_theory_blunder() {
    // out-of-theory, theory: 3... Nxe5 lies along a row
    for reply in ["a6", "Nxe5"] {
        // side to move, P0-P6: 3. Nxe5 drops 0.25, the reply gives it all back
        let mut scores = [0, 0, 0, 0, 0, 300, 0].into_iter();
        // a7a6 is shakmaty's first legal move, not python-chess's: the lines come from python-chess
        let mut engine = scripted_with(|_| scores.next().unwrap()).with_lines(&[
            (
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                &["g1h3", "g1f3"],
            ),
            (
                "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                &["g8h6", "g8f6"],
            ),
            (
                "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                &["g1h3", "g1f3"],
            ),
            (
                "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
                &["g8e7", "g8h6"],
            ),
            (
                "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
                &["f3g5", "f3e5"],
            ),
            (
                "r1bqkbnr/pppp1ppp/2n5/4N3/4P3/8/PPPP1PPP/RNBQKB1R b KQkq - 0 3",
                &["g8e7", "g8h6"],
            ),
            (
                "r1bqkbnr/1ppp1ppp/p1n5/4N3/4P3/8/PPPP1PPP/RNBQKB1R w KQkq - 0 4",
                &["e5f7", "e5d7"],
            ),
            (
                "r1bqkbnr/pppp1ppp/8/4n3/4P3/8/PPPP1PPP/RNBQKB1R w KQkq - 0 4",
                &["h1g1", "f1a6"],
            ),
        ]);
        let pgn = format!("1. e4 e5 2. Nf3 Nc6 3. Nxe5 {reply} *\n");
        let result = analyze(&pgn, &mut engine);
        assert_eq!(
            classes(&result),
            ["book", "book", "book", "book", "blunder", "miss"],
            "{reply}"
        );
        assert_eq!(
            opening(&result),
            Some(row("1. e4 e5 2. Nf3 Nc6 3. Nxe5")),
            "{reply}"
        );
    }
}

#[test]
fn test_analyze_great_in_theory_keeps_the_book_going() {
    let cases: [(&str, &[&str]); 2] = [
        // the opening follows the position, whatever the class of its move
        ("1. e4 e5", &["book", "great"]),
        // a Great does not end the sequence
        ("1. e4 e5 2. Nf3", &["book", "great", "book"]),
    ];
    for (line, expected) in cases {
        // Python stubs is_great_move; here line 2 after 1. e4 makes 1... e5 Great by rule (b),
        // 0.5092 - 0.2489 >= 0.20, and not Brilliant: wp_after 0.4908 < 0.55
        let mut engine = scripted().with_answer(
            AFTER_E4,
            vec![
                Line {
                    score: Score::Cp(10),
                    pv: vec!["e7e5".into()],
                },
                Line {
                    score: Score::Cp(-300),
                    pv: vec!["d7d5".into()],
                },
            ],
        );
        let result = analyze(&format!("{line} *\n"), &mut engine);
        assert_eq!(classes(&result), expected, "{line}");
        assert_eq!(opening(&result), Some(row(line)), "{line}");
    }
}
