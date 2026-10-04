from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from src.backtest import event_study as es
from src.backtest.event_data import DEVELOPMENT_END, load_inputs, load_panel

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(os.environ.get("ARTHASIGNAL_DERIVED_DIR", "~/Desktop/arthasignal-ai/derived")).expanduser() / "events"
COUNTS_OUTPUT = Path("docs/event_counts.json")
LISTING_VISIBLE_AFTER = date(2014, 7, 1)
LISTING_MIN_PANEL_SESSIONS = 20
VOLUME_LOOKBACK = 60
VOLUME_MIN_HISTORY = 40
VOLUME_RATIO = 5.0
VOLUME_ACTION_BUFFER = 20
VOLUME_MIN_SESSIONS_SINCE_LISTING = 60
BREADTH_SMA = 50
TREND_SMA = 200
VOL_WINDOW = 20
TURNOVER_LOOKBACK = 60
RATE_PUBLICATION_LAG_DAYS = 45
NEPALI_MONTHS = (
    "Shrawan", "Bhadra", "Ashwin", "Kartik", "Mangsir", "Poush",
    "Magh", "Falgun", "Chaitra", "Baisakh", "Jestha", "Asadh",
)
POWER_HORIZON = 20
POWER_SAMPLE_SEED = 7
POWER_SAMPLE = 20000


def _merger_symbols(inputs: dict[str, pd.DataFrame]) -> set[str]:
    from sqlalchemy import text

    from src.database.holdout_guard import engine

    with engine.connect() as connection:
        rows = connection.execute(text("SELECT DISTINCT new_symbol FROM symbol_history")).all()
    return {row[0] for row in rows}


def book_close_events(panel: es.Panel, actions: pd.DataFrame) -> pd.DataFrame:
    frame = actions[actions["action_date"] <= DEVELOPMENT_END].copy()
    frame = frame[frame["symbol"].isin(panel.row)]
    pivot = frame.pivot_table(
        index=["symbol", "action_date"], columns="action_type", values="ratio_or_amount", aggfunc="max"
    ).reset_index()
    for column in ("BONUS", "DIVIDEND", "RIGHT"):
        if column not in pivot:
            pivot[column] = np.nan
    pivot = pivot.rename(columns={"action_date": "event_date", "BONUS": "bonus_pct", "DIVIDEND": "cash_pct", "RIGHT": "right_pct"})
    pivot["ex_index"] = pivot["event_date"].map(panel.first_session_on_or_after)
    pivot = pivot[pivot["ex_index"].notna()].copy()
    pivot["ex_index"] = pivot["ex_index"].astype(int)
    pivot["ex_session"] = pivot["ex_index"].map(lambda i: panel.sessions[i])
    pivot["kind"] = np.select(
        [pivot["right_pct"].notna(), pivot["bonus_pct"].notna() & pivot["cash_pct"].notna(), pivot["bonus_pct"].notna()],
        ["right", "bonus_and_cash", "bonus_only"],
        default="cash_only",
    )
    pivot["traded_on_ex"] = [not np.isnan(panel.close[panel.row[s], i]) for s, i in zip(pivot["symbol"], pivot["ex_index"])]
    return pivot.sort_values(["ex_session", "symbol"]).reset_index(drop=True)


def first_price_index(panel: es.Panel) -> dict[str, int]:
    out = {}
    for symbol, r in panel.row.items():
        traded = np.flatnonzero(~np.isnan(panel.close[r]))
        if len(traded):
            out[symbol] = int(traded[0])
    return out


def new_listing_events(panel: es.Panel, excluded: set[str]) -> pd.DataFrame:
    records = []
    for symbol, first in first_price_index(panel).items():
        if first < LISTING_MIN_PANEL_SESSIONS or panel.sessions[first] <= LISTING_VISIBLE_AFTER or symbol in excluded:
            continue
        records.append({"symbol": symbol, "listing_index": first, "listing_date": panel.sessions[first]})
    return pd.DataFrame.from_records(records).sort_values("listing_date").reset_index(drop=True)


