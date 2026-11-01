import json
import subprocess
from datetime import datetime

import pytest

import chess_api
from scripts import baseline

GAME = {"url": "https://www.chess.com/game/live/111",
        "pgn": '[WhiteElo "1000"]\n[BlackElo "1001"]\n\n1. e4 *\n',
        "white": {"rating": 1000}, "black": {"rating": 1001}}


def test_find_games_picks_requested_ids():
    other = {**GAME, "url": "https://www.chess.com/game/live/222"}
    assert baseline.find_games([{"games": [GAME, other]}], {"222"}) == {"222": other}


def test_check_ratings():
    baseline.check_ratings("111", GAME)
    with pytest.raises(ValueError, match="111"):
        baseline.check_ratings("111", {**GAME, "black": {"rating": 1002}})


def test_save_pgn_utf8_lf(tmp_path):
    text = '[White "Jürgen"]\n\n1. e4 *\n'
    assert baseline.save_pgn(tmp_path, "1", text).read_bytes() == text.encode("utf-8")


def test_fetch_reports_missing_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(chess_api, "get_archives", lambda username: ["archive-1"])
    monkeypatch.setattr(chess_api, "_get", lambda url: {"games": [GAME]})
    monkeypatch.setattr(baseline, "FIXTURES_DIR", tmp_path / "fixtures")
    with pytest.raises(SystemExit, match="999"):
        baseline.cmd_fetch("someone", ["111", "999"])
    assert list(tmp_path.iterdir()) == []


def test_class_changes():
    old = [{"ply": 1, "classification": "best"}, {"ply": 2, "classification": "good"}]
    new = [{"ply": 1, "classification": "best"}, {"ply": 2, "classification": "mistake"}]
    assert baseline.class_changes(old, new) == [(2, "good", "mistake")]
    with pytest.raises(ValueError):
        baseline.class_changes(old, new[:1])


def _moves(*classes):
    return [{"ply": i + 1, "classification": c} for i, c in enumerate(classes)]


def _write_analyses(folder, analyses):
    folder.mkdir()
    for gid, (classes, white) in analyses.items():
        (folder / f"{gid}.analysis.json").write_text(json.dumps(
            {"moves": _moves(*classes), "accuracy": {"white": white, "black": 90.0}}), encoding="utf-8")


def test_diff_fixtures(monkeypatch, tmp_path, capsys):
    current, other = tmp_path / "current", tmp_path / "other"
    _write_analyses(other, {"1": (("best", "good"), 80.0), "2": (("best",), 60.0)})
    _write_analyses(current, {"1": (("best", "mistake"), 80.0), "2": (("best",), 65.0)})
    assert baseline.diff_fixtures(current, other, ["1", "2"]) == [
        {"id": "1", "class_changes": [(2, "good", "mistake")],
         "accuracy_old": {"white": 80.0, "black": 90.0}, "accuracy_new": {"white": 80.0, "black": 90.0}},
        {"id": "2", "class_changes": [],
         "accuracy_old": {"white": 60.0, "black": 90.0}, "accuracy_new": {"white": 65.0, "black": 90.0}}]
    monkeypatch.setattr(baseline, "FIXTURES_DIR", current)
    baseline.main(["diff", str(other), "1", "2"])
    assert capsys.readouterr().out.splitlines()[-1] == "total: 1 class changes in 1 games, 1 accuracy changes"


def test_analyzer_commit_marks_uncommitted_edits(monkeypatch):
    def git(status):
        return lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=status if "status" in cmd else "abc\n")
    monkeypatch.setattr(subprocess, "run", git(""))
    assert baseline._analyzer_commit() == "abc"
    monkeypatch.setattr(subprocess, "run", git(" M analyzer.py\n"))
    assert baseline._analyzer_commit() == "abc-dirty"


