//! Opening theory: the Lichess openings database in data/chess-openings/, compiled in.
use std::collections::{HashMap, HashSet};

use shakmaty::fen::Epd;
use shakmaty::san::SanPlus;
use shakmaty::{Chess, EnPassantMode, Position};

pub const TSV: [&str; 5] = [
    include_str!("../../data/chess-openings/a.tsv"),
    include_str!("../../data/chess-openings/b.tsv"),
    include_str!("../../data/chess-openings/c.tsv"),
    include_str!("../../data/chess-openings/d.tsv"),
    include_str!("../../data/chess-openings/e.tsv"),
];

pub struct Book {
    pub positions: HashSet<String>,
    pub named: HashMap<String, (String, String)>,
}

impl Book {
    /// The EPD of every position along the rows, and the EPD of each row's last position -> (eco, name).
    pub fn load() -> Book {
        let mut book = Book {
            positions: HashSet::new(),
            named: HashMap::new(),
        };
        for volume in TSV {
            for row in volume.lines().skip(1) {
                // the rows are fixed at compile time: a malformed one is a bug in the data
                let [eco, name, pgn] = row.split('\t').collect::<Vec<_>>()[..] else {
                    panic!("not an eco, name, pgn row: {row}");
                };
                let mut board = Chess::default();
                book.positions.insert(epd(&board));
                for token in pgn.split_whitespace() {
                    if !token.as_bytes()[0].is_ascii_digit() {
                        // "1." is a move number, "O-O" a move
                        let san: SanPlus = token
                            .parse()
                            .unwrap_or_else(|e| panic!("{token} in {row}: {e}"));
                        let m = san
                            .san
                            .to_move(&board)
                            .unwrap_or_else(|e| panic!("{token} in {row}: {e}"));
                        board.play_unchecked(m);
                        book.positions.insert(epd(&board));
                    }
                }
                book.named
                    .insert(epd(&board), (eco.to_string(), name.to_string()));
            }
        }
        book
    }
}

/// python-chess `board.epd()`: the en passant square only when a capture there is legal.
pub fn epd(pos: &Chess) -> String {
    Epd::from_position(pos, EnPassantMode::Legal).to_string()
}
