import io
import json

import chess.pgn
import pytest

from scripts.baseline import FIXTURES_DIR
from tests.engine_replay import analyze_replayed, read_engine_json

IDS = sorted(p.name.removesuffix(".engine.json") for p in FIXTURES_DIR.glob("*.engine.json"))


@pytest.mark.parametrize("game_id", IDS)
def test_replay_matches_fixture(game_id):
    pgn = (FIXTURES_DIR / f"{game_id}.pgn").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES_DIR / f"{game_id}.analysis.json").read_text(encoding="utf-8"))
    plies = len(list(chess.pgn.read_game(io.StringIO(pgn)).mainline_moves()))
    assert len(expected["moves"]) == plies
    assert analyze_replayed(pgn, read_engine_json(FIXTURES_DIR / f"{game_id}.engine.json")) == expected
