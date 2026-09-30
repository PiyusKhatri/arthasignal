from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from src.services.quant_v5_dataset import build_v5_dataset


def _inputs() -> tuple[
    list[dict[str, object]],
    dict[str, list[dict[str, object]]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    start = date(2025, 1, 1)
    sessions = [start + timedelta(days=index) for index in range(45)]
    market = [
        {"date": session, "open": 1_000.0 + index, "close": 1_001.0 + index}
        for index, session in enumerate(sessions)
    ]
    stocks = {
        "AAA": [
            {
                "symbol": "AAA",
                "date": session,
                "open": 100.0 + index,
                "close": 101.0 + index,
                "turnover": 1_000.0 + 10 * index,
            }
            for index, session in enumerate(sessions)
        ]
    }
    signals = [
        {
            "symbol": "AAA",
            "date": sessions[index],
            "baseline_percentile": 0.1 * index,
            "turnover_percentile": 0.70,
            "features": {"signal": float(index)},
        }
        for index in range(1, 7)
    ]
    membership = [
        {"symbol": "AAA", "date": sessions[index], "is_candidate": index % 2 == 0}
        for index in range(45)
    ]
    return signals, stocks, market, membership


def _row_for(rows: list[dict[str, object]], signal_date: date) -> dict[str, object]:
    return next(row for row in rows if row["date"] == signal_date)


def test_stability_features_are_invariant_to_all_post_signal_observations() -> None:
    signals, stocks, market, membership = _inputs()
    signal_date = signals[-1]["date"]
    before = _row_for(
        build_v5_dataset(
            signal_rows=signals,
            stock_bars=stocks,
            market_bars=market,
            membership_history=membership,
        ),
        signal_date,
    )

    changed_signals = deepcopy(signals)
    changed_stocks = deepcopy(stocks)
    changed_membership = deepcopy(membership)
    for bar in changed_stocks["AAA"]:
        if bar["date"] > signal_date:
            bar["turnover"] = 9_999_999_999.0
    for item in changed_membership:
        if item["date"] > signal_date:
            item["is_candidate"] = True
    changed_signals.append(
        {
            "symbol": "AAA",
            "date": market[30]["date"],
            "baseline_percentile": 0.999,
            "turnover_percentile": 0.999,
            "features": {"signal": 999.0},
        }
    )
    after = _row_for(
        build_v5_dataset(
            signal_rows=changed_signals,
            stock_bars=changed_stocks,
            market_bars=market,
            membership_history=changed_membership,
        ),
        signal_date,
    )

    assert after["stability_features"] == before["stability_features"]


def test_as_of_cutoff_makes_results_invariant_to_appended_future_data() -> None:
    signals, stocks, market, membership = _inputs()
    as_of_date = market[30]["date"]
    kwargs = {
        "signal_rows": signals,
        "stock_bars": stocks,
        "market_bars": market,
        "membership_history": membership,
        "as_of_date": as_of_date,
    }
    before = build_v5_dataset(**kwargs)

    future_date = market[-1]["date"] + timedelta(days=1)
    extended_market = [*market, {"date": future_date, "open": 999_000.0, "close": 1.0}]
    extended_stocks = deepcopy(stocks)
    extended_stocks["AAA"].append(
        {
            "symbol": "AAA",
            "date": future_date,
            "open": 999_000.0,
            "close": 1.0,
            "turnover": 999_000.0,
        }
    )
    extended_signals = [
        *signals,
        {
            "symbol": "AAA",
            "date": future_date,
            "baseline_percentile": 1.0,
            "turnover_percentile": 1.0,
        },
    ]

    after = build_v5_dataset(
        signal_rows=extended_signals,
        stock_bars=extended_stocks,
        market_bars=extended_market,
        membership_history=[
            *membership,
            {"symbol": "AAA", "date": future_date, "is_candidate": True},
        ],
        corporate_actions=[
            {"symbol": "AAA", "action_date": future_date, "action_type": "split"}
        ],
        as_of_date=as_of_date,
    )

    assert after == before


def test_row_is_unlabeled_until_entire_exit_grace_window_is_observable() -> None:
    signals, stocks, market, membership = _inputs()
    signal_date = signals[-1]["date"]
    target_index = market.index(next(item for item in market if item["date"] == signal_date)) + 20

    rows = build_v5_dataset(
        signal_rows=signals,
        stock_bars=stocks,
        market_bars=market,
        membership_history=membership,
        as_of_date=market[target_index - 1]["date"],
    )

    assert all(row["date"] != signal_date for row in rows)

