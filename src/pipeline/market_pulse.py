from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text

from src.database.connection import get_session
from src.database.models import IntradayFloorsheet, IntradayIndexSnapshot, IntradaySnapshot, MarketIndex
from src.pipeline.data_quality import _trailing_trading_days
from src.scrapers import nepse_api

TOP_BROKER_COUNT = 5
TURNOVER_TRAILING_DAYS = 20
NEPT_OFFSET = timezone(timedelta(hours=5, minutes=45))

AD_RATIO_STRONG_THRESHOLD = Decimal("2.0")
AD_RATIO_BROAD_THRESHOLD = Decimal("1.5")


def _today_npt() -> date:
    return datetime.now(timezone.utc).astimezone(NEPT_OFFSET).date()


def _load_intraday_price_changes(today: date) -> tuple[list[tuple[str, Decimal]], str] | None:
    with get_session() as session:
        rows = session.execute(
            select(IntradaySnapshot.symbol, IntradaySnapshot.percent_change, IntradaySnapshot.snapshot_time)
            .where(func.date(IntradaySnapshot.snapshot_time) == today)
            .order_by(IntradaySnapshot.symbol, IntradaySnapshot.snapshot_time.desc())
        ).all()

    if not rows:
        return None

    latest_by_symbol: dict[str, Decimal] = {}
    for symbol, percent_change, _snapshot_time in rows:
        if symbol not in latest_by_symbol and percent_change is not None:
            latest_by_symbol[symbol] = percent_change

    if not latest_by_symbol:
        return None

    return [(symbol, pct) for symbol, pct in latest_by_symbol.items()], "intraday_snapshots"


