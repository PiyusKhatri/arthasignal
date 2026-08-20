from __future__ import annotations

from datetime import date, timedelta

from src.services.quant_historical_context import index_context_as_of


def test_future_index_values_cannot_change_historical_context() -> None:
    start = date(2020, 1, 1)
    dates = [start + timedelta(days=index) for index in range(260)]
    closes = [100.0 + index * 0.5 for index in range(260)]
    target = dates[220]

    before = index_context_as_of({"dates": dates, "closes": closes}, target)
    extended_dates = dates + [dates[-1] + timedelta(days=index + 1) for index in range(60)]
    extended_closes = closes + [9999.0 + index * 100.0 for index in range(60)]
    after = index_context_as_of({"dates": extended_dates, "closes": extended_closes}, target)

    assert before == after
