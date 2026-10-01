from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.backtest import event_study as es
from src.backtest import event_tables as et


def _sessions(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


SESSIONS = _sessions(date(2016, 1, 1), 400)
SYMBOLS = [f"S{i}" for i in range(12)]
SECTORS = {s: ("A" if i % 2 else "B") for i, s in enumerate(SYMBOLS)}
ACTIONS = pd.DataFrame(
    {"symbol": ["S1", "S2"], "action_date": [SESSIONS[100], SESSIONS[300]], "action_type": ["BONUS", "DIVIDEND"],
     "ratio_or_amount": [10.0, 5.0]}
)
CUT = SESSIONS[250]


def _prices(seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i, symbol in enumerate(SYMBOLS):
        close = 100.0
        start = 0 if i < 10 else 150
        for k, day in enumerate(SESSIONS[start:]):
            prev = close
            if rng.random() < 0.03:
                close = prev * 1.10
            else:
                close = prev * float(np.exp(rng.normal(0, 0.02)))
            if symbol == "S1" and day >= SESSIONS[100] and SESSIONS[start + k - 1] < SESSIONS[100]:
                close = close / 1.10
            volume = float(rng.integers(100, 200)) * (8 if rng.random() < 0.02 else 1)
            rows.append((symbol, day, prev, max(prev, close), min(prev, close), close, volume, volume * close))
    return pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])


def _index(prices: pd.DataFrame) -> pd.DataFrame:
    level = prices.groupby("date")["close"].mean().rename("close").reset_index()
    for column in ("open", "high", "low"):
        level[column] = level["close"]
    return level


RATES = pd.DataFrame(
    {"fiscal_year": ["2072/2073"] * 3, "month": ["Poush", "Magh", "Falgun"],
     "treasury_bill_rate": [1.0, 2.0, 3.0], "interbank_commercial_rate": [1.5, 2.5, 3.5]}
)


def _tables(prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    panel = es.build_panel(prices, ACTIONS, SECTORS, sessions=SESSIONS)
    listings = et.new_listing_events(panel, set())
    streaks = et.circuit_streak_events(panel, listings)
    return {
        "listings": listings,
        "streaks": streaks,
        "volume": et.volume_anomaly_events(panel, ACTIONS, listings),
        "state": et.market_state(panel, _index(prices), RATES),
    }


def _until(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    return frame[frame[column] <= CUT].reset_index(drop=True)


@pytest.fixture(scope="module")
def full():
    return _tables(_prices())


def test_fixture_produces_every_event_type(full) -> None:
    assert len(full["listings"]) == 2
    assert (full["streaks"]["direction"] == "up").sum() > 50
    assert len(_until(full["volume"], "event_date")) > 20
    assert full["state"]["trend_up"].notna().sum() > 100


@pytest.mark.parametrize("variant", ["truncated", "changed"])
def test_event_tables_up_to_t_ignore_later_prices(full, variant) -> None:
    prices = _prices()
    if variant == "truncated":
        other = prices[prices["date"] <= CUT]
    else:
        other = prices.copy()
        late = other["date"] > CUT
        rng = np.random.default_rng(11)
        factor = rng.uniform(0.92, 1.08, late.sum())
        other.loc[late, ["open", "high", "low", "close"]] = other.loc[late, ["open", "high", "low", "close"]].mul(factor, axis=0)
        other.loc[late, "volume"] = other.loc[late, "volume"] * 20
    tables = _tables(other)
    pd.testing.assert_frame_equal(_until(full["listings"], "listing_date"), _until(tables["listings"], "listing_date"))
    pd.testing.assert_frame_equal(_until(full["streaks"], "event_date"), _until(tables["streaks"], "event_date"))
    pd.testing.assert_frame_equal(_until(full["volume"], "event_date"), _until(tables["volume"], "event_date"))
    state_full = full["state"][full["state"]["date"] <= CUT].reset_index(drop=True)
    state_other = tables["state"][tables["state"]["date"] <= CUT].reset_index(drop=True)
    pd.testing.assert_frame_equal(state_full, state_other)


def test_interest_rate_is_used_only_after_publication_lag() -> None:
    available = et.nepali_month_available_date("2080/2081", "Shrawan")
    assert available == date(2023, 7, 16) + timedelta(days=round(30.44)) + timedelta(days=et.RATE_PUBLICATION_LAG_DAYS)
    assert et.nepali_month_available_date("2080/2081", "Unknown") is None
