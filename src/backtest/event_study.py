from __future__ import annotations

import bisect
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Hashable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from src.backtest.stats import adjusted_alpha, clustered_mean_interval

FIRST_REAL_OPEN_SESSION = date(2018, 2, 18)
CIRCUIT_RULE_CHANGE = date(2026, 4, 20)
LOCK_TOLERANCE = 0.005
CORRUPT_BUFFER = 0.02
RIGHTS_SUBSCRIPTION_PRICE = 100.0
DIVIDEND_TAX = 0.05
MAX_ENTRY_DELAY = 5
MAX_EXIT_DELAY = 20
PRE_WINDOW = 10
POST_WINDOW = 60
COST_LEVELS = (0.005, 0.010, 0.015)
BLOCK_SESSIONS = 20

ENTRY_OPEN = "next_open"
ENTRY_CLOSE = "next_close"
EXIT_ON_TIME = "on_time"
EXIT_DELAYED = "delayed"
EXIT_STRANDED = "stranded"


def circuit_limit(day: date) -> float:
    return 0.15 if day >= CIRCUIT_RULE_CHANGE else 0.10


@dataclass(frozen=True)
class Panel:
    sessions: tuple[date, ...]
    symbols: tuple[str, ...]
    sector: tuple[str | None, ...]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    turnover: np.ndarray
    total_return: np.ndarray
    one_session_return: np.ndarray
    corrupt: np.ndarray
    action_sessions: Mapping[str, tuple[int, ...]]
    row: Mapping[str, int] = field(default_factory=dict)
    col: Mapping[date, int] = field(default_factory=dict)

    def index_of(self, day: date) -> int | None:
        return self.col.get(day)

    def first_session_on_or_after(self, day: date) -> int | None:
        position = bisect.bisect_left(self.sessions, day)
        return position if position < len(self.sessions) else None


def _action_effects(actions: pd.DataFrame, sessions: Sequence[date]) -> dict[str, dict[int, list[tuple[str, float]]]]:
    effects: dict[str, dict[int, list[tuple[str, float]]]] = defaultdict(lambda: defaultdict(list))
    for symbol, action_date, kind, value in actions[["symbol", "action_date", "action_type", "ratio_or_amount"]].itertuples(index=False):
        position = bisect.bisect_left(sessions, action_date)
        if position >= len(sessions) or value is None or float(value) <= 0:
            continue
        effects[symbol][position].append((str(kind).upper(), float(value)))
    return effects


def _apply_actions(close: float, entries: Iterable[tuple[str, float]]) -> float:
    shares = 1.0
    extra = 0.0
    order = {"DIVIDEND": 0, "BONUS": 1, "RIGHT": 2}
    for kind, value in sorted(entries, key=lambda item: order.get(item[0], 3)):
        if kind == "BONUS":
            shares *= 1.0 + value / 100.0
        elif kind == "RIGHT":
            ratio = value / 100.0
            extra -= RIGHTS_SUBSCRIPTION_PRICE * ratio * shares
            shares *= 1.0 + ratio
        elif kind == "DIVIDEND":
            extra += value * (1.0 - DIVIDEND_TAX) * shares
    return close * shares + extra


