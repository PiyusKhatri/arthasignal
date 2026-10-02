from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import NormalDist
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.scorecard import spec
from src.scorecard.grading import (
    BLOCKED,
    FILLED,
    STRANDED,
    UNFILLED,
    Cube,
    Market,
)
from src.scorecard.metrics import INSUFFICIENT, NO_EVIDENCE, PASS, one_sided_lower, reliability

PROTOCOL_VERSION = "accuracy-v2"
GRADE_VERSION = "accuracy-v2"
VARIANT_FAMILY = "scorecard_accuracy_v2"

GATE_EDGE = 0.08
GATE_MIN_CALLS = 150
GATE_MIN_DATES = 60
MIN_WINDOWS = {5: 40, 10: 40, 20: 40, 40: 30, 80: 25, 120: 25, 160: 25, 240: 25}
EXCLUSION_WARNING = 0.10
EVENT_SHARE = 0.5
TOTAL_SESSIONS_2014_2025 = 2435
LOWER_BOUND_EPSILON = 1e-9


def supportable_horizons(sessions: int = TOTAL_SESSIONS_2014_2025) -> dict[int, dict[str, Any]]:
    out = {}
    for horizon, minimum in MIN_WINDOWS.items():
        windows = sessions // (horizon + 1)
        out[horizon] = {"independent_windows": windows, "minimum": minimum, "can_ever_support_a_claim": windows >= minimum}
    return out


def cumulative_event_returns(market: Market) -> dict[str, np.ndarray]:
    log_return = np.log1p(np.nan_to_num(market.panel.total_return, nan=0.0))
    log_return[market.panel.corrupt] = 0.0
    news = market.action_mask | market.spike
    return {
        "down": np.cumsum(market.down_close, axis=1),
        "news": np.cumsum(news, axis=1),
        "lr_circuit": np.cumsum(np.where(market.down_close, log_return, 0.0), axis=1),
        "lr_news": np.cumsum(np.where(news, log_return, 0.0), axis=1),
    }


