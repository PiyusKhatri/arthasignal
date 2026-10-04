from __future__ import annotations

import argparse
import json
import re
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.archive import schema

MARKET_SOURCE = "daily_prices (equities, research role)"
OVERSUBSCRIBED = re.compile(r"oversubscribed\s+(?:by\s+)?([\d.,]+)\s*times", re.I)
ISSUE_TYPE = re.compile(r"\b(IPO|FPO|right share|rights|debenture)\b", re.I)
ISSUER = re.compile(r"(?:(?:IPO|FPO|right shares?|debenture)\s+issue\s+of\s+(.*?)\s+(?:closing|closes|opens|oversubscribed|is|has|;))|^(.*?)(?:'s|’s)?\s+(?:IPO|FPO|right)", re.I)


def market_series(research: Any, end: date) -> pd.DataFrame:
    with research.connect() as connection:
        prices = pd.read_sql(text(
            "SELECT p.date, p.symbol, p.close::float AS close, p.turnover::float AS turnover FROM daily_prices p "
            "JOIN companies c ON c.symbol = p.symbol WHERE c.instrument_type = 'Equity' AND p.date <= :e AND p.close > 0 ORDER BY p.symbol, p.date"),
            connection, params={"e": end})
    prices["prev"] = prices.groupby("symbol")["close"].shift(1)
    prices["move"] = (prices["close"] - prices["prev"]).where(prices["prev"].notna())
    daily = prices.groupby("date").agg(
        turnover=("turnover", "sum"),
        traded=("symbol", "count"),
        advances=("move", lambda m: int((m > 0).sum())),
        declines=("move", lambda m: int((m < 0).sum())),
        unchanged=("move", lambda m: int((m == 0).sum())),
    ).reset_index()
    total = daily["advances"] + daily["declines"]
    daily["breadth"] = ((daily["advances"] - daily["declines"]) / total).where(total > 0)
    return daily


def store_market(engine: Engine, daily: pd.DataFrame) -> int:
    units = {"turnover": "NPR", "traded": "symbols", "advances": "symbols", "declines": "symbols", "unchanged": "symbols", "breadth": "ratio"}
    rows = []
    for record in daily.to_dict("records"):
        for series, unit in units.items():
            value = record[series]
            if value is None or pd.isna(value):
                continue
            rows.append({"s": f"market_{series}", "ps": record["date"], "pe": record["date"], "v": float(value), "u": unit, "pd": record["date"],
                         "b": "session close", "url": MARKET_SOURCE, "e": ""})
    return _insert(engine, rows)


def _insert(engine: Engine, rows: list[dict[str, Any]]) -> int:
    added = 0
    with engine.begin() as connection:
        for chunk in range(0, len(rows), 5000):
            for row in rows[chunk: chunk + 5000]:
                added += connection.execute(text(
                    "INSERT INTO sentiment_observations (series, period_start, period_end, value, unit, published_date, date_basis, source_url, evidence) "
                    "VALUES (:s, :ps, :pe, :v, :u, :pd, :b, :url, :e) ON CONFLICT (series, period_end, source_url) DO NOTHING"), row).rowcount
    return added


def oversubscription(research: Any) -> pd.DataFrame:
    with research.connect() as connection:
        news = pd.read_sql(text("SELECT url, title, published_at, published_precision FROM news_articles WHERE title ILIKE '%%oversubscribed%%'"), connection)
    out = []
    for row in news.itertuples():
        match = OVERSUBSCRIBED.search(row.title)
        kind = ISSUE_TYPE.search(row.title)
        if not match or not kind:
            continue
        issuer = ISSUER.search(row.title)
        name = (issuer.group(1) or issuer.group(2)).strip() if issuer else None
        out.append({"issue_type": "RIGHT" if kind.group(1).lower().startswith("right") else kind.group(1).upper(),
                    "times": float(match.group(1).replace(",", "").rstrip(".")), "issuer": name,
                    "title": row.title, "url": row.url, "published_at": row.published_at, "final": bool(re.search(r"\bfinal|closed|closing|last day", row.title, re.I))})
    return pd.DataFrame(out)


def store_oversubscription(engine: Engine, frame: pd.DataFrame) -> int:
    rows = []
    for record in frame.to_dict("records"):
        day = pd.Timestamp(record["published_at"]).tz_convert("Asia/Kathmandu").date()
        rows.append({"s": f"issue_oversubscription_{record['issue_type'].lower().replace(' ', '_')}", "ps": None, "pe": day, "v": record["times"], "u": "times",
                     "pd": day, "b": "news publication time", "url": record["url"], "e": record["title"]})
    return _insert(engine, rows)


def main() -> None:
    from src.database.connection import engine
    from src.database.holdout_guard import LAST_DEVELOPMENT_DAY, research_engine

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["market", "oversubscription"])
    args = parser.parse_args()
    schema.apply(engine)
    research = research_engine()
    if args.command == "market":
        daily = market_series(research, LAST_DEVELOPMENT_DAY)
        added = store_market(engine, daily)
        print(json.dumps({"sessions": len(daily), "first": str(daily["date"].min()), "last": str(daily["date"].max()), "rows_added": added}, indent=1))
    else:
        frame = oversubscription(research)
        added = store_oversubscription(engine, frame)
        print(json.dumps({"headlines": len(frame), "rows_added": added}, indent=1))


if __name__ == "__main__":
    main()
