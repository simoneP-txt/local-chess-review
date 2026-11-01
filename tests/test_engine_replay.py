import json

import chess
import pytest
from chess.engine import Cp, Limit, Mate, PovScore, PovWdl, Wdl

import analyzer as A
from analyzer import GameAnalyzer
from tests.engine_replay import (RecordingEngine, ReplayEngine, analyze_replayed,
                                 info_from_json, info_to_json, patch_popen,
                                 read_engine_json, write_engine_json)

W, B = chess.WHITE, chess.BLACK
ONE_MOVE_FEN = "7k/8/6K1/8/8/8/8/R7 b - - 0 1"  # only Kg8 is legal


class StubEngine:
    """Stand-in for SimpleEngine: lines score 0, 10, 20 cp; mated when no move is legal."""
    id = {"name": "Stub"}

    def __init__(self):
        self.options = {}

    def configure(self, options):
        self.options.update(options)

    def analyse(self, board, limit, multipv=None):
        moves = list(board.legal_moves)[: multipv or 1]
        infos = [{"score": PovScore(Cp(10 * i), board.turn), "pv": [m], "depth": limit.depth,
                  "nodes": 100 + i, "time": 0.01, "multipv": i + 1} for i, m in enumerate(moves)]
        infos = infos or [{"score": PovScore(Mate(0), board.turn), "depth": 0}]
        return infos if multipv else infos[0]

    def quit(self):
        pass


def test_info_roundtrip_cp_and_wdl():
    info = {"score": PovScore(Cp(35), B), "pv": [chess.Move.from_uci("e7e5")], "depth": 15,
            "seldepth": 21, "nodes": 1234, "time": 0.05, "nps": 24680, "hashfull": 3,
            "multipv": 1, "wdl": PovWdl(Wdl(500, 400, 100), B)}
    data = info_to_json(info)
    assert (data["score"], data["pv"], data["wdl"]) == ({"cp": 35}, ["e7e5"], [500, 400, 100])
    assert info_from_json(json.loads(json.dumps(data)), B) == info


def test_info_roundtrip_mated():
    info = {"score": PovScore(Mate(0), W), "depth": 0}  # Stockfish on a mated position
    data = info_to_json(info)
    assert data == {"score": {"mate": 0}, "pv": [], "depth": 0}
    back = info_from_json(data, W)
    assert back["score"] == info["score"] and back["pv"] == []
    for color in (W, B):
        assert A.score_to_wp(back["score"], color) == A.score_to_wp(info["score"], color)


def test_recording_engine_records_calls():
    stub, calls = StubEngine(), []
    rec = RecordingEngine(stub, calls, wdl=True)
    assert stub.options == {"UCI_ShowWDL": True} and rec.id == {"name": "Stub"}
    board = chess.Board()
    assert len(rec.analyse(board, Limit(depth=15), multipv=2)) == 2
    assert isinstance(rec.analyse(board, Limit(depth=15)), dict)
    assert [(c["fen"], c["multipv"], c["depth"], len(c["infos"])) for c in calls] == [
        (board.fen(), 2, 15, 2), (board.fen(), None, 15, 1)]


def test_replay_returns_recorded_lines():
    calls, board = [], chess.Board()
    rec = RecordingEngine(StubEngine(), calls)
    lines = rec.analyse(board, Limit(depth=15), multipv=2)
    single = rec.analyse(board, Limit(depth=15))
    replay = ReplayEngine(calls)
    assert replay.analyse(board, Limit(depth=15), multipv=2) == lines
    assert replay.analyse(board, Limit(depth=15)) == single


def test_replay_single_line():
    calls, board = [], chess.Board(ONE_MOVE_FEN)
    RecordingEngine(StubEngine(), calls).analyse(board, Limit(depth=15), multipv=2)
    lines = ReplayEngine(calls).analyse(board, Limit(depth=15), multipv=2)
    assert [line["pv"] for line in lines] == [[chess.Move.from_uci("h8g8")]]


def test_replay_missing_call_raises():
    with pytest.raises(KeyError, match="RNBQKBNR w KQkq - 0 1 multipv=2"):
        ReplayEngine([]).analyse(chess.Board(), Limit(depth=15), multipv=2)


def test_engine_json_layout(tmp_path):
    calls = [{"fen": "f1", "multipv": 2, "depth": 15, "infos": []},
             {"fen": "f2", "multipv": None, "depth": 15, "infos": []}]
    path = tmp_path / "x.engine.json"
    write_engine_json(path, {"depth": 15}, calls)
    assert read_engine_json(path) == {"meta": {"depth": 15}, "calls": calls}
    assert len(path.read_text(encoding="utf-8").splitlines()) == len(calls) + 3


def test_record_then_replay_gives_same_analysis():
    pgn = '[White "a"]\n[Black "b"]\n[WhiteElo "1000"]\n[BlackElo "1000"]\n\n1. e4 e5 2. Nf3 Nc6 *\n'
    calls = []
    with patch_popen(lambda path: RecordingEngine(StubEngine(), calls)):
        live = GameAnalyzer("stub", depth=15, threads=1).analyze_pgn(pgn)
    assert len(calls) == 5  # one multipv=2 search per position, P0 ... P4
    assert analyze_replayed(pgn, {"meta": {"depth": 15}, "calls": calls}) == json.loads(json.dumps(live))
