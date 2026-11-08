"""Flask API tests with the test client: no Stockfish, no browser."""
import os
import sqlite3
from contextlib import closing

import chess
import chess.engine
import pytest

import app
from tests.engine_replay import ScriptedEngine

PGN = '[Event "Test"]\n\n1. e4 e5 *\n'


@pytest.fixture(autouse=True)
def no_depth_override(monkeypatch):
    monkeypatch.delenv("REVIEW_DEPTH", raising=False)  # the defaults must not depend on the shell


@pytest.fixture
def client():
    return app.app.test_client()


@pytest.fixture
def analyzer_calls(monkeypatch):
    """Swaps GameAnalyzer for a stub on a 4-core host. Returns what the view built it with."""
    calls = []

    class StubAnalyzer:
        def __init__(self, engine_path, **kwargs):
            calls.append((engine_path, kwargs))

        def analyze_pgn(self, pgn, white_elo=None, black_elo=None):
            return {"moves": []}

    monkeypatch.setattr(app, "GameAnalyzer", StubAnalyzer)
    monkeypatch.setattr(app, "ENGINE_PATH", "stub-engine")
    monkeypatch.setattr(app, "_host_cores", lambda: 4)
    return calls


@pytest.mark.parametrize("cores, depth", [(1, 12), (2, 12), (4, 15), (8, 15), (16, 15)])
def test_engine_limits_follow_the_cores(cores, depth):
    assert app.engine_limits(cores) == {
        "cores": cores,
        "threads": {"min": 1, "max": cores, "default": 1},
        "depth": {"min": 10, "max": 22, "default": depth},
    }


@pytest.mark.parametrize("value, depth", [("18", 18), ("5", 10), ("99", 22), ("", 15)])
def test_review_depth_env_sets_the_default_depth_inside_the_range(monkeypatch, value, depth):
    monkeypatch.setenv("REVIEW_DEPTH", value)
    assert app.engine_limits(4)["depth"]["default"] == depth


@pytest.mark.parametrize("cores, depth", [(4, 15), (2, 12)])
def test_unparsable_review_depth_is_ignored(monkeypatch, cores, depth):
    monkeypatch.setenv("REVIEW_DEPTH", "abc")
    assert app.engine_limits(cores)["depth"]["default"] == depth


def test_review_depth_env_wins_on_a_small_host(monkeypatch):
    monkeypatch.setenv("REVIEW_DEPTH", "18")
    assert app.engine_limits(2)["depth"]["default"] == 18


@pytest.mark.parametrize("usable, total, expected", [(3, 8, 3), (None, 8, 8), (None, None, 1)],
                         ids=["usable-count", "total-count", "unknown"])
def test_host_cores_prefers_the_usable_count_and_is_at_least_one(
        monkeypatch, usable, total, expected):
    if usable is None:
        monkeypatch.delattr(os, "process_cpu_count", raising=False)  # Python before 3.13
    else:
        monkeypatch.setattr(os, "process_cpu_count", lambda: usable, raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: total)
    assert app._host_cores() == expected


def test_api_engine_reports_the_limits_of_the_host(client, monkeypatch):
    monkeypatch.setattr(app, "_host_cores", lambda: 8)
    resp = client.get("/api/engine")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "cores": 8,
        "threads": {"min": 1, "max": 8, "default": 1},
        "depth": {"min": 10, "max": 22, "default": 15},
    }


@pytest.mark.parametrize("field, value", [
    ("depth", 9), ("depth", 23), ("depth", "15"), ("depth", 15.5), ("depth", True), ("depth", None),
    ("threads", 0), ("threads", 5), ("threads", "2"), ("threads", True),
])
def test_analyze_rejects_a_value_that_is_not_an_integer_in_range(client, analyzer_calls, field, value):
    resp = client.post("/api/analyze", json={"pgn": PGN, field: value})
    assert resp.status_code == 400
    low, high = {"depth": (10, 22), "threads": (1, 4)}[field]  # the host has 4 cores
    assert resp.get_json() == {"error": f"{field} must be an integer between {low} and {high}"}
    assert analyzer_calls == []


def test_analyze_checks_the_pgn_before_depth_and_threads(client, analyzer_calls):
    resp = client.post("/api/analyze", json={"depth": 5})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "missing 'pgn' field"}


@pytest.mark.parametrize("cores, body, expected", [
    (4, {}, {"depth": 15, "threads": 1}),
    (2, {}, {"depth": 12, "threads": 1}),
    (4, {"depth": 18, "threads": 3}, {"depth": 18, "threads": 3}),
    (4, {"depth": 10, "threads": 1}, {"depth": 10, "threads": 1}),
    (4, {"depth": 22, "threads": 4}, {"depth": 22, "threads": 4}),
], ids=["defaults", "defaults-small-host", "chosen", "lowest", "highest"])
def test_analyze_gives_the_analyzer_the_depth_and_threads(
        client, analyzer_calls, monkeypatch, cores, body, expected):
    monkeypatch.setattr(app, "_host_cores", lambda: cores)
    resp = client.post("/api/analyze", json={"pgn": PGN, **body})
    assert resp.status_code == 200
    assert analyzer_calls == [("stub-engine", expected)]


def test_analyze_defaults_to_review_depth(client, analyzer_calls, monkeypatch):
    monkeypatch.setenv("REVIEW_DEPTH", "18")
    client.post("/api/analyze", json={"pgn": PGN})
    assert analyzer_calls == [("stub-engine", {"depth": 18, "threads": 1})]


