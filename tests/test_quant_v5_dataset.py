from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

import pytest

from src.services.quant_v5_dataset import (
    build_v5_dataset,
    build_v5_dataset_with_diagnostics,
)


def _sessions(count: int = 45) -> list[date]:
    start = date(2025, 1, 1)
    return [start + timedelta(days=index) for index in range(count)]


def _market_bars(sessions: list[date]) -> list[dict[str, object]]:
    return [
        {
            "date": session,
            "open": 1_000.0 + index * 2,
            "close": 1_001.0 + index * 2,
        }
        for index, session in enumerate(sessions)
    ]


def _stock_bars(
    symbol: str,
    sessions: list[date],
    *,
    missing: set[date] | None = None,
) -> list[dict[str, object]]:
    missing = missing or set()
    return [
        {
            "symbol": symbol,
            "date": session,
            "open": 100.0 + index,
            "close": 101.0 + index,
            "turnover": 1_000.0 + index * 100,
        }
        for index, session in enumerate(sessions)
        if session not in missing
    ]


def _signal(
    symbol: str,
    session: date,
    *,
    baseline_percentile: float = 0.75,
    turnover_percentile: float = 0.80,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "date": session,
        "baseline_percentile": baseline_percentile,
        "turnover_percentile": turnover_percentile,
        "features": {"momentum": 0.25},
    }


def test_dataset_aligns_completed_signal_to_next_open_and_market_d_plus_20_close() -> None:
    sessions = _sessions()
    signal_date = sessions[7]
    signals = [
        _signal("AAA", sessions[index], baseline_percentile=percentile)
        for index, percentile in zip(range(2, 8), (0.10, 0.20, 0.30, 0.40, 0.50, 0.95))
    ]
    membership = [
        {"symbol": "AAA", "date": sessions[index], "is_candidate": index in {3, 5, 6, 7}}
        for index in range(3, 8)
    ]

    rows = build_v5_dataset(
        signal_rows=signals,
        stock_bars={"AAA": _stock_bars("AAA", sessions)},
        market_bars=_market_bars(sessions),
        membership_history=membership,
    )

    row = next(item for item in rows if item["date"] == signal_date)
    assert row["entry_date"] == sessions[8]
    assert row["target_date"] == sessions[27]
    assert row["exit_date"] == sessions[27]
    assert row["label_end_date"] == sessions[27]
    assert row["entry_price"] == pytest.approx(108.0)
    assert row["exit_price"] == pytest.approx(128.0)

    expected_stock = (128.0 / 108.0 - 1.0) * 100.0
    expected_market = ((1_001.0 + 27 * 2) / (1_000.0 + 8 * 2) - 1.0) * 100.0
    assert row["stock_return_percent"] == pytest.approx(expected_stock)
    assert row["market_return_percent"] == pytest.approx(expected_market)
    assert row["excess_return_percent"] == pytest.approx(expected_stock - expected_market)
    assert row["net_alpha_percent"] == pytest.approx(expected_stock - expected_market - 1.0)
    assert row["round_trip_cost_percent"] == 1.0
    assert row["success_after_cost"] is True
    assert row["success"] is True
    assert row["stability_features"] == {
        "trade_availability_20": 1.0,
        "turnover_cv_20": pytest.approx(0.1697250257),
        "turnover_percentile": 0.80,
        "baseline_percentile_median_5": 0.30,
        "candidate_membership_count_5": 3,
    }


def test_exit_may_use_up_to_three_market_session_grace_days() -> None:
    sessions = _sessions()
    signal_date = sessions[5]
    target = sessions[25]
    missing = {target, sessions[26]}

    rows = build_v5_dataset(
        signal_rows=[_signal("AAA", signal_date)],
        stock_bars={"AAA": _stock_bars("AAA", sessions, missing=missing)},
        market_bars=_market_bars(sessions),
    )

    assert len(rows) == 1
    assert rows[0]["target_date"] == target
    assert rows[0]["exit_date"] == sessions[27]
    assert rows[0]["exit_grace_sessions"] == 2
    assert rows[0]["label_end_date"] == sessions[27]


def test_no_executable_d_plus_one_entry_produces_no_label() -> None:
    sessions = _sessions()
    signal_date = sessions[5]
    missing = {sessions[6]}

    rows, diagnostics = build_v5_dataset_with_diagnostics(
        signal_rows=[_signal("AAA", signal_date)],
        stock_bars={"AAA": _stock_bars("AAA", sessions, missing=missing)},
        market_bars=_market_bars(sessions),
    )

    assert rows == []
    assert diagnostics == {
        "attempted": 1,
        "labeled": 0,
        "missing_entry": 1,
        "missing_exit": 0,
        "corporate_action_void": 0,
        "invalid_stability": 0,
    }


