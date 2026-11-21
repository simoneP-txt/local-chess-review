//! Step 1 of the analysis: read the PGN as python-chess 1.11.2 reads it and prepare every ply.
use std::mem;
use std::ops::ControlFlow;

use pgn_reader::{RawComment, RawTag, Reader, SanPlus, Visitor};
use serde_json::{Map, Value};
use shakmaty::fen::Fen;
use shakmaty::{CastlingMode, Chess, EnPassantMode, Move, Position, PositionError};

use crate::openings::{Book, epd};
use crate::scoring::detect_phase;

pub struct Ply {
    pub before: Chess,
    pub played: Move,
    pub san: String,
    pub uci: String,
    pub fen_before: String,
    pub fen_after: String,
    pub move_number: u32,
    pub color: &'static str,
    pub phase: &'static str,
    /// Seconds left after the move, unrounded, as python-chess node.clock().
    pub clock: Option<f64>,
    /// Every position up to this move is in the book.
    pub in_book: bool,
}

pub struct Game {
    /// python-chess dict(game.headers): the seven roster tags first, then the others in file order.
    pub headers: Map<String, Value>,
    pub white_elo: Option<i64>,
    pub black_elo: Option<i64>,
    pub start: Chess,
    pub start_fen: String,
    pub plies: Vec<Ply>,
    /// (eco, name) of the last named position reached while every position is in the book.
    pub opening: Option<(String, String)>,
}

/// The first game of the PGN and its main line, as python-chess read_game and game.mainline().
pub fn read_game(pgn: &str, book: &Book) -> Result<Game, String> {
    Reader::new(pgn.as_bytes())
        .read_game(&mut GameReader { book })
        // shortcut: an unterminated tag or comment is an error, while python-chess reads on to the
        // end of the PGN; upgrade if a source writes such PGNs.
        .map_err(|e| e.to_string())?
        .unwrap_or_else(|| Err("invalid or empty PGN".to_string()))
}

/// python-chess board.fen(): the en passant square only when a capture there is legal.
pub fn fen(pos: &Chess) -> String {
    Fen::from_position(pos, EnPassantMode::Legal).to_string()
}

/// python-chess 1.11.2 node.clock(): the first `[%clk h:m:s]` its CLOCK_REGEX matches,
/// `\[%clk\s(\d+):(\d+):(\d+(?:\.\d*)?)\]`; a malformed one lets the search go on.
pub fn parse_clock(comment: &str) -> Option<f64> {
    comment
        .match_indices("[%clk")
        .find_map(|(i, tag)| clock_at(&comment[i + tag.len()..]))
}

// shortcut: ASCII digits and u64 arithmetic, while Python's \d also takes other Unicode digits, its
// \s some other separators, and its ints never overflow; upgrade if a source writes non-ASCII clocks.
fn clock_at(rest: &str) -> Option<f64> {
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    let (h, rest) = rest.strip_prefix(char::is_whitespace)?.split_once(':')?;
    let (m, rest) = rest.split_once(':')?;
    // the seconds hold only digits and a dot, so they end at the first ]
    let (s, _) = rest.split_once(']')?;
    let (whole, fraction) = s.split_once('.').unwrap_or((s, ""));
    if !(digits(h) && digits(m) && digits(whole) && fraction.bytes().all(|b| b.is_ascii_digit())) {
        return None;
    }
    let h_m = h
        .parse::<u64>()
        .ok()?
        .checked_mul(3600)?
        .checked_add(m.parse::<u64>().ok()?.checked_mul(60)?)?;
    Some(h_m as f64 + s.parse::<f64>().ok()?)
}

/// python-chess `int(headers.get(tag, 0)) or None`: spaces around and a sign are fine, 0 or no
/// number gives None.
pub fn parse_elo(value: Option<&str>) -> Option<i64> {
    // shortcut: Python int() also reads an underscore between digits (15_00 is 1500), other
    // Unicode digits and values beyond i64; upgrade if a source writes one of them.
    value?.trim().parse::<i64>().ok().filter(|&elo| elo != 0)
}

