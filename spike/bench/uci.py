"""UCI driver of the stack spike: one command sequence for every engine, one parser for its answers.

    python spike/bench/uci.py --label L --threads T -- <engine command line>

analyses the 34 positions of the Opera Game and writes spike/results/L.json only once the
analysis ends. Standard library only: S2 runs it on a macOS runner without the venv.
"""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path

SPIKE = Path(__file__).resolve().parent.parent
RESULTS = SPIKE / "results"
OPERA = json.loads((SPIKE / "opera" / "opera.json").read_text(encoding="utf-8"))
PLIES = len(OPERA["moves"]) + 1  # 34 positions: the start and the final mate included


def setup_commands(threads: int) -> list[str]:
    return [f"setoption name Threads value {threads}", f"setoption name Hash value {OPERA['hash_mb']}",
            f"setoption name MultiPV value {OPERA['multipv']}", "ucinewgame"]


def position_command(ply: int) -> str:
    moves = OPERA["moves"][:ply]
    return "position startpos" + (" moves " + " ".join(moves) if moves else "")


def _int_after(tokens, key):
    return int(tokens[tokens.index(key) + 1]) if tokens and key in tokens else None


def _score(tokens):
    if not tokens:
        return None
    i = tokens.index("score")
    return {tokens[i + 1]: int(tokens[i + 2])}


def parse_position(lines: list[str]) -> dict:
    """The answer to one go: each multipv index keeps its last info line with an exact score."""
    final, bestmove = {}, None
    for line in lines:
        tokens = line.split()  # also drops the trailing \r that adb shell leaves
        if tokens[:1] == ["bestmove"]:
            bestmove = tokens[1]
        elif tokens[:1] == ["info"] and "score" in tokens and not {"lowerbound", "upperbound"} & set(tokens):
            final[_int_after(tokens, "multipv") or 1] = tokens
    first = final.get(1)
    return {"bestmove": bestmove, "score": _score(first), "score2": _score(final.get(2)),
            "nodes": _int_after(first, "nodes"), "engine_ms": _int_after(first, "time")}


def parse_run(raw: dict) -> dict:
    """RawRun -> result. ValueError unless the plies are exactly 0-33, each with a bestmove."""
    positions = sorted(raw["positions"], key=lambda p: p["ply"])
    if [p["ply"] for p in positions] != list(range(PLIES)):
        raise ValueError(f"the plies must be exactly 0-{PLIES - 1}")
    parsed = [{"ply": p["ply"], **parse_position(p["lines"]), "wall_ms": p["wall_ms"]} for p in positions]
    missing = [p["ply"] for p in parsed if p["bestmove"] is None]
    if missing:
        raise ValueError(f"no bestmove at plies {missing}")
    return {"label": raw["label"], "engine_id": raw["engine_id"], "threads": raw["threads"],
            "instances": raw["instances"], "meta": raw["meta"], "total_wall_ms": raw["total_wall_ms"],
            "total_engine_ms": sum(p["engine_ms"] or 0 for p in parsed), "positions": parsed}


def result_path(label: str, root: Path = RESULTS) -> Path:
    """ValueError for a label outside [a-z0-9-], FileExistsError for one already measured."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", label):
        raise ValueError(f"bad label {label!r}: 1-64 characters from a-z, 0-9 and -, not starting with -")
    path = root / f"{label}.json"
    if path.exists():
        raise FileExistsError(f"{path.name} exists: a measurement is never overwritten")
    return path


def save_result(path: Path, result: dict) -> None:
    path.parent.mkdir(exist_ok=True)
    with path.open("x", encoding="utf-8") as f:  # "x" fails on an existing file
        json.dump(result, f, indent=2)


def run_engine(argv: list[str], threads: int, label: str) -> dict:
    """Runs the protocol on one engine process and returns the RawRun. Writes no file.

    RuntimeError if the engine exits before the last bestmove."""
    engine = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              encoding="utf-8", errors="replace")

    def send(command):
        engine.stdin.write(command + "\n")
        engine.stdin.flush()

    def read_until(prefix):
        lines = []
        while not lines or not lines[-1].startswith(prefix):
            line = engine.stdout.readline()
            if not line:
                raise RuntimeError(f"the engine exited before {prefix!r}")
            lines.append(line.rstrip("\n"))
        return lines

    try:
        send("uci")
        engine_id = next((line[8:] for line in read_until("uciok") if line.startswith("id name ")), None)
        for command in setup_commands(threads):
            send(command)
        send("isready")
        read_until("readyok")
        positions, start = [], time.perf_counter()
        for ply in range(PLIES):
            send(position_command(ply))
            go = time.perf_counter()
            send(f"go depth {OPERA['depth']}")
            lines = read_until("bestmove")
            positions.append({"ply": ply, "wall_ms": round((time.perf_counter() - go) * 1000, 1), "lines": lines})
        total_wall_ms = round((time.perf_counter() - start) * 1000, 1)
    except OSError as e:  # a write to an engine that has already exited
        raise RuntimeError(f"the engine exited: {e}") from e
    finally:
        engine.communicate("quit\n")  # ignores a broken pipe, so a complete run survives an engine gone after it
    return {"label": label, "engine_id": engine_id, "threads": threads, "instances": 1,
            "total_wall_ms": total_wall_ms, "meta": {}, "positions": positions}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Analyses the Opera Game on a UCI engine.")
    parser.add_argument("--label", required=True)
    parser.add_argument("--threads", type=int, required=True)
    parser.add_argument("engine", nargs="+", help="the engine command line, after --")
    args = parser.parse_args(argv)
    path = result_path(args.label, RESULTS)  # before the run, so a bad or used label costs nothing
    result = parse_run(run_engine(args.engine, args.threads, args.label))
    save_result(path, result)
    print(f"{path.name}: {result['total_wall_ms'] / 1000:.1f} s")


if __name__ == "__main__":
    main()