def test_no_exit_within_d_plus_20_to_d_plus_23_produces_no_label() -> None:
    sessions = _sessions()
    signal_date = sessions[5]
    missing = set(sessions[25:29])

    rows, diagnostics = build_v5_dataset_with_diagnostics(
        signal_rows=[_signal("AAA", signal_date)],
        stock_bars={"AAA": _stock_bars("AAA", sessions, missing=missing)},
        market_bars=_market_bars(sessions),
    )

    assert rows == []
    assert diagnostics["missing_exit"] == 1


@pytest.mark.parametrize("action_date_index", [6, 25])
def test_corporate_action_in_inclusive_raw_price_window_voids_label(
    action_date_index: int,
) -> None:
    sessions = _sessions()

    rows, diagnostics = build_v5_dataset_with_diagnostics(
        signal_rows=[_signal("AAA", sessions[5])],
        stock_bars={"AAA": _stock_bars("AAA", sessions)},
        market_bars=_market_bars(sessions),
        corporate_actions=[
            {"symbol": "AAA", "action_date": sessions[action_date_index], "action_type": "split"}
        ],
    )

    assert rows == []
    assert diagnostics["corporate_action_void"] == 1


def test_dataset_has_daily_step_one_semantics_only() -> None:
    sessions = _sessions(50)
    signals = [_signal("AAA", sessions[5]), _signal("AAA", sessions[6])]
    kwargs = {
        "signal_rows": signals,
        "stock_bars": {"AAA": _stock_bars("AAA", sessions)},
        "market_bars": _market_bars(sessions),
    }

    assert [row["date"] for row in build_v5_dataset(**kwargs, step=1)] == sessions[5:7]
    with pytest.raises(ValueError, match="step=1"):
        build_v5_dataset(**kwargs, step=2)


def test_dataset_builder_does_not_mutate_any_input() -> None:
    sessions = _sessions()
    signals = [_signal("AAA", sessions[5])]
    stocks = {"AAA": _stock_bars("AAA", sessions)}
    market = _market_bars(sessions)
    actions = [{"symbol": "AAA", "action_date": sessions[40], "action_type": "dividend"}]
    membership = [{"symbol": "AAA", "date": sessions[5], "is_candidate": True}]
    original = deepcopy((signals, stocks, market, actions, membership))

    build_v5_dataset(
        signal_rows=signals,
        stock_bars=stocks,
        market_bars=market,
        corporate_actions=actions,
        membership_history=membership,
    )

    assert (signals, stocks, market, actions, membership) == original


def test_close_path_risk_labels_use_entry_open_through_chosen_exit() -> None:
    sessions = _sessions()
    bars = _stock_bars("AAA", sessions)
    entry_date = sessions[6]
    exit_date = sessions[25]
    for bar in bars:
        if entry_date <= bar["date"] <= exit_date:
            bar["open"] = 100.0
            bar["close"] = 100.0
    next(bar for bar in bars if bar["date"] == sessions[10])["close"] = 94.0
    next(bar for bar in bars if bar["date"] == sessions[15])["close"] = 120.0
    next(bar for bar in bars if bar["date"] == exit_date)["close"] = 110.0

    row = build_v5_dataset(
        signal_rows=[_signal("AAA", sessions[5])],
        stock_bars={"AAA": bars},
        market_bars=_market_bars(sessions),
    )[0]

    assert row["max_adverse_percent"] == pytest.approx(-6.0)
    assert row["max_favorable_percent"] == pytest.approx(20.0)
    assert row["mae_magnitude_percent"] == pytest.approx(6.0)
    assert row["severe_mae"] is True
    assert row["success_after_cost"] is True
    assert row["success"] is True


def test_one_percent_cost_hurdle_is_strict() -> None:
    sessions = _sessions()
    bars = _stock_bars("AAA", sessions)
    next(bar for bar in bars if bar["date"] == sessions[6])["open"] = 100.0
    next(bar for bar in bars if bar["date"] == sessions[25])["close"] = 101.0
    flat_market = [{"date": session, "open": 1_000.0, "close": 1_000.0} for session in sessions]

    row = build_v5_dataset(
        signal_rows=[_signal("AAA", sessions[5])],
        stock_bars={"AAA": bars},
        market_bars=flat_market,
    )[0]

    assert row["excess_return_percent"] == pytest.approx(1.0)
    assert row["net_alpha_percent"] == pytest.approx(0.0)
    assert row["success_after_cost"] is False
    assert row["success"] is False


