"""
PHASE 4 - Flask server.

Endpoints:
  GET  /                           -> dashboard (username form + games list)
  GET  /api/games?username=...     -> JSON of the games for the current month
  GET  /api/game/<u>/<id>          -> single PGN from cache
  POST /api/analyze                -> {pgn, white_elo, black_elo, depth?} -> full analysis
  POST /api/eval                   -> {fen, depth?} -> {eval_cp, eval_white_cp,
                                        wp_white, best_move_uci, best_move_san}
                                        Used by the frontend "variation explorer".
  GET  /api/puzzle?difficulty=...  -> single random Lichess puzzle by rating band
  GET  /review                     -> page with chessboard + review sidebar

Downloaded games are cached in memory to avoid hammering the upstream API on
every "Analyze" click.

Engine: Stockfish is kept alive as a GLOBAL SINGLETON (popen once) to avoid
startup on every /api/eval. Access is serialized via a Lock because
chess.engine.SimpleEngine is not thread-safe.
"""

from __future__ import annotations

import os
import random
import sqlite3
import threading
from pathlib import Path
from typing import Optional

import chess
import chess.engine
from flask import Flask, jsonify, render_template, request

from analyzer import GameAnalyzer, score_to_cp, score_to_wp
from chess_api import get_current_month_games

BASE_DIR = Path(__file__).parent

# Look for the Stockfish executable in two standard locations.
ENGINE_CANDIDATES = [
    BASE_DIR / "engine" / "stockfish.exe",
    BASE_DIR / "engine" / "stockfish",
]
ENGINE_PATH = next((str(p) for p in ENGINE_CANDIDATES if p.exists()), None)

# Analysis depth: env override for higher precision.
ANALYSIS_DEPTH = int(os.environ.get("REVIEW_DEPTH", "15"))

# Depth for "fast" evaluations of the variation explorer: lower to keep UI
# latency low when the user explores alternative moves.
EVAL_DEPTH = int(os.environ.get("EVAL_DEPTH", "12"))

PUZZLE_DB_PATH = BASE_DIR / "engine" / "puzzles.db"

app = Flask(__name__)

# In-memory cache: { username_lower: [games...] }
_games_cache: dict[str, list[dict]] = {}

# ============================================================
# Engine singleton for /api/eval (variation explorer)
# ============================================================
_engine_lock = threading.Lock()
_engine: Optional[chess.engine.SimpleEngine] = None


def _get_engine() -> chess.engine.SimpleEngine:
    """Returns the engine singleton, lazy-initialized on first call."""
    global _engine
    if _engine is None:
        if not ENGINE_PATH:
            raise RuntimeError("Stockfish not found (engine/stockfish.exe).")
        eng = chess.engine.SimpleEngine.popen_uci(ENGINE_PATH)
        eng.configure({"Threads": 2, "Hash": 128})
        _engine = eng
    return _engine


@app.route("/")
def index():
    """Main dashboard."""
    return render_template("index.html")


@app.route("/review")
def review():
    """Interactive review page - the PGN is passed via query/POST JS."""
    return render_template("review.html")


@app.route("/api/games")
def api_games():
    """Returns the games of the current month for a given username."""
    username = (request.args.get("username") or "").strip()
    if not username:
        return jsonify({"error": "username missing"}), 400

    key = username.lower()
    if key not in _games_cache:
        try:
            _games_cache[key] = get_current_month_games(username)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"Upstream API error: {exc}"}), 502

    return jsonify({"username": username, "games": _games_cache[key]})


