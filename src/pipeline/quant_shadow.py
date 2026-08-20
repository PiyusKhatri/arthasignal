from __future__ import annotations

import bisect
import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import Company, CorporateAction, DailyPrice, MarketIndex
from src.database.quant_models import QuantShadowSignal
from src.services.nepse_quant_research import NEPSE_INDEX_NAME, build_quant_research
from src.services.quant_features import DEFAULT_HORIZON_DAYS, FEATURE_VERSION, ROUND_TRIP_COST_PERCENT

logger = logging.getLogger(__name__)
VOID_SEARCH_CAP_TRADING_DAYS = 3


def _latest_entry_prices(session, symbol: str) -> tuple[date, float, float] | None:
    price_row = session.execute(
        select(DailyPrice.date, DailyPrice.close)
        .where(DailyPrice.symbol == symbol)
        .order_by(DailyPrice.date.desc())
        .limit(1)
    ).first()
    if price_row is None:
        return None

    market_close = session.execute(
        select(MarketIndex.close)
        .where(MarketIndex.index_name == NEPSE_INDEX_NAME, MarketIndex.date <= price_row.date)
        .order_by(MarketIndex.date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if market_close is None:
        return None
    return price_row.date, float(price_row.close), float(market_close)


def capture_quant_shadow_signals(limit: int = 300) -> dict[str, Any]:
    with get_session() as session:
        symbols = session.execute(
            select(Company.symbol)
            .where(Company.instrument_type == "Equity", Company.status == "A")
            .order_by(Company.symbol)
            .limit(limit)
        ).scalars().all()

        rows: list[dict[str, Any]] = []
        skipped = 0
        failures: list[str] = []
        created_at = datetime.now(timezone.utc).replace(tzinfo=None)

        for symbol in symbols:
            try:
                research = build_quant_research(session, symbol)
                if not research:
                    skipped += 1
                    continue
                decision = research.get("decision", {})
                probability = decision.get("probability_outperform_nepse_after_cost")
                if probability is None:
                    skipped += 1
                    continue

                prices = _latest_entry_prices(session, symbol)
                if prices is None:
                    skipped += 1
                    continue
                as_of_date, entry_price, market_entry = prices

                rows.append(
                    {
                        "symbol": symbol,
                        "as_of_date": as_of_date,
                        "horizon_days": DEFAULT_HORIZON_DAYS,
                        "feature_version": FEATURE_VERSION,
                        "decision": str(decision.get("research_label") or "neutral"),
                        "probability_outperform": float(probability),
                        "confidence_score": int(decision.get("confidence_score") or 0),
                        "expected_excess_return_percent": decision.get("expected_excess_return_20d_percent"),
                        "entry_price": entry_price,
                        "market_entry": market_entry,
                        "status": "pending",
                        "created_at": created_at,
                    }
                )
            except Exception as exc:
                logger.exception("Failed to capture quant shadow signal for %s", symbol)
                failures.append(f"{symbol}:{type(exc).__name__}")

        inserted = 0
        if rows:
            stmt = pg_insert(QuantShadowSignal).values(rows)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["symbol", "as_of_date", "horizon_days", "feature_version"]
            ).returning(QuantShadowSignal.id)
            inserted = len(session.execute(stmt).fetchall())

    summary = {
        "feature_version": FEATURE_VERSION,
        "symbols_considered": len(symbols),
        "rows_prepared": len(rows),
        "rows_inserted": inserted,
        "skipped": skipped,
        "failures": len(failures),
        "failure_samples": failures[:10],
    }
    logger.info("Quant shadow capture summary: %s", summary)
    return summary


def _market_trading_dates(session) -> list[date]:
    return list(
        session.execute(
            select(MarketIndex.date)
            .where(MarketIndex.index_name == NEPSE_INDEX_NAME)
            .order_by(MarketIndex.date)
        ).scalars().all()
    )


def _target_date(entry_date: date, horizon_days: int, trading_dates: list[date]) -> date | None:
    start = bisect.bisect_right(trading_dates, entry_date)
    target_index = start + horizon_days - 1
    if target_index >= len(trading_dates):
        return None
    return trading_dates[target_index]


def _cutoff_date(target_date: date, trading_dates: list[date]) -> date | None:
    idx = bisect.bisect_left(trading_dates, target_date)
    cutoff_index = idx + VOID_SEARCH_CAP_TRADING_DAYS
    if cutoff_index >= len(trading_dates):
        return None
    return trading_dates[cutoff_index]


def grade_quant_shadow_signals(as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    with get_session() as session:
        trading_dates = _market_trading_dates(session)
        pending = session.execute(
            select(QuantShadowSignal)
            .where(QuantShadowSignal.status == "pending", QuantShadowSignal.feature_version == FEATURE_VERSION)
            .order_by(QuantShadowSignal.as_of_date, QuantShadowSignal.symbol)
        ).scalars().all()

        resolved = 0
        voided = 0
        not_ready = 0
        wins = 0
        losses = 0

        for row in pending:
            target = _target_date(row.as_of_date, row.horizon_days, trading_dates)
            if target is None or target > as_of:
                not_ready += 1
                continue
            cutoff = _cutoff_date(target, trading_dates)
            effective_cutoff = min(cutoff, as_of) if cutoff is not None else as_of

            resolution = session.execute(
                select(DailyPrice.date, DailyPrice.close)
                .where(
                    DailyPrice.symbol == row.symbol,
                    DailyPrice.date >= target,
                    DailyPrice.date <= effective_cutoff,
                )
                .order_by(DailyPrice.date)
                .limit(1)
            ).first()

            if resolution is None:
                if cutoff is None or cutoff > as_of:
                    not_ready += 1
                    continue
                row.status = "void"
                row.void_reason = "No stock trade within the allowed resolution window."
                voided += 1
                continue

            action_exists = session.execute(
                select(CorporateAction.id)
                .where(
                    CorporateAction.symbol == row.symbol,
                    CorporateAction.action_date > row.as_of_date,
                    CorporateAction.action_date <= resolution.date,
                )
                .limit(1)
            ).scalar_one_or_none()
            if action_exists is not None:
                row.status = "void"
                row.void_reason = "Corporate action occurred between capture and resolution; raw-price grading would be distorted."
                voided += 1
                continue

            market_resolution = session.execute(
                select(MarketIndex.close)
                .where(MarketIndex.index_name == NEPSE_INDEX_NAME, MarketIndex.date <= resolution.date)
                .order_by(MarketIndex.date.desc())
                .limit(1)
            ).scalar_one_or_none()
            if market_resolution is None:
                not_ready += 1
                continue

            entry_price = float(row.entry_price)
            market_entry = float(row.market_entry)
            resolution_price = float(resolution.close)
            market_resolution_value = float(market_resolution)
            stock_return = (resolution_price / entry_price - 1.0) * 100.0
            market_return = (market_resolution_value / market_entry - 1.0) * 100.0
            excess_return = stock_return - market_return
            success = excess_return > ROUND_TRIP_COST_PERCENT

            row.status = "resolved"
            row.resolution_date = resolution.date
            row.resolution_price = resolution_price
            row.market_resolution = market_resolution_value
            row.realized_stock_return_percent = stock_return
            row.realized_market_return_percent = market_return
            row.realized_excess_return_percent = excess_return
            row.success_after_cost = success
            resolved += 1
            if success:
                wins += 1
            else:
                losses += 1

    summary = {
        "feature_version": FEATURE_VERSION,
        "pending_before_run": len(pending),
        "resolved": resolved,
        "voided": voided,
        "not_ready": not_ready,
        "wins": wins,
        "losses": losses,
    }
    logger.info("Quant shadow grading summary: %s", summary)
    return summary


def run_quant_shadow_cycle(limit: int = 300) -> dict[str, Any]:
    grading = grade_quant_shadow_signals()
    capture = capture_quant_shadow_signals(limit=limit)
    from src.pipeline.quant_validation import build_quant_validation_status

    validation = build_quant_validation_status()
    return {"grading": grading, "capture": capture, "validation": validation}


if __name__ == "__main__":
    run_quant_shadow_cycle()
