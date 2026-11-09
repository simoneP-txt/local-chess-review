"""get_current_month_games on a stubbed archive: no request leaves the machine."""
import chess_api


def _game(end_time, **rules):
    return {"end_time": end_time, "pgn": "1. e4 *", **rules,
            "white": {"username": "alpha", "rating": 1200, "result": "win"},
            "black": {"username": "beta", "rating": 1210, "result": "resigned"}}


def test_month_games_skip_variants(monkeypatch):
    archive = {"games": [_game(3, rules="chess"), _game(2, rules="chess960"), _game(1)]}  # the last has no rules
    monkeypatch.setattr(chess_api, "get_current_month_archive_url", lambda username: "archive-url")
    monkeypatch.setattr(chess_api, "_get", lambda url: archive)
    monkeypatch.setattr(chess_api, "get_player_country", lambda username: "")
    monkeypatch.setattr(chess_api, "get_player_avatar", lambda username: "")
    assert [game["end_time"] for game in chess_api.get_current_month_games("alpha")] == [3, 1]
