from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.backtest import knowledge_time as kt


def ss_frame(rows):
    return pd.DataFrame(rows, columns=["source_id", "symbol", "fiscal_year", "quarter", "is_correction", "net_profit", "published_date"])


def test_quarter_end_maps_bs_fiscal_quarters():
    assert kt.quarter_end("2079/2080", 1) == date(2022, 10, 16)
    assert kt.quarter_end("2079/2080", 4) == date(2023, 7, 15)
    assert kt.quarter_end(None, 1) is None


def test_sharesansar_date_never_earlier_than_slug_creation_date():
    frame = ss_frame([
        ("abc-2023-05-15", "A", "2079/2080", 3, False, 1.0, date(2023, 5, 15)),
        ("def-2023-06-01", "B", "2079/2080", 3, False, 1.0, date(2023, 5, 10)),
        ("ghi", "C", "2079/2080", 3, False, 1.0, date(2023, 5, 12)),
        ("jkl", "D", "2079/2080", 3, False, 1.0, date(2023, 3, 1)),
    ])
    out = kt.sharesansar_dates(frame).set_index("symbol")
    assert out.loc["A", "knowledge_date"] == date(2023, 5, 15)
    assert out.loc["B", "knowledge_date"] == date(2023, 6, 1)
    assert out.loc["C", "knowledge_date"] == date(2023, 5, 12)
    assert not out.loc["D", "verified"]


def test_merolagani_date_clamped_to_previous_ids():
    base = date(2020, 1, 1)
    rows = [(str(1000 + i), f"S{i}", "2076/2077", 2, False, None, base + timedelta(days=i // 5)) for i in range(60)]
    rows.append(("2000", "LATE", "2076/2077", 2, False, None, date(2019, 1, 1)))
    out = kt.merolagani_dates(ss_frame(rows)).set_index("symbol")
    assert out.loc["LATE", "id_backdated"]
    assert out.loc["LATE", "knowledge_date"] >= base + timedelta(days=5)
    assert out.loc["S30", "knowledge_date"] == base + timedelta(days=6)


def reports():
    return pd.DataFrame({
        "symbol": ["A", "B", "C"], "fiscal_year": ["2079/2080"] * 3, "quarter": [3, 3, 3], "net_profit": [1.0, 2.0, 3.0],
        "published_date": [date(2023, 5, 10), date(2023, 5, 20), date(2023, 5, 12)],
        "ss_date": [date(2023, 5, 10), date(2023, 5, 20), date(2023, 5, 12)],
        "ml_date": [date(2023, 5, 15), date(2023, 5, 11), None],
    })


def test_earliest_rule_is_stable_under_truncation():
    full = reports()
    full["available_date"] = [kt.earliest(a, b) for a, b in zip(full["ss_date"], full["ml_date"])]
    for day in (date(2023, 5, 10), date(2023, 5, 11), date(2023, 5, 13), date(2023, 5, 16), date(2023, 5, 25)):
        cut = kt.truncate_reports(full, day)
        known_full = full[[d <= day for d in full["available_date"]]].set_index("symbol")["available_date"].to_dict()
        assert cut.set_index("symbol")["available_date"].to_dict() == known_full


def test_later_of_rule_depends_on_the_future():
    full = reports()
    later = [max(a, b) if b is not None else a for a, b in zip(full["published_date"], full["ml_date"])]
    day = date(2023, 5, 12)
    known_full = {s for s, d in zip(full["symbol"], later) if d <= day}
    cut = full[full["published_date"] <= day]
    cut_ml = [m if m is not None and m <= day else None for m in cut["ml_date"]]
    known_cut = {s for s, a, b in zip(cut["symbol"], cut["published_date"], cut_ml) if (max(a, b) if b is not None else a) <= day}
    assert known_full != known_cut


def test_combine_takes_earliest_verified_and_drops_unverified():
    ss = kt.sharesansar_dates(ss_frame([
        ("x", "A", "2079/2080", 3, False, 1.0, date(2023, 5, 20)),
        ("y", "B", "2079/2080", 3, False, 1.0, date(2023, 3, 1)),
    ]))
    ml = kt.merolagani_dates(ss_frame([
        ("10", "A", "2079/2080", 3, False, None, date(2023, 5, 11)),
        ("11", "B", "2079/2080", 3, False, None, date(2023, 3, 2)),
    ]))
    out = kt.combine(ss, ml).set_index("symbol")
    assert out.loc["A", "available_date"] == date(2023, 5, 11)
    assert "B" not in out.index