def sessions_since_listing(panel: es.Panel, listings: pd.DataFrame) -> pd.DataFrame:
    records = []
    for symbol, first in zip(listings["symbol"], listings["listing_index"]):
        r = panel.row[symbol]
        traded = np.flatnonzero(~np.isnan(panel.close[r]))
        for i in traded:
            records.append(
                {"symbol": symbol, "date": panel.sessions[i], "session_index": int(i),
                 "market_sessions_since_listing": int(i - first), "own_trading_days_since_listing": None}
            )
    frame = pd.DataFrame.from_records(records)
    if not frame.empty:
        frame["own_trading_days_since_listing"] = frame.groupby("symbol").cumcount()
    return frame


def circuit_flags(panel: es.Panel) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    shape = panel.close.shape
    up_close = np.zeros(shape, dtype=bool)
    down_close = np.zeros(shape, dtype=bool)
    up_locked = np.zeros(shape, dtype=bool)
    down_locked = np.zeros(shape, dtype=bool)
    limits = np.array([es.circuit_limit(day) for day in panel.sessions])
    for r in range(shape[0]):
        traded = np.flatnonzero(~np.isnan(panel.close[r]))
        for previous, current in zip(traded, traded[1:]):
            if current - previous != 1 or panel.corrupt[r, current]:
                continue
            prev_close = panel.close[r, previous]
            limit = limits[current]
            upper = prev_close * (1 + limit - es.LOCK_TOLERANCE)
            lower = prev_close * (1 - limit + es.LOCK_TOLERANCE)
            up_close[r, current] = panel.close[r, current] >= upper
            down_close[r, current] = panel.close[r, current] <= lower
            up_locked[r, current] = panel.low[r, current] >= upper
            down_locked[r, current] = panel.high[r, current] <= lower
    return up_close, down_close, up_locked, down_locked


def circuit_streak_events(panel: es.Panel, listings: pd.DataFrame) -> pd.DataFrame:
    up_close, down_close, up_locked, down_locked = circuit_flags(panel)
    first = first_price_index(panel)
    listing_symbols = set(listings["symbol"])
    records = []
    for direction, closes, locks in (("up", up_close, up_locked), ("down", down_close, down_locked)):
        for r, symbol in enumerate(panel.symbols):
            streak = 0
            locked_streak = 0
            for c in range(len(panel.sessions)):
                if np.isnan(panel.close[r, c]):
                    continue
                if closes[r, c]:
                    streak += 1
                    locked_streak = locked_streak + 1 if locks[r, c] else 0
                    since = c - first.get(symbol, c)
                    records.append(
                        {
                            "symbol": symbol,
                            "event_date": panel.sessions[c],
                            "session_index": c,
                            "direction": direction,
                            "streak_length": streak,
                            "locked_today": bool(locks[r, c]),
                            "locked_streak_length": locked_streak,
                            "market_sessions_since_first_price": since,
                            "new_listing_window": symbol in listing_symbols and since < 60,
                        }
                    )
                else:
                    streak = 0
                    locked_streak = 0
    return pd.DataFrame.from_records(records)


def streak_end_events(streaks: pd.DataFrame, panel: es.Panel) -> pd.DataFrame:
    records = []
    for (symbol, direction), group in streaks.groupby(["symbol", "direction"]):
        r = panel.row[symbol]
        indices = group["session_index"].to_numpy()
        lengths = group["streak_length"].to_numpy()
        for position, (index, length) in enumerate(zip(indices, lengths)):
            next_streak_index = indices[position + 1] if position + 1 < len(indices) else None
            following = np.flatnonzero(~np.isnan(panel.close[r, index + 1 :]))
            if not len(following):
                continue
            next_trade = index + 1 + int(following[0])
            if next_streak_index is not None and next_streak_index == next_trade and lengths[position + 1] == length + 1:
                continue
            records.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "streak_length": int(length),
                    "last_limit_session": panel.sessions[index],
                    "event_date": panel.sessions[next_trade],
                    "session_index": int(next_trade),
                    "new_listing_window": bool(group["new_listing_window"].iloc[position]),
                }
            )
    return pd.DataFrame.from_records(records)


