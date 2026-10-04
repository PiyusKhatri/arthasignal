from __future__ import annotations

import bisect
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from src.backtest import event_study as es
from src.backtest import event_tables as et
from src.scorecard import spec
from src.scorecard.situations import market_state_labels
from src.scorecard.strategies import Strategy

NAME = "model_v0"
VERSION = "v0.1"
MOMENTUM_LOOKBACK = 20
MOMENTUM_PICKS = 10
NEW_LISTING_PICKS = 5
NEW_LISTING_SESSIONS = 60
AVOID_AFTER_BONUS_SESSIONS = 20
AVOID_AFTER_STREAK_SESSIONS = 20
STREAK_MIN = 3
ABSTAIN_STATES = ("market_bear",)
AVOID_E2 = "model_v0_avoid_e2"
AVOID_E4 = "model_v0_avoid_e4"
AVOID_STRATEGIES = (AVOID_E2, AVOID_E4)

PARAMETERS: dict[str, Any] = {
    "momentum_lookback": MOMENTUM_LOOKBACK,
    "momentum_picks": MOMENTUM_PICKS,
    "new_listing_picks": NEW_LISTING_PICKS,
    "new_listing_sessions": NEW_LISTING_SESSIONS,
    "avoid_after_bonus_book_close_sessions": AVOID_AFTER_BONUS_SESSIONS,
    "avoid_after_upper_streak_end_sessions": AVOID_AFTER_STREAK_SESSIONS,
    "upper_streak_min": STREAK_MIN,
    "abstain_market_states": list(ABSTAIN_STATES),
    "probability": None,
    "protocol": "accuracy-v2.1",
    "universe": "companies.instrument_type = 'Equity' after promoter shares were reclassified",
}

DESCRIPTION = (
    "Model v0: the current simple analysis frozen as a rule. Abstains when the NEPSE state is bear "
    "(close and 50-session SMA below the 200-session SMA). Otherwise calls the top 10 stocks by 20-session total "
    "return and the top 5 new listings (first price within 60 sessions, mergers excluded) by the same score, "
    "skipping any stock with a bonus book-close ex-session in the last 20 sessions (avoid rule E2) or whose "
    "upper-circuit close streak of 3 or more ended in the last 20 sessions (avoid rule E4). States no probability."
)


class ModelV0:
    def __init__(self, index: pd.DataFrame, actions: pd.DataFrame, mergers: set[str]) -> None:
        self.index = index[["date", "close"]].sort_values("date")
        bonus = actions[actions["action_type"].astype(str).str.upper() == "BONUS"]
        self.bonus_dates: dict[str, list[date]] = {
            symbol: sorted(group["action_date"]) for symbol, group in bonus.groupby("symbol")
        }
        self.mergers = set(mergers)
        self._cache: dict[int, dict[str, Any]] = {}

    def _context(self, panel: es.Panel) -> dict[str, Any]:
        key = id(panel)
        if key in self._cache:
            return self._cache[key]
        up_close, _, _, _ = et.circuit_flags(panel)
        streak_end = np.zeros(up_close.shape, dtype=bool)
        traded = ~np.isnan(panel.close)
        for r in range(up_close.shape[0]):
            streak = 0
            for c in np.flatnonzero(traded[r]):
                if up_close[r, c]:
                    streak += 1
                else:
                    if streak >= STREAK_MIN:
                        streak_end[r, c] = True
                    streak = 0
        first = np.array(
            [int(np.flatnonzero(traded[r])[0]) if traded[r].any() else -1 for r in range(traded.shape[0])]
        )
        index = self.index[self.index["date"] <= panel.sessions[-1]]
        states = market_state_labels(index, panel.sessions).to_numpy()
        context = {"streak_end": np.cumsum(streak_end, axis=1), "first": first, "states": states}
        self._cache = {key: context}
        return context

    def _bonus_recent(self, symbol: str, panel: es.Panel, t: int) -> bool:
        dates = self.bonus_dates.get(symbol)
        if not dates:
            return False
        start = panel.sessions[max(0, t - AVOID_AFTER_BONUS_SESSIONS + 1)]
        position = bisect.bisect_left(dates, start)
        return position < len(dates) and dates[position] <= panel.sessions[t]

    def avoid_hits(self, panel: es.Panel, t: int) -> pd.DataFrame:
        context = self._context(panel)
        lo = max(0, t - AVOID_AFTER_STREAK_SESSIONS)
        streak_recent = context["streak_end"][:, t] - (context["streak_end"][:, lo] if lo > 0 else 0) > 0
        rows = []
        for r in np.flatnonzero(~np.isnan(panel.close[:, t])):
            symbol = panel.symbols[r]
            if not spec.valid_symbol(symbol):
                continue
            if self._bonus_recent(symbol, panel, t):
                rows.append((symbol, AVOID_E2))
            if streak_recent[r]:
                rows.append((symbol, AVOID_E4))
        return pd.DataFrame(rows, columns=["symbol", "rule"])

    def select(self, panel: es.Panel, t: int) -> pd.DataFrame:
        context = self._context(panel)
        empty = pd.DataFrame(columns=["symbol", "score"])
        if context["states"][t] in ABSTAIN_STATES or t < MOMENTUM_LOOKBACK:
            return empty
        members = np.flatnonzero(~np.isnan(panel.close[:, t]))
        if not len(members):
            return empty
        window = panel.total_return[:, t - MOMENTUM_LOOKBACK + 1 : t + 1]
        corrupt = panel.corrupt[:, t - MOMENTUM_LOOKBACK + 1 : t + 1].any(axis=1)
        score = np.prod(1 + np.nan_to_num(window, nan=0.0), axis=1) - 1
        lo = max(0, t - AVOID_AFTER_STREAK_SESSIONS)
        streak_recent = context["streak_end"][:, t] - (context["streak_end"][:, lo] if lo > 0 else 0) > 0
        rows = []
        for r in members:
            symbol = panel.symbols[r]
            if not spec.valid_symbol(symbol) or corrupt[r] or streak_recent[r] or self._bonus_recent(symbol, panel, t):
                continue
            first = context["first"][r]
            new_listing = (
                symbol not in self.mergers
                and first >= et.LISTING_MIN_PANEL_SESSIONS
                and panel.sessions[first] > et.LISTING_VISIBLE_AFTER
                and t - first < NEW_LISTING_SESSIONS
            )
            rows.append((symbol, float(score[r]), new_listing))
        if not rows:
            return empty
        frame = pd.DataFrame(rows, columns=["symbol", "score", "new_listing"]).sort_values(["score", "symbol"], ascending=[False, True])
        momentum = frame[~frame["new_listing"]].head(MOMENTUM_PICKS)
        listings = frame[frame["new_listing"]].head(NEW_LISTING_PICKS)
        chosen = pd.concat([momentum, listings]).sort_values(["score", "symbol"], ascending=[False, True])
        return chosen[["symbol", "score"]].reset_index(drop=True)


def strategy(index: pd.DataFrame, actions: pd.DataFrame, mergers: set[str]) -> Strategy:
    model = ModelV0(index, actions, mergers)
    return Strategy(NAME, VERSION, None, model.select, dict(PARAMETERS))
