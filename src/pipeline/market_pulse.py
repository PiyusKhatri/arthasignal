from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text

from src.database.connection import get_session
from src.database.models import (
    Company,
    IntradayFloorsheet,
    IntradayIndexSnapshot,
    IntradaySnapshot,
    MarketIndex,
    SectorIndexMapping,
)
from src.pipeline.data_quality import _trailing_trading_days
from src.scrapers import nepse_api

TOP_BROKER_COUNT = 5
TURNOVER_TRAILING_DAYS = 20
NEPT_OFFSET = timezone(timedelta(hours=5, minutes=45))

AD_RATIO_STRONG_THRESHOLD = Decimal("2.0")
AD_RATIO_BROAD_THRESHOLD = Decimal("1.5")

MIN_SECTOR_FLOORSHEET_COVERAGE_FRACTION = Decimal("0.5")


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


def _ad_ratio_interpretation(advances: int, declines: int) -> tuple[Decimal | None, str]:
    if declines == 0 and advances == 0:
        return None, "no_data"
    if declines == 0:
        return None, "strongly_positive"
    if advances == 0:
        return Decimal(0), "strongly_negative"

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
    return ratio, interpretation


def _summarize_advance_decline(changes: list[Decimal]) -> dict[str, Any]:
    advances = sum(1 for pct in changes if pct > 0)
    declines = sum(1 for pct in changes if pct < 0)
    unchanged = sum(1 for pct in changes if pct == 0)
    ratio, interpretation = _ad_ratio_interpretation(advances, declines)
    return {
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "total_symbols": len(changes),
        "advance_decline_ratio": ratio,
        "interpretation": interpretation,
    }


def _compute_advance_decline(today: date) -> dict[str, Any]:
    intraday_result = _load_intraday_price_changes(today)
    if intraday_result is not None:
        changes, source = intraday_result
    else:
        changes, source = _load_daily_price_changes(today)

    summary = _summarize_advance_decline([pct for _s, pct in changes])
    summary["source"] = source
    return summary


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


def _load_sector_index_mapping() -> dict[str, str]:
    with get_session() as session:
        rows = session.execute(select(SectorIndexMapping.companies_sector, SectorIndexMapping.market_index_name)).all()
    return {sector: index_name for sector, index_name in rows}


def _load_price_changes_by_sector(today: date) -> tuple[dict[str, list[tuple[str, Decimal]]], str]:
    with get_session() as session:
        intraday_rows = session.execute(
            select(
                IntradaySnapshot.symbol,
                Company.sector,
                IntradaySnapshot.percent_change,
                IntradaySnapshot.snapshot_time,
            )
            .join(Company, Company.symbol == IntradaySnapshot.symbol)
            .where(
                func.date(IntradaySnapshot.snapshot_time) == today,
                Company.instrument_type == "Equity",
                Company.status == "A",
            )
            .order_by(IntradaySnapshot.symbol, IntradaySnapshot.snapshot_time.desc())
        ).all()

    if intraday_rows:
        latest_by_symbol: dict[str, tuple[str, Decimal]] = {}
        for symbol, sector, percent_change, _snapshot_time in intraday_rows:
            if symbol not in latest_by_symbol and percent_change is not None and sector is not None:
                latest_by_symbol[symbol] = (sector, percent_change)
        if latest_by_symbol:
            by_sector: dict[str, list[tuple[str, Decimal]]] = {}
            for symbol, (sector, pct) in latest_by_symbol.items():
                by_sector.setdefault(sector, []).append((symbol, pct))
            return by_sector, "intraday_snapshots"

    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT dp.symbol, c.sector, dp.close, prev.close AS prev_close
                FROM daily_prices dp
                JOIN companies c ON c.symbol = dp.symbol
                JOIN LATERAL (
                    SELECT close FROM daily_prices p
                    WHERE p.symbol = dp.symbol AND p.date < dp.date
                    ORDER BY p.date DESC LIMIT 1
                ) prev ON true
                WHERE dp.date = :d AND prev.close > 0
                  AND c.instrument_type = 'Equity' AND c.status = 'A' AND c.sector IS NOT NULL
                """
            ),
            {"d": today},
        ).all()

    by_sector = {}
    for row in rows:
        pct = (row.close - row.prev_close) / row.prev_close * Decimal(100)
        by_sector.setdefault(row.sector, []).append((row.symbol, pct))
    return by_sector, "daily_prices"


def _load_market_caps_by_sector(today: date) -> dict[str, dict[str, Decimal]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT DISTINCT ON (f.symbol) f.symbol, c.sector, f.market_capitalization
                FROM fundamentals f
                JOIN companies c ON c.symbol = f.symbol
                WHERE f.reported_date <= :d AND f.market_capitalization IS NOT NULL
                  AND c.instrument_type = 'Equity' AND c.status = 'A' AND c.sector IS NOT NULL
                ORDER BY f.symbol, f.reported_date DESC
                """
            ),
            {"d": today},
        ).all()

    by_sector: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        by_sector.setdefault(row.sector, {})[row.symbol] = row.market_capitalization
    return by_sector


