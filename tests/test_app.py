"""Flask API tests with the test client: no Stockfish, no browser."""
import os

import pytest

import app

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