def volume_anomaly_events(panel: es.Panel, actions: pd.DataFrame, listings: pd.DataFrame) -> pd.DataFrame:
    action_index: dict[str, list[int]] = {}
    for symbol, day in zip(actions["symbol"], actions["action_date"]):
        position = panel.first_session_on_or_after(day)
        if position is not None:
            action_index.setdefault(symbol, []).append(position)
    first = first_price_index(panel)
    records = []
    for r, symbol in enumerate(panel.symbols):
        volume = panel.volume[r]
        traded = np.flatnonzero(~np.isnan(volume))
        if len(traded) <= VOLUME_MIN_HISTORY:
            continue
        actions_here = np.array(sorted(action_index.get(symbol, [])))
        for position in range(VOLUME_MIN_HISTORY, len(traded)):
            c = traded[position]
            window = traded[np.searchsorted(traded, c - VOLUME_LOOKBACK) : position]
            if len(window) < VOLUME_MIN_HISTORY:
                continue
            baseline = float(np.median(volume[window]))
            if baseline <= 0:
                continue
            ratio = volume[c] / baseline
            if ratio < VOLUME_RATIO or panel.corrupt[r, c]:
                continue
            since = c - first[symbol]
            near_past = bool(len(actions_here) and ((actions_here >= c - VOLUME_ACTION_BUFFER) & (actions_here <= c)).any())
            near_future = bool(len(actions_here) and ((actions_here > c) & (actions_here <= c + VOLUME_ACTION_BUFFER)).any())
            ret = panel.total_return[r, c]
            records.append(
                {
                    "symbol": symbol,
                    "event_date": panel.sessions[c],
                    "session_index": int(c),
                    "volume_ratio": float(ratio),
                    "day_return": float(ret) if not np.isnan(ret) else None,
                    "action_in_past_20": near_past,
                    "action_in_next_20": near_future,
                    "sessions_since_first_price": int(since),
                    "eligible_no_news": (not near_past) and (not near_future) and since >= VOLUME_MIN_SESSIONS_SINCE_LISTING,
                }
            )
    return pd.DataFrame.from_records(records)


def nepali_month_available_date(fiscal_year: str, month: str) -> date | None:
    try:
        start_bs = int(fiscal_year.split("/")[0])
        position = NEPALI_MONTHS.index(month)
    except (ValueError, AttributeError):
        return None
    start_ad = start_bs - 57
    month_end = date(start_ad, 7, 16) + timedelta(days=round(30.44 * (position + 1)))
    return month_end + timedelta(days=RATE_PUBLICATION_LAG_DAYS)


