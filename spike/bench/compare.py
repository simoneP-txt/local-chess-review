"""Compares spike results with the reference and prints a Markdown table.

    python spike/bench/compare.py --ref spike/results/pc-native-t1-1.json [--strict] <result>...

A position matches the reference when bestmove and score are equal; score2 and nodes are
counted apart. Two nulls are equal. Standard library only: S2 runs it without the venv.
"""
import argparse
import json
import sys
from pathlib import Path

FIELDS = ("bestmove", "score", "score2", "nodes")


def compare(ref: dict, run: dict) -> dict:
    pairs = list(zip(ref["positions"], run["positions"]))
    return {"positions": sum(a["bestmove"] == b["bestmove"] and a["score"] == b["score"] for a, b in pairs),
            **{field: sum(a[field] == b[field] for a, b in pairs) for field in FIELDS}}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compares spike results with the reference.")
    parser.add_argument("--ref", required=True)
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 when a run with 1 thread and 1 instance has a position unlike the reference")
    parser.add_argument("results", nargs="+")
    args = parser.parse_args(argv)
    ref = json.loads(Path(args.ref).read_text(encoding="utf-8"))
    n = len(ref["positions"])
    print("| label | threads | instances | wall s | engine s | wall / ref | positions | "
          + " | ".join(FIELDS) + " | max frame gap ms |")
    print("|---" * 12 + "|")
    differs = False
    for path in args.results:
        run = json.loads(Path(path).read_text(encoding="utf-8"))
        counts = compare(ref, run)
        print(f"| {run['label']} | {run['threads']} | {run['instances']} | {run['total_wall_ms'] / 1000:.1f} | "
              f"{run['total_engine_ms'] / 1000:.1f} | {run['total_wall_ms'] / ref['total_wall_ms']:.2f} | "
              + " | ".join(f"{count}/{n}" for count in counts.values())
              + f" | {run['meta'].get('max_frame_gap_ms', '')} |")
        differs |= run["threads"] == run["instances"] == 1 and counts["positions"] < n
    return 1 if args.strict and differs else 0


if __name__ == "__main__":
    sys.exit(main())
