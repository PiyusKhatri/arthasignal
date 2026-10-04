from __future__ import annotations

import bisect
import warnings
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.backtest import event_study as es
from src.backtest import event_tables as et
from src.ranker import spec
from src.backtest.knowledge_time import truncate_reports
from src.scorecard.situations import market_state_labels, rate_labels

BROKER_STORE = Path("~/Desktop/arthasignal-ai/derived/broker_flow/features.parquet").expanduser()
STATE_CODES = {"market_bear": 0, "market_sideways": 1, "market_bull": 2, "market_overheated": 3}
STREAK_MIN = 3


def load_extras(engine: Any, end: date, broker_store: Path = BROKER_STORE) -> dict[str, pd.DataFrame]:
    from src.scorecard.info_eval import load_declarations, load_reports

    broker = pd.read_parquet(broker_store, columns=["symbol", "date", "h1", "h2", "h3", "h4", "h5"])
    broker["date"] = pd.to_datetime(broker["date"]).dt.date
    broker = broker[broker["date"] <= end]
    return {"reports": load_reports(engine, end), "declarations": load_declarations(engine, end), "broker": broker}


def truncate(inputs: Mapping[str, Any], extras: Mapping[str, pd.DataFrame], day: date) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    cut = dict(inputs)
    cut["prices"] = inputs["prices"][inputs["prices"]["date"] <= day]
    cut["sessions"] = inputs["sessions"][inputs["sessions"]["date"] <= day]
    cut["actions"] = inputs["actions"][inputs["actions"]["action_date"] <= day]
    cut["index"] = inputs["index"][inputs["index"]["date"] <= day]
    reports = truncate_reports(extras["reports"], day)
    declarations = extras["declarations"][extras["declarations"]["announcement_date"] <= day]
    broker = extras["broker"][extras["broker"]["date"] <= day]
    return cut, {"reports": reports, "declarations": declarations, "broker": broker}


