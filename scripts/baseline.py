"""Baseline tooling. Subcommands: fetch, fixtures, replay, timing."""
import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import chess  # noqa: E402
import chess.engine  # noqa: E402

import chess_api  # noqa: E402
from analyzer import GameAnalyzer  # noqa: E402
from tests.engine_replay import (RecordingEngine, analyze_replayed, patch_popen,  # noqa: E402
                                 read_engine_json, write_engine_json)

FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
ENGINE_PATH = PROJECT_ROOT / "engine" / ("stockfish.exe" if os.name == "nt" else "stockfish")
DEPTH = 15
HASH_MB = 128


def _game_id(game: dict) -> str:
    return game["url"].rstrip("/").rsplit("/", 1)[-1]


def find_games(archives: Iterable[dict], ids: set[str]) -> dict[str, dict]:
    return {gid: g for a in archives for g in a.get("games", [])
            if (gid := _game_id(g)) in ids}


def check_ratings(game_id: str, game: dict) -> None:
    for color in ("white", "black"):
        m = re.search(rf'\[{color.capitalize()}Elo "(\d+)"\]', game.get("pgn", ""))
        header = int(m.group(1)) if m else None
        if header != game[color]["rating"]:
            raise ValueError(f"{game_id}: {color} PGN rating {header} != API rating "
                             f"{game[color]['rating']}")