def build_panel(
    prices: pd.DataFrame,
    actions: pd.DataFrame,
    sectors: Mapping[str, str | None],
    sessions: Sequence[date] | None = None,
) -> Panel:
    prices = prices.sort_values(["symbol", "date"])
    session_list = tuple(sorted(set(prices["date"]))) if sessions is None else tuple(sessions)
    col = {day: i for i, day in enumerate(session_list)}
    symbols = tuple(sorted(prices["symbol"].unique()))
    row = {symbol: i for i, symbol in enumerate(symbols)}
    shape = (len(symbols), len(session_list))
    arrays = {name: np.full(shape, np.nan) for name in ("open", "high", "low", "close", "volume", "turnover")}
    rows = prices["symbol"].map(row).to_numpy()
    cols = prices["date"].map(col).to_numpy()
    keep = ~pd.isna(cols)
    for name in arrays:
        column = name if name in prices.columns else None
        if column is None:
            continue
        arrays[name][rows[keep], cols[keep].astype(int)] = prices.loc[keep, column].astype(float).to_numpy()

    effects = _action_effects(actions, session_list)
    total = np.full(shape, np.nan)
    one = np.full(shape, np.nan)
    corrupt = np.zeros(shape, dtype=bool)
    action_sessions: dict[str, tuple[int, ...]] = {}
    for symbol, i in row.items():
        symbol_effects = effects.get(symbol, {})
        action_sessions[symbol] = tuple(sorted(symbol_effects))
        traded = np.flatnonzero(~np.isnan(arrays["close"][i]))
        for previous, current in zip(traded, traded[1:]):
            prev_close = arrays["close"][i, previous]
            if prev_close <= 0:
                continue
            entries = [e for s in range(previous + 1, current + 1) for e in symbol_effects.get(s, [])]
            value = _apply_actions(arrays["close"][i, current], entries)
            ret = value / prev_close - 1.0
            gap = current - previous
            limit = circuit_limit(session_list[current]) + CORRUPT_BUFFER
            if ret > (1 + limit) ** gap - 1 or ret < (1 - limit) ** gap - 1:
                corrupt[i, current] = True
                continue
            total[i, current] = ret
            if gap == 1:
                one[i, current] = ret
    return Panel(
        sessions=session_list,
        symbols=symbols,
        sector=tuple(sectors.get(symbol) for symbol in symbols),
        total_return=total,
        one_session_return=one,
        corrupt=corrupt,
        action_sessions=action_sessions,
        row=row,
        col=col,
        **arrays,
    )


def benchmark_returns(panel: Panel, members: np.ndarray | None = None) -> np.ndarray:
    values = panel.one_session_return if members is None else panel.one_session_return[members]
    with np.errstate(invalid="ignore"):
        counts = np.sum(~np.isnan(values), axis=0)
        sums = np.nansum(values, axis=0)
    out = np.where(counts > 0, sums / np.maximum(counts, 1), 0.0)
    return out


def sector_benchmarks(panel: Panel) -> dict[str | None, np.ndarray]:
    sectors = np.array([s if s else "" for s in panel.sector])
    return {sector or None: benchmark_returns(panel, sectors == sector) for sector in set(sectors.tolist())}


def _cumulative(daily: np.ndarray, start: int, end: int) -> float:
    if end <= start:
        return 0.0
    return float(np.prod(1.0 + daily[start + 1 : end + 1]) - 1.0)


def stock_cumulative(panel: Panel, symbol_row: int, start: int, end: int) -> float | None:
    if end <= start:
        return 0.0
    segment = panel.total_return[symbol_row, start + 1 : end + 1]
    if panel.corrupt[symbol_row, start + 1 : end + 1].any():
        return None
    return float(np.prod(1.0 + np.nan_to_num(segment, nan=0.0)) - 1.0)


def event_paths(
    panel: Panel,
    events: pd.DataFrame,
    pre: int = PRE_WINDOW,
    post: int = POST_WINDOW,
    universe: np.ndarray | None = None,
    sectors: Mapping[str | None, np.ndarray] | None = None,
) -> pd.DataFrame:
    universe = benchmark_returns(panel) if universe is None else universe
    sectors = sector_benchmarks(panel) if sectors is None else sectors
    offsets = list(range(-pre, post + 1))
    records = []
    for event in events.itertuples(index=False):
        symbol_row = panel.row.get(event.symbol)
        anchor = panel.first_session_on_or_after(event.event_date)
        record: dict[str, Any] = {"symbol": event.symbol, "event_date": event.event_date, "anchor": anchor}
        if symbol_row is None or anchor is None:
            records.append({**record, "status": "no_prices"})
            continue
        reference = anchor - pre - 1
        if reference < 0 or np.isnan(panel.close[symbol_row, : reference + 1]).all():
            records.append({**record, "status": "no_reference"})
            continue
        sector_daily = sectors.get(panel.sector[symbol_row], universe)
        record["status"] = "ok"
        record["anchor_date"] = panel.sessions[anchor]
        for k in offsets:
            end = anchor + k
            if end >= len(panel.sessions):
                record[f"u{k}"] = np.nan
                record[f"s{k}"] = np.nan
                continue
            stock = stock_cumulative(panel, symbol_row, reference, end)
            if stock is None:
                record[f"u{k}"] = np.nan
                record[f"s{k}"] = np.nan
                continue
            record[f"u{k}"] = stock - _cumulative(universe, reference, end)
            record[f"s{k}"] = stock - _cumulative(sector_daily, reference, end)
        records.append(record)
    return pd.DataFrame.from_records(records)


