"""Checks for the spike bench. Run by name from the project root:

    .venv\\Scripts\\python.exe -m pytest spike/bench/check_bench.py -q

The name falls outside test_*.py, so the project suite does not collect it.
python-chess is used only here.
"""
import copy
import json
import sys

import chess
import pytest

import compare
import uci

# A fake engine: logs every command it gets to argv[1]; with "die" in argv it exits at the first go.
FAKE = r"""
import sys
with open(sys.argv[1], "a") as log:
    for line in sys.stdin:
        log.write(line)
        log.flush()
        command = line.split()[:1]
        if command == ["uci"]:
            print("id name Fake 1\nuciok", flush=True)
        elif command == ["isready"]:
            print("readyok", flush=True)
        elif command == ["go"]:
            if "die" in sys.argv:
                sys.exit(1)
            print("info depth 1 multipv 1 score cp 10 nodes 20 time 3 pv e2e4\nbestmove e2e4", flush=True)
        elif command == ["quit"]:
            break
"""


def test_opera_game():
    opera = uci.OPERA
    assert (len(opera["moves"]), opera["depth"], opera["multipv"], opera["hash_mb"]) == (33, 15, 2, 128)
    board = chess.Board()
    for ply, move in enumerate(opera["moves"], 1):
        board.push_uci(move)  # raises on an illegal move
        if ply == 31:
            assert [m.uci() for m in board.legal_moves] == ["d7b8"]
    assert board.is_checkmate()


def test_protocol_commands():
    assert uci.setup_commands(6) == ["setoption name Threads value 6", "setoption name Hash value 128",
                                     "setoption name MultiPV value 2", "ucinewgame"]
    assert uci.position_command(0) == "position startpos"
    assert uci.position_command(2) == "position startpos moves e2e4 e7e5"


def test_parse_mate():
    assert uci.parse_position(["info depth 0 score mate 0", "bestmove (none)"]) == {
        "bestmove": "(none)", "score": {"mate": 0}, "score2": None, "nodes": None, "engine_ms": None}


def test_parse_two_lines():
    lines = ["info depth 14 seldepth 17 multipv 1 score cp 28 nodes 900 nps 9000 time 100 pv e2e4 e7e5",
             "info depth 14 seldepth 17 multipv 2 score cp 20 nodes 950 nps 9000 time 105 pv d2d4 d7d5",
             "info depth 15 seldepth 19 multipv 1 score cp 31 nodes 1200 nps 9000 time 130 pv e2e4 e7e5",
             "info depth 15 seldepth 19 multipv 2 score mate -3 nodes 1250 nps 9000 time 135 pv d2d4 d7d5",
             "bestmove e2e4 ponder e7e5"]
    assert uci.parse_position(lines) == {"bestmove": "e2e4", "score": {"cp": 31}, "score2": {"mate": -3},
                                         "nodes": 1200, "engine_ms": 130}


def test_parse_noise():
    lines = ["info depth 15 seldepth 19 multipv 1 score cp 35 nodes 1000 nps 9000 time 110 pv e2e4\r",
             "info depth 16 seldepth 20 multipv 1 score cp 90 lowerbound nodes 1100 nps 9000 time 120 pv e2e4\r",
             "info depth 16 seldepth 20 multipv 1 score cp -40 upperbound nodes 1150 nps 9000 time 125 pv e2e4\r",
             "info depth 16 currmove e2e4 currmovenumber 1\r",
             "bestmove e2e4 ponder e7e5\r"]
    assert uci.parse_position(lines) == {"bestmove": "e2e4", "score": {"cp": 35}, "score2": None,
                                         "nodes": 1000, "engine_ms": 110}


def test_run_engine_fake(tmp_path):
    log = tmp_path / "commands.txt"
    raw = uci.run_engine([sys.executable, "-c", FAKE, str(log)], 1, "fake-1")
    assert [p["ply"] for p in raw["positions"]] == list(range(34))
    moves = uci.OPERA["moves"]
    positions = ["position startpos"] + [f"position startpos moves {' '.join(moves[:k])}" for k in range(1, 34)]
    assert log.read_text().splitlines() == [
        "uci", "setoption name Threads value 1", "setoption name Hash value 128",
        "setoption name MultiPV value 2", "ucinewgame", "isready",
        *[command for position in positions for command in (position, "go depth 15")], "quit"]
    result = uci.parse_run(raw)
    assert (result["engine_id"], result["positions"][33]["bestmove"], result["total_engine_ms"]) == ("Fake 1", "e2e4", 102)
    with pytest.raises(ValueError):
        uci.parse_run({**raw, "positions": raw["positions"][1:]})
    raw["positions"][5]["lines"] = raw["positions"][5]["lines"][:-1]
    with pytest.raises(ValueError):
        uci.parse_run(raw)


def test_run_engine_dies(tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(uci, "RESULTS", results)
    with pytest.raises(RuntimeError):
        uci.main(["--label", "dies-1", "--threads", "1", "--",
                  sys.executable, "-c", FAKE, str(tmp_path / "commands.txt"), "die"])
    assert list(results.iterdir()) == []


def test_result_path(tmp_path):
    for bad in ("../x", "A", "", "-x", "x\n"):
        with pytest.raises(ValueError):
            uci.result_path(bad, tmp_path)
    (tmp_path / "taken-1.json").write_text("{}")
    with pytest.raises(FileExistsError):
        uci.result_path("taken-1", tmp_path)
    assert uci.result_path("free-1", tmp_path) == tmp_path / "free-1.json"


def test_compare(tmp_path, capsys):
    ref = {"label": "ref", "threads": 1, "instances": 1, "total_wall_ms": 2000.0, "total_engine_ms": 1900,
           "meta": {}, "positions": [{"ply": k, "bestmove": "e2e4", "score": {"cp": k}, "score2": None,
                                      "nodes": 100 + k, "engine_ms": 10, "wall_ms": 11.0} for k in range(34)]}
    run = copy.deepcopy(ref)
    run["label"] = "run"
    run["positions"][3]["bestmove"] = "d2d4"
    run["positions"][5]["nodes"] += 1
    assert compare.compare(ref, run) == {"positions": 33, "bestmove": 33, "score": 34, "score2": 34, "nodes": 33}

    for name, result in (("ref", ref), ("run", run), ("run-t2", {**run, "label": "run-t2", "threads": 2})):
        (tmp_path / f"{name}.json").write_text(json.dumps(result))
    ref_file, run_file, t2_file = (str(tmp_path / f"{name}.json") for name in ("ref", "run", "run-t2"))
    assert compare.main(["--ref", ref_file, run_file]) == 0
    assert compare.main(["--ref", ref_file, "--strict", run_file]) == 1
    assert compare.main(["--ref", ref_file, "--strict", t2_file]) == 0
    assert "| run | 1 | 1 | 2.0 | 1.9 | 1.00 | 33/34 | 33/34 | 34/34 | 34/34 | 33/34 |" in capsys.readouterr().out