/// python-chess Headers.board(): the FEN tag, with or without SetUp, else the starting position.
fn start_position(headers: &Map<String, Value>) -> Result<Chess, String> {
    let tag = |name: &str| headers.get(name).and_then(Value::as_str);
    let chess960 = || Err("Chess960 is not supported".to_string());
    // shortcut: Chess960, named by Variant or by a castling rook off the corner, is an error while
    // python-chess plays it, and any other Variant reads as standard chess; upgrade if a game source
    // sends Chess960 or other variant games (chess_api.py drops them today).
    let variant = tag("Variant").unwrap_or("").to_lowercase();
    if [
        "chess960",
        "chess 960",
        "fischerandom",
        "fischerrandom",
        "fischer random",
    ]
    .contains(&variant.as_str())
    {
        return chess960();
    }
    let Some(fen) = tag("FEN") else {
        return Ok(Chess::default());
    };
    let invalid = || format!("invalid FEN: {fen}");
    let setup: Fen = fen.parse().map_err(|_| invalid())?;
    if CastlingMode::detect(setup.as_setup()) == CastlingMode::Chess960 {
        return chess960();
    }
    // Like python-chess, castling rights without their rook drop; the parser makes move 0 move 1.
    // shortcut: other positions shakmaty rejects and python-chess accepts are errors: no king, pawns
    // on the back rank, the side not to move in check, an inconsistent en passant square
    // (4k3/8/8/8/8/8/8/4K3 w - e3 0 1, whose square python-chess drops); upgrade if a game source
    // sends set-up positions that fail here.
    setup
        .into_position(CastlingMode::Standard)
        .or_else(PositionError::ignore_invalid_castling_rights)
        .map_err(|_| invalid())
}

struct GameReader<'a> {
    book: &'a Book,
}

struct Movetext {
    game: Game,
    pos: Chess,
    in_book: bool,
    /// An illegal move ended the main line.
    ended: bool,
    /// The comments after the last move, joined as python-chess joins them.
    comment: String,
    /// The chunks of a comment that crosses a refill of pgn-reader's buffer (in a PGN over 16 KB).
    partial: Vec<u8>,
}

