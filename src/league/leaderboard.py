from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.league.bots import AVOID, BOTS, Bot
from src.scorecard import spec, v2
from src.scorecard.metrics import INSUFFICIENT, NO_EVIDENCE, PASS

LEADERBOARD_HORIZONS = (5, 10, 20, 40)
TRADED = (spec.STATUS_FILLED, spec.STATUS_BLOCKED, spec.STATUS_STRANDED)
GRADED_COLUMNS = ("call_id", "strategy", "model_version", "symbol", "signal_date", "probability", "situations", "horizon", "status",
                  "exit_date", "gross_return", "universe_mean", "baseline_share", "correct", "failure_cause")


def _normalize(graded: pd.DataFrame | None) -> pd.DataFrame:
    if graded is None or graded.empty:
        return pd.DataFrame(columns=list(GRADED_COLUMNS))
    return graded


def cohort_drawdown(graded: pd.DataFrame, horizon: int, session_index: Mapping[date, int]) -> dict[str, Any]:
    frame = graded[graded["status"] != spec.STATUS_DATA_ERROR]
    if frame.empty:
        return {"cohorts": 0, "max_drawdown": None, "final_equity": None}
    traded = frame["status"].isin(TRADED)
    frame = frame.assign(net=np.where(traded, frame["gross_return"].astype(float) - spec.PRIMARY_COST, 0.0))
    by_date = frame.groupby("signal_date")["net"].mean().sort_index()
    equity, peak, worst, last, cohorts = 1.0, 1.0, 0.0, None, 0
    for day, net in by_date.items():
        position = session_index.get(day)
        if position is None or (last is not None and position < last + horizon + 1):
            continue
        last = position
        cohorts += 1
        equity *= 1 + float(net)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return {"cohorts": cohorts, "max_drawdown": round(worst, 4), "final_equity": round(equity, 4)}


def avoid_view(graded: pd.DataFrame) -> pd.DataFrame:
    frame = graded.copy()
    frame["correct"] = ~frame["correct"].astype(bool)
    frame["baseline_share"] = 1 - frame["baseline_share"].astype(float)
    return frame


def bot_cell(
    bot: Bot,
    graded: pd.DataFrame,
    horizon: int,
    session_index: Mapping[date, int],
    live_sessions: Sequence[date],
    tests: int,
) -> dict[str, Any]:
    frame = graded[graded["horizon"] == horizon]
    sessions = len(live_sessions)
    calls_per_date = frame.groupby("signal_date").size() if not frame.empty else pd.Series(dtype=int)
    coverage = {
        "live_sessions": sessions,
        "sessions_with_calls": int(len(calls_per_date)),
        "share_of_sessions_with_calls": round(len(calls_per_date) / sessions, 4) if sessions else None,
        "calls_per_session": round(float(len(frame)) / sessions, 3) if sessions else None,
    }
    if frame.empty:
        return {"bot": bot.name, "version": bot.version, "side": bot.side, "horizon": horizon, "primary": horizon in bot.primary_horizons,
                "calls": 0, "verdict": INSUFFICIENT, "coverage": coverage}
    scored = avoid_view(frame) if bot.side == AVOID else frame
    cell = v2.cell_metrics_v2(scored, horizon, session_index, max(sessions, 1), tests)
    if bot.side == AVOID and cell.get("graded"):
        graded_rows = frame[frame["status"] != spec.STATUS_DATA_ERROR]
        traded = graded_rows["status"].isin(TRADED).to_numpy()
        gross = graded_rows["gross_return"].to_numpy(dtype=float)
        universe = graded_rows["universe_mean"].to_numpy(dtype=float)
        trail = float(np.nanmean(np.where(traded, gross - spec.PRIMARY_COST - universe, 0.0)))
        cell["avoided_excess_expectancy_1pct"] = round(trail, 5)
        cell["gates"]["expectancy_all_costs"] = True
        cell["gates"]["excess_expectancy_1pct"] = trail < 0
        if not cell["gates"]["sample"]:
            cell["verdict"] = INSUFFICIENT
        else:
            cell["verdict"] = PASS if all(cell["gates"].values()) else NO_EVIDENCE
    claimed = cell.get("calibration", {}).get("claimed")
    return {
        "bot": bot.name,
        "version": bot.version,
        "side": bot.side,
        "horizon": horizon,
        "primary": horizon in bot.primary_horizons,
        "calls": cell.get("calls", 0),
        "graded": cell.get("graded", 0),
        "win_rate": cell.get("win_rate"),
        "baseline": cell.get("baseline"),
        "edge": cell.get("edge"),
        "edge_lower_90": cell.get("edge_lower_90"),
        "edge_lower_penalized": cell.get("edge_lower_penalized"),
        "expectancy": cell.get("expectancy"),
        "excess_expectancy_1pct": cell.get("excess_expectancy_1pct"),
        "avoided_excess_expectancy_1pct": cell.get("avoided_excess_expectancy_1pct"),
        "calibration": cell.get("calibration") if claimed else "uncalibrated: no probability stated, none may be shown",
        "drawdown": cohort_drawdown(frame, horizon, session_index),
        "coverage": coverage,
        "distinct_dates": cell.get("distinct_dates"),
        "independent_windows": cell.get("independent_windows"),
        "min_windows": cell.get("min_windows"),
        "folds": cell.get("folds"),
        "gates": cell.get("gates"),
        "penalty_tests": tests,
        "verdict": cell.get("verdict", INSUFFICIENT),
    }


