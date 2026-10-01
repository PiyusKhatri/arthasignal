from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src.backtest import event_study as es
from src.backtest import event_tables as et
from src.scorecard import spec
from src.scorecard.grading import Market


def market_state_labels(index: pd.DataFrame, sessions: tuple[date, ...]) -> pd.Series:
    close = index.set_index("date")["close"].reindex(pd.Index(sessions)).ffill()
    sma50 = close.rolling(50, min_periods=50).mean()
    sma200 = close.rolling(200, min_periods=200).mean()
    labels = pd.Series(None, index=close.index, dtype=object)
    defined = sma200.notna()
    overheated = defined & (close >= spec.OVERHEATED_RATIO * sma200)
    bull = defined & ~overheated & (close > sma200) & (sma50 > sma200)
    bear = defined & (close < sma200) & (sma50 < sma200)
    labels[defined] = "market_sideways"
    labels[bull] = "market_bull"
    labels[bear] = "market_bear"
    labels[overheated] = "market_overheated"
    return labels


def _window_any(mask: np.ndarray, back: int, forward: int) -> np.ndarray:
    n = mask.shape[1]
    padded = np.concatenate([np.zeros((mask.shape[0], 1), dtype=np.int64), np.cumsum(mask, axis=1)], axis=1)
    t = np.arange(n)
    lo = np.clip(t - back, 0, n)
    hi = np.clip(t + forward + 1, 0, n)
    return (padded[:, hi] - padded[:, lo]) > 0


def upper_streak_ends(market: Market) -> np.ndarray:
    panel = market.panel
    up_close, _, _, _ = et.circuit_flags(panel)
    ends = np.zeros(up_close.shape, dtype=bool)
    for r in range(up_close.shape[0]):
        streak = 0
        for c in np.flatnonzero(market.traded[r]):
            if up_close[r, c]:
                streak += 1
            else:
                if streak >= spec.UPPER_STREAK_MIN:
                    ends[r, c] = True
                streak = 0
    return ends


def new_listing_mask(market: Market, mergers: set[str]) -> np.ndarray:
    n_sym, n = market.traded.shape
    mask = np.zeros((n_sym, n), dtype=bool)
    for symbol, r in market.panel.row.items():
        traded = np.flatnonzero(market.traded[r])
        if not len(traded) or symbol in mergers:
            continue
        first = traded[0]
        if first < et.LISTING_MIN_PANEL_SESSIONS or market.sessions[first] <= et.LISTING_VISIBLE_AFTER:
            continue
        mask[r, first : min(first + spec.NEW_LISTING_SESSIONS, n)] = True
    return mask


def rate_labels(rates: pd.DataFrame, sessions: tuple[date, ...]) -> tuple[np.ndarray, np.ndarray]:
    rows = []
    for fy, month, tbill in rates[["fiscal_year", "month", "treasury_bill_rate"]].itertuples(index=False):
        available = et.nepali_month_available_date(fy, month)
        if available is not None and pd.notna(tbill):
            rows.append((available, float(tbill)))
    frame = pd.DataFrame(rows, columns=["available", "rate"]).sort_values("available")
    positions = np.searchsorted(frame["available"].to_numpy(), np.array(sessions), side="right") - 1
    known = np.array([frame["rate"].iloc[p] if p >= 0 else np.nan for p in positions])
    earlier = np.concatenate([np.full(spec.RATE_LOOKBACK_SESSIONS, np.nan), known[: -spec.RATE_LOOKBACK_SESSIONS]])
    with np.errstate(invalid="ignore"):
        change = known - earlier
        return np.nan_to_num(change >= spec.RATE_CHANGE_POINTS), np.nan_to_num(change <= -spec.RATE_CHANGE_POINTS)


def situation_matrix(
    market: Market,
    index: pd.DataFrame,
    rates: pd.DataFrame,
    mergers: set[str],
) -> dict[str, np.ndarray]:
    n_sym, n = market.traded.shape
    state = market_state_labels(index, market.sessions).to_numpy()
    labels: dict[str, np.ndarray] = {"all": np.ones((n_sym, n), dtype=bool)}
    for name in ("market_bull", "market_sideways", "market_overheated", "market_bear"):
        labels[name] = np.broadcast_to(state == name, (n_sym, n)).copy()
    labels["pre_book_close"] = _window_any(np.roll(market.action_mask, -1, axis=1) & (np.arange(n) < n - 1), 0, spec.PRE_BOOK_CLOSE_SESSIONS - 1)
    labels["post_book_close"] = _window_any(market.action_mask, spec.POST_BOOK_CLOSE_SESSIONS - 1, 0)
    labels["new_listing"] = new_listing_mask(market, mergers)
    labels["post_upper_circuit"] = _window_any(upper_streak_ends(market), spec.POST_UPPER_SESSIONS - 1, 0)
    labels["post_lower_circuit"] = _window_any(market.down_close, spec.POST_LOWER_SESSIONS - 1, 0)
    labels["volume_anomaly"] = market.spike.copy()
    rising, falling = rate_labels(rates, market.sessions)
    labels["rate_rising"] = np.broadcast_to(rising.astype(bool), (n_sym, n)).copy()
    labels["rate_falling"] = np.broadcast_to(falling.astype(bool), (n_sym, n)).copy()
    return labels


def labels_for(matrix: dict[str, np.ndarray], r: int, t: int) -> list[str]:
    return [name for name in spec.SITUATIONS if matrix[name][r, t]]
