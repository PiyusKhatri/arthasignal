from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

import numpy as np
import pandas as pd

from src.backtest import event_study as es
from src.scorecard import spec

STATUS_CODES = {name: code for code, name in enumerate(spec.STATUSES)}
PENDING = STATUS_CODES[spec.STATUS_PENDING]
FILLED = STATUS_CODES[spec.STATUS_FILLED]
UNFILLED = STATUS_CODES[spec.STATUS_UNFILLED]
BLOCKED = STATUS_CODES[spec.STATUS_BLOCKED]
STRANDED = STATUS_CODES[spec.STATUS_STRANDED]
DATA_ERROR = STATUS_CODES[spec.STATUS_DATA_ERROR]


@dataclass
class Market:
    panel: es.Panel
    traded: np.ndarray
    lockup: np.ndarray
    lockdown: np.ndarray
    down_close: np.ndarray
    w_close: np.ndarray
    w_open: np.ndarray
    corrupt_open: np.ndarray
    poison: np.ndarray
    sector_code: np.ndarray
    sector_names: tuple[str, ...]
    nepse_open: np.ndarray
    nepse_close: np.ndarray
    action_mask: np.ndarray
    spike: np.ndarray
    open_rule: np.ndarray

    @property
    def sessions(self) -> tuple[date, ...]:
        return self.panel.sessions


def _previous_close(close: np.ndarray) -> np.ndarray:
    frame = pd.DataFrame(close.T)
    return frame.ffill().shift(1).to_numpy().T


def volume_spikes(panel: es.Panel) -> np.ndarray:
    volume = pd.DataFrame(panel.volume.T)
    baseline = volume.shift(1).rolling(spec.VOLUME_LOOKBACK, min_periods=spec.VOLUME_MIN_HISTORY).median()
    ratio = (volume / baseline).to_numpy().T
    with np.errstate(invalid="ignore"):
        return np.nan_to_num(ratio, nan=0.0) >= spec.VOLUME_RATIO


def build_market(panel: es.Panel, actions: pd.DataFrame, index: pd.DataFrame) -> Market:
    sessions = panel.sessions
    shape = panel.close.shape
    traded = ~np.isnan(panel.close)
    prev_close = _previous_close(panel.close)
    limits = np.array([es.circuit_limit(day) for day in sessions])
    with np.errstate(invalid="ignore"):
        lockup = traded & (panel.low >= prev_close * (1 + limits - es.LOCK_TOLERANCE))
        lockdown = traded & (panel.high <= prev_close * (1 - limits + es.LOCK_TOLERANCE))
        down_close = traded & (panel.close <= prev_close * (1 - limits + es.LOCK_TOLERANCE))
    effects = es._action_effects(actions, sessions)
    w_close = np.full(shape, np.nan)
    w_open = np.full(shape, np.nan)
    corrupt_open = np.zeros(shape, dtype=bool)
    action_mask = np.zeros(shape, dtype=bool)
    for symbol, r in panel.row.items():
        symbol_effects = effects.get(symbol, {})
        for c in symbol_effects:
            action_mask[r, c] = True
        idx = np.flatnonzero(traded[r])
        if not len(idx):
            continue
        wealth = 1.0
        first = idx[0]
        w_close[r, first] = wealth
        if panel.open[r, first] > 0 and panel.close[r, first] > 0:
            w_open[r, first] = panel.open[r, first] / panel.close[r, first]
        for p, c in zip(idx, idx[1:]):
            close_p = panel.close[r, p]
            entries = [e for s in range(p + 1, c + 1) for e in symbol_effects.get(s, [])]
            if close_p > 0 and panel.open[r, c] > 0:
                adjusted_open = es._apply_actions(panel.open[r, c], entries)
                ratio = adjusted_open / close_p
                limit = es.circuit_limit(sessions[c]) + es.CORRUPT_BUFFER
                gap = c - p
                if ratio - 1 > (1 + limit) ** gap - 1 or ratio - 1 < (1 - limit) ** gap - 1:
                    corrupt_open[r, c] = True
                w_open[r, c] = wealth * ratio
            ret = panel.total_return[r, c]
            if not np.isnan(ret):
                wealth *= 1 + ret
            w_close[r, c] = wealth
    w_close = pd.DataFrame(w_close.T).ffill().to_numpy().T
    poison = np.cumsum(panel.corrupt | corrupt_open, axis=1)
    sectors = [s if s else "unknown" for s in panel.sector]
    names = tuple(sorted(set(sectors)))
    code = np.array([names.index(s) for s in sectors])
    level = index.set_index("date").reindex(pd.Index(sessions))
    nepse_close = level["close"].ffill().to_numpy(dtype=float)
    nepse_open = level["open"].fillna(level["close"]).ffill().to_numpy(dtype=float)
    open_rule = np.array([day >= spec.FIRST_REAL_OPEN_SESSION for day in sessions])
    return Market(
        panel=panel, traded=traded, lockup=lockup, lockdown=lockdown, down_close=down_close, w_close=w_close,
        w_open=w_open, corrupt_open=corrupt_open, poison=poison, sector_code=code, sector_names=names,
        nepse_open=nepse_open, nepse_close=nepse_close, action_mask=action_mask, spike=volume_spikes(panel),
        open_rule=open_rule,
    )