def market_state(panel: es.Panel, index: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    sessions = pd.Index(panel.sessions)
    nepse = index.set_index("date")["close"].reindex(sessions).ffill()
    returns = nepse.pct_change()
    frame = pd.DataFrame(index=sessions)
    frame["nepse_close"] = nepse
    frame["nepse_sma_50"] = nepse.rolling(50, min_periods=50).mean()
    frame["nepse_sma_200"] = nepse.rolling(TREND_SMA, min_periods=TREND_SMA).mean()
    frame["trend_up"] = np.where(
        frame["nepse_sma_200"].isna(), np.nan, (frame["nepse_close"] > frame["nepse_sma_200"]).astype(float)
    )
    frame["nepse_return_20"] = nepse / nepse.shift(20) - 1
    frame["realized_vol_20"] = returns.rolling(VOL_WINDOW, min_periods=VOL_WINDOW).std() * np.sqrt(240)
    close = pd.DataFrame(panel.close.T, index=sessions).ffill(limit=5)
    sma = close.rolling(BREADTH_SMA, min_periods=BREADTH_SMA).mean()
    traded_today = pd.DataFrame(~np.isnan(panel.close.T), index=sessions)
    above = (close > sma) & traded_today & sma.notna()
    counted = traded_today & sma.notna()
    frame["breadth_above_sma50"] = above.sum(axis=1) / counted.sum(axis=1).replace(0, np.nan)
    one = pd.DataFrame(panel.one_session_return.T, index=sessions)
    frame["advancers"] = (one > 0).sum(axis=1)
    frame["decliners"] = (one < 0).sum(axis=1)
    turnover = pd.Series(np.nansum(panel.turnover, axis=0), index=sessions)
    frame["equity_turnover"] = turnover
    frame["turnover_vs_60d_median"] = turnover / turnover.shift(1).rolling(TURNOVER_LOOKBACK, min_periods=40).median()
    frame["equal_weight_return"] = es.benchmark_returns(panel)
    rate_rows = []
    for fy, month, tbill, interbank in rates[["fiscal_year", "month", "treasury_bill_rate", "interbank_commercial_rate"]].itertuples(index=False):
        available = nepali_month_available_date(fy, month)
        if available is not None:
            rate_rows.append((available, tbill, interbank))
    rate_frame = pd.DataFrame(rate_rows, columns=["available", "tbill", "interbank"]).sort_values("available")
    positions = np.searchsorted(rate_frame["available"].to_numpy(), np.array(panel.sessions), side="right") - 1
    frame["tbill_rate_known"] = [float(rate_frame["tbill"].iloc[p]) if p >= 0 and pd.notna(rate_frame["tbill"].iloc[p]) else np.nan for p in positions]
    frame["interbank_rate_known"] = [float(rate_frame["interbank"].iloc[p]) if p >= 0 and pd.notna(rate_frame["interbank"].iloc[p]) else np.nan for p in positions]
    frame.index.name = "date"
    return frame.reset_index()


def abnormal_dispersion(panel: es.Panel, horizon: int = POWER_HORIZON) -> float:
    rng = np.random.default_rng(POWER_SAMPLE_SEED)
    universe = es.benchmark_returns(panel)
    values = []
    tries = 0
    while len(values) < POWER_SAMPLE and tries < POWER_SAMPLE * 5:
        tries += 1
        r = int(rng.integers(0, len(panel.symbols)))
        c = int(rng.integers(0, len(panel.sessions) - horizon - 1))
        if np.isnan(panel.close[r, c]):
            continue
        stock = es.stock_cumulative(panel, r, c, c + horizon)
        if stock is None:
            continue
        values.append(stock - float(np.prod(1 + universe[c + 1 : c + horizon + 1]) - 1))
    return float(np.std(values))


def minimum_detectable(sd: float, events: int, clusters: int, alpha: float, power: float = 0.8) -> float | None:
    if clusters < 2:
        return None
    z = NormalDist().inv_cdf(1 - alpha / 2) + NormalDist().inv_cdf(power)
    per_cluster = max(events / clusters, 1.0)
    return z * sd / np.sqrt(per_cluster) / np.sqrt(clusters)


def _per_year(frame: pd.DataFrame, column: str) -> dict[str, int]:
    if frame.empty:
        return {}
    return {str(k): int(v) for k, v in frame.groupby(pd.to_datetime(frame[column]).dt.year).size().items()}


def build_all(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    inputs = load_inputs()
    panel = load_panel(inputs)
    actions = inputs["actions"][inputs["actions"]["action_type"].isin(["BONUS", "DIVIDEND", "RIGHT"])]
    mergers = _merger_symbols(inputs)
    books = book_close_events(panel, actions)
    listings = new_listing_events(panel, mergers)
    since = sessions_since_listing(panel, listings)
    streaks = circuit_streak_events(panel, listings)
    ends = streak_end_events(streaks, panel)
    volume = volume_anomaly_events(panel, actions, listings)
    state = market_state(panel, inputs["index"], inputs["rates"])
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in (
        ("book_close", books), ("new_listing", listings), ("since_listing", since), ("circuit_days", streaks),
        ("circuit_streak_end", ends), ("volume_anomaly", volume), ("market_state", state),
    ):
        frame.to_parquet(output_dir / f"{name}.parquet", index=False)

    sd = abnormal_dispersion(panel)
    def power(frame: pd.DataFrame, date_column: str, alpha: float = 0.05 / 8) -> dict[str, Any]:
        n = int(len(frame))
        clusters = int(frame[date_column].nunique()) if n else 0
        mde = minimum_detectable(sd, n, clusters, alpha)
        return {"events": n, "distinct_dates": clusters, "mde_20_session_pct_at_alpha_0.00625_power_0.8": round(mde * 100, 2) if mde else None}

    up_starts = streaks[(streaks["direction"] == "up") & (streaks["streak_length"] == 1)]
    up_three = streaks[(streaks["direction"] == "up") & (streaks["streak_length"] == 3)]
    down_three = streaks[(streaks["direction"] == "down") & (streaks["streak_length"] == 3)]
    up_ends = ends[ends["direction"] == "up"]
    clean_volume = volume[volume["eligible_no_news"]]
    report = {
        "development_end": DEVELOPMENT_END.isoformat(),
        "panel_symbols": len(panel.symbols),
        "panel_sessions": len(panel.sessions),
        "survivorship_note": "corporate actions exist only for symbols active when they were scraped; 0 of 259 delisted equities have any",
        "abnormal_20_session_sd_pct_random_symbol_days": round(sd * 100, 2),
        "book_close": {
            "by_kind": books["kind"].value_counts().to_dict(),
            "by_year": _per_year(books, "ex_session"),
            "symbols": int(books["symbol"].nunique()),
            "traded_on_ex_session": int(books["traded_on_ex"].sum()),
            "power_all": power(books, "ex_session"),
            "power_bonus_any": power(books[books["bonus_pct"].notna()], "ex_session"),
            "power_cash_only": power(books[books["kind"] == "cash_only"], "ex_session"),
            "power_right": power(books[books["kind"] == "right"], "ex_session"),
        },
        "new_listing": {
            "by_year": _per_year(listings, "listing_date"),
            "count": int(len(listings)),
            "merger_symbols_excluded": int(len(mergers & set(first_price_index(panel)))),
            "power": power(listings, "listing_date"),
        },
        "circuit": {
            "upper_close_days": int((streaks["direction"] == "up").sum()),
            "lower_close_days": int((streaks["direction"] == "down").sum()),
            "upper_locked_days": int(((streaks["direction"] == "up") & streaks["locked_today"]).sum()),
            "lower_locked_days": int(((streaks["direction"] == "down") & streaks["locked_today"]).sum()),
            "upper_streak_length_distribution": streaks[streaks["direction"] == "up"].groupby("symbol")["streak_length"].max().describe().round(2).to_dict(),
            "upper_streaks_reaching_3_by_year": _per_year(up_three, "event_date"),
            "upper_streaks_reaching_3_excluding_new_listings": int((~up_three["new_listing_window"]).sum()),
            "lower_streaks_reaching_3_by_year": _per_year(down_three, "event_date"),
            "upper_streak_ends_by_length": up_ends["streak_length"].clip(upper=6).value_counts().sort_index().to_dict(),
            "power_upper_streak_start": power(up_starts, "event_date"),
            "power_upper_streak_3": power(up_three, "event_date"),
            "power_upper_streak_end_len_ge_3_seasoned": power(up_ends[(up_ends["streak_length"] >= 3) & ~up_ends["new_listing_window"]], "event_date"),
            "power_lower_streak_3": power(down_three, "event_date"),
        },
        "volume_anomaly": {
            "all_by_year": _per_year(volume, "event_date"),
            "no_news_by_year": _per_year(clean_volume, "event_date"),
            "no_news": int(len(clean_volume)),
            "excluded_action_in_past_20": int(volume["action_in_past_20"].sum()),
            "excluded_action_in_next_20": int(volume["action_in_next_20"].sum()),
            "power_no_news": power(clean_volume, "event_date"),
            "power_no_news_up_day": power(clean_volume[clean_volume["day_return"] > 0], "event_date"),
        },
        "market_state": {
            "sessions": int(len(state)),
            "trend_defined_from": str(state.loc[state["nepse_sma_200"].notna(), "date"].min()),
            "share_sessions_trend_up": round(float(state["trend_up"].dropna().astype(float).mean()), 3),
            "breadth_mean": round(float(state["breadth_above_sma50"].mean()), 3),
            "rate_known_from": str(state.loc[state["tbill_rate_known"].notna(), "date"].min()),
            "trend_regime_switches": int(state["trend_up"].dropna().astype(int).diff().abs().sum()),
            "independent_years": round(len(state) / 240, 1),
        },
    }
    COUNTS_OUTPUT.write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    argparse.ArgumentParser().parse_args()
    report = build_all()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