def test_eval_after_white_mates(client, monkeypatch):
    monkeypatch.setattr(app, "_get_engine", lambda: ScriptedEngine())  # Mate(0) for the mated side
    fen = "rnbqkbnr/ppppp2p/5p2/6pQ/3PP3/8/PPP2PPP/RNB1KBNR b KQkq - 1 3"  # 1. e4 f6 2. d4 g5 3. Qh5#
    resp = client.post("/api/eval", json={"fen": fen})
    assert resp.status_code == 200
    data = resp.get_json()
    assert (data["eval_white_cp"], data["wp_white"], data["eval_cp"]) == (10000, 1.0, -10000)


@pytest.mark.parametrize("depth", [9, 23, "12", 12.5, True, None])
def test_eval_rejects_a_depth_that_is_not_an_integer_in_range(client, monkeypatch, depth):
    monkeypatch.setattr(app, "_get_engine", lambda: ScriptedEngine())  # the real Stockfish must not start
    resp = client.post("/api/eval", json={"fen": chess.STARTING_FEN, "depth": depth})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "depth must be an integer between 10 and 22"}


@pytest.mark.parametrize("depth", [10, 22])
def test_eval_accepts_a_depth_at_the_ends_of_the_range(client, monkeypatch, depth):
    monkeypatch.setattr(app, "_get_engine", lambda: ScriptedEngine())
    resp = client.post("/api/eval", json={"fen": chess.STARTING_FEN, "depth": depth})
    assert resp.status_code == 200
    assert resp.get_json()["depth"] == depth


def test_eval_does_not_check_its_default_depth(client, monkeypatch):
    monkeypatch.setattr(app, "_get_engine", lambda: ScriptedEngine())
    monkeypatch.setattr(app, "EVAL_DEPTH", 8)  # set from the environment, outside 10-22
    resp = client.post("/api/eval", json={"fen": chess.STARTING_FEN})
    assert resp.status_code == 200
    assert resp.get_json()["depth"] == 8


def test_eval_rejects_an_impossible_position(client, monkeypatch):
    monkeypatch.setattr(app, "_get_engine", lambda: ScriptedEngine())
    resp = client.post("/api/eval", json={"fen": "8/8/8/8/8/8/8/8 w - - 0 1"})  # parses, but has no kings
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "invalid FEN: NO_WHITE_KING|NO_BLACK_KING|EMPTY"}


def test_eval_opens_a_new_engine_after_an_engine_error(client, monkeypatch):
    calls = []

    class DeadEngine(ScriptedEngine):
        """Stockfish after its process died: analyse and quit raise."""

        def analyse(self, board, limit, multipv=None):
            raise chess.engine.EngineTerminatedError("engine event loop dead")

        def quit(self):
            calls.append("quit")
            raise chess.engine.EngineTerminatedError("engine event loop dead")

    engines = iter([DeadEngine(), ScriptedEngine()])

    def popen_uci(path):
        calls.append(path)
        return next(engines)

    monkeypatch.setattr(app, "ENGINE_PATH", "fake-engine")
    monkeypatch.setattr(app, "_engine", None)
    monkeypatch.setattr(chess.engine.SimpleEngine, "popen_uci", popen_uci)
    assert client.post("/api/eval", json={"fen": chess.STARTING_FEN}).status_code == 500
    assert client.post("/api/eval", json={"fen": chess.STARTING_FEN}).status_code == 200
    assert calls == ["fake-engine", "quit", "fake-engine"]


@pytest.mark.parametrize("difficulty, ratings", [
    ("beginner", {400, 799}),
    ("easy", {800}),
    ("intermediate", {1599}),
    ("advanced", {1600}),
    ("expert", {2399}),
    ("master", {2400, 2800}),
    # A missing or unknown difficulty stays on easy.
    (None, {800}),
    ("medium", {800}),
    ("hard", {800}),
    ("xyz", {800}),
])
def test_puzzle_bands(client, monkeypatch, tmp_path, difficulty, ratings):
    db = tmp_path / "puzzles.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE puzzles (id TEXT PRIMARY KEY, fen TEXT, moves TEXT, rating INTEGER, themes TEXT)")
        # Highest first: a SELECT wider than its COUNT would then return a rating outside the band.
        conn.executemany("INSERT INTO puzzles VALUES (?, 'fen', 'e2e4 e7e5', ?, 'mate')",
                         [(str(rating), rating) for rating in (2800, 2400, 2399, 1600, 1599, 800, 799, 400)])
        conn.commit()
    monkeypatch.setattr(app, "PUZZLE_DB_PATH", db)
    query = {} if difficulty is None else {"difficulty": difficulty}

    # 20 requests miss one of two puzzles with probability 2^-19.
    assert {client.get("/api/puzzle", query_string=query).get_json()["rating"] for _ in range(20)} == ratings


def test_puzzle_answer_does_not_echo_the_difficulty(client, monkeypatch, tmp_path):
    db = tmp_path / "puzzles.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE puzzles (id TEXT PRIMARY KEY, fen TEXT, moves TEXT, rating INTEGER, themes TEXT)")
        conn.execute("INSERT INTO puzzles VALUES ('a', 'fen', 'e2e4 e7e5', 800, 'mate')")
        conn.commit()
    monkeypatch.setattr(app, "PUZZLE_DB_PATH", db)
    data = client.get("/api/puzzle", query_string={"difficulty": "xyz"}).get_json()
    assert set(data) == {"id", "fen", "moves", "rating", "themes"}