def test_turnover_cv_zero_fills_missing_market_sessions() -> None:
    sessions = _sessions()
    signal_date = sessions[7]
    missing_session = sessions[3]
    bars = _stock_bars("AAA", sessions, missing={missing_session})

    row = build_v5_dataset(
        signal_rows=[_signal("AAA", signal_date)],
        stock_bars={"AAA": bars},
        market_bars=_market_bars(sessions),
    )[0]

    expected_turnovers = [1_000.0, 1_100.0, 1_200.0, 0.0, 1_400.0, 1_500.0, 1_600.0, 1_700.0]
    expected_mean = sum(expected_turnovers) / len(expected_turnovers)
    expected_variance = sum((value - expected_mean) ** 2 for value in expected_turnovers) / len(
        expected_turnovers
    )
    assert row["stability_features"]["trade_availability_20"] == pytest.approx(7 / 8)
    assert row["stability_features"]["turnover_cv_20"] == pytest.approx(
        expected_variance**0.5 / expected_mean
    )


def test_zero_turnover_stability_window_is_omitted_and_diagnosed() -> None:
    sessions = _sessions()
    bars = _stock_bars("AAA", sessions)
    for bar in bars[:8]:
        bar["turnover"] = 0.0

    rows, diagnostics = build_v5_dataset_with_diagnostics(
        signal_rows=[_signal("AAA", sessions[7])],
        stock_bars={"AAA": bars},
        market_bars=_market_bars(sessions),
    )

    assert rows == []
    assert diagnostics["invalid_stability"] == 1


def test_baseline_median_uses_only_prior_five_market_sessions_and_neutral_fallback() -> None:
    sessions = _sessions()
    current = _signal("AAA", sessions[10], baseline_percentile=0.95)
    old_history = [
        _signal("AAA", sessions[index], baseline_percentile=value)
        for index, value in zip(range(0, 5), (0.01, 0.02, 0.03, 0.04, 0.05))
    ]

    rows = build_v5_dataset(
        signal_rows=[*old_history, current, _signal("BBB", sessions[10])],
        stock_bars={
            "AAA": _stock_bars("AAA", sessions),
            "BBB": _stock_bars("BBB", sessions),
        },
        market_bars=_market_bars(sessions),
    )

    aaa = next(row for row in rows if row["symbol"] == "AAA" and row["date"] == sessions[10])
    bbb = next(row for row in rows if row["symbol"] == "BBB")
    assert aaa["stability_features"]["baseline_percentile_median_5"] == 0.50
    assert bbb["stability_features"]["baseline_percentile_median_5"] == 0.50


def test_baseline_median_can_use_non_candidate_rank_history() -> None:
    sessions = _sessions()
    current = _signal("AAA", sessions[10], baseline_percentile=0.95)
    baseline_history = [
        _signal("AAA", sessions[index], baseline_percentile=value)
        for index, value in zip(range(5, 10), (0.10, 0.20, 0.30, 0.40, 0.50))
    ]

    row = build_v5_dataset(
        signal_rows=[current],
        baseline_history=baseline_history,
        stock_bars={"AAA": _stock_bars("AAA", sessions)},
        market_bars=_market_bars(sessions),
    )[0]

    assert row["stability_features"]["baseline_percentile_median_5"] == pytest.approx(0.30)


def test_current_signal_date_membership_cannot_change_lagged_count() -> None:
    sessions = _sessions()
    history = [
        {"symbol": "AAA", "date": sessions[index], "is_candidate": index in {3, 5, 6}}
        for index in range(2, 8)
    ]
    kwargs = {
        "signal_rows": [_signal("AAA", sessions[7])],
        "stock_bars": {"AAA": _stock_bars("AAA", sessions)},
        "market_bars": _market_bars(sessions),
    }

    without_current = build_v5_dataset(**kwargs, membership_history=history)[0]
    with_current_history = [
        {**item, "is_candidate": True} if item["date"] == sessions[7] else item
        for item in history
    ]
    with_current = build_v5_dataset(**kwargs, membership_history=with_current_history)[0]

    assert without_current["stability_features"]["candidate_membership_count_5"] == 3
    assert with_current["stability_features"]["candidate_membership_count_5"] == 3