@dataclass
class Cube:
    horizon: int
    status: np.ndarray
    gross: np.ndarray
    exit_index: np.ndarray
    entry_open_rule: np.ndarray
    universe_median: np.ndarray
    universe_mean: np.ndarray
    sector_median: np.ndarray
    nepse: np.ndarray
    correct: np.ndarray
    baseline_share: np.ndarray


def _take(matrix: np.ndarray, columns: np.ndarray) -> np.ndarray:
    rows = np.arange(matrix.shape[0])[:, None]
    return matrix[rows, columns]


def correctness(
    horizon: int, status: np.ndarray, gross: np.ndarray, median: np.ndarray, mean: np.ndarray, sector_median: np.ndarray
) -> np.ndarray:
    filled = status == FILLED
    with np.errstate(invalid="ignore"):
        net_positive = gross - spec.PRIMARY_COST > 0
        cls = spec.horizon_class(horizon)
        if cls == "short":
            ok = filled & net_positive & (gross > median)
        elif cls == "mid":
            ok = filled & net_positive & (gross > median) & (gross > sector_median)
        else:
            ok = filled & (gross > mean)
    return np.nan_to_num(ok, nan=False).astype(bool)


def build_cube(market: Market, horizon: int, last_index: int) -> Cube:
    n_sym, n = market.traded.shape
    t = np.arange(n)
    entry = np.minimum(t + 1, n - 1)
    nominal_exit = t + 1 + horizon
    pending_t = nominal_exit > last_index
    exit_col = np.minimum(nominal_exit, n - 1)
    open_rule = market.open_rule[entry]

    status = np.full((n_sym, n), PENDING, dtype=np.int8)
    gross = np.full((n_sym, n), np.nan)
    exit_index = np.full((n_sym, n), -1, dtype=np.int32)

    entry_ok = _take(market.traded, np.broadcast_to(entry, (n_sym, n))) & ~_take(
        market.lockup, np.broadcast_to(entry, (n_sym, n))
    )
    exit_cols = np.broadcast_to(exit_col, (n_sym, n))
    exit_ok = _take(market.traded, exit_cols) & ~_take(market.lockdown, exit_cols)
    found = exit_ok.copy()
    chosen = exit_cols.copy()
    for k in range(1, spec.MAX_EXIT_DEFERRAL + 1):
        candidate = np.minimum(exit_col + k, n - 1)
        within = (exit_col + k) <= last_index
        cols = np.broadcast_to(candidate, (n_sym, n))
        ok = _take(market.traded, cols) & ~_take(market.lockdown, cols) & within[None, :]
        newly = ok & ~found
        chosen = np.where(newly, cols, chosen)
        found |= ok
    stranded_col = np.minimum(np.minimum(exit_col + spec.MAX_EXIT_DEFERRAL, last_index), n - 1)
    final_col = np.where(found, chosen, np.broadcast_to(stranded_col, (n_sym, n)))

    entry_cols = np.broadcast_to(entry, (n_sym, n))
    w_entry = np.where(open_rule[None, :], _take(market.w_open, entry_cols), _take(market.w_close, entry_cols))
    w_exit_open = _take(market.w_open, final_col)
    w_exit_close = _take(market.w_close, final_col)
    w_exit = np.where(open_rule[None, :] & found, w_exit_open, w_exit_close)
    with np.errstate(invalid="ignore", divide="ignore"):
        ret = w_exit / w_entry - 1.0
    poison_after = _take(market.poison, final_col)
    poison_before = _take(market.poison, np.maximum(entry_cols - 1, 0))
    corrupt = (poison_after - poison_before) > 0

    status[:] = np.where(found, FILLED, np.where(exit_ok, FILLED, STRANDED))
    status = np.where(found & ~exit_ok, BLOCKED, status).astype(np.int8)
    status = np.where(corrupt | ~np.isfinite(ret), DATA_ERROR, status).astype(np.int8)
    status = np.where(~entry_ok, UNFILLED, status).astype(np.int8)
    status = np.where(pending_t[None, :], PENDING, status).astype(np.int8)
    gross = np.where(np.isin(status, (FILLED, BLOCKED, STRANDED)), ret, np.nan)
    exit_index = np.where(status != PENDING, final_col, -1).astype(np.int32)

    universe = market.traded
    member = universe & np.isin(status, (FILLED, BLOCKED, STRANDED))
    values = np.where(member, gross, np.nan)
    with np.errstate(invalid="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            universe_median = np.nanmedian(values, axis=0)
            universe_mean = np.nanmean(values, axis=0)
            sector_median_by_code = np.vstack(
                [np.nanmedian(np.where(market.sector_code[:, None] == c, values, np.nan), axis=0)
                 for c in range(len(market.sector_names))]
            )
    sector_median = sector_median_by_code[market.sector_code]
    correct = correctness(horizon, status, gross, universe_median[None, :], universe_mean[None, :], sector_median)
    gradable = universe & np.isin(status, (FILLED, UNFILLED, BLOCKED, STRANDED))
    with np.errstate(invalid="ignore", divide="ignore"):
        baseline_share = np.where(gradable.sum(axis=0) > 0, (correct & gradable).sum(axis=0) / gradable.sum(axis=0), np.nan)
    nepse = np.where(
        open_rule,
        market.nepse_open[exit_col] / market.nepse_open[entry] - 1.0,
        market.nepse_close[exit_col] / market.nepse_close[entry] - 1.0,
    )
    nepse = np.where(pending_t, np.nan, nepse)
    return Cube(
        horizon=horizon, status=status, gross=gross, exit_index=exit_index, entry_open_rule=open_rule,
        universe_median=universe_median, universe_mean=universe_mean, sector_median=sector_median,
        nepse=nepse, correct=correct, baseline_share=baseline_share,
    )


def _range_any(cumulative: np.ndarray, rows: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    before = np.where(start > 0, cumulative[rows, np.maximum(start - 1, 0)], 0)
    return (cumulative[rows, end] - before) > 0


def failure_causes(market: Market, cube: Cube, rows: np.ndarray, cols: np.ndarray, cumulative: Mapping[str, np.ndarray]) -> np.ndarray:
    status = cube.status[rows, cols]
    correct = cube.correct[rows, cols]
    gross = cube.gross[rows, cols]
    entry = cols + 1
    end = np.maximum(cube.exit_index[rows, cols], entry)
    causes = np.full(len(rows), None, dtype=object)
    wrong = (status == FILLED) & ~correct
    circuit = wrong & _range_any(cumulative["down"], rows, entry, end)
    news = wrong & ~circuit & _range_any(cumulative["news"], rows, entry, end)
    mean = cube.universe_mean[cols]
    median = cube.universe_median[cols]
    sector = cube.sector_median[rows, cols]
    with np.errstate(invalid="ignore"):
        market_move = wrong & ~circuit & ~news & (mean < 0) & (gross >= mean - spec.FAILURE_MARGIN)
        sector_move = wrong & ~circuit & ~news & ~market_move & (sector < median) & (gross >= sector - spec.FAILURE_MARGIN)
    model = wrong & ~circuit & ~news & ~market_move & ~sector_move
    causes[np.isin(status, (UNFILLED, BLOCKED, STRANDED))] = "liquidity"
    causes[circuit] = "circuit"
    causes[news] = "news"
    causes[market_move] = "market"
    causes[sector_move] = "sector"
    causes[model] = "model"
    return causes


def cumulative_flags(market: Market) -> dict[str, np.ndarray]:
    return {
        "down": np.cumsum(market.down_close, axis=1),
        "news": np.cumsum(market.action_mask | market.spike, axis=1),
    }


def grade_calls(
    market: Market,
    cubes: Mapping[int, Cube],
    calls: pd.DataFrame,
    cumulative: Mapping[str, np.ndarray] | None = None,
) -> pd.DataFrame:
    cumulative = cumulative_flags(market) if cumulative is None else cumulative
    session_index = {day: i for i, day in enumerate(market.sessions)}
    rows = calls["symbol"].map(market.panel.row)
    cols = calls["signal_date"].map(session_index)
    known = rows.notna() & cols.notna()
    sessions = np.array(market.sessions, dtype=object)
    frames = []
    for horizon, cube in cubes.items():
        unknown = calls[~known]
        if len(unknown):
            frames.append(
                pd.DataFrame(
                    {"call_id": unknown["call_id"].to_numpy(), "grade_version": spec.GRADE_VERSION, "horizon": horizon,
                     "horizon_class": spec.horizon_class(horizon), "status": spec.STATUS_UNFILLED, "entry_rule": None,
                     "entry_date": None, "exit_date": None, "gross_return": np.nan, "universe_median": np.nan,
                     "universe_mean": np.nan, "sector_median": np.nan, "nepse_return": np.nan,
                     "baseline_share": np.nan, "correct": False, "failure_cause": "liquidity"}
                )
            )
        r = rows[known].to_numpy(dtype=int)
        t = cols[known].to_numpy(dtype=int)
        ids = calls.loc[known, "call_id"].to_numpy()
        status = cube.status[r, t]
        mature = status != PENDING
        r, t, ids, status = r[mature], t[mature], ids[mature], status[mature]
        exit_i = cube.exit_index[r, t]
        frames.append(
            pd.DataFrame(
                {
                    "call_id": ids,
                    "grade_version": spec.GRADE_VERSION,
                    "horizon": horizon,
                    "horizon_class": spec.horizon_class(horizon),
                    "status": np.array(spec.STATUSES, dtype=object)[status],
                    "entry_rule": np.where(cube.entry_open_rule[t], "next_open", "next_close"),
                    "entry_date": sessions[np.minimum(t + 1, len(sessions) - 1)],
                    "exit_date": sessions[np.maximum(exit_i, 0)],
                    "gross_return": cube.gross[r, t],
                    "universe_median": cube.universe_median[t],
                    "universe_mean": cube.universe_mean[t],
                    "sector_median": cube.sector_median[r, t],
                    "nepse_return": cube.nepse[t],
                    "baseline_share": cube.baseline_share[t],
                    "correct": cube.correct[r, t],
                    "failure_cause": failure_causes(market, cube, r, t, cumulative),
                }
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
