"""
Downloads and filters the public Lichess puzzle database into a local SQLite.

Source: https://database.lichess.org/lichess_db_puzzle.csv.zst (~250 MB
compressed, ~3 million puzzles). We decompress "on the fly" with zstandard
so the full CSV never lives on disk. Filters:
  - puzzles of 1 or 2 moves for the player (Moves contains 2 or 4 plies total,
    because the first ply is the opponent's "setup" move)
  - rating in [400, 2800]
  - popularity >= 80 and nbPlays >= 100 (puzzles well-validated by the community)

Destination SQLite schema (engine/puzzles.db):
  puzzles(
    id TEXT PRIMARY KEY,
    fen TEXT,            -- position BEFORE the setup move
    moves TEXT,          -- space-separated UCI sequence (setup + solution)
    rating INTEGER,
    themes TEXT          -- space-separated themes
  )
  Secondary index on rating to allow fast SELECTs by difficulty band.

Usage (run from the project root):
  python scripts/download_puzzles.py            # download + filter
  python scripts/download_puzzles.py --keep-csv # also keeps the decompressed CSV

Works from Windows PowerShell. Estimated time: 5-15 min depending on connection.
"""

from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
import time
from pathlib import Path

import requests
import zstandard as zstd

LICHESS_URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"

# This script lives in scripts/, so the project root is one level up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "engine" / "puzzles.db"
CSV_PATH = PROJECT_ROOT / "engine" / "lichess_puzzles.csv"

# Quality filters: we want "good" puzzles (played many times and validated).
MIN_RATING = 400
MAX_RATING = 2800
MIN_POPULARITY = 80
MIN_NB_PLAYS = 100

# Length of Moves in plies. A "1-move" puzzle has 2 plies (setup + 1 solution).
# A "2-move" one has 4. We keep only these two cases.
ALLOWED_PLY_LENGTHS = {2, 4}

# Per-rating-band cap: prevents the DB from growing to hundreds of MiB. With
# 100-point bands between 400 and 2800 (24 bands) and 5000 puzzles per band,
# the final DB is ~120k puzzles, ~20 MiB, with even distribution across
# difficulties. A player on "Expert" still finds plenty of puzzles like one
# on "Easy". Set to None to disable any limit.
BAND_SIZE = 100
MAX_PER_RATING_BAND = 5000


def stream_csv(keep_csv: bool):
    """Generator yielding decompressed CSV rows on the fly."""
    print(f"[1/3] Downloading {LICHESS_URL} ...", flush=True)
    with requests.get(LICHESS_URL, stream=True, timeout=300) as resp:
        resp.raise_for_status()

        dctx = zstd.ZstdDecompressor()
        # Output buffer: write decompressed bytes to a file (if requested) and/or
        # consume them directly.
        out_file = open(CSV_PATH, "wb") if keep_csv else None
        text_buffer = io.StringIO()

        bytes_in = 0
        last_print = time.monotonic()
        with dctx.stream_reader(resp.raw) as reader:
            while True:
                chunk = reader.read(2**20)  # 1 MiB
                if not chunk:
                    break
                bytes_in += len(chunk)
                if out_file:
                    out_file.write(chunk)

                text = chunk.decode("utf-8", errors="replace")
                # Accumulate then split by line; keep the last partial.
                text_buffer.write(text)
                buf = text_buffer.getvalue()
                lines = buf.split("\n")
                text_buffer = io.StringIO()
                text_buffer.write(lines[-1])  # partial
                for line in lines[:-1]:
                    yield line

                now = time.monotonic()
                if now - last_print > 2.0:
                    print(f"      ...decompressed {bytes_in / 2**20:.1f} MiB", flush=True)
                    last_print = now

        # Possibly the trailing line.
        residue = text_buffer.getvalue().strip()
        if residue:
            yield residue
        if out_file:
            out_file.close()


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE puzzles (
            id     TEXT PRIMARY KEY,
            fen    TEXT NOT NULL,
            moves  TEXT NOT NULL,
            rating INTEGER NOT NULL,
            themes TEXT
        )
    """)
    conn.execute("CREATE INDEX idx_puzzles_rating ON puzzles(rating)")
    conn.commit()
    return conn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-csv", action="store_true",
                        help="Also keep the decompressed CSV in engine/")
    args = parser.parse_args()

    conn = init_db()
    cur = conn.cursor()
    print("[2/3] Filtering and inserting into SQLite ...", flush=True)

    inserted = 0
    seen = 0
    last_commit = time.monotonic()

    # Per-rating-band counter to enforce MAX_PER_RATING_BAND.
    band_counter: dict[int, int] = {}

    csv_iter = stream_csv(keep_csv=args.keep_csv)
    # First line = header.
    header_line = next(csv_iter, None)
    if header_line is None:
        print("ERROR: empty stream.", file=sys.stderr)
        sys.exit(1)
    header = next(csv.reader([header_line]))
    idx = {name: i for i, name in enumerate(header)}

    required = ["PuzzleId", "FEN", "Moves", "Rating", "Popularity", "NbPlays", "Themes"]
    for r in required:
        if r not in idx:
            print(f"ERROR: column {r} missing from header. Header={header}", file=sys.stderr)
            sys.exit(1)

    for line in csv_iter:
        if not line.strip():
            continue
        seen += 1
        try:
            row = next(csv.reader([line]))
        except Exception:
            continue
        if len(row) < len(header):
            continue
        try:
            rating = int(row[idx["Rating"]])
            popularity = int(row[idx["Popularity"]])
            nb_plays = int(row[idx["NbPlays"]])
        except ValueError:
            continue

        if not (MIN_RATING <= rating <= MAX_RATING):
            continue
        if popularity < MIN_POPULARITY or nb_plays < MIN_NB_PLAYS:
            continue

        moves = row[idx["Moves"]].strip()
        ply_count = len(moves.split())
        if ply_count not in ALLOWED_PLY_LENGTHS:
            continue

        # Per-rating-band cap: as soon as a band is full, skip subsequent ones.
        if MAX_PER_RATING_BAND is not None:
            band = (rating // BAND_SIZE) * BAND_SIZE
            if band_counter.get(band, 0) >= MAX_PER_RATING_BAND:
                continue
            band_counter[band] = band_counter.get(band, 0) + 1

        cur.execute(
            "INSERT OR IGNORE INTO puzzles (id, fen, moves, rating, themes) VALUES (?, ?, ?, ?, ?)",
            (row[idx["PuzzleId"]], row[idx["FEN"]], moves, rating, row[idx["Themes"]]),
        )
        inserted += 1

        # Periodic commit to avoid buffering too much in memory.
        now = time.monotonic()
        if now - last_commit > 5.0:
            conn.commit()
            print(f"      ...seen {seen}, inserted {inserted}", flush=True)
            last_commit = now

    conn.commit()
    print("[3/3] Compacting database (VACUUM) ...", flush=True)
    conn.execute("VACUUM")
    conn.close()

    size_mb = DB_PATH.stat().st_size / 2**20
    print(f"\nDone. {inserted} puzzles saved in {DB_PATH} ({size_mb:.1f} MiB).")


if __name__ == "__main__":
    main()
