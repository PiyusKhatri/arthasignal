from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import IntradaySnapshot, PriceAlert, PriceAlertCondition, SignalAlert
from src.pipeline.market_pulse import compute_active_signals

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_price_alerts() -> dict[str, Any]:
    with get_session() as session:
        alerts = session.execute(select(PriceAlert).where(PriceAlert.is_active == True)).scalars().all()  # noqa: E712

        if not alerts:
            return {"alerts_checked": 0, "alerts_triggered": 0}

        symbols = {a.symbol for a in alerts}
        latest_prices: dict[str, Decimal] = {}
        for symbol in symbols:
            price = session.execute(
                select(IntradaySnapshot.ltp)
                .where(IntradaySnapshot.symbol == symbol)
                .where(IntradaySnapshot.ltp.is_not(None))
                .order_by(IntradaySnapshot.snapshot_time.desc())
                .limit(1)
            ).scalar_one_or_none()
            if price is not None:
                latest_prices[symbol] = Decimal(str(price))

        triggered_count = 0
        now = datetime.now(timezone.utc)
        for alert in alerts:
            price = latest_prices.get(alert.symbol)
            if price is None:
                continue

            target = Decimal(str(alert.target_price))
            condition_met = (
                price >= target if alert.condition == PriceAlertCondition.ABOVE else price <= target
            )
            if condition_met:
                alert.triggered_at = now
                alert.is_active = False
                triggered_count += 1

    logger.info("check_price_alerts: checked=%d triggered=%d", len(alerts), triggered_count)
    return {"alerts_checked": len(alerts), "alerts_triggered": triggered_count}


def check_signal_alerts() -> dict[str, Any]:
    active_signals_data = compute_active_signals()
    symbols_by_signal_name: dict[str, list[str]] = {}
    for row in active_signals_data["signals"]:
        symbols_by_signal_name.setdefault(row["signal_name"], []).append(row["symbol"])

    with get_session() as session:
        alerts = session.execute(select(SignalAlert).where(SignalAlert.is_active == True)).scalars().all()  # noqa: E712

        triggered_count = 0
        now = datetime.now(timezone.utc)
        for alert in alerts:
            matching_symbols = symbols_by_signal_name.get(alert.signal_name, [])

            if alert.symbol is not None:
                if alert.symbol in matching_symbols:
                    alert.triggered_at = now
                    alert.triggered_symbol = alert.symbol
                    alert.is_active = False
                    triggered_count += 1
            elif matching_symbols:
                alert.triggered_at = now
                alert.triggered_symbol = matching_symbols[0]
                alert.is_active = False
                triggered_count += 1

    logger.info("check_signal_alerts: checked=%d triggered=%d", len(alerts), triggered_count)
    return {"alerts_checked": len(alerts), "alerts_triggered": triggered_count}
