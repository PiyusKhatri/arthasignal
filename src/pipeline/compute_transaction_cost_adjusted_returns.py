from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import BacktestResult, TransactionCostAdjustedReturn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TRACKED_SIGNALS = [
    "rsi_14 < 30 (oversold)",
    "rsi_14 > 70 (overbought)",
    "close < bollinger_lower",
    "doji",
    "shooting_star",
    "marubozu_bearish",
]

BROKERAGE_RATE_PER_SIDE = Decimal("0.33")
SEBON_FEE_RATE_PER_SIDE = Decimal("0.015")
DP_CHARGE_NPR = Decimal("25")
ASSUMED_TRADE_SIZE_NPR = Decimal("50000")
DP_CHARGE_PCT = DP_CHARGE_NPR / ASSUMED_TRADE_SIZE_NPR * Decimal("100")
BID_ASK_SPREAD_ESTIMATE_PCT = Decimal("0.50")

COST_SCENARIOS = {
    "official_only": (Decimal(2) * BROKERAGE_RATE_PER_SIDE) + (Decimal(2) * SEBON_FEE_RATE_PER_SIDE) + DP_CHARGE_PCT,
    "with_spread_estimate": (
        (Decimal(2) * BROKERAGE_RATE_PER_SIDE)
        + (Decimal(2) * SEBON_FEE_RATE_PER_SIDE)
        + DP_CHARGE_PCT
        + BID_ASK_SPREAD_ESTIMATE_PCT
    ),
}


def _load_backtest_results() -> dict[str, dict[int, dict[str, Any]]]:
    with get_session() as session:
        rows = session.execute(
            select(BacktestResult).where(BacktestResult.signal_name.in_(TRACKED_SIGNALS))
        ).scalars().all()
        by_signal: dict[str, dict[int, dict[str, Any]]] = {}
        for row in rows:
            by_signal.setdefault(row.signal_name, {})[row.forward_days] = {
                "sample_size": row.sample_size,
                "mean_return": row.mean_return,
            }
        return by_signal


def _upsert(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(TransactionCostAdjustedReturn).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["signal_name", "forward_days", "cost_scenario"],
        set_={
            "sample_size": stmt.excluded.sample_size,
            "mean_return": stmt.excluded.mean_return,
            "round_trip_cost_pct": stmt.excluded.round_trip_cost_pct,
            "adjusted_mean_return": stmt.excluded.adjusted_mean_return,
            "remains_positive": stmt.excluded.remains_positive,
            "computed_at": stmt.excluded.computed_at,
        },
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


def compute_transaction_cost_adjusted_returns() -> dict[str, Any]:
    results_by_signal = _load_backtest_results()
    computed_at = datetime.utcnow()

    rows = []
    for signal_name in TRACKED_SIGNALS:
        per_horizon = results_by_signal.get(signal_name, {})
        for forward_days, stats in per_horizon.items():
            for scenario, round_trip_cost in COST_SCENARIOS.items():
                mean_return = stats["mean_return"]
                adjusted_mean_return = None
                remains_positive = None
                if mean_return is not None:
                    adjusted_mean_return = mean_return - round_trip_cost
                    remains_positive = adjusted_mean_return > 0

                rows.append(
                    {
                        "signal_name": signal_name,
                        "forward_days": forward_days,
                        "cost_scenario": scenario,
                        "sample_size": stats["sample_size"],
                        "mean_return": mean_return,
                        "round_trip_cost_pct": round_trip_cost,
                        "adjusted_mean_return": adjusted_mean_return,
                        "remains_positive": remains_positive,
                        "computed_at": computed_at,
                    }
                )

    stored = _upsert(rows)
    logger.info("Stored %d transaction_cost_adjusted_returns rows", stored)
    return {"rows_stored": stored}


if __name__ == "__main__":
    compute_transaction_cost_adjusted_returns()
