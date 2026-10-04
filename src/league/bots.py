from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.backtest import event_study as es
from src.backtest import event_tables as et
from src.scorecard import model_v0, spec
from src.scorecard.situations import market_state_labels
from src.scorecard.strategies import Strategy

LEAGUE = "paper_bot_league_v1"
BUY = "buy"
AVOID = "avoid"

RISK: dict[str, Any] = {
    "max_calls_per_day": 10,
    "max_calls_per_sector": 3,
    "liquidity_lookback_sessions": 60,
    "min_traded_sessions_in_lookback": 50,
    "min_median_turnover_npr": 2_000_000,
    "skip_upper_limit_close_on_signal_day": True,
    "skip_unresolved_price_steps_in_last_sessions": 120,
    "skip_quarantined_symbols": True,
    "equity_only": True,
    "position_weight": "equal, 1 / max_calls_per_day of paper capital per call",
    "suspend_on_v2_kill_rule": "no new calls for a bot x horizon once rolling_monitor_v2 flags suspend at its primary horizon",
}

MOMENTUM_LOOKBACK = 20
RANKER_MOMENTUM_LOOKBACK = 120
RANKER_MOMENTUM_SKIP = 5
RANKER_REVERSAL_LOOKBACK = 5
RANKER_VOLATILITY_LOOKBACK = 60
TIMER_BREADTH_SMA = 50
TIMER_BREADTH_MIN = 0.5
TIMER_INDEX_LOOKBACK = 20
TIMER_ON_STATES = ("market_bull", "market_sideways")
COMBINED_CANDIDATES = 20
NEW_LISTING_PICKS = 5


@dataclass(frozen=True)
class Bot:
    name: str
    version: str
    side: str
    primary_horizons: tuple[int, ...]
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


BOTS: tuple[Bot, ...] = (
    Bot(
        "bot_avoid_e2e4", "b1", AVOID, (20,),
        "Avoid list: every equity with a bonus book-close ex-session in the last 20 sessions (E2) or whose upper-circuit "
        "close streak of 3 or more ended in the last 20 sessions (E4). One observation per stock and date.",
        {"bonus_sessions": model_v0.AVOID_AFTER_BONUS_SESSIONS, "streak_sessions": model_v0.AVOID_AFTER_STREAK_SESSIONS,
         "streak_min": model_v0.STREAK_MIN, "risk_rules": "quarantine and equity filter only"},
    ),
    Bot(
        "bot_momentum", "b1", BUY, (5, 10, 20),
        "Top 10 established equities by 20-session total return, abstaining when the NEPSE state is bear. No avoid filter.",
        {"lookback": MOMENTUM_LOOKBACK, "abstain_states": ["market_bear"], "excludes_new_listings": True},
    ),
    Bot(
        "bot_new_listing", "b1", BUY, (40,),
        "Top 5 new listings (first price within 60 sessions, mergers excluded) by total return over the last 20 sessions, "
        "abstaining when the NEPSE state is bear. No avoid filter.",
        {"lookback": MOMENTUM_LOOKBACK, "picks": NEW_LISTING_PICKS, "new_listing_sessions": model_v0.NEW_LISTING_SESSIONS,
         "abstain_states": ["market_bear"]},
    ),
    Bot(
        "bot_ranker_spec", "b1", BUY, (5, 10, 20),
        "Cross-sectional ranker spec with fixed equal weights and no fitting: mean percentile rank of 120-session momentum "
        "skipping the last 5 sessions, 5-session reversal, low 60-session volatility and 60-session median turnover. Top 10.",
        {"momentum_lookback": RANKER_MOMENTUM_LOOKBACK, "momentum_skip": RANKER_MOMENTUM_SKIP,
         "reversal_lookback": RANKER_REVERSAL_LOOKBACK, "volatility_lookback": RANKER_VOLATILITY_LOOKBACK,
         "weights": "equal", "fitted": False},
    ),
    Bot(
        "bot_market_timer", "b1", BUY, (5, 10, 20),
        "Market-state timer: on when the NEPSE state is bull or sideways, at least half of traded equities close above their "
        "50-session average and NEPSE's 20-session return is positive. When on, calls the 10 most liquid eligible equities.",
        {"on_states": list(TIMER_ON_STATES), "breadth_sma": TIMER_BREADTH_SMA, "breadth_min": TIMER_BREADTH_MIN,
         "index_lookback": TIMER_INDEX_LOOKBACK, "basket": "top 10 by 60-session median turnover"},
    ),
    Bot(
        "bot_combined", "b1", BUY, (5, 10, 20),
        "Combined: only when the market timer is on; candidates are the top 20 of the momentum score and the top 20 of the "
        "ranker spec, minus every E2/E4 avoid hit; ranked by the mean of the two percentile ranks. Top 10.",
        {"candidates_each": COMBINED_CANDIDATES, "uses": ["bot_momentum", "bot_ranker_spec", "bot_market_timer", "bot_avoid_e2e4"]},
    ),
)
BOT_BY_NAME = {bot.name: bot for bot in BOTS}

