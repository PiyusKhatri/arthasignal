from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Sequence

from src.database.connection import get_session
from src.services.nepse_quant_research import _load_stock_series
from src.services.quant_robustness import annotate_liquidity_buckets


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


def prepare_predictable_investable_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Filter with information that was observable on the prediction date only.

    V4 deliberately does not filter by future horizon slippage or future trading
    availability. Those are legitimate after-the-fact execution diagnostics, but
    using them to decide which historical rows enter the model would leak future
    information into the backtest.
    """
    _attach_trailing_turnover(rows)
    annotate_liquidity_buckets(rows)

    kept = [row for row in rows if row.get("liquidity_bucket") in {"medium", "high"}]
    return kept, {
        "rows_before": len(rows),
        "rows_after": len(kept),
        "excluded_low_liquidity": sum(row.get("liquidity_bucket") == "low" for row in rows),
        "excluded_unclassified_liquidity": sum(
            row.get("liquidity_bucket") not in {"low", "medium", "high"} for row in rows
        ),
        "liquidity_rule": "keep same-date medium/high terciles of trailing 20-observation average turnover",
        "uses_future_horizon_slippage_for_filtering": False,
        "uses_future_trading_availability_for_filtering": False,
        "note": (
            "The legacy label still resolves after 20 stock observations, so thin-name horizon drift remains a diagnostic "
            "limitation. V4 does not use that future drift to improve historical selection."
        ),
    }
