from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text

from src.database.connection import get_session
from src.database.models import (
    Broker,
    Company,
    DailyPrice,
    IntradayFloorsheet,
    IntradayIndexSnapshot,
    IntradaySnapshot,
    MarketIndex,
    SectorIndexMapping,
    SignalConfidence,
    SignalTimeframe,
    SymbolLiquidityTier,
    TechnicalSignal,
)
from src.pipeline.data_quality import _trailing_trading_days
from src.pipeline.extract_signal_calls import DOJI_REQUIRED_LIQUIDITY_TIER, DOJI_SIGNAL_NAME, TARGET_SIGNAL_HORIZONS
from src.pipeline.run_signal_backtests import build_signal_conditions
from src.scrapers import nepse_api

TOP_BROKER_COUNT = 5
TURNOVER_TRAILING_DAYS = 20
NEPT_OFFSET = timezone(timedelta(hours=5, minutes=45))

AD_RATIO_STRONG_THRESHOLD = Decimal("2.0")
AD_RATIO_BROAD_THRESHOLD = Decimal("1.5")

MIN_SECTOR_FLOORSHEET_COVERAGE_FRACTION = Decimal("0.5")

VOLUME_TRAILING_DAYS = 20
VOLUME_SPIKE_RATIO_THRESHOLD = Decimal("2.0")
VOLUME_DROUGHT_RATIO_THRESHOLD = Decimal("0.3")
SINGLE_BROKER_VOLUME_SHARE_THRESHOLD = Decimal("0.15")


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


def _load_broker_names() -> dict[str, str]:
    with get_session() as session:
        rows = session.execute(select(Broker.broker_id, Broker.broker_name)).all()
    return {broker_id: broker_name for broker_id, broker_name in rows}


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
    broker_names = _load_broker_names()

    top_brokers = [
        {
            "broker_id": broker_id,
            "broker_name": broker_names.get(broker_id),
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
    broker_names: dict[str, str],
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
            "broker_name": broker_names.get(broker_id),
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
    broker_names = _load_broker_names()

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
            floorsheet_by_sector.get(sector, []), today_turnover, total_sector_symbols, broker_names
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


def compute_active_signals() -> dict[str, Any]:
    with get_session() as session:
        latest_date = session.execute(
            select(func.max(TechnicalSignal.date)).where(TechnicalSignal.timeframe == SignalTimeframe.DAILY)
        ).scalar_one_or_none()

        if latest_date is None:
            return {"as_of_date": None, "signals": []}

        rows = session.execute(
            select(TechnicalSignal, DailyPrice.close, Company.company_name, Company.sector)
            .join(DailyPrice, (DailyPrice.symbol == TechnicalSignal.symbol) & (DailyPrice.date == TechnicalSignal.date))
            .join(Company, Company.symbol == TechnicalSignal.symbol)
            .where(TechnicalSignal.timeframe == SignalTimeframe.DAILY)
            .where(TechnicalSignal.date == latest_date)
            .where(Company.instrument_type == "Equity")
            .where(Company.status == "A")
        ).all()

        symbols = [row.TechnicalSignal.symbol for row in rows]
        prev_closes: dict[str, Decimal] = {}
        if symbols:
            prev_rows = session.execute(
                text(
                    """
                    SELECT dp.symbol, prev.close AS prev_close
                    FROM daily_prices dp
                    JOIN LATERAL (
                        SELECT close FROM daily_prices p
                        WHERE p.symbol = dp.symbol AND p.date < dp.date
                        ORDER BY p.date DESC LIMIT 1
                    ) prev ON true
                    WHERE dp.symbol = ANY(:symbols) AND dp.date = :d
                    """
                ),
                {"symbols": symbols, "d": latest_date},
            ).all()
            prev_closes = {row.symbol: row.prev_close for row in prev_rows}

        liquidity_rows = session.execute(select(SymbolLiquidityTier.symbol, SymbolLiquidityTier.liquidity_tier)).all()
        liquidity_by_symbol = {row.symbol: row.liquidity_tier for row in liquidity_rows}

        confidence_rows = (
            session.execute(select(SignalConfidence).where(SignalConfidence.signal_name.in_(TARGET_SIGNAL_HORIZONS)))
            .scalars()
            .all()
        )
        session.expunge_all()

    confidence_by_name = {row.signal_name: row for row in confidence_rows}
    conditions = build_signal_conditions()

    results = []
    for signal_row, close_price, company_name, sector in rows:
        prev_close = prev_closes.get(signal_row.symbol)
        percent_change = None
        if prev_close:
            percent_change = (close_price - prev_close) / prev_close * Decimal(100)

        liquidity_tier = liquidity_by_symbol.get(signal_row.symbol)

        for signal_name in TARGET_SIGNAL_HORIZONS:
            active = bool(conditions[signal_name](signal_row, close_price, None, None))
            if signal_name == DOJI_SIGNAL_NAME and liquidity_tier != DOJI_REQUIRED_LIQUIDITY_TIER:
                active = False
            if not active:
                continue

            confidence = confidence_by_name.get(signal_name)
            results.append(
                {
                    "symbol": signal_row.symbol,
                    "company_name": company_name,
                    "sector": sector,
                    "latest_close": close_price,
                    "percent_change": percent_change,
                    "signal_name": signal_name,
                    "tier": confidence.tier.value if confidence is not None else None,
                    "avg_win_rate_minus_baseline": confidence.avg_win_rate_minus_baseline if confidence is not None else None,
                    "recommended_holding_period": confidence.recommended_holding_period if confidence is not None else None,
                }
            )

    results.sort(
        key=lambda r: r["avg_win_rate_minus_baseline"] if r["avg_win_rate_minus_baseline"] is not None else Decimal("-Infinity"),
        reverse=True,
    )
    return {"as_of_date": latest_date, "signals": results}


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


def _load_symbol_volumes(today: date) -> dict[str, int]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT dp.symbol, dp.volume
                FROM daily_prices dp
                JOIN companies c ON c.symbol = dp.symbol
                WHERE dp.date = :d AND c.instrument_type = 'Equity' AND c.status = 'A'
                """
            ),
            {"d": today},
        ).all()
    return {row.symbol: row.volume for row in rows}


def _load_symbol_trailing_avg_volumes(today: date) -> dict[str, Decimal]:
    trailing_days = _trailing_trading_days(today, VOLUME_TRAILING_DAYS)
    if not trailing_days:
        return {}
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT dp.symbol, sum(dp.volume) / :n AS avg_volume
                FROM daily_prices dp
                JOIN companies c ON c.symbol = dp.symbol
                WHERE dp.date = ANY(:days) AND c.instrument_type = 'Equity' AND c.status = 'A'
                GROUP BY dp.symbol
                """
            ),
            {"days": trailing_days, "n": len(trailing_days)},
        ).all()
    return {row.symbol: row.avg_volume for row in rows}


