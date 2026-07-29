from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import SymbolLiquidityTier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIN_OBSERVATIONS = 50
LIQUIDITY_TIERS = ("high_liquidity", "medium_liquidity", "low_liquidity")


def _load_median_turnover_by_symbol() -> list[tuple[str, float, int]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT dp.symbol, percentile_cont(0.5) WITHIN GROUP (ORDER BY dp.turnover) AS median_turnover, count(*) AS n
                FROM daily_prices dp
                JOIN companies c ON c.symbol = dp.symbol
                WHERE c.instrument_type = 'Equity' AND c.status = 'A'
                GROUP BY dp.symbol
                HAVING count(*) >= :min_obs
                ORDER BY median_turnover DESC
                """
            ),
            {"min_obs": MIN_OBSERVATIONS},
        ).all()
    return [(r.symbol, float(r.median_turnover), r.n) for r in rows]


def _assign_tiers(ranked: list[tuple[str, float, int]]) -> list[dict[str, Any]]:
    n = len(ranked)
    tier_size = n // 3
    computed_at = datetime.utcnow()

    rows = []
    for i, (symbol, median_turnover, _count) in enumerate(ranked):
        if i < tier_size:
            tier = "high_liquidity"
        elif i < 2 * tier_size:
            tier = "medium_liquidity"
        else:
            tier = "low_liquidity"
        rows.append(
            {
                "symbol": symbol,
                "avg_daily_turnover": median_turnover,
                "liquidity_tier": tier,
                "computed_at": computed_at,
            }
        )
    return rows


def _upsert_liquidity_tiers(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(SymbolLiquidityTier).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol"],
        set_={
            "avg_daily_turnover": stmt.excluded.avg_daily_turnover,
            "liquidity_tier": stmt.excluded.liquidity_tier,
            "computed_at": stmt.excluded.computed_at,
        },
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


def compute_liquidity_tiers() -> dict[str, Any]:
    ranked = _load_median_turnover_by_symbol()
    rows = _assign_tiers(ranked)
    stored = _upsert_liquidity_tiers(rows)

    counts = {tier: sum(1 for r in rows if r["liquidity_tier"] == tier) for tier in LIQUIDITY_TIERS}
    logger.info("Stored %d symbol_liquidity_tier rows: %s", stored, counts)
    return {"symbols_tiered": stored, "tier_counts": counts}


if __name__ == "__main__":
    compute_liquidity_tiers()
