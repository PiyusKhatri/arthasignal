from __future__ import annotations

from datetime import date, timedelta

import pytest

from archive.quant_research_v1.services.quant_execution_diagnostics import (
    contiguous_block_diagnostics,
    holding_diagnostics,
    reconstruct_completed_trades,
    rolling_performance_diagnostics,
    trade_breakdowns,
    turnover_source_diagnostics,
)


def _dates(count: int) -> list[date]:
    start = date(2026, 1, 1)
    return [start + timedelta(days=index) for index in range(count)]


def test_turnover_sources_attribute_sell_notional_by_reason() -> None:
    dates = _dates(4)
    log = [
        {"date": dates[0], "side": "buy", "symbol": "AAA", "notional": 0.2, "fee": 0.001},
        {"date": dates[1], "side": "sell", "symbol": "AAA", "reason": "replacement", "notional": 0.24, "fee": 0.0012},
        {"date": dates[1], "side": "buy", "symbol": "BBB", "notional": 0.2, "fee": 0.001},
        {"date": dates[3], "side": "sell", "symbol": "BBB", "reason": "expired", "notional": 0.18, "fee": 0.0009},
    ]
    result = turnover_source_diagnostics(log)
    assert result["sell_count"] == 2
    assert result["by_exit_reason"]["replacement"]["sell_notional_x_initial_capital"] == pytest.approx(0.24)
    assert result["by_exit_reason"]["expired"]["sell_notional_x_initial_capital"] == pytest.approx(0.18)
    assert sum(
        row["share_of_sell_notional"]
        for row in result["by_exit_reason"].values()
    ) == pytest.approx(1.0)


def test_reconstruct_completed_trade_preserves_entry_metadata_and_holding_period() -> None:
    dates = _dates(5)
    predictions = [
        {
            "row": {
                "date": dates[0],
                "symbol": "AAA",
                "sector": "Banking",
                "liquidity_bucket": "high",
                "baseline_rank": 3,
                "baseline_score": 0.8,
                "v4_market_regime": "bull",
                "v4_sector_regime": "leading",
            },
            "market_regime": "bull",
            "sector_regime": "leading",
            "override_action": "promote",
            "final_score": 0.9,
            "expected_excess_return_percent": 2.2,
            "predicted_mae_percent": 4.0,
            "predicted_mfe_percent": 8.0,
        }
    ]
    log = [
        {"date": dates[0], "side": "buy", "symbol": "AAA", "reason": "entry", "score": 0.9, "notional": 0.2, "fee": 0.001},
        {"date": dates[3], "side": "sell", "symbol": "AAA", "reason": "expired", "notional": 0.22, "fee": 0.0011},
    ]
    prices = {"AAA": {dates[0]: 100.0, dates[3]: 110.0}}
    trades = reconstruct_completed_trades(
        log,
        market_dates=dates,
        price_history=prices,
        predictions=predictions,
    )
    assert len(trades) == 1
    trade = trades[0]
    assert trade["holding_sessions"] == 3
    assert trade["gross_stock_return_percent"] == pytest.approx(10.0)
    assert trade["market_regime"] == "bull"
    assert trade["sector"] == "Banking"
    assert trade["override_action"] == "promote"
    assert trade["baseline_rank_bucket"] == "top_5"


def test_rolling_diagnostics_identify_persistent_weak_window() -> None:
    dates = _dates(80)
    strategy = []
    baseline = []
    for index, trading_date in enumerate(dates):
        strategy_return = -0.002 if index < 60 else 0.003
        baseline_return = 0.001
        strategy.append({"date": trading_date, "return": strategy_return})
        baseline.append({"date": trading_date, "return": baseline_return})
    result = rolling_performance_diagnostics(strategy, baseline, windows=(20,))
    worst = result["20"]["worst_5"][0]
    assert worst["incremental_return_percent"] < 0.0
    assert result["20"]["positive_window_rate"] < 0.5


def test_contiguous_blocks_keep_serial_structure() -> None:
    dates = _dates(40)
    strategy = [
        {"date": trading_date, "return": (0.002 if index < 20 else -0.002)}
        for index, trading_date in enumerate(dates)
    ]
    baseline = [{"date": trading_date, "return": 0.0} for trading_date in dates]
    result = contiguous_block_diagnostics(strategy, baseline, block_length=20)
    assert result["blocks"] == 2
    assert result["positive_block_rate"] == pytest.approx(0.5)
    assert result["worst_10"][0]["incremental_return_percent"] < 0.0
    assert result["best_10"][0]["incremental_return_percent"] > 0.0


def test_trade_breakdowns_report_exit_and_holding_buckets() -> None:
    rows = [
        {
            "year": 2026,
            "market_regime": "bull",
            "sector": "Banking",
            "sector_regime": "leading",
            "liquidity_bucket": "high",
            "override_action": "promote",
            "baseline_rank_bucket": "top_5",
            "holding_bucket": "6_10",
            "exit_reason": "replacement",
            "gross_stock_return_percent": 5.0,
            "holding_sessions": 8,
            "exit_notional": 0.2,
        },
        {
            "year": 2026,
            "market_regime": "bull",
            "sector": "Banking",
            "sector_regime": "leading",
            "liquidity_bucket": "high",
            "override_action": "hold",
            "baseline_rank_bucket": "rank_6_10",
            "holding_bucket": "11_15",
            "exit_reason": "expired",
            "gross_stock_return_percent": -2.0,
            "holding_sessions": 12,
            "exit_notional": 0.2,
        },
    ]
    result = trade_breakdowns(rows)
    assert result["sector"]["Banking"]["trades"] == 2
    assert result["override_action"]["promote"]["mean_gross_return_percent"] == pytest.approx(5.0)
    assert result["exit_reason"]["expired"]["mean_gross_return_percent"] == pytest.approx(-2.0)
    assert result["holding_bucket"]["11_15"]["mean_holding_sessions"] == pytest.approx(12.0)


def test_holding_diagnostics_split_by_exit_reason() -> None:
    rows = [
        {"holding_sessions": 4, "exit_reason": "replacement"},
        {"holding_sessions": 8, "exit_reason": "replacement"},
        {"holding_sessions": 20, "exit_reason": "expired"},
    ]
    result = holding_diagnostics(rows)
    assert result["completed_trades"] == 3
    assert result["by_exit_reason"]["replacement"]["mean_sessions"] == pytest.approx(6.0)
    assert result["by_exit_reason"]["expired"]["mean_sessions"] == pytest.approx(20.0)