def leaderboard(
    graded: pd.DataFrame,
    live_sessions: Sequence[date],
    tests: int,
    bots: Sequence[Bot] = BOTS,
    horizons: Sequence[int] = LEADERBOARD_HORIZONS,
) -> dict[int, list[dict[str, Any]]]:
    graded = _normalize(graded)
    session_index = {day: i for i, day in enumerate(live_sessions)}
    board: dict[int, list[dict[str, Any]]] = {}
    for horizon in horizons:
        rows = []
        for bot in bots:
            frame = graded[(graded["strategy"] == bot.name) & (graded["model_version"] == bot.version)]
            rows.append(bot_cell(bot, frame, horizon, session_index, live_sessions, tests))
        rows.sort(key=lambda r: (r.get("edge") is None, -(r.get("edge") or 0.0)))
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        board[horizon] = rows
    return board


def suspended(graded: pd.DataFrame, bot: Bot, sessions: Sequence[date]) -> dict[int, bool]:
    graded = _normalize(graded)
    out = {}
    for horizon in bot.primary_horizons:
        frame = graded[(graded["strategy"] == bot.name) & (graded["model_version"] == bot.version)]
        if frame.empty:
            out[horizon] = False
            continue
        scored = avoid_view(frame) if bot.side == AVOID else frame
        monitor = v2.rolling_monitor_v2(scored, horizon, sessions)
        if monitor.empty:
            out[horizon] = False
            continue
        flag = monitor["kill_edge"] | monitor["kill_brier"] if bot.side == AVOID else monitor["suspend"]
        out[horizon] = bool(flag.iloc[-1])
    return out


def markdown(board: Mapping[int, list[dict[str, Any]]], as_of: date) -> str:
    lines = [f"# Paper-bot league, {as_of.isoformat()}", ""]
    for horizon, rows in board.items():
        lines += [f"## {horizon} sessions", "",
                  "| Rank | Bot | Side | Calls | Win | Baseline | Edge | Lower 90 | Exp. 1% | Excess 1% | Max DD | Coverage | Verdict |",
                  "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
        for r in rows:
            exp = (r.get("expectancy") or {}).get("1.0%")
            excess = r.get("avoided_excess_expectancy_1pct") if r["side"] == AVOID else r.get("excess_expectancy_1pct")
            dd = (r.get("drawdown") or {}).get("max_drawdown")
            cov = r["coverage"].get("share_of_sessions_with_calls")

            def f(x: Any) -> str:
                return "-" if x is None else f"{x:+.4f}" if isinstance(x, float) else str(x)

            lines.append(f"| {r['rank']} | {r['bot']} {r['version']}{' *' if r['primary'] else ''} | {r['side']} | {r.get('graded', 0)} | "
                         f"{f(r.get('win_rate'))} | {f(r.get('baseline'))} | {f(r.get('edge'))} | {f(r.get('edge_lower_90'))} | "
                         f"{f(exp)} | {f(excess)} | {f(dd)} | {f(cov)} | {r['verdict']} |")
        lines.append("")
    lines.append("`*` marks a declared primary horizon. Avoid edges are (buy baseline − buy correctness). No bot states a probability, so calibration is \"uncalibrated\".")
    return "\n".join(lines) + "\n"
