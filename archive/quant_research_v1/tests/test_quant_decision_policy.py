from __future__ import annotations

from datetime import date, timedelta

import pytest

from archive.quant_research_v1.services.quant_decision_policy import (
    build_v3_candidate_scores,
    calibration_buckets,
    classify_market_regime,
    classify_sector_regime,
    consensus_market_regime,
    consensus_sector_regime,
    monotonic_risk_ladder,
    non_overlapping_dynamic_portfolio,
    select_dynamic_setups,
)


def _row(
    trading_date: date,
    symbol: str,
    *,
    market_return: float,
    sector_relative: float = 3.0,
    excess_return: float = 3.0,
    adverse: float = -2.0,
    sector: str = "Bank",
) -> dict:
    stock_vs_market = 3.0
    stock_return = market_return + stock_vs_market
    stock_vs_sector = stock_vs_market - sector_relative
    return {
        "date": trading_date,
        "label_end_date": trading_date + timedelta(days=28),
        "symbol": symbol,
        "sector": sector,
        "stock_return_percent": market_return + excess_return,
        "market_return_percent": market_return,
        "excess_return_percent": excess_return,
        "max_adverse_percent": adverse,
        "features": {
            "return_20d": stock_return,
            "return_60d": stock_return * 1.5,
            "relative_strength_market_20d": stock_vs_market,
            "relative_strength_sector_20d": stock_vs_sector,
            "annualized_volatility_20d": 22.0,
            "distance_sma50_percent": 4.0,
            "turnover_ratio_20d": 1.2,
        },
    }


def test_regime_inference_is_point_in_time_and_sector_relative() -> None:
    strong = _row(date(2024, 1, 1), "A", market_return=7.0, sector_relative=3.0)
    sideways = _row(date(2024, 1, 2), "B", market_return=0.0, sector_relative=0.0)
    stress = _row(date(2024, 1, 3), "C", market_return=-7.0, sector_relative=-3.0)

    assert classify_market_regime(strong) == "strong_bull"
    assert classify_market_regime(sideways) == "sideways"
    assert classify_market_regime(stress) == "high_stress"
    assert classify_sector_regime(strong) == "leading"
    assert classify_sector_regime(sideways) == "neutral"
    assert classify_sector_regime(stress) == "lagging"


def test_exact_index_context_overrides_stock_level_fallback() -> None:
    row = _row(date(2024, 1, 1), "A", market_return=-8.0, sector_relative=-3.0)
    row["regime_context"] = {
        "market_close": 2200.0,
        "market_sma50": 2100.0,
        "market_sma200": 1950.0,
        "market_return_20d_percent": 4.0,
        "market_return_60d_percent": 12.0,
        "market_drawdown_252d_percent": -4.0,
        "market_annualized_volatility_60d_percent": 20.0,
        "sector_relative_strength_20d_percent": 3.5,
    }
    assert classify_market_regime(row) == "strong_bull"
    assert classify_sector_regime(row) == "leading"


def test_consensus_regime_uses_median_not_one_outlier_without_exact_context() -> None:
    trading_date = date(2024, 1, 1)
    rows = [
        _row(trading_date, "A", market_return=4.0, sector_relative=3.0),
        _row(trading_date, "B", market_return=4.5, sector_relative=2.5),
        _row(trading_date, "C", market_return=-12.0, sector_relative=-8.0),
    ]
    assert consensus_market_regime(rows) == "bull"
    assert consensus_sector_regime(rows) == "leading"


def test_risk_ladder_enforces_nested_adverse_probabilities() -> None:
    result = monotonic_risk_ladder(0.20, 0.45, 0.10)
    assert result["p_adverse_3pct"] >= result["p_adverse_5pct"] >= result["p_adverse_8pct"]
    assert result["risk_score"] == pytest.approx(
        0.45 * result["p_adverse_3pct"]
        + 0.35 * result["p_adverse_5pct"]
        + 0.20 * result["p_adverse_8pct"]
    )
    assert result["safety_score"] == pytest.approx(1.0 - result["risk_score"])


