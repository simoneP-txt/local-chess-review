"""
PHASE 1 (refactor) - Public chess platform API wrapper with enriched data.

For each game returns: date, opponent, result, time class, PGN,
post-game Elo of both players, ISO country code (for flags).
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import chess.pgn
import requests

HEADERS = {
    "User-Agent": "ChessReviewLocal/1.0 (contact: simonepaoletti17@gmail.com)"
}

BASE_URL = "https://api.chess.com/pub/player"

# Profile cache (country) so we don't hammer the API on every game.
_profile_cache: dict[str, dict[str, Any]] = {}


def _get(url: str) -> dict[str, Any]:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_archives(username: str) -> list[str]:
    return _get(f"{BASE_URL}/{username.lower()}/games/archives").get("archives", [])


def get_current_month_archive_url(username: str) -> str | None:
    now = datetime.utcnow()
    target = f"/{now.year}/{now.month:02d}"
    for url in get_archives(username):
        if url.endswith(target):
            return url
    return None


def _get_player_profile(username: str) -> dict[str, str]:
    """
    Returns a dict with 'country' (ISO-2 code) and 'avatar' (URL) for the player.
    Both fields may be empty strings if absent. Result is cached.
    """
    if not username:
        return {"country": "", "avatar": ""}
    key = username.lower()
    if key in _profile_cache:
        return _profile_cache[key]
    try:
        data = _get(f"{BASE_URL}/{key}")
        country_url = data.get("country", "")
        country_code = country_url.rsplit("/", 1)[-1].upper() if country_url else ""
        avatar = data.get("avatar", "") or ""
        _profile_cache[key] = {"country": country_code, "avatar": avatar}
        return _profile_cache[key]
    except Exception:
        _profile_cache[key] = {"country": "", "avatar": ""}
        return _profile_cache[key]


def get_player_country(username: str) -> str:
    """ISO-2 country code of the player (e.g. 'IT', 'US')."""
    return _get_player_profile(username).get("country", "")


def get_player_avatar(username: str) -> str:
    """Avatar URL of the player (empty string if absent)."""
    return _get_player_profile(username).get("avatar", "")


# -------- extraction helpers --------

DRAW_RESULTS = {
    "agreed", "repetition", "stalemate", "insufficient",
    "50move", "timevsinsufficient",
}


def _user_result(game: dict[str, Any], username: str) -> str:
    user_lower = username.lower()
    white_user = game.get("white", {}).get("username", "").lower()
    user_side = "white" if white_user == user_lower else "black"
    r = game.get(user_side, {}).get("result", "")
    if r == "win":
        return "win"
    if r in DRAW_RESULTS:
        return "draw"
    return "loss"


def _parse_date_from_pgn(pgn: str) -> str:
    try:
        g = chess.pgn.read_game(io.StringIO(pgn))
        if g is None:
            return ""
        return g.headers.get("Date", "") or g.headers.get("UTCDate", "")
    except Exception:
        return ""


# -------- main API --------

def get_current_month_games(username: str) -> list[dict[str, Any]]:
    """
    Downloads the current month's games and returns them in a normalized form.
    Output per game:
      id, date, opponent, result, time_class, end_time,
      white, black, user_color,
      white_rating, black_rating,
      white_country, black_country,
      pgn
    """
    url = get_current_month_archive_url(username)
    if not url:
        return []

    payload = _get(url)
    games_raw = payload.get("games", [])
    out: list[dict[str, Any]] = []

    # Country is fetched lazily inside the loop; the cache handles deduplication.
    for g in games_raw:
        pgn = g.get("pgn", "")
        if not pgn:
            continue
        white = g.get("white", {})
        black = g.get("black", {})
        white_user = white.get("username", "")
        black_user = black.get("username", "")
        user_color = "white" if white_user.lower() == username.lower() else "black"

        out.append({
            "date": _parse_date_from_pgn(pgn),
            "end_time": g.get("end_time", 0),  # epoch seconds, useful for precise sorting
            "opponent": black_user if user_color == "white" else white_user,
            "result": _user_result(g, username),
            "time_class": g.get("time_class", ""),
            "time_control": g.get("time_control", ""),
            "white": white_user,
            "black": black_user,
            "user_color": user_color,
            "white_rating": white.get("rating", 0),
            "black_rating": black.get("rating", 0),
            "white_country": get_player_country(white_user),
            "black_country": get_player_country(black_user),
            "white_avatar": get_player_avatar(white_user),
            "black_avatar": get_player_avatar(black_user),
            "pgn": pgn,
        })

    # Order: most recent first (end_time is more reliable than the PGN Date).
    out.sort(key=lambda x: (x.get("end_time") or 0, x.get("date") or ""), reverse=True)
    for i, item in enumerate(out):
        item["id"] = i
    return out


if __name__ == "__main__":
    import json
    import sys
    user = sys.argv[1] if len(sys.argv) > 1 else "hikaru"
    games = get_current_month_games(user)
    print(json.dumps(games[:2], indent=2, ensure_ascii=False))
    print(f"Total games for current month: {len(games)}")