def _load_sector_index_performance(sector_index_map: dict[str, str], today: date) -> dict[str, dict[str, Any]]:
    index_names = list(sector_index_map.values())
    result: dict[str, dict[str, Any]] = {}

    with get_session() as session:
        intraday_rows = session.execute(
            select(IntradayIndexSnapshot)
            .where(
                IntradayIndexSnapshot.index_name.in_(index_names),
                func.date(IntradayIndexSnapshot.snapshot_time) == today,
            )
            .order_by(IntradayIndexSnapshot.index_name, IntradayIndexSnapshot.snapshot_time.desc())
        ).scalars().all()

        for row in intraday_rows:
            if row.index_name not in result:
                result[row.index_name] = {
                    "source": "intraday_index_snapshots",
                    "current_value": row.current_value,
                    "percent_change": row.percent_change,
                    "points_change": row.points_change,
                }

        remaining = [name for name in index_names if name not in result]
        if remaining:
            market_index_rows = session.execute(
                select(MarketIndex).where(MarketIndex.index_name.in_(remaining), MarketIndex.date == today)
            ).scalars().all()
            for row in market_index_rows:
                result[row.index_name] = {
                    "source": "market_index",
                    "current_value": row.close,
                    "percent_change": row.percent_change,
                    "points_change": row.points_change,
                }

    remaining = [name for name in index_names if name not in result]
    if remaining:
        try:
            live_rows = nepse_api.get_nepse_index()
            live_by_name = {r.get("index"): r for r in live_rows}
        except Exception:
            live_by_name = {}
        for name in remaining:
            live_row = live_by_name.get(name)
            if live_row is not None:
                result[name] = {
                    "source": "live_fallback",
                    "current_value": live_row.get("currentValue"),
                    "percent_change": live_row.get("perChange"),
                    "points_change": live_row.get("change"),
                }

    for name in index_names:
        if name not in result:
            result[name] = {"source": "unavailable", "current_value": None, "percent_change": None, "points_change": None}

    return result


def _load_sector_turnover(today: date) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    with get_session() as session:
        today_rows = session.execute(
            text(
                """
                SELECT c.sector, sum(dp.turnover) AS turnover
                FROM daily_prices dp
                JOIN companies c ON c.symbol = dp.symbol
                WHERE dp.date = :d AND c.instrument_type = 'Equity' AND c.status = 'A' AND c.sector IS NOT NULL
                GROUP BY c.sector
                """
            ),
            {"d": today},
        ).all()
        today_turnover = {row.sector: row.turnover for row in today_rows}

        trailing_days = _trailing_trading_days(today, TURNOVER_TRAILING_DAYS)
        trailing_avg: dict[str, Decimal] = {}
        if trailing_days:
            trailing_rows = session.execute(
                text(
                    """
                    SELECT c.sector, sum(dp.turnover) / :n AS avg_turnover
                    FROM daily_prices dp
                    JOIN companies c ON c.symbol = dp.symbol
                    WHERE dp.date = ANY(:days) AND c.instrument_type = 'Equity' AND c.status = 'A'
                      AND c.sector IS NOT NULL
                    GROUP BY c.sector
                    """
                ),
                {"days": trailing_days, "n": len(trailing_days)},
            ).all()
            trailing_avg = {row.sector: row.avg_turnover for row in trailing_rows}

    return today_turnover, trailing_avg


def _load_floorsheet_by_sector(today: date) -> dict[str, list[tuple[str, str, Decimal, str]]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT c.sector, f.buyer_broker_id, f.seller_broker_id, f.contract_amount, f.symbol
                FROM intraday_floorsheet f
                JOIN companies c ON c.symbol = f.symbol
                WHERE date_trunc('day', f.snapshot_time) = :d AND c.sector IS NOT NULL
                """
            ),
            {"d": today},
        ).all()

    by_sector: dict[str, list[tuple[str, str, Decimal, str]]] = {}
    for row in rows:
        by_sector.setdefault(row.sector, []).append((row.buyer_broker_id, row.seller_broker_id, row.contract_amount, row.symbol))
    return by_sector


def _load_sector_symbol_counts() -> dict[str, int]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT sector, count(*) FROM companies
                WHERE instrument_type = 'Equity' AND status = 'A' AND sector IS NOT NULL
                GROUP BY sector
                """
            )
        ).all()
    return {row.sector: row.count for row in rows}


