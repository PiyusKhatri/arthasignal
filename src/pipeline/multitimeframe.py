from __future__ import annotations

from datetime import timedelta
from typing import Sequence


def _group_key(bar_date, freq: str):
    if freq == "W":
        iso_year, iso_week, iso_weekday = bar_date.isocalendar()
        if iso_weekday == 7:
            iso_year, iso_week, _ = (bar_date + timedelta(days=1)).isocalendar()
        return (iso_year, iso_week)
    if freq == "M":
        return (bar_date.year, bar_date.month)
    raise ValueError(f"unsupported freq: {freq!r}")


def resample_ohlcv(bars: Sequence[dict], freq: str) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for bar in bars:
        key = _group_key(bar["date"], freq)
        groups.setdefault(key, []).append(bar)

    result = []
    for group_bars in groups.values():
        result.append(
            {
                "period_start": group_bars[0]["date"],
                "period_end": group_bars[-1]["date"],
                "open": group_bars[0]["open"],
                "high": max(b["high"] for b in group_bars),
                "low": min(b["low"] for b in group_bars),
                "close": group_bars[-1]["close"],
                "volume": sum(b["volume"] for b in group_bars),
            }
        )

    return result
