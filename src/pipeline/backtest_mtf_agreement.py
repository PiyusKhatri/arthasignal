from __future__ import annotations

import bisect
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import BacktestResult, MtfAgreementBacktestResult, SignalTimeframe, TechnicalSignal
from src.pipeline.backtest_signals import DEFAULT_FORWARD_DAYS, backtest_multiple_signals_with_episodes
from src.pipeline.compute_baseline import BASELINE_SIGNAL_NAME
from src.pipeline.multi_timeframe_agreement import _classify_macd, _classify_rsi, _classify_trend, _overall_state
from src.pipeline.run_signal_backtests import build_signal_conditions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TRACKED_SIGNALS = [
    "rsi_14 < 30 (oversold)",
    "close < bollinger_lower",
    "doji",
    "shooting_star",
    "marubozu_bearish",
    "marubozu_bullish",
]

HIGH_AGREEMENT_GROUP = "high_agreement"
LOW_AGREEMENT_GROUP = "low_agreement"


def _load_timeframe_signals(timeframe: SignalTimeframe) -> dict[str, tuple[list[date], list[TechnicalSignal]]]:
    with get_session() as session:
        rows = session.execute(
            select(TechnicalSignal)
            .where(TechnicalSignal.timeframe == timeframe)
            .order_by(TechnicalSignal.symbol, TechnicalSignal.date)
        ).scalars().all()
        session.expunge_all()

    by_symbol: dict[str, list[TechnicalSignal]] = {}
    for row in rows:
        by_symbol.setdefault(row.symbol, []).append(row)

    indexed: dict[str, tuple[list[date], list[TechnicalSignal]]] = {}
    for symbol, symbol_rows in by_symbol.items():
        dates = [r.date for r in symbol_rows]
        indexed[symbol] = (dates, symbol_rows)
    return indexed


def _latest_row(indexed: dict[str, tuple[list[date], list[TechnicalSignal]]], symbol: str, as_of: date) -> TechnicalSignal | None:
    entry = indexed.get(symbol)
    if entry is None:
        return None
    dates, rows = entry
    pos = bisect.bisect_right(dates, as_of) - 1
    if pos < 0:
        return None
    return rows[pos]


def _row_overall_state(row: TechnicalSignal) -> str:
    rsi_state = _classify_rsi(row.rsi_14)
    macd_state = _classify_macd(row.macd_line, row.macd_signal)
    trend_state = _classify_trend(row.sma_20, row.sma_50)
    return _overall_state([rsi_state, macd_state, trend_state])


def _agreement_score(daily_row: TechnicalSignal, weekly_row: TechnicalSignal | None, monthly_row: TechnicalSignal | None) -> int:
    states = [_row_overall_state(daily_row)]
    if weekly_row is not None:
        states.append(_row_overall_state(weekly_row))
    if monthly_row is not None:
        states.append(_row_overall_state(monthly_row))

    counts = {s: states.count(s) for s in ("bullish", "bearish", "neutral")}
    return max(counts.values())


def _load_baseline_win_rate_by_horizon() -> dict[int, Any]:
    with get_session() as session:
        rows = session.execute(
            select(BacktestResult.forward_days, BacktestResult.win_rate).where(
                BacktestResult.signal_name == BASELINE_SIGNAL_NAME
            )
        ).all()
        return {r.forward_days: r.win_rate for r in rows}


def _upsert_results(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(MtfAgreementBacktestResult).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["signal_name", "agreement_group", "forward_days"],
        set_={
            "sample_size": stmt.excluded.sample_size,
            "win_rate": stmt.excluded.win_rate,
            "win_rate_minus_baseline": stmt.excluded.win_rate_minus_baseline,
            "computed_at": stmt.excluded.computed_at,
        },
    )
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


def run_mtf_agreement_backtest(forward_days: list[int] = DEFAULT_FORWARD_DAYS) -> dict[str, Any]:
    logger.info("Loading weekly and monthly technical_signals for bulk agreement lookups")
    weekly_by_symbol = _load_timeframe_signals(SignalTimeframe.WEEKLY)
    monthly_by_symbol = _load_timeframe_signals(SignalTimeframe.MONTHLY)

    all_conditions = build_signal_conditions()
    signal_conditions = {name: all_conditions[name] for name in TRACKED_SIGNALS}

    logger.info("Running episode-capturing backtest for %d signals", len(TRACKED_SIGNALS))
    results = backtest_multiple_signals_with_episodes(signal_conditions, forward_days, dedup_episodes=True)

    baseline_win_rate_by_horizon = _load_baseline_win_rate_by_horizon()
    computed_at = datetime.utcnow()

    rows = []
    diagnostics: dict[str, Any] = {}

    for signal_name in TRACKED_SIGNALS:
        episodes = results[signal_name]["all_episodes"]
        group_returns: dict[str, dict[int, list[Decimal]]] = {
            HIGH_AGREEMENT_GROUP: {n: [] for n in forward_days},
            LOW_AGREEMENT_GROUP: {n: [] for n in forward_days},
        }
        score_counts = {0: 0, 1: 0, 2: 0, 3: 0}
        skipped_mid_range = 0

        for episode in episodes:
            symbol = episode["symbol"]
            ep_date = episode["date"]
            daily_row = episode["row"]

            weekly_row = _latest_row(weekly_by_symbol, symbol, ep_date)
            monthly_row = _latest_row(monthly_by_symbol, symbol, ep_date)
            score = _agreement_score(daily_row, weekly_row, monthly_row)
            score_counts[score] += 1

            if score >= 2:
                group = HIGH_AGREEMENT_GROUP
            elif score <= 1:
                group = LOW_AGREEMENT_GROUP
            else:
                skipped_mid_range += 1
                continue

            for n in forward_days:
                fr = episode["forward_returns"].get(n)
                if fr is not None:
                    group_returns[group][n].append(fr)

        diagnostics[signal_name] = {"total_episodes": len(episodes), "score_counts": score_counts}

        for group in (HIGH_AGREEMENT_GROUP, LOW_AGREEMENT_GROUP):
            for n in forward_days:
                returns = group_returns[group][n]
                sample_size = len(returns)
                win_rate = None
                if sample_size > 0:
                    wins = sum(1 for r in returns if r > 0)
                    win_rate = Decimal(wins) / Decimal(sample_size) * Decimal(100)

                baseline_win_rate = baseline_win_rate_by_horizon.get(n)
                win_rate_minus_baseline = None
                if win_rate is not None and baseline_win_rate is not None:
                    win_rate_minus_baseline = win_rate - baseline_win_rate

                rows.append(
                    {
                        "signal_name": signal_name,
                        "agreement_group": group,
                        "forward_days": n,
                        "sample_size": sample_size,
                        "win_rate": win_rate,
                        "win_rate_minus_baseline": win_rate_minus_baseline,
                        "computed_at": computed_at,
                    }
                )

    stored = _upsert_results(rows)
    logger.info("Stored %d mtf_agreement_backtest_results rows", stored)
    for signal_name, diag in diagnostics.items():
        logger.info("%s: %s", signal_name, diag)

    return {"rows_stored": stored, "diagnostics": diagnostics}


if __name__ == "__main__":
    run_mtf_agreement_backtest()