def window_abnormal(
    panel: Panel,
    symbol: str,
    anchor: int,
    start: int,
    end: int,
    benchmark: np.ndarray,
) -> float | None:
    symbol_row = panel.row[symbol]
    first = anchor + start - 1
    last = anchor + end
    if first < 0 or last >= len(panel.sessions):
        return None
    stock = stock_cumulative(panel, symbol_row, first, last)
    if stock is None:
        return None
    return stock - _cumulative(benchmark, first, last)


def _locked_up(panel: Panel, r: int, c: int) -> bool:
    previous = panel.close[r, :c]
    traded = np.flatnonzero(~np.isnan(previous))
    if not len(traded):
        return False
    prev_close = previous[traded[-1]]
    limit = circuit_limit(panel.sessions[c])
    return bool(panel.low[r, c] >= prev_close * (1 + limit - LOCK_TOLERANCE))


def _locked_down(panel: Panel, r: int, c: int) -> bool:
    previous = panel.close[r, :c]
    traded = np.flatnonzero(~np.isnan(previous))
    if not len(traded):
        return False
    prev_close = previous[traded[-1]]
    limit = circuit_limit(panel.sessions[c])
    return bool(panel.high[r, c] <= prev_close * (1 - limit + LOCK_TOLERANCE))


@dataclass(frozen=True)
class Trade:
    symbol: str
    knowledge_date: date
    entry_date: date
    entry_price: float
    entry_rule: str
    entry_delay: int
    exit_date: date
    exit_status: str
    exit_delay: int
    gross_return: float
    universe_return: float
    sector_return: float
    entry_index: int
    exit_index: int


def simulate_trade(
    panel: Panel,
    symbol: str,
    knowledge_index: int,
    hold_sessions: int,
    universe: np.ndarray,
    sector_daily: np.ndarray,
    last_index: int | None = None,
) -> tuple[Trade | None, str]:
    r = panel.row.get(symbol)
    if r is None:
        return None, "no_prices"
    last_index = len(panel.sessions) - 1 if last_index is None else last_index
    entry = None
    rule = None
    for delay in range(1, MAX_ENTRY_DELAY + 1):
        c = knowledge_index + delay
        if c > last_index:
            return None, "no_entry_session"
        if np.isnan(panel.close[r, c]):
            continue
        if _locked_up(panel, r, c):
            continue
        if panel.sessions[c] >= FIRST_REAL_OPEN_SESSION and panel.open[r, c] > 0:
            entry, rule, price = c, ENTRY_OPEN, float(panel.open[r, c])
        else:
            entry, rule, price = c, ENTRY_CLOSE, float(panel.close[r, c])
        break
    if entry is None:
        return None, "not_filled"
    target = entry + hold_sessions - (1 if rule == ENTRY_OPEN else 0)
    if target > last_index:
        return None, "exit_after_window"
    exit_index = None
    status = EXIT_ON_TIME
    for delay in range(0, MAX_EXIT_DELAY + 1):
        c = target + delay
        if c > last_index:
            break
        if np.isnan(panel.close[r, c]) or _locked_down(panel, r, c):
            continue
        exit_index = c
        status = EXIT_ON_TIME if delay == 0 else EXIT_DELAYED
        break
    if exit_index is None:
        traded = np.flatnonzero(~np.isnan(panel.close[r, entry : min(target + MAX_EXIT_DELAY, last_index) + 1]))
        exit_index = entry + int(traded[-1])
        status = EXIT_STRANDED
    if rule == ENTRY_OPEN:
        first_leg = panel.close[r, entry] / price - 1.0
        rest = stock_cumulative(panel, r, entry, exit_index)
        bench_start = entry - 1
    else:
        first_leg = 0.0
        rest = stock_cumulative(panel, r, entry, exit_index)
        bench_start = entry
    if rest is None:
        return None, "corrupt_price"
    gross = (1 + first_leg) * (1 + rest) - 1
    return (
        Trade(
            symbol=symbol,
            knowledge_date=panel.sessions[knowledge_index],
            entry_date=panel.sessions[entry],
            entry_price=price,
            entry_rule=rule,
            entry_delay=entry - knowledge_index,
            exit_date=panel.sessions[exit_index],
            exit_status=status,
            exit_delay=exit_index - target if status != EXIT_STRANDED else MAX_EXIT_DELAY,
            gross_return=gross,
            universe_return=_cumulative(universe, bench_start, exit_index),
            sector_return=_cumulative(sector_daily, bench_start, exit_index),
            entry_index=entry,
            exit_index=exit_index,
        ),
        "filled",
    )


