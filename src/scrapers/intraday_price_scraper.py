from __future__ import annotations

from typing import Any

from src.scrapers.sharesansar_scraper import scrape_today_price


def get_intraday_snapshot() -> list[dict[str, Any]]:
    rows = scrape_today_price()
    results = []

    for row in rows:
        symbol = row.get("symbol")
        if not symbol:
            continue

        ltp = row.get("lastTradedPrice")
        prev_close = row.get("previousDayClosePrice")
        percent_change = None
        if ltp is not None and prev_close is not None and prev_close != 0:
            percent_change = (ltp - prev_close) / prev_close * 100

        results.append(
            {
                "symbol": symbol,
                "ltp": ltp,
                "volume_so_far": int(row["totalTradedQuantity"]) if row.get("totalTradedQuantity") is not None else None,
                "turnover_so_far": row.get("totalTradedValue"),
                "day_high_so_far": row.get("highPrice"),
                "day_low_so_far": row.get("lowPrice"),
                "percent_change": percent_change,
            }
        )

    return results
