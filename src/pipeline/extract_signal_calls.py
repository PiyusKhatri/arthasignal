from __future__ import annotations

import bisect
import logging
import os
import subprocess
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import DailyPrice, SignalCall, SignalCallStatus, SignalTimeframe, SymbolLiquidityTier, TechnicalSignal
from src.pipeline.run_signal_backtests import build_signal_conditions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TARGET_SIGNAL_HORIZONS = {
    "rsi_14 < 30 (oversold)": 20,
    "close < bollinger_lower": 20,
    "rsi_14 > 70 (overbought)": 20,
    "doji": 20,
}

DOJI_SIGNAL_NAME = "doji"
DOJI_REQUIRED_LIQUIDITY_TIER = "high_liquidity"


def _resolve_commit_hash() -> str:
    github_sha = os.environ.get("GITHUB_SHA")
    if github_sha:
        return github_sha
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip()


def _load_high_liquidity_symbols() -> set[str]:
    with get_session() as session:
        rows = session.execute(
            select(SymbolLiquidityTier.symbol).where(
                SymbolLiquidityTier.liquidity_tier == DOJI_REQUIRED_LIQUIDITY_TIER
            )
        ).scalars().all()
    return set(rows)


def _load_signal_entries() -> list[tuple[TechnicalSignal, Decimal | None]]:
    with get_session() as session:
        session.execute(text("SET statement_timeout = '600s'"))
        rows = session.execute(
            select(TechnicalSignal, DailyPrice.close)
            .join(
                DailyPrice,
                (DailyPrice.symbol == TechnicalSignal.symbol) & (DailyPrice.date == TechnicalSignal.date),
            )
            .where(TechnicalSignal.timeframe == SignalTimeframe.DAILY)
            .order_by(TechnicalSignal.symbol, TechnicalSignal.date)
        ).all()
        entries = [(r[0], r[1]) for r in rows]
        session.expunge_all()
    return entries


def _load_open_price_index() -> dict[str, tuple[list[date], list[Decimal]]]:
    with get_session() as session:
        rows = session.execute(
            select(DailyPrice.symbol, DailyPrice.date, DailyPrice.open).order_by(
                DailyPrice.symbol, DailyPrice.date
            )
        ).all()

    index: dict[str, tuple[list[date], list[Decimal]]] = {}
    current_symbol = None
    dates: list[date] = []
    opens: list[Decimal] = []
    for symbol, entry_date, open_price in rows:
        if symbol != current_symbol:
            if current_symbol is not None:
                index[current_symbol] = (dates, opens)
            current_symbol = symbol
            dates, opens = [], []
        dates.append(entry_date)
        opens.append(open_price)
    if current_symbol is not None:
        index[current_symbol] = (dates, opens)
    return index


def _next_trading_day_open(
    open_index: dict[str, tuple[list[date], list[Decimal]]], symbol: str, entry_date: date
) -> Decimal | None:
    series = open_index.get(symbol)
    if series is None:
        return None
    dates, opens = series
    pos = bisect.bisect_right(dates, entry_date)
    if pos >= len(dates):
        return None
    return opens[pos]


def _detect_episodes(
    condition_fn, signal_entries: list[tuple[TechnicalSignal, Decimal | None]], start_date: date, end_date: date
) -> list[tuple[str, date]]:
    condition_active_by_symbol: dict[str, bool] = {}
    prev_entry_by_symbol: dict[str, tuple[TechnicalSignal, Decimal | None]] = {}
    episodes: list[tuple[str, date]] = []

    for row, close in signal_entries:
        prev_row, prev_close = prev_entry_by_symbol.get(row.symbol, (None, None))
        condition_true = condition_fn(row, close, prev_row, prev_close)
        prev_entry_by_symbol[row.symbol] = (row, close)

        was_active = condition_active_by_symbol.get(row.symbol, False)
        condition_active_by_symbol[row.symbol] = condition_true

        if not condition_true or was_active:
            continue

        if start_date <= row.date <= end_date:
            episodes.append((row.symbol, row.date))

    return episodes


def _upsert_signal_calls(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(SignalCall).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "signal_name", "entry_date"])
    stmt = stmt.returning(SignalCall.id)
    with get_session() as session:
        result = session.execute(stmt)
        return len(result.fetchall())


def extract_signal_calls(start_date: date, end_date: date) -> dict[str, Any]:
    conditions = build_signal_conditions()
    signal_entries = _load_signal_entries()
    open_index = _load_open_price_index()
    high_liquidity_symbols = _load_high_liquidity_symbols()
    commit_hash = _resolve_commit_hash()
    created_at = datetime.utcnow()

    rows: list[dict[str, Any]] = []
    skipped_missing_price = 0
    by_signal: dict[str, int] = {}

    for signal_name, horizon in TARGET_SIGNAL_HORIZONS.items():
        condition_fn = conditions[signal_name]
        episodes = _detect_episodes(condition_fn, signal_entries, start_date, end_date)

        count_for_signal = 0
        for symbol, entry_date in episodes:
            if signal_name == DOJI_SIGNAL_NAME and symbol not in high_liquidity_symbols:
                continue

            entry_price = _next_trading_day_open(open_index, symbol, entry_date)
            if entry_price is None:
                skipped_missing_price += 1
                continue

            rows.append(
                {
                    "symbol": symbol,
                    "signal_name": signal_name,
                    "entry_date": entry_date,
                    "entry_price": entry_price,
                    "status": SignalCallStatus.PENDING,
                    "forward_days_horizon": horizon,
                    "signal_logic_commit_hash": commit_hash,
                    "created_at": created_at,
                }
            )
            count_for_signal += 1

        by_signal[signal_name] = count_for_signal

    inserted = _upsert_signal_calls(rows)

    summary = {
        "rows_extracted": len(rows),
        "rows_inserted": inserted,
        "skipped_missing_next_day_price": skipped_missing_price,
        "by_signal": by_signal,
    }
    logger.info("Signal call extraction summary: %s", summary)
    return summary


if __name__ == "__main__":
    from datetime import timedelta

    today = date.today()
    extract_signal_calls(today - timedelta(days=30), today)