def _range_sum(cumulative: np.ndarray, rows: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    return cumulative[rows, end] - cumulative[rows, start]


def failure_causes_v2(
    market: Market, cube: Cube, rows: np.ndarray, cols: np.ndarray, cumulative: Mapping[str, np.ndarray]
) -> np.ndarray:
    status = cube.status[rows, cols]
    correct = cube.correct[rows, cols]
    gross = cube.gross[rows, cols]
    entry = np.minimum(cols + 1, market.traded.shape[1] - 1)
    end = np.maximum(cube.exit_index[rows, cols], entry)
    causes = np.full(len(rows), None, dtype=object)
    wrong = (status == FILLED) & ~correct
    with np.errstate(invalid="ignore", divide="ignore"):
        stock = np.log1p(gross)
        market_part = np.log1p(cube.universe_mean[cols])
        sector_part = np.log1p(cube.sector_median[rows, cols]) - np.log1p(cube.universe_median[cols])
        idio_part = stock - np.log1p(cube.sector_median[rows, cols])
    components = np.vstack([np.minimum(np.nan_to_num(market_part), 0), np.minimum(np.nan_to_num(sector_part), 0), np.minimum(np.nan_to_num(idio_part), 0)])
    largest = np.argmin(components, axis=0)
    any_negative = components.min(axis=0) < 0
    circuit_share = _range_sum(cumulative["lr_circuit"], rows, entry, end)
    news_share = _range_sum(cumulative["lr_news"], rows, entry, end)
    idio = np.nan_to_num(idio_part)
    with np.errstate(invalid="ignore"):
        circuit = (idio < 0) & (circuit_share <= EVENT_SHARE * idio)
        news = (idio < 0) & ~circuit & (news_share <= EVENT_SHARE * idio)
    causes[np.isin(status, (UNFILLED, BLOCKED, STRANDED))] = "liquidity"
    causes[wrong & any_negative & (largest == 0)] = "market"
    causes[wrong & any_negative & (largest == 1)] = "sector"
    idio_wrong = wrong & any_negative & (largest == 2)
    causes[idio_wrong & circuit] = "circuit"
    causes[idio_wrong & news] = "news"
    causes[idio_wrong & ~circuit & ~news] = "model"
    causes[wrong & ~any_negative] = "model"
    return causes


def cell_metrics_v2(
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
    excluded = int((frame["status"] == spec.STATUS_DATA_ERROR).sum())
    graded = frame[frame["status"] != spec.STATUS_DATA_ERROR].copy()
    n = int(len(graded))
    if n == 0:
        return {"calls": calls, "excluded": excluded, "verdict": INSUFFICIENT}
    graded["signal_index"] = graded["signal_date"].map(session_index)
    graded["block"] = graded["signal_index"] // (horizon + 1)
    graded["fold"] = graded["signal_index"] * spec.GATE_FOLDS // sessions
    correct = graded["correct"].astype(float).to_numpy()
    baseline_share = graded["baseline_share"].astype(float).to_numpy()
    diff = correct - baseline_share
    by_block: dict[Any, list[float]] = defaultdict(list)
    for block, value in zip(graded["block"], diff):
        by_block[int(block)].append(float(value))
    lower_plain = one_sided_lower(by_block, spec.ONE_SIDED_ALPHA)
    lower_penalized = one_sided_lower(by_block, spec.ONE_SIDED_ALPHA / tests)
    traded = graded["status"].isin((spec.STATUS_FILLED, spec.STATUS_BLOCKED, spec.STATUS_STRANDED)).to_numpy()
    gross = graded["gross_return"].to_numpy(dtype=float)
    expectancy = {f"{c * 100:.1f}%": float(np.nanmean(np.where(traded, gross - c, 0.0))) for c in spec.COSTS}
    excess = np.where(traded, gross - spec.PRIMARY_COST - graded["universe_mean"].to_numpy(dtype=float), 0.0)
    excess_expectancy = float(np.nanmean(excess))
    probability = pd.to_numeric(graded["probability"], errors="coerce")
    stated = probability.notna().to_numpy()
    calibration: dict[str, Any] = {"claimed": bool(stated.any())}
    if stated.any():
        p = probability[stated].to_numpy(dtype=float)
        y = correct[stated]
        b = baseline_share[stated]
        brier = float(np.mean((p - y) ** 2))
        brier_baseline = float(np.mean((b - y) ** 2))
        buckets, ece = reliability(p, y)
        calibration.update(
            {
                "brier": round(brier, 4),
                "brier_baseline_forecast": round(brier_baseline, 4),
                "brier_skill": round(1 - brier / brier_baseline, 4) if brier_baseline > 0 else None,
                "ece": None if ece is None else round(ece, 4),
                "mean_gap": round(abs(float(p.mean()) - float(y.mean())), 4),
                "reliability": buckets,
            }
        )
        calibration_ok = (
            ece is not None and ece <= spec.GATE_ECE and calibration["mean_gap"] <= spec.GATE_MEAN_CALIBRATION
            and brier <= brier_baseline
        )
    else:
        calibration_ok = True
    folds = []
    for fold in range(spec.GATE_FOLDS):
        group = graded[graded["fold"] == fold]
        edge = float((group["correct"].astype(float) - group["baseline_share"]).mean()) if len(group) else None
        folds.append({"fold": fold + 1, "calls": int(len(group)), "edge": None if edge is None else round(edge, 4)})
    folds_positive = sum(1 for f in folds if f["edge"] is not None and f["edge"] > 0)
    dates = int(graded["signal_date"].nunique())
    windows = int(graded["block"].nunique())
    edge_value = float(diff.mean())
    gates = {
        "edge_8_points": edge_value >= GATE_EDGE,
        "edge_lower_bound_above_zero": (
            lower_plain is not None and lower_penalized is not None
            and lower_plain > LOWER_BOUND_EPSILON and lower_penalized > LOWER_BOUND_EPSILON
        ),
        "expectancy_all_costs": all(v > 0 for v in expectancy.values()),
        "excess_expectancy_1pct": excess_expectancy > 0,
        "folds": folds_positive >= spec.GATE_FOLDS_POSITIVE,
        "sample": n >= GATE_MIN_CALLS and dates >= GATE_MIN_DATES and windows >= MIN_WINDOWS[horizon],
        "calibration": calibration_ok,
        "not_leaky": not leaky,
    }
    if not gates["sample"]:
        verdict = INSUFFICIENT
    elif all(gates.values()):
        verdict = PASS
    else:
        verdict = NO_EVIDENCE
    causes = graded.loc[~graded["correct"].astype(bool), "failure_cause"].value_counts(normalize=True).round(4).to_dict()
    return {
        "calls": calls,
        "graded": n,
        "excluded_unresolved_steps": excluded,
        "excluded_share": round(excluded / calls, 4),
        "exclusion_warning": excluded / calls > EXCLUSION_WARNING,
        "distinct_dates": dates,
        "independent_windows": windows,
        "min_windows": MIN_WINDOWS[horizon],
        "win_rate": round(float(correct.mean()), 4),
        "baseline": round(float(baseline_share.mean()), 4),
        "edge": round(edge_value, 4),
        "edge_lower_90": None if lower_plain is None else round(lower_plain, 4),
        "edge_lower_penalized": None if lower_penalized is None else round(lower_penalized, 4),
        "penalty_tests": tests,
        "expectancy": {k: round(v, 5) for k, v in expectancy.items()},
        "excess_expectancy_1pct": round(excess_expectancy, 5),
        "calibration": calibration,
        "folds": folds,
        "folds_positive": folds_positive,
        "failure_causes": causes,
        "implausible": float(correct.mean()) >= spec.IMPLAUSIBLE_WIN_RATE and n >= GATE_MIN_CALLS,
        "gates": gates,
        "verdict": verdict,
    }


def matrix_column_v2(
    graded: pd.DataFrame,
    horizon: int,
    session_index: Mapping[date, int],
    sessions: int,
    strategy_versions: int = 1,
    leaky: bool = False,
) -> dict[str, dict[str, Any]]:
    tests = max(1, strategy_versions) * len(spec.SITUATIONS) * len(spec.HORIZONS)
    exploded = graded[graded["horizon"] == horizon].explode("situations")
    return {
        situation: cell_metrics_v2(exploded[exploded["situations"] == situation], horizon, session_index, sessions, tests, leaky)
        for situation in spec.SITUATIONS
    }


def rolling_monitor_v2(graded: pd.DataFrame, horizon: int, sessions: Sequence[date]) -> pd.DataFrame:
    frame = graded[(graded["horizon"] == horizon) & (graded["status"] != spec.STATUS_DATA_ERROR)].sort_values("exit_date")
    z = NormalDist().inv_cdf(1 - spec.ONE_SIDED_ALPHA)
    records = []
    for point in range(spec.MONITOR_SESSIONS, len(sessions), spec.MONITOR_STEP):
        as_of = sessions[point]
        matured = frame[frame["exit_date"] <= as_of].tail(spec.MONITOR_CALLS)
        if len(matured) < spec.MONITOR_CALLS:
            continue
        y = matured["correct"].astype(float)
        b = matured["baseline_share"].astype(float)
        diff = y - b
        traded = matured["status"].isin((spec.STATUS_FILLED, spec.STATUS_BLOCKED, spec.STATUS_STRANDED))
        net = np.where(traded, matured["gross_return"] - spec.PRIMARY_COST - matured["universe_mean"], 0.0)
        p = pd.to_numeric(matured["probability"], errors="coerce")
        stated = p.notna()
        brier = float(((p[stated] - y[stated]) ** 2).mean()) if stated.any() else None
        brier_baseline = float(((b[stated] - y[stated]) ** 2).mean()) if stated.any() else None
        records.append(
            {
                "as_of": as_of,
                "edge": float(diff.mean()),
                "edge_lower": float(diff.mean() - z * diff.std(ddof=1) / np.sqrt(len(diff))),
                "excess_expectancy": float(np.mean(net)),
                "excess_expectancy_upper": float(np.mean(net) + z * np.std(net, ddof=1) / np.sqrt(len(net))),
                "brier": brier,
                "brier_baseline": brier_baseline,
            }
        )
    monitor = pd.DataFrame.from_records(records)
    if monitor.empty:
        return monitor
    negative = monitor["edge_lower"] < 0
    monitor["kill_edge"] = negative & negative.shift(1, fill_value=False)
    monitor["kill_expectancy"] = (monitor["excess_expectancy"] < 0) & (monitor["excess_expectancy_upper"] < 0)
    monitor["kill_brier"] = monitor["brier"].notna() & (monitor["brier"] > monitor["brier_baseline"])
    monitor["suspend"] = monitor["kill_edge"] | monitor["kill_expectancy"] | monitor["kill_brier"]
    return monitor


def grade_calls_v2(market: Market, cubes: Mapping[int, Cube], calls: pd.DataFrame, cumulative: Mapping[str, np.ndarray]) -> pd.DataFrame:
    from src.scorecard.grading import grade_calls

    return grade_calls(market, cubes, calls, cumulative, causes_fn=failure_causes_v2, grade_version=GRADE_VERSION)
