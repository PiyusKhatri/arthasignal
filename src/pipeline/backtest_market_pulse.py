from __future__ import annotations

import logging
import math
import statistics
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import MarketPulseBacktestResult
from src.pipeline.market_pulse import _ad_ratio_interpretation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FORWARD_DAYS = [1, 3, 5]
MARKET_WIDE_VOLUME_SPIKE_RATIO_THRESHOLD = Decimal("1.5")
VOLUME_TRAILING_WINDOW = 20
SIGNIFICANCE_ALPHA = 0.05

DEFAULT_COMPANY_STATUSES = ("A",)
SURVIVORSHIP_CORRECTED_STATUSES = ("A", "D")


def _load_nepse_index_series() -> tuple[list[date], list[Decimal]]:
    with get_session() as session:
        rows = session.execute(
            text("SELECT date, close FROM market_index WHERE index_name = 'NEPSE Index' ORDER BY date")
        ).all()
    return [r.date for r in rows], [r.close for r in rows]


def _load_daily_pct_changes_by_date(statuses: tuple[str, ...]) -> dict[date, list[Decimal]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT dp.symbol, dp.date, dp.close
                FROM daily_prices dp
                JOIN companies c ON c.symbol = dp.symbol
                WHERE c.instrument_type = 'Equity' AND c.status = ANY(:statuses)
                ORDER BY dp.symbol, dp.date
                """
            ),
            {"statuses": list(statuses)},
        ).all()

    changes_by_date: dict[date, list[Decimal]] = {}
    prev_symbol = None
    prev_close = None
    for row in rows:
        if row.symbol != prev_symbol:
            prev_close = None
        if prev_close is not None and prev_close != 0:
            pct = (row.close - prev_close) / prev_close * Decimal(100)
            changes_by_date.setdefault(row.date, []).append(pct)
        prev_symbol = row.symbol
        prev_close = row.close

    return changes_by_date


def _load_daily_turnover_series(statuses: tuple[str, ...]) -> tuple[list[date], list[Decimal]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT dp.date, sum(dp.turnover) AS turnover
                FROM daily_prices dp
                JOIN companies c ON c.symbol = dp.symbol
                WHERE c.instrument_type = 'Equity' AND c.status = ANY(:statuses)
                GROUP BY dp.date
                ORDER BY dp.date
                """
            ),
            {"statuses": list(statuses)},
        ).all()
    return [r.date for r in rows], [r.turnover for r in rows]


def _classify_breadth_days(changes_by_date: dict[date, list[Decimal]]) -> dict[date, str]:
    classifications = {}
    for d, changes in changes_by_date.items():
        advances = sum(1 for c in changes if c > 0)
        declines = sum(1 for c in changes if c < 0)
        _ratio, interpretation = _ad_ratio_interpretation(advances, declines)
        classifications[d] = interpretation
    return classifications


def _classify_volume_days(turnover_dates: list[date], turnovers: list[Decimal]) -> dict[date, str]:
    classifications = {}
    for i in range(VOLUME_TRAILING_WINDOW, len(turnover_dates)):
        trailing = turnovers[i - VOLUME_TRAILING_WINDOW : i]
        trailing_avg = sum(trailing) / Decimal(VOLUME_TRAILING_WINDOW)
        if trailing_avg == 0:
            continue
        ratio = turnovers[i] / trailing_avg
        classifications[turnover_dates[i]] = (
            "volume_spike_gt_1.5x" if ratio > MARKET_WIDE_VOLUME_SPIKE_RATIO_THRESHOLD else "normal"
        )
    return classifications


def _forward_returns_by_date(index_dates: list[date], index_closes: list[Decimal]) -> dict[date, dict[int, Decimal]]:
    result: dict[date, dict[int, Decimal]] = {}
    for idx, d in enumerate(index_dates):
        current_close = index_closes[idx]
        if not current_close:
            continue
        per_horizon = {}
        for n in FORWARD_DAYS:
            target_idx = idx + n
            if target_idx >= len(index_closes):
                continue
            future_close = index_closes[target_idx]
            if future_close is None:
                continue
            per_horizon[n] = (future_close - current_close) / current_close * Decimal(100)
        result[d] = per_horizon
    return result


def _baseline_returns_by_horizon(forward_returns_by_date: dict[date, dict[int, Decimal]]) -> dict[int, list[Decimal]]:
    returns_by_horizon: dict[int, list[Decimal]] = {n: [] for n in FORWARD_DAYS}
    for fr in forward_returns_by_date.values():
        for n in FORWARD_DAYS:
            if n in fr:
                returns_by_horizon[n].append(fr[n])
    return returns_by_horizon


def _condition_returns_by_horizon(
    condition_by_date: dict[date, str], forward_returns_by_date: dict[date, dict[int, Decimal]]
) -> dict[str, dict[int, list[Decimal]]]:
    grouped: dict[str, dict[int, list[Decimal]]] = {}
    for d, condition in condition_by_date.items():
        fr = forward_returns_by_date.get(d)
        if fr is None:
            continue
        for n in FORWARD_DAYS:
            if n not in fr:
                continue
            grouped.setdefault(condition, {}).setdefault(n, []).append(fr[n])
    return grouped