def _load_symbol_broker_quantities(today: date) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT symbol, buyer_broker_id, seller_broker_id, contract_quantity
                FROM intraday_floorsheet
                WHERE date_trunc('day', snapshot_time) = :d
                """
            ),
            {"d": today},
        ).all()

    buy_totals_by_symbol: dict[str, dict[str, int]] = {}
    sell_totals_by_symbol: dict[str, dict[str, int]] = {}
    for row in rows:
        if row.contract_quantity is None:
            continue
        if row.buyer_broker_id:
            symbol_totals = buy_totals_by_symbol.setdefault(row.symbol, {})
            symbol_totals[row.buyer_broker_id] = symbol_totals.get(row.buyer_broker_id, 0) + row.contract_quantity
        if row.seller_broker_id:
            symbol_totals = sell_totals_by_symbol.setdefault(row.symbol, {})
            symbol_totals[row.seller_broker_id] = symbol_totals.get(row.seller_broker_id, 0) + row.contract_quantity

    return buy_totals_by_symbol, sell_totals_by_symbol


def _compute_volume_spikes_and_droughts(
    volumes: dict[str, int], trailing_avgs: dict[str, Decimal]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    spikes = []
    droughts = []

    for symbol, volume in volumes.items():
        avg_volume = trailing_avgs.get(symbol)
        if not avg_volume:
            continue

        ratio = Decimal(volume) / avg_volume
        entry = {
            "symbol": symbol,
            "today_volume": volume,
            "trailing_20_day_avg_volume": avg_volume,
            "ratio": ratio,
        }
        if ratio > VOLUME_SPIKE_RATIO_THRESHOLD:
            spikes.append(entry)
        elif ratio < VOLUME_DROUGHT_RATIO_THRESHOLD:
            droughts.append(entry)

    spikes.sort(key=lambda e: e["ratio"], reverse=True)
    droughts.sort(key=lambda e: e["ratio"])
    return spikes, droughts


def _flag_concentrated_side(
    side: str, totals_by_symbol: dict[str, dict[str, int]], volumes: dict[str, int]
) -> list[dict[str, Any]]:
    flags = []
    for symbol, broker_totals in totals_by_symbol.items():
        total_volume = volumes.get(symbol)
        if not total_volume:
            continue

        for broker_id, quantity in broker_totals.items():
            share = Decimal(quantity) / Decimal(total_volume)
            if share > SINGLE_BROKER_VOLUME_SHARE_THRESHOLD:
                flags.append(
                    {
                        "symbol": symbol,
                        "broker_id": broker_id,
                        "side": side,
                        "broker_quantity": quantity,
                        "today_volume": total_volume,
                        "share_of_volume": share,
                    }
                )
    return flags


def _compute_concentrated_broker_activity(
    volumes: dict[str, int],
    buy_totals_by_symbol: dict[str, dict[str, int]],
    sell_totals_by_symbol: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    flags = _flag_concentrated_side("buy", buy_totals_by_symbol, volumes)
    flags += _flag_concentrated_side("sell", sell_totals_by_symbol, volumes)
    flags.sort(key=lambda e: e["share_of_volume"], reverse=True)
    return flags


def compute_volume_anomalies(snapshot_time: datetime | None = None) -> dict[str, Any]:
    today = snapshot_time.date() if snapshot_time is not None else _today_npt()

    volumes = _load_symbol_volumes(today)
    trailing_avgs = _load_symbol_trailing_avg_volumes(today)
    buy_totals_by_symbol, sell_totals_by_symbol = _load_symbol_broker_quantities(today)

    volume_spikes, volume_droughts = _compute_volume_spikes_and_droughts(volumes, trailing_avgs)
    concentrated_broker_activity = _compute_concentrated_broker_activity(
        volumes, buy_totals_by_symbol, sell_totals_by_symbol
    )

    return {
        "date": today,
        "volume_spikes": volume_spikes,
        "volume_droughts": volume_droughts,
        "concentrated_broker_activity": concentrated_broker_activity,
    }


if __name__ == "__main__":
    import pprint

    pprint.pprint(compute_overall_market_pulse())
