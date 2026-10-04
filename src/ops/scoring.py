from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from src.league import run as league_run
from src.league.leaderboard import leaderboard, markdown
from src.scorecard import daily
from src.tips import run as tips_run

logger = logging.getLogger(__name__)


def grade(engine: Any, state: dict[str, Any]) -> dict[str, Any]:
    out = {"league": league_run.grade_matured(engine, state, False), "model_v0": daily.grade_matured(engine, state, False)}
    pairs = tips_run.tracked_pairs(engine)
    out["tips"] = league_run.grade_matured(engine, state, False, pairs=pairs) if pairs else "no tip calls"
    return out


def metrics(engine: Any, state: dict[str, Any], as_of: Any, markdown_dir: Path | None) -> dict[str, Any]:
    graded = league_run.load_live_graded(engine)
    live = [d for d in state["panel"].sessions if d >= league_run.LEAGUE_START]
    board = leaderboard(graded, live, league_run.penalty_tests(engine))
    out: dict[str, Any] = {"league_rows_written": league_run.write_leaderboard(engine, board, as_of),
                           "league_top": {h: [(r["bot"], r.get("graded"), r.get("edge"), r["verdict"]) for r in rows] for h, rows in board.items()}}
    if markdown_dir is not None:
        markdown_dir.mkdir(parents=True, exist_ok=True)
        path = markdown_dir / f"leaderboard_{as_of.isoformat()}.md"
        path.write_text(markdown(board, as_of))
        out["markdown"] = str(path)
    tip_boards = tips_run.boards(engine, state)
    out["tip_rows_written"] = sum(league_run.write_leaderboard(engine, b, as_of, table="tip_leaderboard")
                                  for b in (tip_boards["aggregate"], tip_boards["channels"]) if b)
    out["tip_targets"] = tips_run.target_report(engine, state)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Grade matured live calls and write scorecard leaderboards")
    parser.add_argument("command", choices=("grade", "metrics"))
    parser.add_argument("--markdown-dir", type=Path)
    args = parser.parse_args()
    from src.database.connection import engine

    state, latest = tips_run.live_state()
    if args.command == "grade":
        report = grade(engine, state)
    else:
        report = metrics(engine, state, latest, args.markdown_dir)
    print(json.dumps({"latest_session": latest.isoformat(), **report}, indent=2, default=str))


if __name__ == "__main__":
    main()
