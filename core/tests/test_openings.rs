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
