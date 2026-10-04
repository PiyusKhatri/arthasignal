from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.scorecard import info_eval as ie
from src.scorecard import info_spec


def _sessions(n: int) -> list[date]:
    days, day = [], date(2015, 1, 1)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


SESSIONS = _sessions(400)


def test_prior_fiscal_year() -> None:
    assert ie.prior_fiscal_year("2081/2082") == "2080/2081"
    assert ie.prior_fiscal_year(None) is None


def test_knowledge_session_is_strictly_after_publication() -> None:
    t = ie.knowledge_index(SESSIONS, [SESSIONS[10], SESSIONS[10] + timedelta(hours=0)])
    assert list(t) == [11, 11]


def test_growth_uses_only_an_earlier_published_prior_report() -> None:
    reports = pd.DataFrame({
        "symbol": ["A", "A", "B", "B"],
        "fiscal_year": ["2071/2072", "2072/2073", "2071/2072", "2072/2073"],
        "quarter": [1, 1, 1, 1],
        "net_profit": [10e6, 15e6, -5e6, 2e6],
        "available_date": [SESSIONS[5], SESSIONS[200], SESSIONS[300], SESSIONS[250]],
    })
    frame = ie.add_growth(reports, SESSIONS)
    a = frame[(frame.symbol == "A") & (frame.fiscal_year == "2072/2073")].iloc[0]
    b = frame[(frame.symbol == "B") & (frame.fiscal_year == "2072/2073")].iloc[0]
    assert np.isclose(a.growth, 0.5)
    assert np.isnan(b.growth)


def test_quartile_threshold_ignores_reports_known_at_or_after_t() -> None:
    n = info_spec.QUARTILE_MIN_REPORTS
    reports = pd.DataFrame({"t": list(range(100, 100 + n)) + [200, 200], "growth": [0.0] * n + [9.0, 9.0]})
    thresholds = ie.quartile_thresholds(reports, 400)
    assert thresholds[200] == 0.0
    assert 100 not in thresholds


def test_selection_does_not_change_when_later_data_is_removed() -> None:
    rng = np.random.default_rng(1)
    rows = []
    for s in range(30):
        for k, fy in enumerate(["2071/2072", "2072/2073", "2073/2074"]):
            rows.append({"symbol": f"S{s}", "fiscal_year": fy, "quarter": 1, "net_profit": float(rng.normal(20e6, 10e6)),
                         "available_date": SESSIONS[20 + 120 * k + s], "published_date": SESSIONS[20 + 120 * k + s], "ml_date": None})
    reports = pd.DataFrame(rows)
    declarations = pd.DataFrame(columns=["symbol", "fiscal_year", "cash", "bonus", "total", "announcement_date", "bookclose_date", "available_date"])
    symbols = {f"S{s}" for s in range(30)}
    full = ie.select_events(reports, declarations, SESSIONS, symbols, {})
    audit = ie.lookahead_audit(reports, declarations, SESSIONS, symbols, {}, full)
    assert audit["dates_checked"] > 0 and audit["leaky"] == []