def _compute_sector_broker_concentration(
    floorsheet_rows: list[tuple[str, str, Decimal, str]],
    sector_turnover: Decimal | None,
    total_sector_symbols: int,
) -> dict[str, Any]:
    if not floorsheet_rows:
        return {
            "available": False,
            "symbols_covered": 0,
            "total_sector_symbols": total_sector_symbols,
            "top_brokers": [],
            "note": "no floorsheet data captured for this sector today",
        }

    broker_totals: dict[str, Decimal] = {}
    symbols_covered: set[str] = set()
    floorsheet_turnover = Decimal(0)

    for buyer_id, seller_id, amount, symbol in floorsheet_rows:
        if amount is None:
            continue
        symbols_covered.add(symbol)
        floorsheet_turnover += amount
        if buyer_id:
            broker_totals[buyer_id] = broker_totals.get(buyer_id, Decimal(0)) + amount
        if seller_id:
            broker_totals[seller_id] = broker_totals.get(seller_id, Decimal(0)) + amount

    ranked = sorted(broker_totals.items(), key=lambda kv: kv[1], reverse=True)[:TOP_BROKER_COUNT]
    denominator = sector_turnover if sector_turnover else floorsheet_turnover

    coverage_fraction = (
        Decimal(len(symbols_covered)) / Decimal(total_sector_symbols) if total_sector_symbols else Decimal(0)
    )
    coverage_reliable = coverage_fraction >= MIN_SECTOR_FLOORSHEET_COVERAGE_FRACTION

    top_brokers = [
        {
            "broker_id": broker_id,
            "total_contract_value": value,
            "fraction_of_sector_turnover": (value / denominator) if denominator else None,
        }
        for broker_id, value in ranked
    ]

    note = (
        f"broker totals cover {len(symbols_covered)}/{total_sector_symbols} sector symbols "
        f"({coverage_fraction:.0%}); "
    )
    note += (
        "coverage is broad enough to be indicative"
        if coverage_reliable
        else "coverage is too thin to trust as representative of the whole sector"
    )

    return {
        "available": True,
        "coverage_reliable": coverage_reliable,
        "symbols_covered": len(symbols_covered),
        "total_sector_symbols": total_sector_symbols,
        "floorsheet_turnover": floorsheet_turnover,
        "top_brokers": top_brokers,
        "note": note,
    }


def compute_sector_wise_pulse(snapshot_time: datetime | None = None) -> list[dict[str, Any]]:
    today = snapshot_time.date() if snapshot_time is not None else _today_npt()

    sector_index_map = _load_sector_index_mapping()
    changes_by_sector, changes_source = _load_price_changes_by_sector(today)
    market_caps_by_sector = _load_market_caps_by_sector(today)
    sector_index_performance = _load_sector_index_performance(sector_index_map, today)
    today_turnover_by_sector, trailing_avg_by_sector = _load_sector_turnover(today)
    floorsheet_by_sector = _load_floorsheet_by_sector(today)
    sector_symbol_counts = _load_sector_symbol_counts()

    results = []
    for sector, index_name in sector_index_map.items():
        sector_changes = changes_by_sector.get(sector, [])
        advance_decline = _summarize_advance_decline([pct for _s, pct in sector_changes])
        advance_decline["source"] = changes_source

        market_caps = market_caps_by_sector.get(sector, {})
        weighted_numerator = Decimal(0)
        weighted_denominator = Decimal(0)
        symbols_used = 0
        symbols_excluded_no_market_cap = 0
        for symbol, pct in sector_changes:
            cap = market_caps.get(symbol)
            if cap is None:
                symbols_excluded_no_market_cap += 1
                continue
            weighted_numerator += cap * pct
            weighted_denominator += cap
            symbols_used += 1

        weighted_avg_pct = (weighted_numerator / weighted_denominator) if weighted_denominator else None

        today_turnover = today_turnover_by_sector.get(sector)
        trailing_avg = trailing_avg_by_sector.get(sector)
        turnover_ratio = (today_turnover / trailing_avg) if today_turnover is not None and trailing_avg else None

        total_sector_symbols = sector_symbol_counts.get(sector, 0)
        broker_concentration = _compute_sector_broker_concentration(
            floorsheet_by_sector.get(sector, []), today_turnover, total_sector_symbols
        )

        results.append(
            {
                "sector": sector,
                "advance_decline": advance_decline,
                "sector_index": {"index_name": index_name, **sector_index_performance[index_name]},
                "market_cap_weighted_percent_change": weighted_avg_pct,
                "market_cap_weighting": {
                    "symbols_used": symbols_used,
                    "symbols_excluded_no_market_cap": symbols_excluded_no_market_cap,
                },
                "turnover_trend": {
                    "today_turnover": today_turnover,
                    "trailing_20_day_avg_turnover": trailing_avg,
                    "turnover_vs_trailing_avg_ratio": turnover_ratio,
                },
                "broker_concentration": broker_concentration,
            }
        )

    results.sort(
        key=lambda r: r["market_cap_weighted_percent_change"]
        if r["market_cap_weighted_percent_change"] is not None
        else Decimal("-Infinity"),
        reverse=True,
    )
    return results


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
