from __future__ import annotations

from datetime import date, timedelta

RANGE_WINDOWS: dict[str, timedelta | None] = {
    "1W": timedelta(weeks=1),
    "1M": timedelta(days=30),
    "3M": timedelta(days=91),
    "6M": timedelta(days=182),
    "1Y": timedelta(days=365),
    "3Y": timedelta(days=365 * 3),
    "5Y": timedelta(days=365 * 5),
    "ALL": None,
}

VALID_RANGES = {"1D", *RANGE_WINDOWS}


def range_cutoff(range_: str) -> date | None:
    window = RANGE_WINDOWS[range_]
    if window is None:
        return None
    return date.today() - window
