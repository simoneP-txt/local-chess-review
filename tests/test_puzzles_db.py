"""engine/puzzles.db and the scripts that build it.

Only the check of the real file needs engine/puzzles.db, and it opens the file read only.
The tests of the scripts run on a temporary DB and on a fake download.
"""
import sqlite3
from contextlib import closing
from unittest.mock import MagicMock

import chess
import pytest

from scripts import download_puzzles, trim_puzzles


@pytest.mark.skipif(not trim_puzzles.DB_PATH.exists(), reason="engine/puzzles.db is missing")
def test_puzzles_db_has_the_columns_lengths_ratings_and_legal_solutions():
    uri = f"file:{trim_puzzles.DB_PATH.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(puzzles)")]
        assert columns == ["id", "fen", "moves", "rating", "themes"]
        for puzzle_id, fen, moves, rating in conn.execute("SELECT id, fen, moves, rating FROM puzzles"):
            plies = moves.split()
            assert len(plies) in (2, 4), puzzle_id
            assert 400 <= rating <= 2800, puzzle_id
            board = chess.Board(fen)
            for uci in plies:
                move = chess.Move.from_uci(uci)
                assert board.is_legal(move), (puzzle_id, uci)
                board.push(move)


def test_trim_keeps_top_band(tmp_path):
    db = tmp_path / "puzzles.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE puzzles (id TEXT PRIMARY KEY, rating INTEGER)")
        conn.executemany("INSERT INTO puzzles VALUES (?, ?)", [("a", 2799), ("b", 2800)])
        conn.commit()

    trim_puzzles.trim(db, per_band=10)

    with closing(sqlite3.connect(db)) as conn:
        assert sorted(row[0] for row in conn.execute("SELECT rating FROM puzzles")) == [2799, 2800]


# é is c3 a9 in UTF-8.
@pytest.mark.parametrize("chunks, lines", [
    ([b"PuzzleId\nabc\xc3", b"\xa9\n"], ["PuzzleId", "abcé"]),
    ([b"PuzzleId\nabc\xc3", b"\xa9"], ["PuzzleId", "abcé"]),
    ([b"PuzzleId\nabc\xc3"], ["PuzzleId", "abc�"]),
], ids=["split-row", "split-last-row-without-newline", "stream-ends-inside-the-character"])
def test_stream_csv_keeps_split_utf8(monkeypatch, chunks, lines):
    decompressor = MagicMock()
    reader = decompressor.return_value.stream_reader.return_value.__enter__.return_value
    reader.read.side_effect = [*chunks, b""]
    monkeypatch.setattr(download_puzzles.requests, "get", MagicMock())
    monkeypatch.setattr(download_puzzles.zstd, "ZstdDecompressor", decompressor)

    assert list(download_puzzles.stream_csv(keep_csv=False)) == lines
