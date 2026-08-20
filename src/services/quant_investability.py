from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from statistics import mean
from typing import Any, Sequence

from src.database.connection import get_session
from src.services.nepse_quant_research import NEPSE_INDEX_NAME, _load_index_series, _load_stock_series
from src.services.quant_features import DEFAULT_HORIZON_DAYS
from src.services.quant_robustness import annotate_liquidity_buckets

MAX_HORIZON_SLIPPAGE_SESSIONS = 3


def horizon_slippage_sessions(
    market_dates: Sequence[Any],
    entry_date: Any,
    actual_end_date: Any,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> int | None:
    if not market_dates:
        return None
    entry_index = bisect_right(market_dates, entry_date) - 1
    if entry_index < 0:
        return None
    expected_index = entry_index + horizon_days
    if expected_index >= len(market_dates):
        return None
    actual_index = bisect_right(market_dates, actual_end_date) - 1
    if actual_index < 0:
        return None
    return max(0, actual_index - expected_index)


def _attach_trailing_turnover(rows: Sequence[dict[str, Any]]) -> None:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if symbol:
            by_symbol[symbol].append(row)

    with get_session() as session:
        for symbol, symbol_rows in by_symbol.items():
            stock = _load_stock_series(session, symbol)
            turnovers = stock.get("turnovers", [])
            for row in symbol_rows:
                index = int(row.get("index") or 0)
                window = [
                    float(value or 0.0)
                    for value in turnovers[max(0, index - 19) : index + 1]
                ]
                positive = [value for value in window if value > 0.0]
                row["trailing_turnover_20d"] = mean(positive) if positive else 0.0


def prepare_investable_research_rows(
    rows: list[dict[str, Any]],
    *,
    max_horizon_slippage_sessions: int = MAX_HORIZON_SLIPPAGE_SESSIONS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep research observations closer to a realistically executable 20-session trade.

    Liquidity is measured cross-sectionally on each historical date from trailing
    turnover known at the time. The bottom tercile is excluded. Rows whose 20th
    stock observation resolves more than a few NEPSE sessions after the intended
    20-market-session horizon are also excluded.
    """
    _attach_trailing_turnover(rows)
    annotate_liquidity_buckets(rows)

    with get_session() as session:
        market = _load_index_series(session, NEPSE_INDEX_NAME)
    market_dates = market.get("dates", [])

    low_liquidity = 0
    horizon_delayed = 0
    horizon_unknown = 0
    kept: list[dict[str, Any]] = []

    for row in rows:
        slippage = horizon_slippage_sessions(
            market_dates,
            row["date"],
            row.get("label_end_date"),
        ) if row.get("label_end_date") is not None else None
        row["horizon_slippage_sessions"] = slippage

        if row.get("liquidity_bucket") == "low":
            low_liquidity += 1
            continue
        if slippage is None:
            horizon_unknown += 1
            continue
        if slippage > max_horizon_slippage_sessions:
            horizon_delayed += 1
            continue
        kept.append(row)

    return kept, {
        "rows_before": len(rows),
        "rows_after": len(kept),
        "excluded_low_liquidity": low_liquidity,
        "excluded_horizon_delayed": horizon_delayed,
        "excluded_horizon_unknown": horizon_unknown,
        "max_horizon_slippage_sessions": max_horizon_slippage_sessions,
        "liquidity_rule": "exclude same-date bottom tercile of trailing 20-session average turnover",
        "horizon_rule": (
            f"exclude observations resolving more than {max_horizon_slippage_sessions} NEPSE sessions "
            f"after the intended {DEFAULT_HORIZON_DAYS}-session horizon"
        ),
    }
