from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import NormalDist
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.backtest.stats import clustered_mean_interval
from src.scorecard import spec

PASS = "PASS"
NO_EVIDENCE = "NO EVIDENCE"
INSUFFICIENT = "INSUFFICIENT SAMPLE"


def one_sided_lower(values_by_cluster: Mapping[Any, Sequence[float]], alpha: float) -> float | None:
    interval = clustered_mean_interval(values_by_cluster, 2 * alpha)
    return None if interval is None else interval.low


def penalty_tests(strategy_versions: int, cells: int) -> int:
    return max(1, strategy_versions) * max(1, cells)


def reliability(probability: np.ndarray, correct: np.ndarray) -> tuple[list[dict[str, Any]], float | None]:
    buckets = np.clip(np.floor(probability * 10), 0, 9).astype(int)
    rows = []
    weighted = 0.0
    weight = 0
    for b in sorted(set(buckets.tolist())):
        mask = buckets == b
        n = int(mask.sum())
        stated = float(probability[mask].mean())
        observed = float(correct[mask].mean())
        rows.append({"bucket": f"{b / 10:.1f}-{(b + 1) / 10:.1f}", "calls": n, "stated": round(stated, 4), "observed": round(observed, 4)})
        if n >= spec.RELIABILITY_MIN_BUCKET:
            weighted += n * abs(stated - observed)
            weight += n
    return rows, (weighted / weight if weight else None)


def top_bottom_spread(frame: pd.DataFrame) -> float | None:
    spreads = []
    for _, group in frame[frame["score"].notna() & frame["gross_return"].notna()].groupby("signal_date"):
        if len(group) < 10 or group["score"].nunique() < 2:
            continue
        ordered = group.sort_values(["score", "symbol"], ascending=[False, True])
        k = max(1, int(np.ceil(len(ordered) * 0.2)))
        spreads.append(ordered["gross_return"].head(k).mean() - ordered["gross_return"].tail(k).mean())
    return float(np.mean(spreads)) if spreads else None


def cell_metrics(
    frame: pd.DataFrame,
    horizon: int,
    session_index: Mapping[date, int],
    sessions: int,
    tests: int,
    leaky: bool = False,
) -> dict[str, Any]:
    calls = int(len(frame))
    if calls == 0:
        return {"calls": 0, "verdict": INSUFFICIENT}
    data_errors = int((frame["status"] == spec.STATUS_DATA_ERROR).sum())
    graded = frame[frame["status"] != spec.STATUS_DATA_ERROR].copy()
    n = int(len(graded))
    if n == 0:
        return {"calls": calls, "data_errors": data_errors, "verdict": INSUFFICIENT}
    graded["signal_index"] = graded["signal_date"].map(session_index)
    graded["block"] = graded["signal_index"] // (horizon + 1)
    graded["fold"] = graded["signal_index"] * spec.GATE_FOLDS // sessions
    correct = graded["correct"].astype(float).to_numpy()
    win = float(correct.mean())
    baseline = float(graded["baseline_share"].mean())
    by_block: dict[Any, list[float]] = defaultdict(list)
    for block, value in zip(graded["block"], correct):
        by_block[int(block)].append(float(value))
    plain_alpha = spec.ONE_SIDED_ALPHA
    penalized_alpha = spec.ONE_SIDED_ALPHA / tests
    lower_plain = one_sided_lower(by_block, plain_alpha)
    lower_penalized = one_sided_lower(by_block, penalized_alpha)
    traded = graded["status"].isin((spec.STATUS_FILLED, spec.STATUS_BLOCKED, spec.STATUS_STRANDED))
    gross = graded["gross_return"].where(traded)
    expectancy = {}
    for cost in spec.COSTS:
        net = np.where(traded, gross - cost, 0.0)
        expectancy[f"{cost * 100:.1f}%"] = float(np.nanmean(net))
    probability = graded["probability"].astype(float).to_numpy()
    brier = float(np.mean((probability - correct) ** 2))
    buckets, ece = reliability(probability, correct)
    mean_gap = abs(float(probability.mean()) - win)
    folds = []
    for fold in range(spec.GATE_FOLDS):
        group = graded[graded["fold"] == fold]
        edge = float((group["correct"].astype(float) - group["baseline_share"]).mean()) if len(group) else None
        folds.append({"fold": fold + 1, "calls": int(len(group)), "edge": None if edge is None else round(edge, 4)})
    folds_positive = sum(1 for f in folds if f["edge"] is not None and f["edge"] > 0)
    dates = int(graded["signal_date"].nunique())
    windows = int(graded["block"].nunique())
    causes = graded.loc[~graded["correct"].astype(bool), "failure_cause"].value_counts(normalize=True).round(4).to_dict()
    statuses = frame["status"].value_counts().to_dict()
    gates = {
        "win_rate": win >= spec.GATE_WIN_RATE,
        "lower_bound": (lower_plain or 0) >= spec.GATE_LOWER_BOUND and (lower_penalized or 0) >= spec.GATE_LOWER_BOUND,
        "edge": win - baseline >= spec.GATE_EDGE,
        "expectancy_all_costs": all(v > 0 for v in expectancy.values()),
        "folds": folds_positive >= spec.GATE_FOLDS_POSITIVE,
        "sample": n >= spec.GATE_MIN_CALLS and dates >= spec.GATE_MIN_DATES,
        "calibration": ece is not None and ece <= spec.GATE_ECE and mean_gap <= spec.GATE_MEAN_CALIBRATION,
        "data_quality": data_errors / calls <= spec.GATE_MAX_DATA_ERROR and not leaky,
    }
    if not gates["sample"]:
        verdict = INSUFFICIENT
    elif all(gates.values()):
        verdict = PASS
    else:
        verdict = NO_EVIDENCE
    universe_excess = (gross - graded["universe_mean"]).where(traded)
    nepse_excess = (gross - spec.PRIMARY_COST - graded["nepse_return"]).where(traded)
    return {
        "calls": calls,
        "graded": n,
        "data_errors": data_errors,
        "status": statuses,
        "coverage": round(float((graded["status"] == spec.STATUS_FILLED).mean()), 4),
        "distinct_dates": dates,
        "independent_windows": windows,
        "win_rate": round(win, 4),
        "baseline": round(baseline, 4),
        "edge": round(win - baseline, 4),
        "lower_90": None if lower_plain is None else round(lower_plain, 4),
        "lower_penalized": None if lower_penalized is None else round(lower_penalized, 4),
        "penalty_tests": tests,
        "expectancy": {k: round(v, 5) for k, v in expectancy.items()},
        "excess_vs_universe_mean": None if universe_excess.isna().all() else round(float(universe_excess.mean()), 5),
        "excess_vs_nepse_1pct": None if nepse_excess.isna().all() else round(float(nepse_excess.mean()), 5),
        "brier": round(brier, 4),
        "ece": None if ece is None else round(ece, 4),
        "mean_stated_probability": round(float(probability.mean()), 4),
        "reliability": buckets,
        "folds": folds,
        "folds_positive": folds_positive,
        "top_minus_bottom": top_bottom_spread(graded) if spec.horizon_class(horizon) == "long" else None,
        "failure_causes": causes,
        "implausible": win >= spec.IMPLAUSIBLE_WIN_RATE and n >= spec.GATE_MIN_CALLS,
        "gates": gates,
        "verdict": verdict,
    }


