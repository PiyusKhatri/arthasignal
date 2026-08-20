from __future__ import annotations

import math
from bisect import bisect_right
from statistics import mean, pstdev
from typing import Any

from src.database.connection import get_session
from src.services.nepse_quant_research import NEPSE_INDEX_NAME, _load_index_series, _sector_index_name


def _safe_return(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def index_context_as_of(series: dict[str, list[Any]], target_date: Any) -> dict[str, float | None]:
    """Return index state using only observations available on or before target_date."""
    dates = series.get("dates", [])
    closes = series.get("closes", [])
    if not dates or not closes:
        return {}
    index = bisect_right(dates, target_date) - 1
    if index < 20:
        return {}

    latest = float(closes[index])
    ret20 = _safe_return(latest, float(closes[index - 20]))
    ret60 = _safe_return(latest, float(closes[index - 60])) if index >= 60 else None
    sma50 = mean(float(value) for value in closes[index - 49 : index + 1]) if index >= 49 else None
    sma200 = mean(float(value) for value in closes[index - 199 : index + 1]) if index >= 199 else None
    peak_window = [float(value) for value in closes[max(0, index - 251) : index + 1]]
    peak = max(peak_window) if peak_window else latest
    drawdown = _safe_return(latest, peak)

    vol_window = [float(value) for value in closes[max(0, index - 60) : index + 1]]
    daily_returns = [
        current / previous - 1.0
        for previous, current in zip(vol_window[:-1], vol_window[1:])
        if previous > 0
    ]
    volatility = pstdev(daily_returns) * math.sqrt(252.0) * 100.0 if len(daily_returns) >= 2 else None
    return {
        "close": latest,
        "return_20d_percent": ret20,
        "return_60d_percent": ret60,
        "sma50": sma50,
        "sma200": sma200,
        "drawdown_252d_percent": drawdown,
        "annualized_volatility_60d_percent": volatility,
    }


def attach_exact_regime_context(rows: list[dict[str, Any]]) -> None:
    """Attach point-in-time NEPSE and sector index context to historical rows."""
    sectors = sorted({str(row.get("sector") or "") for row in rows if row.get("sector")})
    with get_session() as session:
        market_series = _load_index_series(session, NEPSE_INDEX_NAME)
        sector_series: dict[str, dict[str, list[Any]]] = {}
        for sector in sectors:
            index_name = _sector_index_name(session, sector)
            sector_series[sector] = _load_index_series(session, index_name)

    market_cache: dict[Any, dict[str, float | None]] = {}
    sector_cache: dict[tuple[str, Any], dict[str, float | None]] = {}
    for row in rows:
        trading_date = row["date"]
        if trading_date not in market_cache:
            market_cache[trading_date] = index_context_as_of(market_series, trading_date)
        market = market_cache[trading_date]

        sector = str(row.get("sector") or "")
        sector_key = (sector, trading_date)
        if sector_key not in sector_cache:
            sector_cache[sector_key] = index_context_as_of(sector_series.get(sector, {}), trading_date)
        sector_context = sector_cache[sector_key]

        market_return = market.get("return_20d_percent")
        sector_return = sector_context.get("return_20d_percent")
        row["regime_context"] = {
            "market_close": market.get("close"),
            "market_return_20d_percent": market_return,
            "market_return_60d_percent": market.get("return_60d_percent"),
            "market_sma50": market.get("sma50"),
            "market_sma200": market.get("sma200"),
            "market_drawdown_252d_percent": market.get("drawdown_252d_percent"),
            "market_annualized_volatility_60d_percent": market.get("annualized_volatility_60d_percent"),
            "sector_return_20d_percent": sector_return,
            "sector_relative_strength_20d_percent": (
                float(sector_return) - float(market_return)
                if sector_return is not None and market_return is not None
                else None
            ),
        }
