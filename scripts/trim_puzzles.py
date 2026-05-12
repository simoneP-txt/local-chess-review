"""
Reduces the local puzzle DB (engine/puzzles.db) to a uniformly distributed
subset by rating band.

Logic: for each 100-point rating band (400-499, 500-599, ..., 2700-2800)
we keep at most MAX_PER_RATING_BAND puzzles, randomly chosen among the
existing ones. This guarantees variety across all difficulties without
having to re-download the Lichess CSV.

Usage:
  python scripts/trim_puzzles.py                       # default: 5000 per band
  python scripts/trim_puzzles.py --per-band 3000       # more aggressive (~14 MiB)
  python scripts/trim_puzzles.py --per-band 10000      # richer (~40 MiB)

The script works "in place" on the existing DB. If you want to keep the
original, make a copy before running.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

# This script lives in scripts/, so the project root is one level up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "engine" / "puzzles.db"

MIN_RATING_BAND = 400
MAX_RATING_BAND = 2800
BAND_SIZE = 100
DEFAULT_PER_BAND = 5000


def trim(db_path: Path, per_band: int) -> None:
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}. Run scripts/download_puzzles.py first.")

    size_before = db_path.stat().st_size / 2**20
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    total_before = cur.execute("SELECT COUNT(*) FROM puzzles").fetchone()[0]
    print(f"Before: {total_before} puzzles, {size_before:.1f} MiB")

    # For each band: identify the IDs to KEEP, delete the others.
    # Approach: a single final DELETE built from a temporary "keep_ids" table.
    # To avoid huge queries, we work band by band into the temp table.
    cur.execute("CREATE TEMPORARY TABLE keep_ids (id TEXT PRIMARY KEY)")

    bands = list(range(MIN_RATING_BAND, MAX_RATING_BAND, BAND_SIZE))
    for low in bands:
        high = low + BAND_SIZE - 1
        cur.execute(
            "INSERT OR IGNORE INTO keep_ids (id) "
            "SELECT id FROM puzzles WHERE rating BETWEEN ? AND ? "
            "ORDER BY RANDOM() LIMIT ?",
            (low, high, per_band),
        )

    cur.execute("DELETE FROM puzzles WHERE id NOT IN (SELECT id FROM keep_ids)")
    conn.commit()

    print("VACUUM in progress (physical compaction of the file)...")
    conn.execute("VACUUM")
    total_after = cur.execute("SELECT COUNT(*) FROM puzzles").fetchone()[0]
    conn.close()

    size_after = db_path.stat().st_size / 2**20
    print(f"After:  {total_after} puzzles, {size_after:.1f} MiB "
          f"(saved {size_before - size_after:.1f} MiB)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-band", type=int, default=DEFAULT_PER_BAND,
                        help=f"Max puzzles per {BAND_SIZE}-point rating band "
                             f"(default: {DEFAULT_PER_BAND})")
    args = parser.parse_args()
    trim(DB_PATH, args.per_band)


if __name__ == "__main__":
    main()