@app.route("/api/game/<username>/<int:game_id>")
def api_game_pgn(username: str, game_id: int):
    """Fetches a single PGN from the cache (lighter than passing it via query string)."""
    games = _games_cache.get(username.lower())
    if not games or game_id < 0 or game_id >= len(games):
        return jsonify({"error": "game not found; reload the list"}), 404
    return jsonify(games[game_id])


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Runs the Stockfish analysis on the provided PGN."""
    if not ENGINE_PATH:
        return jsonify({
            "error": "Stockfish not found. Place the executable at engine/stockfish.exe"
        }), 500

    payload = request.get_json(silent=True) or {}
    pgn = payload.get("pgn")
    if not pgn:
        return jsonify({"error": "missing 'pgn' field"}), 400

    depth = int(payload.get("depth", ANALYSIS_DEPTH))
    white_elo = payload.get("white_elo")
    black_elo = payload.get("black_elo")
    try:
        analyzer = GameAnalyzer(ENGINE_PATH, depth=depth)
        result = analyzer.analyze_pgn(pgn, white_elo=white_elo, black_elo=black_elo)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"analysis error: {exc}"}), 500

    return jsonify(result)


# ============================================================
# /api/eval - used by the frontend variation explorer
# ============================================================
@app.route("/api/eval", methods=["POST"])
def api_eval():
    """
    Evaluates an arbitrary FEN position and returns eval + best move.
    The variation explorer calls this every time the user makes an
    "off-game" move on the chessboard.
    """
    payload = request.get_json(silent=True) or {}
    fen = payload.get("fen")
    if not fen:
        return jsonify({"error": "missing 'fen' field"}), 400
    depth = int(payload.get("depth", EVAL_DEPTH))

    try:
        board = chess.Board(fen)
    except ValueError as exc:
        return jsonify({"error": f"invalid FEN: {exc}"}), 400

    try:
        with _engine_lock:
            eng = _get_engine()
            info = eng.analyse(board, chess.engine.Limit(depth=depth))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"engine error: {exc}"}), 500

    score = info.get("score")
    pv = info.get("pv") or []
    best_move = pv[0] if pv else None
    side = board.turn

    eval_white_cp = score_to_cp(score, chess.WHITE) if score else 0
    wp_white = score_to_wp(score, chess.WHITE) if score else 0.5
    eval_cp = score_to_cp(score, side) if score else 0

    return jsonify({
        "eval_cp": eval_cp,
        "eval_white_cp": eval_white_cp,
        "wp_white": round(wp_white, 4),
        "best_move_uci": best_move.uci() if best_move else "",
        "best_move_san": board.san(best_move) if best_move else "",
        "depth": depth,
    })


# ============================================================
# /api/puzzle - serves a random puzzle from the local Lichess DB
# ============================================================
DIFFICULTY_BANDS = {
    "easy":   (600, 1200),
    "medium": (1200, 1700),
    "hard":   (1700, 2200),
    "expert": (2200, 2800),
}


@app.route("/api/puzzle")
def api_puzzle():
    """Returns a random Lichess puzzle in the requested rating band.

    Query: ?difficulty=easy|medium|hard|expert (default easy)
    Responds 503 if the DB has not been downloaded.
    """
    if not PUZZLE_DB_PATH.exists():
        return jsonify({
            "error": "Puzzle DB not found. Run: python download_puzzles.py",
            "missing_db": True,
        }), 503

    difficulty = (request.args.get("difficulty") or "easy").lower()
    band = DIFFICULTY_BANDS.get(difficulty, DIFFICULTY_BANDS["easy"])

    try:
        conn = sqlite3.connect(PUZZLE_DB_PATH)
        conn.row_factory = sqlite3.Row
        # Count puzzles in the band so we can pick a uniformly random OFFSET.
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM puzzles WHERE rating BETWEEN ? AND ?",
            band,
        )
        n = cur.fetchone()["n"]
        if n == 0:
            return jsonify({"error": "No puzzle in the requested band"}), 404
        offset = random.randint(0, n - 1)
        cur = conn.execute(
            "SELECT id, fen, moves, rating, themes "
            "FROM puzzles WHERE rating BETWEEN ? AND ? "
            "LIMIT 1 OFFSET ?",
            (*band, offset),
        )
        row = cur.fetchone()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"puzzle DB error: {exc}"}), 500

    return jsonify({
        "id": row["id"],
        "fen": row["fen"],
        "moves": row["moves"].split(),  # array of UCI strings
        "rating": row["rating"],
        "themes": (row["themes"] or "").split(),
        "difficulty": difficulty,
    })


@app.route("/api/health")
def api_health():
    return jsonify({
        "ok": True,
        "engine_found": bool(ENGINE_PATH),
        "engine_path": ENGINE_PATH,
        "depth_default": ANALYSIS_DEPTH,
        "eval_depth": EVAL_DEPTH,
        "puzzles_db_present": PUZZLE_DB_PATH.exists(),
    })


@app.teardown_appcontext
def _shutdown_engine(exception=None):
    # We keep the engine alive across requests (singleton). An explicit cleanup
    # only happens at Flask process shutdown.
    pass


if __name__ == "__main__":
    # host 0.0.0.0 -> reachable from other devices on the LAN (phone).
    # debug=False to avoid the double-process startup (incompatible with engine cache).
    try:
        app.run(host="0.0.0.0", port=5000, debug=False)
    finally:
        if _engine is not None:
            try: _engine.quit()
            except Exception: pass
