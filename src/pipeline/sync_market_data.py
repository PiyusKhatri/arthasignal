from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from src.database.connection import get_session
from src.database.models import Company, DailyPrice, MarketIndex, TechnicalSignal, TradingCalendar
from src.pipeline.backfill_calendar import is_trading_day, run_calendar_backfill
from src.pipeline.backfill_daily_index import run_daily_index_refresh
from src.pipeline.backfill_signals import run_signals_backfill
from src.pipeline.compute_liquidity_tiers import compute_liquidity_tiers
from src.pipeline.data_quality import check_daily_pipeline_health
from src.pipeline.db_writers import insert_new_daily_prices, insert_new_market_index_rows
from src.pipeline.extract_signal_calls import extract_signal_calls
from src.pipeline.grade_signal_calls import grade_signal_calls
from src.pipeline.market_hours_guard import SESSION_END, _current_npt_time
from src.pipeline.run_daily import run_daily_pipeline
from src.scrapers.index_scraper import INDEX_NAME_TO_ID, get_index_history
from src.scrapers.sharesansar_scraper import get_price_history
from src.scrapers.symbols import get_all_listed_symbols

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_INITIAL_LOOKBACK_DAYS = 45
MIN_HISTORY_WINDOW_YEARS = 0.10
EOD_SETTLE_BUFFER_MINUTES = 15
SIGNAL_CALL_EXTRACTION_LOOKBACK_DAYS = 30


def _latest_date(model: type, column_name: str = "date") -> date | None:
    column = getattr(model, column_name)
    with get_session() as session:
        return session.scalar(select(func.max(column)))


def _active_equity_symbols() -> list[str]:
    with get_session() as session:
        rows = session.scalars(
            select(Company.symbol)
            .where(Company.status == "A")
            .where(Company.instrument_type == "Equity")
            .order_by(Company.symbol)
        ).all()
    if rows:
        return list(rows)
    return get_all_listed_symbols()


def get_data_freshness() -> dict[str, Any]:
    with get_session() as session:
        latest_price = session.scalar(select(func.max(DailyPrice.date)))
        latest_index = session.scalar(select(func.max(MarketIndex.date)))
        latest_technical = session.scalar(select(func.max(TechnicalSignal.date)))

        latest_price_count = 0
        if latest_price is not None:
            latest_price_count = session.scalar(
                select(func.count()).select_from(DailyPrice).where(DailyPrice.date == latest_price)
            ) or 0

    return {
        "today": date.today(),
        "latest_daily_price": latest_price,
        "latest_daily_price_rows": latest_price_count,
        "latest_market_index": latest_index,
        "latest_technical_signal": latest_technical,
    }


def _history_years_for_window(start_date: date, end_date: date) -> float:
    days = max(1, (end_date - start_date).days + 14)
    return max(MIN_HISTORY_WINDOW_YEARS, days / 365.0)


def _default_repair_start(latest: date | None, end_date: date) -> date:
    if latest is None:
        return end_date - timedelta(days=DEFAULT_INITIAL_LOOKBACK_DAYS)
    return min(latest + timedelta(days=1), end_date)


def _should_refresh_eod(now_npt: datetime | None = None) -> bool:
    now_npt = now_npt or _current_npt_time()
    close = now_npt.replace(
        hour=SESSION_END.hour,
        minute=SESSION_END.minute,
        second=0,
        microsecond=0,
    )
    return now_npt >= close + timedelta(minutes=EOD_SETTLE_BUFFER_MINUTES)


def repair_price_gaps(start_date: date, end_date: date) -> dict[str, Any]:
    if start_date > end_date:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "symbols_processed": 0,
            "rows_inserted": 0,
            "duplicates_skipped": 0,
            "failures": 0,
        }

    symbols = _active_equity_symbols()
    years = _history_years_for_window(start_date, end_date)
    logger.info(
        "Repairing daily price gaps for %d active equities between %s and %s (history window %.2f years)",
        len(symbols),
        start_date,
        end_date,
        years,
    )

    inserted_total = 0
    duplicates_total = 0
    failures = 0

    for index, symbol in enumerate(symbols, 1):
        try:
            rows = get_price_history(symbol, years=years)
            window_rows = [row for row in rows if start_date <= row["date"] <= end_date]
            inserted, duplicates = insert_new_daily_prices(window_rows)
            inserted_total += inserted
            duplicates_total += duplicates
            if window_rows:
                logger.info(
                    "[%d/%d] %s: %d rows in window, %d inserted, %d duplicates",
                    index,
                    len(symbols),
                    symbol,
                    len(window_rows),
                    inserted,
                    duplicates,
                )
        except Exception:
            failures += 1
            logger.exception("[%d/%d] Failed to repair price history for %s", index, len(symbols), symbol)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "symbols_processed": len(symbols),
        "rows_inserted": inserted_total,
        "duplicates_skipped": duplicates_total,
        "failures": failures,
    }


def repair_index_gaps(start_date: date, end_date: date) -> dict[str, Any]:
    if start_date > end_date:
        return {
            "start_date": start_date,
            "end_date": end_date,
            "indices_processed": 0,
            "rows_inserted": 0,
            "duplicates_skipped": 0,
            "failures": 0,
        }

    years = _history_years_for_window(start_date, end_date)
    inserted_total = 0
    duplicates_total = 0
    failures = 0

    for index_name in INDEX_NAME_TO_ID:
        try:
            rows = get_index_history(index_name, years=years)
            window_rows = [row for row in rows if start_date <= row["date"] <= end_date]
            inserted, duplicates = insert_new_market_index_rows(window_rows)
            inserted_total += inserted
            duplicates_total += duplicates
            logger.info(
                "%s: %d index rows in repair window, %d inserted, %d duplicates",
                index_name,
                len(window_rows),
                inserted,
                duplicates,
            )
        except Exception:
            failures += 1
            logger.exception("Failed to repair index history for %s", index_name)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "indices_processed": len(INDEX_NAME_TO_ID),
        "rows_inserted": inserted_total,
        "duplicates_skipped": duplicates_total,
        "failures": failures,
    }