def matrix_column(
    graded: pd.DataFrame,
    horizon: int,
    session_index: Mapping[date, int],
    sessions: int,
    strategy_versions: int = 1,
    leaky: bool = False,
) -> dict[str, dict[str, Any]]:
    tests = penalty_tests(strategy_versions, len(spec.SITUATIONS) * len(spec.HORIZONS))
    exploded = graded[graded["horizon"] == horizon].explode("situations")
    return {
        situation: cell_metrics(exploded[exploded["situations"] == situation], horizon, session_index, sessions, tests, leaky)
        for situation in spec.SITUATIONS
    }


def build_matrix(
    graded: pd.DataFrame,
    session_index: Mapping[date, int],
    sessions: int,
    strategy_versions: int = 1,
    leaky: bool = False,
) -> dict[str, dict[str, Any]]:
    matrix: dict[str, dict[str, Any]] = {situation: {} for situation in spec.SITUATIONS}
    for horizon in spec.HORIZONS:
        column = matrix_column(graded, horizon, session_index, sessions, strategy_versions, leaky)
        for situation, cell in column.items():
            matrix[situation][str(horizon)] = cell
    return matrix


def rolling_monitor(graded: pd.DataFrame, horizon: int, sessions: Sequence[date]) -> pd.DataFrame:
    frame = graded[(graded["horizon"] == horizon) & (graded["status"] != spec.STATUS_DATA_ERROR)].sort_values("exit_date")
    records = []
    z = NormalDist().inv_cdf(1 - spec.ONE_SIDED_ALPHA)
    for point in range(spec.MONITOR_SESSIONS, len(sessions), spec.MONITOR_STEP):
        as_of = sessions[point]
        matured = frame[frame["exit_date"] <= as_of].tail(spec.MONITOR_CALLS)
        if len(matured) < spec.MONITOR_CALLS:
            continue
        diff = matured["correct"].astype(float) - matured["baseline_share"]
        traded = matured["status"].isin((spec.STATUS_FILLED, spec.STATUS_BLOCKED, spec.STATUS_STRANDED))
        net = np.where(traded, matured["gross_return"] - spec.PRIMARY_COST, 0.0)
        se_edge = diff.std(ddof=1) / np.sqrt(len(diff))
        se_net = np.std(net, ddof=1) / np.sqrt(len(net))
        brier = float(((matured["probability"] - matured["correct"].astype(float)) ** 2).mean())
        records.append(
            {
                "as_of": as_of,
                "edge": float(diff.mean()),
                "edge_lower": float(diff.mean() - z * se_edge),
                "expectancy": float(np.mean(net)),
                "expectancy_upper": float(np.mean(net) + z * se_net),
                "brier": brier,
            }
        )
    monitor = pd.DataFrame.from_records(records)
    if monitor.empty:
        return monitor
    edge_negative = monitor["edge_lower"] < 0
    monitor["kill_edge"] = edge_negative & edge_negative.shift(1, fill_value=False)
    monitor["kill_expectancy"] = (monitor["expectancy"] < 0) & (monitor["expectancy_upper"] < 0)
    monitor["kill_brier"] = monitor["brier"] > 0.25
    monitor["suspend"] = monitor["kill_edge"] | monitor["kill_expectancy"] | monitor["kill_brier"]
    return monitor