def test_replay_prints_class_and_accuracy_changes(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(baseline, "FIXTURES_DIR", tmp_path)
    (tmp_path / "1.pgn").write_text("1. e4 *\n", encoding="utf-8")
    (tmp_path / "1.analysis.json").write_text(json.dumps(
        {"moves": _moves("best"), "accuracy": {"white": 80.0, "black": 90.0}}), encoding="utf-8")
    new = {"moves": _moves("good"), "accuracy": {"white": 70.0, "black": 90.0}}
    monkeypatch.setattr(baseline, "read_engine_json", lambda path: None)
    monkeypatch.setattr(baseline, "analyze_replayed", lambda pgn, engine_json: new)
    baseline.cmd_replay(["1"])
    assert capsys.readouterr().out.splitlines() == [
        "1 ply 1: best -> good",
        "1 accuracy {'white': 80.0, 'black': 90.0} -> {'white': 70.0, 'black': 90.0}",
        "1: 1 changes"]
    assert json.loads((tmp_path / "1.analysis.json").read_text(encoding="utf-8")) == new


def test_replay_names_the_game_without_a_recorded_search(monkeypatch, tmp_path):
    monkeypatch.setattr(baseline, "FIXTURES_DIR", tmp_path)
    (tmp_path / "1.pgn").write_text("1. e4 *\n", encoding="utf-8")
    (tmp_path / "1.analysis.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(baseline, "read_engine_json", lambda path: None)

    def missing(pgn, engine_json):
        raise KeyError("no recorded search")
    monkeypatch.setattr(baseline, "analyze_replayed", missing)
    with pytest.raises(SystemExit, match="^1: 'no recorded search'$"):
        baseline.cmd_replay(["1"])


def test_engine_seconds():
    calls = [{"infos": [{"time": 0.2}, {"time": 0.3}]}, {"infos": [{"time": 0.1}]}, {"infos": [{}]}]
    assert baseline.engine_seconds(calls) == pytest.approx(0.4)


def test_timing_row():
    fixture = {"moves": [{"ply": 1, "classification": "best"}, {"ply": 2, "classification": "good"}]}
    run = lambda cls, acc, t: {"elapsed_s": t, "engine_s": t / 2, "result": {
        "moves": [{"ply": 1, "classification": "best"}, {"ply": 2, "classification": cls}],
        "accuracy": {"white": acc, "black": 50.0}}}
    row = baseline.timing_row(fixture, {"elapsed_s": 9.0}, [run("good", 80.0, 4.0), run("mistake", 79.0, 6.0)])
    assert (row["plies"], row["t1"], row["t2"], row["s_per_ply"]) == (2, 4.0, 6.0, 2.5)
    assert row["engine_share"] == pytest.approx(0.5)
    assert (row["changed_runs"], row["changed_vs_fixture"]) == (1, [0, 1])
    assert row["acc_delta"] == {"white": 1.0, "black": 0.0}
    assert row["fixture_s"] == 9.0


@pytest.fixture
def dirs(monkeypatch, tmp_path):
    fixtures = tmp_path / "fixtures"
    for name, value in (("PROJECT_ROOT", tmp_path), ("FIXTURES_DIR", fixtures)):
        monkeypatch.setattr(baseline, name, value)
    return fixtures


def test_write_new_never_overwrites(tmp_path):
    path = tmp_path / "m.md"
    path.write_text("old", encoding="utf-8")
    with pytest.raises(SystemExit, match="another --label"):
        baseline._write_new(path, "new")
    assert path.read_text(encoding="utf-8") == "old"


def test_timing_fails_before_the_laps(dirs, monkeypatch, tmp_path):
    fixtures = dirs
    monkeypatch.setattr(baseline, "analyze_recorded", lambda *a, **k: pytest.fail("Stockfish started"))
    fixtures.mkdir()
    (fixtures / "1.pgn").write_text("1. e4 *\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="1.analysis.json"):
        baseline.cmd_timing("rete", "prova")
    existing = tmp_path / "docs" / "measurements" / f"{datetime.now():%Y-%m-%d}-tempi-prova.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("old", encoding="utf-8")
    with pytest.raises(SystemExit, match="another --label"):
        baseline.cmd_timing("rete", "prova")
    assert existing.read_text(encoding="utf-8") == "old"
