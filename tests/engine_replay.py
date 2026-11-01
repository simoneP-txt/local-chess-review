"""Record Stockfish searches during an analysis and replay them without the engine."""
import json
from unittest import mock

import chess
import chess.engine
from chess.engine import Cp, Mate, PovScore, PovWdl, Wdl

from analyzer import GameAnalyzer

_OPTIONAL = ("depth", "seldepth", "nodes", "time", "nps", "hashfull", "tbhits", "multipv")


def info_to_json(info):
    score = info["score"].relative
    data = {"score": {"mate": score.mate()} if score.is_mate() else {"cp": score.score()},
            "pv": [m.uci() for m in info.get("pv", [])]}
    data.update({k: info[k] for k in _OPTIONAL if k in info})
    if "wdl" in info:
        w = info["wdl"].relative
        data["wdl"] = [w.wins, w.draws, w.losses]
    return data


def info_from_json(data, turn):
    score = data["score"]
    info = {k: data[k] for k in _OPTIONAL if k in data}
    info["score"] = PovScore(Mate(score["mate"]) if "mate" in score else Cp(score["cp"]), turn)
    info["pv"] = [chess.Move.from_uci(u) for u in data["pv"]]
    if "wdl" in data:
        info["wdl"] = PovWdl(Wdl(*data["wdl"]), turn)
    return info


class RecordingEngine:
    def __init__(self, engine, calls, wdl=False):
        self._engine, self._calls = engine, calls
        self.id = engine.id
        if wdl:
            engine.configure({"UCI_ShowWDL": True})

    def configure(self, options):
        self._engine.configure(options)

    def analyse(self, board, limit, multipv=None):
        result = self._engine.analyse(board, limit, multipv=multipv)
        infos = result if multipv is not None else [result]
        self._calls.append({"fen": board.fen(), "multipv": multipv, "depth": limit.depth,
                            "infos": [info_to_json(i) for i in infos]})
        return result

    def quit(self):
        self._engine.quit()


class ReplayEngine:
    def __init__(self, calls):
        self._index = {(c["fen"], c["multipv"] or 1, c["depth"]): c["infos"] for c in calls}

    def configure(self, options):
        pass

    def analyse(self, board, limit, multipv=None):
        fen, depth = board.fen(), limit.depth
        try:
            infos = self._index[(fen, multipv or 1, depth)]
        except KeyError:
            raise KeyError(f"no recorded search: {fen} multipv={multipv or 1} depth={depth}") from None
        lines = [info_from_json(i, board.turn) for i in infos]
        return lines if multipv is not None else lines[0]

    def quit(self):
        pass


class ScriptedEngine:
    """Fake engine for tests: line i plays the i-th legal move, every line scores cp_for(board)."""

    def __init__(self, cp_for=None):
        self.cp_for, self.calls = cp_for, []

    def configure(self, options):
        pass

    def analyse(self, board, limit, multipv=None):
        self.calls.append((board.fen(), multipv))
        score = PovScore(Cp(self.cp_for(board) if self.cp_for else 10), board.turn)
        infos = [{"score": score, "pv": [m]} for m in list(board.legal_moves)[: multipv or 1]]
        if not infos:  # Stockfish gives one line without moves: mated, or stalemate
            infos = [{"score": PovScore(Mate(0) if board.is_checkmate() else Cp(0), board.turn), "pv": []}]
        return infos if multipv is not None else infos[0]

    def quit(self):
        pass


def patch_popen(factory):
    return mock.patch.object(chess.engine.SimpleEngine, "popen_uci", staticmethod(factory))


def write_engine_json(path, meta, calls):
    rows = ",\n".join(json.dumps(c) for c in calls)
    body = f'{{"meta": {json.dumps(meta)},\n"calls": [\n' + (rows + "\n" if calls else "") + "]}\n"
    path.write_text(body, encoding="utf-8", newline="\n")


def read_engine_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def analyze_replayed(pgn, engine_json):
    with patch_popen(lambda path: ReplayEngine(engine_json["calls"])):
        result = GameAnalyzer("replay", depth=engine_json["meta"]["depth"], threads=1).analyze_pgn(pgn)
    return json.loads(json.dumps(result))