def _frame(matrix: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(matrix.T)


def _rolling(matrix: np.ndarray, window: int, how: str, min_periods: int | None = None) -> np.ndarray:
    roll = _frame(matrix).rolling(window, min_periods=min_periods or max(2, window // 2))
    return getattr(roll, how)().to_numpy().T


def _since(event_cols: Mapping[int, Sequence[int]], n_sym: int, n: int) -> np.ndarray:
    out = np.full((n_sym, n), float(spec.CAP_SESSIONS), dtype=np.float64)
    t = np.arange(n)
    for r, cols in event_cols.items():
        cols = np.array(sorted(set(cols)), dtype=int)
        if not len(cols):
            continue
        position = np.searchsorted(cols, t, side="right") - 1
        valid = position >= 0
        out[r, valid] = np.minimum(t[valid] - cols[position[valid]], spec.CAP_SESSIONS)
    return out


def _event_columns(frame: pd.DataFrame, date_col: str, sessions: Sequence[date], row: Mapping[str, int], side: str = "left") -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for symbol, day in zip(frame["symbol"], frame[date_col]):
        r = row.get(symbol)
        if r is None or day is None or pd.isna(day):
            continue
        c = bisect.bisect_left(sessions, day) if side == "left" else bisect.bisect_right(sessions, day)
        if c < len(sessions):
            out.setdefault(r, []).append(c)
    return out


def _latest_value(frame: pd.DataFrame, value_col: str, sessions: Sequence[date], row: Mapping[str, int], n_sym: int,
                  date_col: str = "available_date") -> tuple[np.ndarray, np.ndarray]:
    n = len(sessions)
    value = np.full((n_sym, n), np.nan)
    count = np.zeros((n_sym, n))
    frame = frame.assign(k=[bisect.bisect_right(sessions, d) for d in frame[date_col]]).sort_values(["symbol", "k"])
    for symbol, group in frame.groupby("symbol"):
        r = row.get(symbol)
        if r is None:
            continue
        ks = group["k"].to_numpy()
        vals = group[value_col].to_numpy(dtype=float)
        position = np.searchsorted(ks, np.arange(n), side="right") - 1
        valid = position >= 0
        value[r, valid] = vals[position[valid]]
        count[r] = np.where(valid, position + 1, 0)
    return value, count


def compute(panel: es.Panel, inputs: Mapping[str, Any], extras: Mapping[str, pd.DataFrame]) -> dict[str, np.ndarray]:
    from src.scorecard.info_eval import add_growth

    sessions = list(panel.sessions)
    n_sym, n = panel.close.shape
    traded = ~np.isnan(panel.close)
    tr = np.where(traded, np.nan_to_num(panel.total_return, nan=0.0), np.nan)
    cum = np.concatenate([np.zeros((n_sym, 1)), np.cumsum(np.log1p(np.nan_to_num(panel.total_return, nan=0.0)), axis=1)], axis=1)

    def window_return(start_back: int, end_back: int) -> np.ndarray:
        t = np.arange(n)
        a = np.clip(t - start_back + 1, 0, n)
        b = np.clip(t - end_back + 1, 0, n)
        out = np.expm1(cum[:, b] - cum[:, a])
        out[:, t < start_back - 1] = np.nan
        return out

    f: dict[str, np.ndarray] = {}
    f["ret_1"] = window_return(1, 0)
    f["ret_5"] = window_return(5, 0)
    f["ret_20"] = window_return(20, 0)
    f["ret_60"] = window_return(60, 0)
    f["mom_120_skip5"] = window_return(120, 5)
    f["vol_20"] = _rolling(tr, 20, "std")
    f["vol_60"] = _rolling(tr, 60, "std")
    turnover = np.where(traded, panel.turnover, np.nan)
    f["turnover_20"] = np.log1p(_rolling(turnover, 20, "median"))
    f["turnover_ratio_5_60"] = _rolling(turnover, 5, "mean", 1) / _rolling(turnover, 60, "mean", 10)
    close = np.where(traded, panel.close, np.nan)
    for w in (20, 50, 200):
        f[f"close_sma{w}"] = close / _rolling(close, w, "mean") - 1
    f["dist_high_240"] = close / _rolling(close, 240, "max", 20) - 1
    up_close, down_close, _, _ = et.circuit_flags(panel)
    f["upper_hits_20"] = _rolling(up_close.astype(float), 20, "sum", 1)
    f["lower_hits_20"] = _rolling(down_close.astype(float), 20, "sum", 1)
    f["traded_share_60"] = _rolling(traded.astype(float), 60, "mean", 1)
    first = np.array([int(np.flatnonzero(traded[r])[0]) if traded[r].any() else n for r in range(n_sym)])
    f["age_sessions"] = np.clip(np.arange(n)[None, :] - first[:, None], 0, spec.CAP_SESSIONS).astype(float)
    f["log_price"] = np.log(close)

    codes = pd.Series(panel.sector).fillna("none").astype("category").cat.codes.to_numpy()
    sector_ret, sector_dev60, sector_vol = (np.full((n_sym, n), np.nan) for _ in range(3))
    for code in np.unique(codes):
        rows = codes == code
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            m20 = np.nanmedian(np.where(traded[rows], f["ret_20"][rows], np.nan), axis=0)
            m60 = np.nanmedian(np.where(traded[rows], f["ret_60"][rows], np.nan), axis=0)
            mv = np.nanmedian(np.where(traded[rows], f["vol_20"][rows], np.nan), axis=0)
        sector_ret[rows] = m20
        sector_dev60[rows] = m60
        sector_vol[rows] = mv
    f["ret_20_vs_sector"] = f["ret_20"] - sector_ret
    f["ret_60_vs_sector"] = f["ret_60"] - sector_dev60
    f["vol_20_vs_sector"] = f["vol_20"] - sector_vol
    f["sector_ret_20"] = sector_ret

    actions = inputs["actions"]
    for kind, name in (("BONUS", "since_bonus"), ("RIGHT", "since_right"), ("DIVIDEND", "since_dividend")):
        frame = actions[actions["action_type"].astype(str).str.upper() == kind]
        f[name] = _since(_event_columns(frame, "action_date", sessions, panel.row), n_sym, n)
    f["e2_bonus_20"] = (f["since_bonus"] < 20).astype(float)
    ends: dict[int, list[int]] = {}
    for r in range(n_sym):
        streak = 0
        for c in np.flatnonzero(traded[r]):
            if up_close[r, c]:
                streak += 1
            else:
                if streak >= STREAK_MIN:
                    ends.setdefault(r, []).append(int(c))
                streak = 0
    f["e4_streak_20"] = (_since(ends, n_sym, n) < 20).astype(float)
    declarations = extras["declarations"]
    f["since_dividend_declaration"] = _since(_event_columns(declarations, "announcement_date", sessions, panel.row, "right"), n_sym, n)
    bonus_pct, _ = _latest_value(declarations.assign(available_date=declarations["announcement_date"]), "bonus", sessions, panel.row, n_sym)
    f["declared_bonus_pct"] = bonus_pct

    broker = extras["broker"]
    index = {d: i for i, d in enumerate(sessions)}
    rows = broker["symbol"].map(panel.row)
    cols = broker["date"].map(index)
    keep = rows.notna() & cols.notna()
    for k in range(1, 6):
        m = np.full((n_sym, n), np.nan)
        m[rows[keep].astype(int).to_numpy(), cols[keep].astype(int).to_numpy()] = broker.loc[keep, f"h{k}"].to_numpy(dtype=float)
        f[f"broker_h{k}"] = m

    reports = add_growth(extras["reports"], sessions)
    growth, count = _latest_value(reports.assign(g=reports["growth"].clip(-5, 5)), "g", sessions, panel.row, n_sym)
    positive, _ = _latest_value(reports.assign(p=(reports["net_profit"] > 0).astype(float)), "p", sessions, panel.row, n_sym)
    f["profit_growth_yoy"] = growth
    f["profit_positive"] = positive
    f["report_count"] = np.minimum(count, 40)
    report_cols = _event_columns(reports, "available_date", sessions, panel.row, "right")
    f["since_report"] = _since(report_cols, n_sym, n)

    level = inputs["index"].set_index("date")["close"].reindex(pd.Index(sessions)).ffill().to_numpy()
    nepse_20 = np.full(n, np.nan)
    nepse_60 = np.full(n, np.nan)
    nepse_20[20:] = level[20:] / level[:-20] - 1
    nepse_60[60:] = level[60:] / level[:-60] - 1
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        breadth = np.nanmean(np.where(traded, (close > _rolling(close, 50, "mean")).astype(float), np.nan), axis=0)
        market_turnover = np.nansum(turnover, axis=0)
    mt = pd.Series(market_turnover)
    turnover_ratio = (mt.rolling(5, min_periods=1).mean() / mt.rolling(60, min_periods=10).mean()).to_numpy()
    states = market_state_labels(inputs["index"], tuple(sessions)).map(STATE_CODES).to_numpy(dtype=float)
    rising, falling = rate_labels(inputs["rates"], tuple(sessions))
    for name, series in (("nepse_ret_20", nepse_20), ("nepse_ret_60", nepse_60), ("breadth_sma50", breadth),
                         ("market_turnover_ratio_5_60", turnover_ratio), ("state_code", states),
                         ("rate_rising", rising.astype(float)), ("rate_falling", falling.astype(float))):
        f[name] = np.broadcast_to(series, (n_sym, n)).astype(float)
    return {k: np.where(traded, f[k], np.nan).astype(np.float32) for k in spec.FEATURES}


def rank_transform(features: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    out = {}
    for name, matrix in features.items():
        if name in spec.MARKET_FEATURES:
            out[name] = matrix
        else:
            out[name] = pd.DataFrame(matrix).rank(axis=0, pct=True).to_numpy(dtype=np.float32)
    return out
