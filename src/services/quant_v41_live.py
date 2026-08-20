from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import Company, SymbolLiquidityTier
from src.services.nepse_quant_research import NEPSE_INDEX_NAME, _load_index_series, _load_stock_series, _sector_index_name
from src.services.quant_features import build_feature_row
from src.services.quant_historical_context import index_context_as_of
from src.services.quant_residual_alpha import build_baseline_candidate_pool

CURRENT_LIQUIDITY_MAP = {
    "high_liquidity": "high",
    "medium_liquidity": "medium",
}


def _regime_context(
    market_context: dict[str, float | None],
    sector_context: dict[str, float | None],
) -> dict[str, float | None]:
    market_return = market_context.get("return_20d_percent")
    sector_return = sector_context.get("return_20d_percent")
    return {
        "market_close": market_context.get("close"),
        "market_return_20d_percent": market_return,
        "market_return_60d_percent": market_context.get("return_60d_percent"),
        "market_sma50": market_context.get("sma50"),
        "market_sma200": market_context.get("sma200"),
        "market_drawdown_252d_percent": market_context.get("drawdown_252d_percent"),
        "market_annualized_volatility_60d_percent": market_context.get("annualized_volatility_60d_percent"),
        "sector_return_20d_percent": sector_return,
        "sector_relative_strength_20d_percent": (
            float(sector_return) - float(market_return)
            if sector_return is not None and market_return is not None
            else None
        ),
    }


def build_current_v41_rows(session: Session, *, limit: int = 300) -> dict[str, Any]:
    """Build today's V4.1 feature-only universe using observable information only.

    A stock must have traded on the latest NEPSE index date. This deliberately
    avoids inventing an executable entry for a stale/suspended name.
    """
    market = _load_index_series(session, NEPSE_INDEX_NAME)
    if not market.get("dates") or not market.get("closes"):
        return {"as_of_date": None, "rows": [], "candidates": [], "reason": "missing_market_index"}

    as_of_date = market["dates"][-1]
    market_context = index_context_as_of(market, as_of_date)
    market_close = market_context.get("close")
    if market_close is None:
        return {"as_of_date": as_of_date, "rows": [], "candidates": [], "reason": "missing_market_close"}

    records = session.execute(
        select(Company, SymbolLiquidityTier.liquidity_tier)
        .join(SymbolLiquidityTier, SymbolLiquidityTier.symbol == Company.symbol)
        .where(
            Company.instrument_type == "Equity",
            Company.status == "A",
            SymbolLiquidityTier.liquidity_tier.in_(tuple(CURRENT_LIQUIDITY_MAP)),
        )
        .order_by(SymbolLiquidityTier.avg_daily_turnover.desc(), Company.symbol)
        .limit(limit)
    ).all()

    sector_series: dict[str, dict[str, list[Any]]] = {}
    sector_context: dict[str, dict[str, float | None]] = {}
    rows: list[dict[str, Any]] = []
    skipped_stale = 0
    skipped_features = 0

    for company, tier in records:
        stock = _load_stock_series(session, company.symbol)
        if not stock.get("dates") or stock["dates"][-1] != as_of_date:
            skipped_stale += 1
            continue
        if len(stock.get("closes", [])) < 61:
            skipped_features += 1
            continue

        sector_key = str(company.sector or "")
        if sector_key not in sector_series:
            sector_index_name = _sector_index_name(session, company.sector)
            sector_series[sector_key] = _load_index_series(session, sector_index_name)
            sector_context[sector_key] = index_context_as_of(sector_series[sector_key], as_of_date)

        features = build_feature_row(
            dates=stock["dates"],
            closes=stock["closes"],
            volumes=stock["volumes"],
            turnovers=stock["turnovers"],
            index=len(stock["closes"]) - 1,
            market_dates=market["dates"],
            market_closes=market["closes"],
            sector_dates=sector_series[sector_key].get("dates", []),
            sector_closes=sector_series[sector_key].get("closes", []),
        )
        if features is None:
            skipped_features += 1
            continue

        tier_value = str(getattr(tier, "value", tier))
        bucket = CURRENT_LIQUIDITY_MAP.get(tier_value)
        if bucket is None:
            continue
        rows.append(
            {
                "date": as_of_date,
                "symbol": company.symbol,
                "sector": company.sector,
                "features": features,
                "liquidity_bucket": bucket,
                "regime_context": _regime_context(market_context, sector_context[sector_key]),
                "entry_price": float(stock["raw_closes"][-1]),
                "market_entry": float(market_close),
            }
        )

    candidates = build_baseline_candidate_pool(rows)
    return {
        "as_of_date": as_of_date,
        "market_entry": float(market_close),
        "rows": rows,
        "candidates": candidates,
        "symbols_considered": len(records),
        "eligible_rows": len(rows),
        "candidate_rows": len(candidates),
        "skipped_stale_or_untraded": skipped_stale,
        "skipped_features": skipped_features,
    }
