"""
PHASE 4 - Flask server.

Endpoints:
  GET  /                           -> dashboard (username form + games list)
  GET  /api/games?username=...     -> JSON of the games for the current month
  GET  /api/game/<u>/<id>          -> single PGN from cache
  GET  /api/engine                 -> {cores, threads: {min, max, default},
                                        depth: {min, max, default}} of the host that runs
                                        Stockfish. Used by the dashboard engine panel.
  POST /api/analyze                -> {pgn, white_elo, black_elo, depth?, threads?} -> full
                                        analysis; 400 if depth or threads is not an integer
                                        inside the range of /api/engine
  POST /api/eval                   -> {fen, depth?} -> {eval_cp, eval_white_cp,
                                        wp_white, best_move_uci, best_move_san}
                                        Used by the frontend "variation explorer".
                                        400 if depth is not an integer between 10 and 22
                                        or the FEN is not a valid position
  GET  /api/puzzle?difficulty=...  -> single random Lichess puzzle by rating band
  GET  /review                     -> page with chessboard + review sidebar

Downloaded games are cached in memory to avoid hammering the upstream API on
every "Analyze" click.

Engine: Stockfish is kept alive as a GLOBAL SINGLETON (popen once) to avoid
startup on every /api/eval. Access is serialized via a Lock because
chess.engine.SimpleEngine is not thread-safe. After an engine error /api/eval
drops it, and the next request opens a new one.
"""

from __future__ import annotations

import os
import random
import sqlite3
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional

import chess
import chess.engine
from flask import Flask, jsonify, render_template, request

from analyzer import GameAnalyzer, score_to_cp, score_to_wp
from chess_api import get_current_month_games

# When packaged with PyInstaller, bundled resources live in sys._MEIPASS.
# In dev, BASE_DIR is the source folder. USER_DATA_DIR is the folder next
# to the .exe (or the source folder in dev), so the user can drop their own
# engine/puzzles.db there without rebuilding the .exe.
_FROZEN = getattr(sys, "frozen", False)
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
USER_DATA_DIR = Path(sys.executable).parent if _FROZEN else Path(__file__).parent

# Look for the Stockfish executable: bundled location first, then next to the .exe.
ENGINE_CANDIDATES = [
    BASE_DIR / "engine" / "stockfish.exe",
    BASE_DIR / "engine" / "stockfish",
    USER_DATA_DIR / "engine" / "stockfish.exe",
    USER_DATA_DIR / "engine" / "stockfish",
]
ENGINE_PATH = next((str(p) for p in ENGINE_CANDIDATES if p.exists()), None)

# Range of the review depth the user may choose: /api/analyze checks it, and /api/eval
# checks a depth the request sends.
DEPTH_MIN, DEPTH_MAX = 10, 22

# Depth for "fast" evaluations of the variation explorer: lower to keep UI
# latency low when the user explores alternative moves.
EVAL_DEPTH = int(os.environ.get("EVAL_DEPTH", "12"))

# Puzzles DB: prefer the one next to the .exe (user-installable post-install)
# over the bundled one (which normally isn't shipped due to size).
_PUZZLE_DB_CANDIDATES = [
    USER_DATA_DIR / "engine" / "puzzles.db",
    BASE_DIR / "engine" / "puzzles.db",
]
PUZZLE_DB_PATH = next(
    (p for p in _PUZZLE_DB_CANDIDATES if p.exists()),
    USER_DATA_DIR / "engine" / "puzzles.db",
)

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)

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


# ============================================================
# /api/engine - what the host that runs Stockfish allows
# ============================================================
def _host_cores() -> int:
    """Logical CPUs the engine host may use (os.process_cpu_count exists from Python 3.13)."""
    return (getattr(os, "process_cpu_count", None) or os.cpu_count)() or 1


def engine_limits(cores: int) -> dict:
    """Range and default of the review depth and of the Stockfish Threads."""
    # The default is 1 Thread: more of them made depth-15 searches slower on the test PC.
    # REVIEW_DEPTH (documented) replaces the default depth, clamped like any choice.
    try:
        depth = int(os.environ["REVIEW_DEPTH"])
    except (KeyError, ValueError):  # unset, empty or not a number: use the computed default
        depth = 12 if cores <= 2 else 15
    return {
        "cores": cores,
        "threads": {"min": 1, "max": cores, "default": 1},
        "depth": {"min": DEPTH_MIN, "max": DEPTH_MAX,
                  "default": max(DEPTH_MIN, min(DEPTH_MAX, depth))},
    }