EMPTY = pd.DataFrame(columns=["symbol", "score"])


def _percentile(values: np.ndarray) -> np.ndarray:
    return pd.Series(values).rank(pct=True, method="average").to_numpy()


class League:
    def __init__(self, index: pd.DataFrame, actions: pd.DataFrame, mergers: set[str]) -> None:
        self.index = index[["date", "close"]].sort_values("date")
        self.v0 = model_v0.ModelV0(index, actions, mergers)
        self.mergers = set(mergers)
        self._cache: dict[int, dict[str, Any]] = {}

    def _context(self, panel: es.Panel) -> dict[str, Any]:
        key = id(panel)
        if key in self._cache:
            return self._cache[key]
        traded = ~np.isnan(panel.close)
        log_return = np.log1p(np.nan_to_num(panel.total_return, nan=0.0))
        first = np.array([int(np.flatnonzero(traded[r])[0]) if traded[r].any() else -1 for r in range(traded.shape[0])])
        index = self.index[self.index["date"] <= panel.sessions[-1]]
        states = market_state_labels(index, panel.sessions).to_numpy()
        index_close = index.set_index("date")["close"].reindex(pd.Index(panel.sessions)).ffill().to_numpy()
        context = {
            "traded": traded,
            "cum_lr": np.concatenate([np.zeros((traded.shape[0], 1)), np.cumsum(log_return, axis=1)], axis=1),
            "cum_traded": np.concatenate([np.zeros((traded.shape[0], 1)), np.cumsum(traded, axis=1)], axis=1),
            "cum_corrupt": np.concatenate([np.zeros((traded.shape[0], 1)), np.cumsum(panel.corrupt, axis=1)], axis=1),
            "first": first,
            "states": states,
            "index_close": index_close,
        }
        self._cache = {key: context}
        return context

    def _trailing(self, context: dict[str, Any], start: int, end: int) -> np.ndarray:
        start = max(start, 0)
        return np.expm1(context["cum_lr"][:, end + 1] - context["cum_lr"][:, start])

    def _new_listing(self, panel: es.Panel, context: dict[str, Any], r: int, t: int) -> bool:
        first = context["first"][r]
        return bool(
            panel.symbols[r] not in self.mergers
            and first >= et.LISTING_MIN_PANEL_SESSIONS
            and panel.sessions[first] > et.LISTING_VISIBLE_AFTER
            and t - first < model_v0.NEW_LISTING_SESSIONS
        )

    def _upper_limit_close(self, panel: es.Panel, context: dict[str, Any], r: int, t: int) -> bool:
        previous = np.flatnonzero(context["traded"][r, :t])
        if not len(previous):
            return False
        prev_close = panel.close[r, previous[-1]]
        limit = es.circuit_limit(panel.sessions[t])
        return bool(prev_close > 0 and panel.close[r, t] >= prev_close * (1 + limit - es.LOCK_TOLERANCE))

    def eligible(self, panel: es.Panel, t: int) -> tuple[np.ndarray, np.ndarray]:
        context = self._context(panel)
        lookback = RISK["liquidity_lookback_sessions"]
        lo = max(0, t - lookback + 1)
        members = np.flatnonzero(context["traded"][:, t])
        traded_count = context["cum_traded"][:, t + 1] - context["cum_traded"][:, lo]
        corrupt = context["cum_corrupt"][:, t + 1] - context["cum_corrupt"][:, max(0, t - RANKER_MOMENTUM_LOOKBACK + 1)] > 0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            turnover = np.nanmedian(np.where(context["traded"][:, lo : t + 1], panel.turnover[:, lo : t + 1], np.nan), axis=1)
        keep = []
        for r in members:
            if not spec.valid_symbol(panel.symbols[r]):
                continue
            if traded_count[r] < RISK["min_traded_sessions_in_lookback"] or not turnover[r] >= RISK["min_median_turnover_npr"]:
                continue
            if corrupt[r] or self._upper_limit_close(panel, context, r, t):
                continue
            keep.append(r)
        return np.array(keep, dtype=int), turnover

    def avoid_symbols(self, panel: es.Panel, t: int) -> set[str]:
        return set(self.v0.avoid_hits(panel, t)["symbol"])

    @staticmethod
    def _cap(panel: es.Panel, rows: np.ndarray, scores: np.ndarray, limit: int) -> pd.DataFrame:
        if not len(rows):
            return EMPTY.copy()
        order = np.lexsort((np.array([panel.symbols[r] for r in rows]), -scores))
        per_sector: dict[Any, int] = {}
        chosen = []
        for i in order:
            sector = panel.sector[rows[i]]
            if per_sector.get(sector, 0) >= RISK["max_calls_per_sector"]:
                continue
            per_sector[sector] = per_sector.get(sector, 0) + 1
            chosen.append(i)
            if len(chosen) >= limit:
                break
        return pd.DataFrame({"symbol": [panel.symbols[rows[i]] for i in chosen], "score": [float(scores[i]) for i in chosen]})

    def momentum_scores(self, panel: es.Panel, t: int) -> tuple[np.ndarray, np.ndarray]:
        context = self._context(panel)
        rows, _ = self.eligible(panel, t)
        rows = np.array([r for r in rows if not self._new_listing(panel, context, r, t)], dtype=int)
        if t < MOMENTUM_LOOKBACK or not len(rows):
            return np.array([], dtype=int), np.array([])
        return rows, self._trailing(context, t - MOMENTUM_LOOKBACK + 1, t)[rows]

    def ranker_scores(self, panel: es.Panel, t: int) -> tuple[np.ndarray, np.ndarray]:
        context = self._context(panel)
        rows, turnover = self.eligible(panel, t)
        if t < RANKER_MOMENTUM_LOOKBACK or not len(rows):
            return np.array([], dtype=int), np.array([])
        momentum = self._trailing(context, t - RANKER_MOMENTUM_LOOKBACK + 1, t - RANKER_MOMENTUM_SKIP)[rows]
        reversal = -self._trailing(context, t - RANKER_REVERSAL_LOOKBACK + 1, t)[rows]
        window = panel.total_return[rows, t - RANKER_VOLATILITY_LOOKBACK + 1 : t + 1]
        with np.errstate(all="ignore"):
            volatility = -np.nanstd(window, axis=1)
        volatility = np.nan_to_num(volatility, nan=-np.inf)
        score = (_percentile(momentum) + _percentile(reversal) + _percentile(volatility) + _percentile(turnover[rows])) / 4
        return rows, score

    def timer_on(self, panel: es.Panel, t: int) -> bool:
        context = self._context(panel)
        if context["states"][t] not in TIMER_ON_STATES or t < max(TIMER_BREADTH_SMA, TIMER_INDEX_LOOKBACK):
            return False
        closes = pd.DataFrame(panel.close[:, t - TIMER_BREADTH_SMA + 1 : t + 1].T).ffill().to_numpy().T
        members = np.flatnonzero(context["traded"][:, t])
        if not len(members):
            return False
        with np.errstate(all="ignore"):
            sma = np.nanmean(closes[members], axis=1)
            breadth = float(np.mean(panel.close[members, t] > sma))
        level = context["index_close"]
        start = level[t - TIMER_INDEX_LOOKBACK]
        return bool(breadth >= TIMER_BREADTH_MIN and np.isfinite(start) and start > 0 and level[t] / start - 1 > 0)

    def select_avoid(self, panel: es.Panel, t: int) -> pd.DataFrame:
        hits = self.v0.avoid_hits(panel, t)
        if hits.empty:
            return EMPTY.copy()
        symbols = sorted(set(hits["symbol"]))
        return pd.DataFrame({"symbol": symbols, "score": [float(len(hits[hits["symbol"] == s])) for s in symbols]})

    def select_momentum(self, panel: es.Panel, t: int) -> pd.DataFrame:
        if self._context(panel)["states"][t] == "market_bear":
            return EMPTY.copy()
        rows, scores = self.momentum_scores(panel, t)
        return self._cap(panel, rows, scores, RISK["max_calls_per_day"])

    def select_new_listing(self, panel: es.Panel, t: int) -> pd.DataFrame:
        context = self._context(panel)
        if context["states"][t] == "market_bear" or t < MOMENTUM_LOOKBACK:
            return EMPTY.copy()
        rows = []
        for r in np.flatnonzero(context["traded"][:, t]):
            if not spec.valid_symbol(panel.symbols[r]) or not self._new_listing(panel, context, r, t):
                continue
            if self._upper_limit_close(panel, context, r, t):
                continue
            rows.append(r)
        rows = np.array(rows, dtype=int)
        if not len(rows):
            return EMPTY.copy()
        scores = self._trailing(context, t - MOMENTUM_LOOKBACK + 1, t)[rows]
        return self._cap(panel, rows, scores, NEW_LISTING_PICKS)

    def select_ranker(self, panel: es.Panel, t: int) -> pd.DataFrame:
        rows, scores = self.ranker_scores(panel, t)
        return self._cap(panel, rows, scores, RISK["max_calls_per_day"])

    def select_timer(self, panel: es.Panel, t: int) -> pd.DataFrame:
        if not self.timer_on(panel, t):
            return EMPTY.copy()
        rows, turnover = self.eligible(panel, t)
        return self._cap(panel, rows, turnover[rows] / 1e6, RISK["max_calls_per_day"])

    def select_combined(self, panel: es.Panel, t: int) -> pd.DataFrame:
        if not self.timer_on(panel, t):
            return EMPTY.copy()
        m_rows, m_scores = self.momentum_scores(panel, t)
        r_rows, r_scores = self.ranker_scores(panel, t)
        if not len(m_rows) or not len(r_rows):
            return EMPTY.copy()
        m_pct = dict(zip(m_rows, _percentile(m_scores)))
        r_pct = dict(zip(r_rows, _percentile(r_scores)))
        top_m = [m_rows[i] for i in np.argsort(-m_scores, kind="stable")[:COMBINED_CANDIDATES]]
        top_r = [r_rows[i] for i in np.argsort(-r_scores, kind="stable")[:COMBINED_CANDIDATES]]
        avoided = self.avoid_symbols(panel, t)
        candidates = sorted({r for r in top_m + top_r if r in m_pct and r in r_pct and panel.symbols[r] not in avoided})
        if not candidates:
            return EMPTY.copy()
        rows = np.array(candidates, dtype=int)
        scores = np.array([(m_pct[r] + r_pct[r]) / 2 for r in rows])
        return self._cap(panel, rows, scores, RISK["max_calls_per_day"])

    def selector(self, name: str) -> Callable[[es.Panel, int], pd.DataFrame]:
        return {
            "bot_avoid_e2e4": self.select_avoid,
            "bot_momentum": self.select_momentum,
            "bot_new_listing": self.select_new_listing,
            "bot_ranker_spec": self.select_ranker,
            "bot_market_timer": self.select_timer,
            "bot_combined": self.select_combined,
        }[name]


def bot_parameters(bot: Bot) -> dict[str, Any]:
    return {"league": LEAGUE, "side": bot.side, "primary_horizons": list(bot.primary_horizons), "risk": RISK,
            "probability": None, **bot.parameters}


def strategies(index: pd.DataFrame, actions: pd.DataFrame, mergers: set[str]) -> list[Strategy]:
    league = League(index, actions, mergers)
    return [Strategy(bot.name, bot.version, None, league.selector(bot.name), bot_parameters(bot)) for bot in BOTS]