def test_dynamic_capacity_reduces_breadth_and_abstains_in_bad_regimes() -> None:
    dates = [date(2024, 1, 1) + timedelta(days=index * 30) for index in range(5)]
    market_returns = [7.0, 3.0, 0.0, -3.0, -7.0]
    rows: list[dict] = []
    execution: list[float] = []
    risk3: list[float] = []
    risk5: list[float] = []
    risk8: list[float] = []
    ranks: list[float] = []

    for trading_date, market_return in zip(dates, market_returns):
        for index in range(12):
            rows.append(
                _row(
                    trading_date,
                    f"S{index:02d}",
                    market_return=market_return,
                    sector_relative=3.0,
                )
            )
            execution.append(0.72)
            risk3.append(0.30)
            risk5.append(0.20)
            risk8.append(0.10)
            ranks.append(float(index))

    candidates = build_v3_candidate_scores(rows, execution, (risk3, risk5, risk8), ranks)
    selection = select_dynamic_setups(candidates)
    selected_by_date = {day["date"]: day["selected"] for day in selection["days"]}

    assert selected_by_date[dates[0]] == 10
    assert selected_by_date[dates[1]] == 5
    assert selected_by_date[dates[2]] == 3
    assert selected_by_date[dates[3]] == 0
    assert selected_by_date[dates[4]] == 0


def test_lagging_sector_and_excessive_risk_are_rejected() -> None:
    trading_date = date(2024, 1, 1)
    rows = [
        _row(trading_date, "GOOD", market_return=4.0, sector_relative=3.0, sector="Bank"),
        _row(trading_date, "LAG", market_return=4.0, sector_relative=-3.0, sector="Hydro"),
        _row(trading_date, "RISK", market_return=4.0, sector_relative=3.0, sector="Finance"),
    ]
    candidates = build_v3_candidate_scores(
        rows,
        [0.70, 0.80, 0.80],
        ([0.20, 0.20, 0.80], [0.15, 0.15, 0.70], [0.10, 0.10, 0.60]),
        [1.0, 2.0, 3.0],
    )
    selection = select_dynamic_setups(candidates)
    symbols = {candidate["row"]["symbol"] for candidate in selection["selected"]}
    assert symbols == {"GOOD"}


def test_calibration_buckets_are_predefined_and_report_realized_outcomes() -> None:
    trading_date = date(2024, 1, 1)
    rows = [
        _row(trading_date + timedelta(days=index), f"S{index}", market_return=2.0, excess_return=excess)
        for index, excess in enumerate([0.0, 2.0, 3.0, -1.0, 5.0, 8.0])
    ]
    probabilities = [0.45, 0.52, 0.57, 0.62, 0.67, 0.75]
    result = calibration_buckets(rows, probabilities)
    assert result["lt_50"]["calls"] == 1
    assert result["50_55"]["calls"] == 1
    assert result["55_60"]["calls"] == 1
    assert result["60_65"]["calls"] == 1
    assert result["65_70"]["calls"] == 1
    assert result["70_plus"]["calls"] == 1
    assert result["70_plus"]["actual_success_rate"] == pytest.approx(1.0)


def test_dynamic_portfolio_does_not_reuse_overlapping_entry_dates() -> None:
    rows = []
    probabilities = []
    risk3 = []
    risk5 = []
    risk8 = []
    ranks = []
    start = date(2024, 1, 1)
    for day in range(25):
        trading_date = start + timedelta(days=day * 2)
        for index in range(5):
            rows.append(_row(trading_date, f"S{index}", market_return=3.0, sector_relative=3.0))
            probabilities.append(0.70)
            risk3.append(0.20)
            risk5.append(0.15)
            risk8.append(0.10)
            ranks.append(float(index))

    candidates = build_v3_candidate_scores(rows, probabilities, (risk3, risk5, risk8), ranks)
    selection = select_dynamic_setups(candidates)
    result = non_overlapping_dynamic_portfolio(selection)
    assert result["cohorts"] > 1
    assert result["mean_net_excess_bootstrap_95"]["low"] is not None