def _standard_normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _welch_t_test_p_value(sample: list[Decimal], baseline: list[Decimal]) -> float | None:
    n1, n2 = len(sample), len(baseline)
    if n1 < 2 or n2 < 2:
        return None

    s1 = [float(x) for x in sample]
    s2 = [float(x) for x in baseline]
    var1 = statistics.variance(s1)
    var2 = statistics.variance(s2)
    se_squared = var1 / n1 + var2 / n2
    if se_squared <= 0:
        return None

    t_stat = (statistics.mean(s1) - statistics.mean(s2)) / math.sqrt(se_squared)
    return 2 * (1 - _standard_normal_cdf(abs(t_stat)))


def _stats_for_returns(returns: list[Decimal]) -> dict[str, Any]:
    return {"sample_size": len(returns), "mean": statistics.mean(returns) if returns else None}


def _store_results(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(MarketPulseBacktestResult).values(rows)
    update_columns = {c: getattr(stmt.excluded, c) for c in rows[0] if c not in ("metric_name", "condition", "forward_days")}
    stmt = stmt.on_conflict_do_update(index_elements=["metric_name", "condition", "forward_days"], set_=update_columns)
    with get_session() as session:
        session.execute(stmt)
    return len(rows)


def _build_rows(
    metric_name: str,
    condition_returns: dict[str, dict[int, list[Decimal]]],
    baseline_returns: dict[int, list[Decimal]],
    now: datetime,
) -> list[dict[str, Any]]:
    rows = []
    for n in FORWARD_DAYS:
        baseline_stat = _stats_for_returns(baseline_returns[n])
        rows.append(
            {
                "metric_name": metric_name,
                "condition": "unconditional_baseline",
                "forward_days": n,
                "sample_size": baseline_stat["sample_size"],
                "mean_forward_return": baseline_stat["mean"],
                "mean_forward_return_minus_baseline": Decimal(0) if baseline_stat["mean"] is not None else None,
                "p_value": None,
                "is_significant": None,
                "computed_at": now,
            }
        )

    for condition, by_horizon in condition_returns.items():
        for n, returns in by_horizon.items():
            stat = _stats_for_returns(returns)
            baseline_stat = _stats_for_returns(baseline_returns[n])
            baseline_mean = baseline_stat["mean"]
            delta = stat["mean"] - baseline_mean if stat["mean"] is not None and baseline_mean is not None else None
            p_value = _welch_t_test_p_value(returns, baseline_returns[n])
            rows.append(
                {
                    "metric_name": metric_name,
                    "condition": condition,
                    "forward_days": n,
                    "sample_size": stat["sample_size"],
                    "mean_forward_return": stat["mean"],
                    "mean_forward_return_minus_baseline": delta,
                    "p_value": Decimal(str(round(p_value, 6))) if p_value is not None else None,
                    "is_significant": p_value < SIGNIFICANCE_ALPHA if p_value is not None else None,
                    "computed_at": now,
                }
            )
    return rows


def _run_backtest(statuses: tuple[str, ...]) -> dict[str, Any]:
    index_dates, index_closes = _load_nepse_index_series()
    forward_returns_by_date = _forward_returns_by_date(index_dates, index_closes)
    baseline_returns = _baseline_returns_by_horizon(forward_returns_by_date)

    changes_by_date = _load_daily_pct_changes_by_date(statuses)
    breadth_by_date = _classify_breadth_days(changes_by_date)
    breadth_returns = _condition_returns_by_horizon(breadth_by_date, forward_returns_by_date)

    turnover_dates, turnovers = _load_daily_turnover_series(statuses)
    volume_by_date = _classify_volume_days(turnover_dates, turnovers)
    volume_returns = _condition_returns_by_horizon(volume_by_date, forward_returns_by_date)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    breadth_rows = _build_rows("advance_decline_breadth", breadth_returns, baseline_returns, now)
    volume_rows = _build_rows("market_wide_volume", volume_returns, baseline_returns, now)

    return {
        "breadth_days_classified": len(breadth_by_date),
        "volume_days_classified": len(volume_by_date),
        "rows": breadth_rows + volume_rows,
    }


def run_market_pulse_backtest(survivorship_corrected: bool = True) -> dict[str, Any]:
    statuses = SURVIVORSHIP_CORRECTED_STATUSES if survivorship_corrected else DEFAULT_COMPANY_STATUSES
    result = _run_backtest(statuses)
    rows_stored = _store_results(result["rows"])

    summary = {
        "survivorship_corrected": survivorship_corrected,
        "statuses_included": statuses,
        "breadth_days_classified": result["breadth_days_classified"],
        "volume_days_classified": result["volume_days_classified"],
        "rows_stored": rows_stored,
    }
    logger.info("Market pulse backtest summary: %s", summary)
    return summary


if __name__ == "__main__":
    run_market_pulse_backtest()
