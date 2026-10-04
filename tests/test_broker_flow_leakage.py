from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtest.broker_flow_features import FeatureInputs, build_feature_store, floorsheet_files

FEATURES = ["h1", "h2", "h3", "h4", "h5"]
SESSIONS = 640
SYMBOLS = 30
BROKERS = [str(code) for code in range(1, 13)]
CUT_INDEX = 600


def _sessions() -> list[date]:
    days = []
    day = date(2015, 1, 1)
    while len(days) < SESSIONS:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _market(seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[date]]:
    rng = np.random.default_rng(seed)
    sessions = _sessions()
    symbols = [f"S{i:02d}" for i in range(SYMBOLS)]
    price_rows = []
    trade_rows = []
    for symbol in symbols:
        close = 100.0
        for day in sessions:
            close *= float(np.exp(rng.normal(0.0, 0.035)))
            price_rows.append((symbol, day, round(close, 2)))
            for _ in range(int(rng.integers(5, 10))):
                trade_rows.append(
                    (
                        symbol,
                        day,
                        str(rng.choice(BROKERS)),
                        str(rng.choice(BROKERS)),
                        int(rng.integers(10, 500)),
                        round(close * float(rng.uniform(0.98, 1.02)), 2),
                    )
                )
    prices = pd.DataFrame(price_rows, columns=["symbol", "date", "close"])
    trades = pd.DataFrame(trade_rows, columns=["symbol", "d", "buyer", "seller", "q", "rate"])
    actions = pd.DataFrame({"symbol": ["S03", "S07"], "action_date": [sessions[300], sessions[450]]})
    return prices, trades, actions, sessions


def _write_files(root: Path, trades: pd.DataFrame) -> list[tuple[date, Path]]:
    for day, group in trades.groupby("d"):
        folder = root / f"year={day.year}" / f"month={day.month:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {
                "transaction_no": [f"{day:%Y%m%d}{i}" for i in range(len(group))],
                "symbol": group["symbol"].values,
                "security_name": group["symbol"].values,
                "buyer_broker_code": group["buyer"].values,
                "buyer_broker_name": group["buyer"].values,
                "seller_broker_code": group["seller"].values,
                "seller_broker_name": group["seller"].values,
                "contract_quantity": group["q"].astype("int64").values,
                "contract_rate": group["rate"].values,
                "contract_amount": (group["q"] * group["rate"]).values,
                "business_date": [day.isoformat()] * len(group),
                "page_number": [1] * len(group),
                "data_quality_gap_pct": [0.0] * len(group),
            }
        ).to_parquet(folder / f"day={day.day:02d}.parquet", index=False)
    return floorsheet_files(root, end=date(2025, 1, 19))


def _companies() -> pd.DataFrame:
    return pd.DataFrame({"symbol": [f"S{i:02d}" for i in range(SYMBOLS)], "instrument_type": "Equity"})


def _build(tmp: Path, name: str, prices, trades, actions) -> pd.DataFrame:
    files = _write_files(tmp / name / "raw", trades)
    return build_feature_store(
        FeatureInputs(files, prices, actions, _companies()), tmp / name / "derived", memory_limit="1GB"
    ).set_index(["symbol", "date"]).sort_index()


@pytest.fixture(scope="module")
def market():
    return _market()


@pytest.fixture(scope="module")
def full(tmp_path_factory, market):
    prices, trades, actions, _ = market
    return _build(tmp_path_factory.mktemp("full"), "full", prices, trades, actions)


def _assert_same_until(left: pd.DataFrame, right: pd.DataFrame, cut: date) -> None:
    a = left[left.index.get_level_values("date") <= cut][FEATURES + ["eligible"]]
    b = right[right.index.get_level_values("date") <= cut][FEATURES + ["eligible"]]
    assert a.index.equals(b.index)
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-15)


def test_synthetic_data_exercises_every_feature_before_the_cut(full, market) -> None:
    cut = market[3][CUT_INDEX]
    before = full[full.index.get_level_values("date") <= cut]
    for feature in FEATURES:
        assert before[feature].notna().sum() > 100, feature
    assert before["eligible"].sum() > 1000


def test_removing_data_after_t_does_not_change_features_up_to_t(tmp_path, market, full) -> None:
    prices, trades, actions, sessions = market
    cut = sessions[CUT_INDEX]
    truncated = _build(
        tmp_path,
        "cut",
        prices[prices["date"] <= cut],
        trades[trades["d"] <= cut],
        actions[actions["action_date"] <= cut],
    )
    _assert_same_until(full, truncated, cut)


def test_changing_data_after_t_does_not_change_features_up_to_t(tmp_path, market, full) -> None:
    prices, trades, actions, sessions = market
    cut = sessions[CUT_INDEX]
    rng = np.random.default_rng(99)
    trades = trades.copy()
    later = trades["d"] > cut
    trades.loc[later, "q"] = (trades.loc[later, "q"] * rng.uniform(0.1, 9.0, later.sum())).round().clip(lower=1)
    trades.loc[later, "buyer"] = "1"
    prices = prices.copy()
    later_prices = prices["date"] > cut
    prices.loc[later_prices, "close"] = prices.loc[later_prices, "close"] * 3.0
    actions = pd.concat(
        [actions, pd.DataFrame({"symbol": ["S01", "S02"], "action_date": [sessions[CUT_INDEX + 2], sessions[CUT_INDEX + 5]]})]
    )
    changed = _build(tmp_path, "changed", prices, trades, actions)
    _assert_same_until(full, changed, cut)
    after = full.index.get_level_values("date") > cut
    assert not np.allclose(full.loc[after, "h1"].fillna(0), changed.loc[after, "h1"].fillna(0))


def test_ratio_features_ignore_a_uniform_quantity_scale(tmp_path, market, full) -> None:
    prices, trades, actions, _ = market
    scaled = trades.copy()
    scaled["q"] = scaled["q"] * 50
    rebuilt = _build(tmp_path, "scaled", prices, scaled, actions)
    pd.testing.assert_frame_equal(full[FEATURES], rebuilt[FEATURES], check_exact=False, rtol=1e-9, atol=1e-12)