def save_pgn(out_dir: Path, game_id: str, pgn: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{game_id}.pgn"
    path.write_text(pgn, encoding="utf-8", newline="\n")
    return path


def fixture_ids() -> list[str]:
    return sorted(p.stem for p in FIXTURES_DIR.glob("*.pgn"))


def fetch_games(username: str, ids: list[str]) -> dict[str, dict]:
    wanted = set(ids)
    found: dict[str, dict] = {}
    for url in reversed(chess_api.get_archives(username)):
        found.update(find_games([chess_api._get(url)], wanted - found.keys()))
        if wanted <= found.keys():
            break
    missing = sorted(wanted - found.keys())
    if missing:
        raise SystemExit(f"missing ids: {', '.join(missing)}")
    for gid, g in found.items():
        check_ratings(gid, g)
    return found


def cmd_fetch(username: str, ids: list[str]) -> None:
    for gid, g in fetch_games(username, ids).items():
        print(save_pgn(FIXTURES_DIR, gid, g["pgn"]))


def class_changes(old_moves: list[dict], new_moves: list[dict]) -> list[tuple[int, str, str]]:
    return [(n["ply"], o["classification"], n["classification"])
            for o, n in zip(old_moves, new_moves, strict=True) if o["classification"] != n["classification"]]


def analyze_recorded(pgn: str, threads: int, wdl: bool) -> tuple[dict, list[dict], float, str]:
    real = chess.engine.SimpleEngine.popen_uci
    calls: list[dict] = []
    engines: list[RecordingEngine] = []

    def factory(path):
        engines.append(RecordingEngine(real(str(ENGINE_PATH)), calls, wdl=wdl))
        return engines[0]

    analyzer = GameAnalyzer(str(ENGINE_PATH), depth=DEPTH, threads=threads, hash_mb=HASH_MB)
    with patch_popen(factory):
        start = time.perf_counter()
        result = analyzer.analyze_pgn(pgn)
        elapsed = time.perf_counter() - start
    return result, calls, elapsed, engines[0].id["name"]


def _analyzer_commit() -> str:
    git = lambda *args: subprocess.run(["git", *args, "--", "analyzer.py"], cwd=PROJECT_ROOT,
                                       capture_output=True, text=True, check=True).stdout.strip()
    commit = git("log", "-1", "--format=%H")
    return f"{commit}-dirty" if git("status", "--porcelain") else commit


def generate_fixture(game_id: str, out_dir: Path = FIXTURES_DIR, wdl: bool = True) -> dict:
    pgn = (FIXTURES_DIR / f"{game_id}.pgn").read_text(encoding="utf-8")
    result, calls, elapsed, engine_name = analyze_recorded(pgn, threads=1, wdl=wdl)
    commit = _analyzer_commit()
    meta = {"date": datetime.now().astimezone().isoformat(timespec="seconds"), "engine": engine_name,
            "threads": 1, "hash_mb": HASH_MB, "depth": DEPTH, "wdl": wdl,
            "python_chess": chess.__version__, "analyzer_commit": commit, "elapsed_s": round(elapsed, 1)}
    out_dir.mkdir(parents=True, exist_ok=True)
    write_engine_json(out_dir / f"{game_id}.engine.json", meta, calls)
    _write_analysis(out_dir / f"{game_id}.analysis.json", result)
    return meta


def _write_analysis(path: Path, result: dict) -> None:
    path.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")


def cmd_fixtures(ids: list[str]) -> None:
    for gid in ids or fixture_ids():
        meta = generate_fixture(gid)
        data = json.loads((FIXTURES_DIR / f"{gid}.analysis.json").read_text(encoding="utf-8"))
        print(f"{gid}: {len(data['moves'])} ply, {meta['elapsed_s']} s", flush=True)


def cmd_replay(ids: list[str]) -> None:
    for gid in ids or fixture_ids():
        pgn = (FIXTURES_DIR / f"{gid}.pgn").read_text(encoding="utf-8")
        path = FIXTURES_DIR / f"{gid}.analysis.json"
        old = json.loads(path.read_text(encoding="utf-8"))
        try:
            new = analyze_replayed(pgn, read_engine_json(FIXTURES_DIR / f"{gid}.engine.json"))
        except KeyError as e:
            raise SystemExit(f"{gid}: {e}") from None
        changes = class_changes(old["moves"], new["moves"])
        for ply, before, after in changes:
            print(f"{gid} ply {ply}: {before} -> {after}")
        if old["accuracy"] != new["accuracy"]:
            print(f"{gid} accuracy {old['accuracy']} -> {new['accuracy']}")
        print(f"{gid}: {len(changes)} changes")
        _write_analysis(path, new)


def engine_seconds(calls: list[dict]) -> float:
    return sum(max((i.get("time", 0) for i in c["infos"]), default=0) for c in calls)


def timing_row(fixture: dict, fixture_meta: dict, runs: list[dict]) -> dict:
    r1, r2 = runs
    return {"plies": len(fixture["moves"]), "t1": r1["elapsed_s"], "t2": r2["elapsed_s"],
            "s_per_ply": (r1["elapsed_s"] + r2["elapsed_s"]) / 2 / len(fixture["moves"]),
            "engine_share": sum(r["engine_s"] / r["elapsed_s"] for r in runs) / 2,
            "changed_runs": len(class_changes(r1["result"]["moves"], r2["result"]["moves"])),
            "changed_vs_fixture": [len(class_changes(fixture["moves"], r["result"]["moves"])) for r in runs],
            "acc_delta": {c: round(r1["result"]["accuracy"][c] - r2["result"]["accuracy"][c], 2)
                          for c in ("white", "black")},
            "fixture_s": fixture_meta["elapsed_s"]}


def _cpu_name() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except (ImportError, OSError):
        return platform.processor()


def _new_measurement(name: str) -> Path:
    path = PROJECT_ROOT / "docs" / "measurements" / name
    if path.exists():
        raise SystemExit(f"{path} already exists: pick another --label")
    return path


def _write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except FileExistsError:
        raise SystemExit(f"{path} already exists: pick another --label") from None


def cmd_timing(power: str, label: str) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    out = _new_measurement(f"{today}-tempi-{label}.md")
    ids = fixture_ids()
    pgns = {g: (FIXTURES_DIR / f"{g}.pgn").read_text(encoding="utf-8") for g in ids}
    # read the fixtures before the laps, so a game without them fails now and not after an hour
    fixtures = {g: json.loads((FIXTURES_DIR / f"{g}.analysis.json").read_text(encoding="utf-8")) for g in ids}
    metas = {g: read_engine_json(FIXTURES_DIR / f"{g}.engine.json")["meta"] for g in ids}
    runs: dict[str, list[dict]] = {g: [] for g in ids}
    engine_name = ""
    for lap in (1, 2):
        for gid in ids:
            result, calls, elapsed, engine_name = analyze_recorded(pgns[gid], threads=2, wdl=False)
            runs[gid].append({"elapsed_s": elapsed, "engine_s": engine_seconds(calls), "result": result})
            print(f"giro {lap} {gid}: {elapsed:.1f} s", flush=True)
    rows = [(gid, timing_row(fixtures[gid], metas[gid], runs[gid])) for gid in ids]
    fixture_total = sum(m["elapsed_s"] for m in metas.values())
    lines = [f"# Tempi e rumore, {label} ({today})", "",
             f"- Data: {today}", f"- Sistema: {platform.platform()}", f"- CPU: {_cpu_name()}",
             f"- Thread logici: {os.cpu_count()}", f"- Alimentazione: {power}",
             f"- Stockfish: {engine_name}", f"- python-chess: {chess.__version__}",
             f"- Commit di analyzer.py: {_analyzer_commit()}",
             f"- Configurazione: Threads=2, Hash={HASH_MB}, depth {DEPTH}, due giri completi", "",
             "| Partita | Semimosse | Giro 1 (s) | Giro 2 (s) | s/semimossa | Quota Stockfish "
             "| Classi diverse giro 1 vs 2 | Classi diverse vs fixture (giro 1, giro 2) "
             "| Delta accuracy bianco | Delta accuracy nero |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for gid, r in rows:
        lines.append(f"| {gid} | {r['plies']} | {r['t1']:.1f} | {r['t2']:.1f} | {r['s_per_ply']:.2f} "
                     f"| {r['engine_share']:.0%} | {r['changed_runs']} "
                     f"| {r['changed_vs_fixture'][0]}, {r['changed_vs_fixture'][1]} "
                     f"| {r['acc_delta']['white']:+.2f} | {r['acc_delta']['black']:+.2f} |")
    rs = [r for _, r in rows]
    plies = sum(r["plies"] for r in rs)
    t1, t2 = sum(r["t1"] for r in rs), sum(r["t2"] for r in rs)
    lines.append(f"| Totale | {plies} | {t1:.1f} | {t2:.1f} | {(t1 + t2) / 2 / plies:.2f} "
                 f"| {sum(r['engine_share'] for r in rs) / len(rs):.0%} | {sum(r['changed_runs'] for r in rs)} "
                 f"| {sum(r['changed_vs_fixture'][0] for r in rs)}, {sum(r['changed_vs_fixture'][1] for r in rs)} "
                 f"| | |")
    lines += ["", f"Generazione delle fixture (Threads=1): {fixture_total:.1f} s", ""]
    _write_new(out, "\n".join(lines))
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fetch", help="download the PGNs of the given games")
    p.add_argument("username")
    p.add_argument("ids", nargs="+")
    for name, help_ in (("fixtures", "record Stockfish and freeze the analysis"),
                        ("replay", "regenerate analysis.json from the recorded searches")):
        sub.add_parser(name, help=help_).add_argument("ids", nargs="*")
    p = sub.add_parser("timing", help="measure speed and run-to-run noise")
    p.add_argument("--power", required=True, choices=("rete", "batteria"))
    p.add_argument("--label", required=True, help="goes in the file name, e.g. fase1")
    args = parser.parse_args(argv)
    if args.command == "fetch":
        cmd_fetch(args.username, args.ids)
    elif args.command == "timing":
        print(cmd_timing(args.power, args.label))
    elif args.command == "fixtures":
        cmd_fixtures(args.ids)
    else:
        cmd_replay(args.ids)


if __name__ == "__main__":
    main()