def _load_daily_price_changes(today: date) -> tuple[list[tuple[str, Decimal]], str]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT dp.symbol, dp.close, prev.close AS prev_close
                FROM daily_prices dp
                JOIN companies c ON c.symbol = dp.symbol
                JOIN LATERAL (
                    SELECT close FROM daily_prices p
                    WHERE p.symbol = dp.symbol AND p.date < dp.date
                    ORDER BY p.date DESC LIMIT 1
                ) prev ON true
                WHERE dp.date = :d AND prev.close > 0
                  AND c.instrument_type = 'Equity' AND c.status = 'A'
                """
            ),
            {"d": today},
        ).all()

    changes = [
        (row.symbol, (row.close - row.prev_close) / row.prev_close * Decimal(100))
        for row in rows
    ]
    return changes, "daily_prices"


def _compute_advance_decline(today: date) -> dict[str, Any]:
    intraday_result = _load_intraday_price_changes(today)
    if intraday_result is not None:
        changes, source = intraday_result
    else:
        changes, source = _load_daily_price_changes(today)

    advances = sum(1 for _s, pct in changes if pct > 0)
    declines = sum(1 for _s, pct in changes if pct < 0)
    unchanged = sum(1 for _s, pct in changes if pct == 0)

    if declines == 0 and advances == 0:
        ratio = None
        interpretation = "no_data"
    elif declines == 0:
        ratio = None
        interpretation = "strongly_positive"
    elif advances == 0:
        ratio = Decimal(0)
        interpretation = "strongly_negative"
    else:
        ratio = Decimal(advances) / Decimal(declines)
        if ratio >= AD_RATIO_STRONG_THRESHOLD:
            interpretation = "strongly_positive"
        elif ratio >= AD_RATIO_BROAD_THRESHOLD:
            interpretation = "broadly_positive"
        elif ratio <= Decimal(1) / AD_RATIO_STRONG_THRESHOLD:
            interpretation = "strongly_negative"
        elif ratio <= Decimal(1) / AD_RATIO_BROAD_THRESHOLD:
            interpretation = "broadly_negative"
        else:
            interpretation = "mixed"

    return {
        "source": source,
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "total_symbols": len(changes),
        "advance_decline_ratio": ratio,
        "interpretation": interpretation,
    }


def _compute_nepse_index(today: date) -> dict[str, Any]:
    with get_session() as session:
        row = session.execute(
            select(IntradayIndexSnapshot)
            .where(
                IntradayIndexSnapshot.index_name == "NEPSE Index",
                func.date(IntradayIndexSnapshot.snapshot_time) == today,
            )
            .order_by(IntradayIndexSnapshot.snapshot_time.desc())
            .limit(1)
        ).scalar_one_or_none()

        if row is not None:
            return {
                "source": "intraday_index_snapshots",
                "current_value": row.current_value,
                "percent_change": row.percent_change,
                "points_change": row.points_change,
            }

        row = session.execute(
            select(MarketIndex).where(MarketIndex.index_name == "NEPSE Index", MarketIndex.date == today)
        ).scalar_one_or_none()

        if row is not None:
            return {
                "source": "market_index",
                "current_value": row.close,
                "percent_change": row.percent_change,
                "points_change": row.points_change,
            }

    try:
        live_rows = nepse_api.get_nepse_index()
        live_row = next((r for r in live_rows if r.get("index") == "NEPSE Index"), None)
        if live_row is not None:
            return {
                "source": "live_fallback",
                "current_value": live_row.get("currentValue"),
                "percent_change": live_row.get("perChange"),
                "points_change": live_row.get("change"),
            }
    except Exception:
        pass

    return {"source": "unavailable", "current_value": None, "percent_change": None, "points_change": None}


def _compute_broker_concentration(today: date, total_market_turnover: Decimal | None) -> dict[str, Any]:
    with get_session() as session:
        rows = session.execute(
            select(
                IntradayFloorsheet.buyer_broker_id,
                IntradayFloorsheet.seller_broker_id,
                IntradayFloorsheet.contract_amount,
                IntradayFloorsheet.symbol,
            ).where(func.date(IntradayFloorsheet.snapshot_time) == today)
        ).all()

    if not rows:
        return {
            "available": False,
            "symbols_covered": 0,
            "top_brokers": [],
            "note": "no floorsheet data captured for today",
        }

    broker_totals: dict[str, Decimal] = {}
    symbols_covered: set[str] = set()
    floorsheet_turnover = Decimal(0)

    for buyer_id, seller_id, amount, symbol in rows:
        if amount is None:
            continue
        symbols_covered.add(symbol)
        floorsheet_turnover += amount
        if buyer_id:
            broker_totals[buyer_id] = broker_totals.get(buyer_id, Decimal(0)) + amount
        if seller_id:
            broker_totals[seller_id] = broker_totals.get(seller_id, Decimal(0)) + amount

    ranked = sorted(broker_totals.items(), key=lambda kv: kv[1], reverse=True)[:TOP_BROKER_COUNT]
    denominator = total_market_turnover if total_market_turnover else floorsheet_turnover

    top_brokers = [
        {
            "broker_id": broker_id,
            "total_contract_value": value,
            "fraction_of_total_market_turnover": (value / denominator) if denominator else None,
        }
        for broker_id, value in ranked
    ]

    return {
        "available": True,
        "symbols_covered": len(symbols_covered),
        "floorsheet_turnover": floorsheet_turnover,
        "top_brokers": top_brokers,
        "note": (
            "broker totals are limited to symbols with captured floorsheet data "
            f"({len(symbols_covered)} symbols); fraction_of_total_market_turnover uses the true "
            "market-wide turnover as denominator, so this understates true concentration if "
            "floorsheet coverage is partial"
        ),
    }


def _compute_turnover_trend(today: date) -> dict[str, Any]:
    with get_session() as session:
        today_turnover = session.execute(
            text("SELECT sum(turnover) FROM daily_prices WHERE date = :d"), {"d": today}
        ).scalar_one()

        trailing_days = _trailing_trading_days(today, TURNOVER_TRAILING_DAYS)
        trailing_avg = None
        if trailing_days:
            trailing_avg = session.execute(
                text("SELECT sum(turnover) / :n FROM daily_prices WHERE date = ANY(:days)"),
                {"days": trailing_days, "n": len(trailing_days)},
            ).scalar_one()

    ratio = None
    if trailing_avg:
        ratio = today_turnover / trailing_avg if today_turnover is not None else None

    return {
        "today_turnover": today_turnover,
        "trailing_20_day_avg_turnover": trailing_avg,
        "turnover_vs_trailing_avg_ratio": ratio,
    }


def compute_overall_market_pulse(snapshot_time: datetime | None = None) -> dict[str, Any]:
    today = snapshot_time.date() if snapshot_time is not None else _today_npt()

    advance_decline = _compute_advance_decline(today)
    nepse_index = _compute_nepse_index(today)
    turnover_trend = _compute_turnover_trend(today)
    broker_concentration = _compute_broker_concentration(today, turnover_trend["today_turnover"])

    return {
        "date": today,
        "advance_decline": advance_decline,
        "nepse_index": nepse_index,
        "broker_concentration": broker_concentration,
        "turnover_trend": turnover_trend,
    }


if __name__ == "__main__":
    import pprint

    pprint.pprint(compute_overall_market_pulse())