impl Visitor for GameReader<'_> {
    type Tags = Map<String, Value>;
    type Movetext = Movetext;
    type Output = Result<Game, String>;

    fn begin_tags(&mut self) -> ControlFlow<Self::Output, Self::Tags> {
        let roster = [
            ("Event", "?"),
            ("Site", "?"),
            ("Date", "????.??.??"),
            ("Round", "?"),
            ("White", "?"),
            ("Black", "?"),
            ("Result", "*"),
        ];
        let tags = roster.into_iter().map(|(k, v)| (k.into(), v.into()));
        ControlFlow::Continue(tags.collect())
    }

    fn tag(
        &mut self,
        tags: &mut Self::Tags,
        name: &[u8],
        value: RawTag<'_>,
    ) -> ControlFlow<Self::Output> {
        // the raw value, escapes kept, as python-chess; a repeated tag keeps its place
        tags.insert(
            String::from_utf8_lossy(name).into(),
            String::from_utf8_lossy(value.as_bytes()).into(),
        );
        ControlFlow::Continue(())
    }

    fn begin_movetext(&mut self, headers: Self::Tags) -> ControlFlow<Self::Output, Self::Movetext> {
        let start = match start_position(&headers) {
            Ok(start) => start,
            Err(e) => return ControlFlow::Break(Err(e)),
        };
        let elo = |tag| parse_elo(headers.get(tag).and_then(Value::as_str));
        let (white_elo, black_elo) = (elo("WhiteElo"), elo("BlackElo"));
        ControlFlow::Continue(Movetext {
            in_book: self.book.positions.contains(&epd(&start)),
            pos: start.clone(),
            game: Game {
                headers,
                white_elo,
                black_elo,
                start_fen: fen(&start),
                start,
                plies: Vec::new(),
                opening: None,
            },
            ended: false,
            comment: String::new(),
            partial: Vec::new(),
        })
    }

    fn san(&mut self, m: &mut Self::Movetext, san_plus: SanPlus) -> ControlFlow<Self::Output> {
        if m.ended {
            return ControlFlow::Continue(());
        }
        // Like python-chess 1.11.2, the first illegal move ends the main line before itself.
        // shortcut: pgn-reader reads the movetext, not python-chess. -- and Z0 end the main line
        // here, while 0000 and @@@@ are dropped and the next move is read for the wrong side, so a
        // different game is analysed without error. Long notation (e2e4, e7-e5, Ng1f3) may read
        // differently. A line of spaces between moves ends a python-chess game and not this one,
        // and after two or more blank lines below the tags python-chess reads no moves. The result
        // after the moves does not replace a Result tag of *, and malformed tag lines read another
        // way. Upgrade if a game source writes null moves, long notation or such layouts.
        let Ok(played) = san_plus.san.to_move(&m.pos) else {
            m.ended = true;
            return ControlFlow::Continue(());
        };
        let before = m.pos.clone();
        m.pos.play_unchecked(played);
        let after = epd(&m.pos);
        // Once out of theory, stays out: a transposition back is no Book (analyzer.py:700).
        m.in_book = m.in_book && self.book.positions.contains(&after);
        if m.in_book
            && let Some(named) = self.book.named.get(&after)
        {
            m.game.opening = Some(named.clone());
        }
        m.game.plies.push(Ply {
            san: SanPlus::from_move(before.clone(), played).to_string(),
            uci: played.to_uci(CastlingMode::Standard).to_string(),
            fen_before: fen(&before),
            fen_after: fen(&m.pos),
            move_number: before.fullmoves().get(),
            color: before.turn().fold_wb("white", "black"),
            phase: detect_phase(&before, m.game.plies.len() as u32 + 1),
            clock: None,
            in_book: m.in_book,
            before,
            played,
        });
        m.comment.clear();
        ControlFlow::Continue(())
    }

    fn partial_comment(
        &mut self,
        m: &mut Self::Movetext,
        comment: RawComment<'_>,
    ) -> ControlFlow<Self::Output> {
        m.partial.extend_from_slice(comment.as_bytes());
        ControlFlow::Continue(())
    }

    fn comment(
        &mut self,
        m: &mut Self::Movetext,
        comment: RawComment<'_>,
    ) -> ControlFlow<Self::Output> {
        m.partial.extend_from_slice(comment.as_bytes());
        let raw = mem::take(&mut m.partial);
        // a comment before the first move belongs to the game; after an illegal move, to nothing
        if m.ended {
            return ControlFlow::Continue(());
        }
        // shortcut: after a variation that plays no move, or whose first move is illegal, the
        // comment goes to the last main-line ply and the game goes on; python-chess makes it the
        // next move's starting comment, and after an illegal first move in a variation it ends
        // the main line. Upgrade if a game source writes variations.
        let Some(ply) = m.game.plies.last_mut() else {
            return ControlFlow::Continue(());
        };
        // python-chess drops one space after { and one before }, then joins a move's comments
        // with a space, skipping the empty ones
        let text = String::from_utf8_lossy(&raw);
        let text = text.strip_prefix(' ').unwrap_or(&text);
        let text = text.strip_suffix(' ').unwrap_or(text);
        if !text.is_empty() {
            if !m.comment.is_empty() {
                m.comment.push(' ');
            }
            m.comment.push_str(text);
        }
        ply.clock = parse_clock(&m.comment);
        ControlFlow::Continue(())
    }

    fn end_game(&mut self, m: Self::Movetext) -> Self::Output {
        Ok(m.game)
    }
}