@app.route("/api/engine")
def api_engine():
    """Limits and defaults of the engine host, for the dashboard panel and the review page."""
    return jsonify(engine_limits(_host_cores()))


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

    limits = engine_limits(_host_cores())
    chosen = {}
    for name in ("depth", "threads"):
        low, high = limits[name]["min"], limits[name]["max"]
        value = payload.get(name, limits[name]["default"])
        # type() is int: a JSON true is a bool, and bool is a subclass of int.
        if type(value) is not int or not low <= value <= high:
            return jsonify({"error": f"{name} must be an integer between {low} and {high}"}), 400
        chosen[name] = value

    white_elo = payload.get("white_elo")
    black_elo = payload.get("black_elo")
    try:
        analyzer = GameAnalyzer(ENGINE_PATH, **chosen)
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
    global _engine
    payload = request.get_json(silent=True) or {}
    fen = payload.get("fen")
    if not fen:
        return jsonify({"error": "missing 'fen' field"}), 400
    depth = payload.get("depth", EVAL_DEPTH)
    # Only a depth the request sends: EVAL_DEPTH comes from the environment and may lie outside the range.
    if "depth" in payload and (type(depth) is not int or not DEPTH_MIN <= depth <= DEPTH_MAX):
        return jsonify({"error": f"depth must be an integer between {DEPTH_MIN} and {DEPTH_MAX}"}), 400

    try:
        board = chess.Board(fen)
    except ValueError as exc:
        return jsonify({"error": f"invalid FEN: {exc}"}), 400
    if not board.is_valid():
        return jsonify({"error": f"invalid FEN: {board.status().name}"}), 400

    with _engine_lock:
        try:
            info = _get_engine().analyse(board, chess.engine.Limit(depth=depth))
        except Exception as exc:  # noqa: BLE001
            # A broken engine stays broken: drop it, so the next request opens a new one.
            if _engine is not None:
                try:
                    _engine.quit()
                except Exception:  # noqa: BLE001
                    pass
            _engine = None
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
# Low bound included, high bound excluded: 2801 keeps the top rating, 2800, in master.
DIFFICULTY_BANDS = {
    "beginner":     (400, 800),
    "easy":         (800, 1200),
    "intermediate": (1200, 1600),
    "advanced":     (1600, 2000),
    "expert":       (2000, 2400),
    "master":       (2400, 2801),
}


@app.route("/api/puzzle")
def api_puzzle():
    """Returns a random Lichess puzzle in the requested rating band.

    Query: ?difficulty=beginner|easy|intermediate|advanced|expert|master (anything else: easy)
    Responds 503 if the DB has not been downloaded.
    """
    if not PUZZLE_DB_PATH.exists():
        return jsonify({
            "error": "Puzzle DB not found. Run: python scripts/download_puzzles.py",
            "missing_db": True,
        }), 503

    difficulty = (request.args.get("difficulty") or "easy").lower()
    band = DIFFICULTY_BANDS.get(difficulty, DIFFICULTY_BANDS["easy"])

    try:
        conn = sqlite3.connect(PUZZLE_DB_PATH)
        conn.row_factory = sqlite3.Row
        # Count puzzles in the band so we can pick a uniformly random OFFSET.
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM puzzles WHERE rating >= ? AND rating < ?",
            band,
        )
        n = cur.fetchone()["n"]
        if n == 0:
            return jsonify({"error": "No puzzle in the requested band"}), 404
        offset = random.randint(0, n - 1)
        cur = conn.execute(
            "SELECT id, fen, moves, rating, themes "
            "FROM puzzles WHERE rating >= ? AND rating < ? "
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
    })


@app.route("/api/health")
def api_health():
    return jsonify({
        "ok": True,
        "engine_found": bool(ENGINE_PATH),
        "engine_path": ENGINE_PATH,
        "depth_default": engine_limits(_host_cores())["depth"]["default"],
        "eval_depth": EVAL_DEPTH,
        "puzzles_db_present": PUZZLE_DB_PATH.exists(),
    })


@app.teardown_appcontext
def _shutdown_engine(exception=None):
    # We keep the engine alive across requests (singleton). An explicit cleanup
    # only happens at Flask process shutdown.
    pass


def _open_browser_when_ready(url: str = "http://127.0.0.1:5000", delay: float = 1.2) -> None:
    """Open the default browser after a short delay so Flask has time to bind."""
    threading.Timer(delay, lambda: webbrowser.open(url)).start()


if __name__ == "__main__":
    # host 0.0.0.0 -> reachable from other devices on the LAN (phone).
    # debug=False to avoid the double-process startup (incompatible with engine cache).
    if _FROZEN:
        # Packaged .exe: auto-open the browser so the user doesn't have to type a URL.
        _open_browser_when_ready()
        print("Chess Review is running. The browser will open shortly.")
        print("LAN URL: http://<your-pc-ip>:5000  (open this on your phone)")
    try:
        app.run(host="0.0.0.0", port=5000, debug=False)
    finally:
        if _engine is not None:
            try: _engine.quit()
            except Exception: pass