def simulate_trades(
    panel: Panel,
    events: pd.DataFrame,
    hold_sessions: int,
    last_index: int | None = None,
    universe: np.ndarray | None = None,
    sectors: Mapping[str | None, np.ndarray] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    universe = benchmark_returns(panel) if universe is None else universe
    sectors = sector_benchmarks(panel) if sectors is None else sectors
    outcomes: dict[str, int] = defaultdict(int)
    trades = []
    for event in events.itertuples(index=False):
        knowledge = panel.index_of(event.knowledge_date)
        if knowledge is None:
            outcomes["knowledge_not_a_session"] += 1
            continue
        r = panel.row.get(event.symbol)
        sector_daily = sectors.get(panel.sector[r], universe) if r is not None else universe
        trade, outcome = simulate_trade(panel, event.symbol, knowledge, hold_sessions, universe, sector_daily, last_index)
        outcomes[outcome] += 1
        if trade is not None:
            trades.append({**event._asdict(), **trade.__dict__})
    return pd.DataFrame(trades), dict(outcomes)


def interval(values_by_cluster: Mapping[Hashable, Sequence[float]], alpha: float) -> dict[str, Any] | None:
    result = clustered_mean_interval(values_by_cluster, alpha)
    if result is None:
        return None
    return {
        "mean_pct": round(result.mean * 100, 3),
        "low_pct": round(result.low * 100, 3),
        "high_pct": round(result.high * 100, 3),
        "clusters": result.clusters,
        "observations": result.observations,
        "alpha": round(alpha, 6),
    }


def clustered_intervals(
    frame: pd.DataFrame,
    value: str,
    alpha: float,
    date_column: str = "entry_date",
    block_column: str | None = "entry_index",
) -> dict[str, Any]:
    clean = frame[frame[value].notna()]
    by_date: dict[Any, list[float]] = defaultdict(list)
    for day, v in zip(clean[date_column], clean[value]):
        by_date[day].append(float(v))
    out: dict[str, Any] = {"by_date": interval(by_date, alpha)}
    if block_column is not None:
        by_block: dict[Any, list[float]] = defaultdict(list)
        for position, v in zip(clean[block_column], clean[value]):
            by_block[int(position) // BLOCK_SESSIONS].append(float(v))
        out["by_20_session_block"] = interval(by_block, alpha)
    lows = [i["low_pct"] for i in out.values() if i is not None]
    out["conservative_low_pct"] = min(lows) if lows else None
    return out


@dataclass
class TestCounter:
    family: str
    base_alpha: float = 0.05
    planned: int | None = None
    tests: list[str] = field(default_factory=list)

    def record(self, name: str) -> float:
        if name in self.tests:
            raise ValueError(f"test {name} was already run in family {self.family}")
        self.tests.append(name)
        if self.planned is not None and len(self.tests) > self.planned:
            raise ValueError(f"family {self.family} planned {self.planned} tests but ran {len(self.tests)}")
        return self.alpha

    @property
    def count(self) -> int:
        return max(len(self.tests), self.planned or 0)

    @property
    def alpha(self) -> float:
        return adjusted_alpha(self.base_alpha, self.count)


def summarize_trades(
    trades: pd.DataFrame,
    alpha: float,
    benchmark: str = "universe_return",
    costs: Sequence[float] = COST_LEVELS,
) -> dict[str, Any]:
    if trades.empty:
        return {"trades": 0}
    out: dict[str, Any] = {
        "trades": int(len(trades)),
        "entry_dates": int(trades["entry_date"].nunique()),
        "mean_gross_pct": round(float(trades["gross_return"].mean()) * 100, 3),
        "mean_benchmark_pct": round(float(trades[benchmark].mean()) * 100, 3),
        "exit_status": trades["exit_status"].value_counts().to_dict(),
        "entry_rule": trades["entry_rule"].value_counts().to_dict(),
    }
    for cost in costs:
        column = f"excess_{cost}"
        trades = trades.assign(**{column: trades["gross_return"] - cost - trades[benchmark]})
        out[f"excess_{cost * 100:.1f}%"] = clustered_intervals(trades, column, alpha)
    return out


def ceil_fraction(count: int, fraction: float) -> int:
    return int(math.ceil(count * fraction))
