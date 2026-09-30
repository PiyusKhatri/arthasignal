from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.services.quant_robustness import (
    annotate_liquidity_buckets,
    cost_stress_test,
    high_confidence_diagnostics,
    non_overlapping_portfolio_backtest,
    top_k_sweep,
)


def _rows(days: int = 40, names: int = 12) -> tuple[list[dict], list[float], list[float]]:
    rows: list[dict] = []
    probabilities: list[float] = []
    rank_scores: list[float] = []
    start = date(2024, 1, 1)
    for day in range(days):
        trading_date = start + timedelta(days=day * 2)
        for rank in range(names):
            winner = rank < 5
            stock_return = 5.0 if winner else -0.5
            market_return = 1.0
            rows.append(
                {
                    "date": trading_date,
                    "label_end_date": trading_date + timedelta(days=28),
                    "symbol": f"S{rank:02d}",
                    "sector": "Bank" if rank % 2 == 0 else "Hydro",
                    "success": winner,
                    "stock_return_percent": stock_return,
                    "market_return_percent": market_return,
                    "excess_return_percent": stock_return - market_return,
                    "max_adverse_percent": -2.0 if winner else -5.0,
                    "trailing_turnover_20d": float(rank + 1) * 1_000_000,
                    "features": {
                        "return_20d": 8.0 if winner else -2.0,
                        "relative_strength_market_20d": 5.0 if winner else -3.0,
                    },
                }
            )
            probabilities.append(0.75 if winner else 0.35)
            rank_scores.append(float(names - rank))
    return rows, probabilities, rank_scores


def test_liquidity_buckets_are_point_in_time_cross_sectional_terciles() -> None:
    rows, _, _ = _rows(days=1, names=12)
    annotate_liquidity_buckets(rows)
    counts = {bucket: sum(row.get("liquidity_bucket") == bucket for row in rows) for bucket in ("low", "medium", "high")}
    assert counts == {"low": 4, "medium": 4, "high": 4}
    assert rows[0]["liquidity_bucket"] == "low"
    assert rows[-1]["liquidity_bucket"] == "high"


def test_high_confidence_diagnostics_report_cluster_interval_and_dates() -> None:
    rows, probabilities, _ = _rows(days=30)
    result = high_confidence_diagnostics(rows, probabilities, bootstrap_iterations=200)
    assert result["calls"] == 30 * 5
    assert result["independent_entry_dates"] == 30
    assert result["precision"] == pytest.approx(1.0)
    assert result["precision_date_cluster_bootstrap_95"]["low_95"] == pytest.approx(1.0)
    assert result["mean_excess_return_percent"] == pytest.approx(4.0)


def test_cost_stress_cannot_improve_precision_or_net_return_when_cost_rises() -> None:
    rows, probabilities, scores = _rows(days=20)
    result = cost_stress_test(rows, probabilities, scores, costs=(0.5, 1.0, 2.0), rank_k=10)
    ordered = [result["cost_0.50pct"], result["cost_1.00pct"], result["cost_2.00pct"]]
    precisions = [row["rank_p_at_10"] for row in ordered]
    returns = [row["rank_top_10_mean_net_excess_percent"] for row in ordered]
    assert precisions[0] >= precisions[1] >= precisions[2]
    assert returns[0] > returns[1] > returns[2]


def test_top_k_sweep_uses_only_dates_with_enough_names() -> None:
    rows, _, scores = _rows(days=10, names=12)
    result = top_k_sweep(rows, scores, ks=(5, 10, 20))
    assert result["p_at_5"]["dates"] == 10
    assert result["p_at_10"]["dates"] == 10
    assert result["p_at_20"]["dates"] == 0
    assert result["p_at_5"]["precision_after_cost"] == pytest.approx(1.0)


def test_non_overlapping_portfolio_never_reuses_overlapping_horizons() -> None:
    rows, _, scores = _rows(days=40, names=12)
    result = non_overlapping_portfolio_backtest(rows, scores, k=10, cost_percent=0.5)
    assert result["cohorts"] > 1
    recent = result["recent_cohorts"]
    for previous, current in zip(recent[:-1], recent[1:]):
        assert date.fromisoformat(current["date"]) > date.fromisoformat(previous["end_date"])
    assert result["mean_net_excess_return_percent"] is not None
    assert result["mean_net_excess_bootstrap_95"]["low"] is not None