def should_auto_repair_gaps(as_of_date: date | None = None) -> bool:
    """Return True when the database is stale enough that a historical repair is warranted."""

    as_of_date = as_of_date or date.today()
    latest_price = _latest_date(DailyPrice)
    latest_index = _latest_date(MarketIndex)
    if latest_price is None or latest_index is None:
        return True

    if (as_of_date - latest_price).days > 3 or (as_of_date - latest_index).days > 3:
        return True

    oldest_latest = min(latest_price, latest_index)
    with get_session() as session:
        expected_missing_days = session.scalar(
            select(func.count())
            .select_from(TradingCalendar)
            .where(TradingCalendar.is_trading_day.is_(True))
            .where(TradingCalendar.date > oldest_latest)
            .where(TradingCalendar.date < as_of_date)
        ) or 0
    return expected_missing_days > 0


def repair_market_gaps(from_date: date | None = None, through_date: date | None = None) -> dict[str, Any]:
    end_date = through_date or date.today()
    freshness = get_data_freshness()

    price_start = from_date or _default_repair_start(freshness["latest_daily_price"], end_date)
    index_start = from_date or _default_repair_start(freshness["latest_market_index"], end_date)

    return {
        "prices": repair_price_gaps(price_start, end_date),
        "indices": repair_index_gaps(index_start, end_date),
    }


def run_market_sync(
    *,
    repair_gaps: bool = True,
    refresh_today: bool = True,
    from_date: date | None = None,
    recompute_signals: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    before = get_data_freshness()
    logger.info("Market sync starting. Freshness before sync: %s", before)

    gap_summary: dict[str, Any] | None = None
    if repair_gaps:
        gap_summary = repair_market_gaps(from_date=from_date)

    today_summary: dict[str, Any] | None = None
    index_today_summary: dict[str, Any] | None = None

    if refresh_today:
        now_npt = _current_npt_time()
        if not is_trading_day(now_npt.date()):
            logger.info("%s is not a trading day; skipping EOD refresh", now_npt.date())
            today_summary = {"skipped": True, "reason": "not a trading day"}
            index_today_summary = {"skipped": True, "reason": "not a trading day"}
        elif not _should_refresh_eod(now_npt):
            logger.info(
                "NEPSE session has not reached the %d-minute post-close settle buffer; skipping EOD refresh",
                EOD_SETTLE_BUFFER_MINUTES,
            )
            today_summary = {"skipped": True, "reason": "session not settled"}
            index_today_summary = {"skipped": True, "reason": "session not settled"}
        else:
            today_summary = run_daily_pipeline()
            index_today_summary = run_daily_index_refresh(today=now_npt.date())

    calendar_summary = run_calendar_backfill(attempt_confirmed_for_today=refresh_today)

    signals_summary: dict[str, Any] | None = None
    liquidity_summary: dict[str, Any] | None = None
    extraction_summary: dict[str, Any] | None = None
    grading_summary: dict[str, Any] | None = None

    if recompute_signals:
        signals_summary = run_signals_backfill()
        liquidity_summary = compute_liquidity_tiers()
        extraction_start = max(
            date.today() - timedelta(days=SIGNAL_CALL_EXTRACTION_LOOKBACK_DAYS),
            from_date or date.min,
        )
        extraction_summary = extract_signal_calls(extraction_start, date.today())
        grading_summary = grade_signal_calls()

    quality_summary = check_daily_pipeline_health()
    after = get_data_freshness()

    summary = {
        "before": before,
        "gap_repair": gap_summary,
        "today_refresh": today_summary,
        "today_index_refresh": index_today_summary,
        "calendar": calendar_summary,
        "signals": signals_summary,
        "liquidity": liquidity_summary,
        "signal_call_extraction": extraction_summary,
        "signal_call_grading": grading_summary,
        "quality": quality_summary,
        "after": after,
        "execution_time_seconds": round(time.perf_counter() - started, 2),
    }
    logger.info("Market sync complete. Freshness after sync: %s", after)
    return summary


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize NEPSE market data, repair gaps, rebuild the trading calendar, and refresh signals."
    )
    parser.add_argument("--status", action="store_true", help="Print database freshness without fetching or writing data.")
    parser.add_argument("--today", action="store_true", help="Refresh only today's settled EOD data and derived analytics.")
    parser.add_argument(
        "--repair-gaps",
        action="store_true",
        help="Repair historical price/index gaps and derived analytics without forcing today's EOD refresh.",
    )
    parser.add_argument("--from", dest="from_date", type=_parse_date, help="Repair gaps beginning on YYYY-MM-DD.")
    parser.add_argument("--skip-signals", action="store_true", help="Skip technical/signal and liquidity recomputation.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.status:
        result: dict[str, Any] = get_data_freshness()
    else:
        only_today = bool(args.today)
        only_repair = bool(args.repair_gaps)
        result = run_market_sync(
            repair_gaps=not only_today,
            refresh_today=not only_repair,
            from_date=args.from_date,
            recompute_signals=not args.skip_signals,
        )

    print(json.dumps(result, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
