"""Opening theory: the Lichess openings database in data/chess-openings/."""
import functools
import sys
from pathlib import Path

import chess

DATA_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "data" / "chess-openings"


@functools.lru_cache
def book() -> tuple[frozenset[str], dict[str, tuple[str, str]]]:
    """The EPD of every position along the rows, and the EPD of each row's last position -> (eco, name)."""
    positions, named = set(), {}
    for volume in "abcde":  # a missing file raises: without its rows the Book would be wrong
        rows = (DATA_DIR / f"{volume}.tsv").read_text(encoding="utf-8").splitlines()
        for row in rows[1:]:
            eco, name, pgn = row.split("\t")
            board = chess.Board()
            positions.add(board.epd())
            for token in pgn.split():
                if not token[0].isdigit():  # "1." is a move number, "O-O" a move
                    board.push_san(token)
                    positions.add(board.epd())
            named[board.epd()] = (eco, name)
    return frozenset(positions), named
