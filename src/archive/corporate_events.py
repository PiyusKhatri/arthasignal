from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

VERSION = "events_v2"
QUARTER = re.compile(r"\b(1st|2nd|3rd|4th|first|second|third|fourth|final)\s+quarter", re.I)
RULES = (
    ("quarterly_report", re.compile(r"quarter.{0,40}(company analysis|financial statement|report|unaudited)|net (profit|loss).{0,80}quarter", re.I)),
    ("right_share_issue", re.compile(r"\bright(s)?\s+share", re.I)),
    ("dividend_proposal", re.compile(r"(propos|recommend|endors|declar)\w*.{0,90}(dividend|bonus)|(dividend|bonus).{0,60}(propos|recommend|endors)", re.I)),
    ("dividend_approval", re.compile(r"(approv|ratif|passed)\w*.{0,90}(dividend|bonus)", re.I)),
    ("dividend_distribution", re.compile(r"(distribut|deposit|credit|warrant|submit\w* demat|dmat)\w*.{0,90}(dividend|bonus)|(dividend|bonus).{0,60}(distribut|deposit|credit|warrant)", re.I)),
    ("book_close", re.compile(r"book\s*clos", re.I)),
    ("agm", re.compile(r"\b(annual general meeting|AGM)\b", re.I)),
    ("sgm", re.compile(r"\b(special general meeting|SGM|extra.?ordinary general meeting|EGM)\b", re.I)),
    ("ipo_fpo", re.compile(r"\b(IPO|FPO|initial public|further public)\b", re.I)),
    ("auction", re.compile(r"auction", re.I)),
    ("merger", re.compile(r"\bmerg|acqui", re.I)),
    ("promoter_sale", re.compile(r"promoter", re.I)),
    ("debenture", re.compile(r"debenture|\bbond\b", re.I)),
    ("interest_rate_notice", re.compile(r"interest rate", re.I)),
    ("annual_report", re.compile(r"annual report|annual financial|audited|financial highlights", re.I)),
    ("registrar_change", re.compile(r"registrar", re.I)),
)
CASH = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s*(?:cash)", re.I)
CASH_AFTER = re.compile(r"cash\s+dividend\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*(?:%|percent)", re.I)
BONUS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s*(?:bonus)", re.I)
BONUS_AFTER = re.compile(r"bonus\s+shares?\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*(?:%|percent)", re.I)
RATIO = re.compile(r"\b(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\b")
FISCAL = re.compile(r"\b(20\d\d)\s*[/-]\s*(\d{2,4})\b")


def classify(title: str) -> str:
    if QUARTER.search(title) and re.search(r"net (profit|loss)|company analysis|financial statement|unaudited|quarterly report", title, re.I):
        return "quarterly_report"
    for name, pattern in RULES[1:]:
        if pattern.search(title):
            return name
    return "other"


def numbers(title: str) -> dict[str, Any]:
    cash = CASH.search(title) or CASH_AFTER.search(title)
    bonus = BONUS.search(title) or BONUS_AFTER.search(title)
    ratio = RATIO.search(title)
    fiscal = FISCAL.search(title)
    return {"cash_pct": float(cash.group(1)) if cash else None, "bonus_pct": float(bonus.group(1)) if bonus else None,
            "right_ratio": f"{ratio.group(1)}:{ratio.group(2)}" if ratio else None,
            "fiscal_year": f"{fiscal.group(1)}/{fiscal.group(2)[-2:]}" if fiscal else None}


def rebuild(engine: Engine) -> dict[str, Any]:
    from src.archive import schema

    schema.apply(engine)
    with engine.connect() as connection:
        rows = pd.read_sql(text("SELECT a.id, a.symbol, a.title, a.published_date FROM corporate_announcements a "
                                "WHERE NOT EXISTS (SELECT 1 FROM announcement_events e WHERE e.announcement_id = a.id AND e.version = :v)"),
                           connection, params={"v": VERSION})
    payload = []
    for row in rows.itertuples():
        kind = classify(row.title)
        parsed = numbers(row.title)
        if kind not in ("dividend_proposal", "dividend_approval", "dividend_distribution", "right_share_issue"):
            parsed = {**parsed, "cash_pct": None, "bonus_pct": None, "right_ratio": None if kind != "right_share_issue" else parsed["right_ratio"]}
        payload.append({"a": int(row.id), "v": VERSION, "s": row.symbol, "t": kind, "pd": row.published_date, **parsed})
    with engine.begin() as connection:
        for item in payload:
            connection.execute(text(
                "INSERT INTO announcement_events (announcement_id, version, symbol, event_type, fiscal_year, cash_pct, bonus_pct, right_ratio, published_date) "
                "VALUES (:a, :v, :s, :t, :fiscal_year, :cash_pct, :bonus_pct, :right_ratio, :pd) ON CONFLICT DO NOTHING"), item)
    return {"classified": len(payload), "version": VERSION}


def coverage(research: Any) -> dict[str, Any]:
    with research.connect() as connection:
        frame = pd.read_sql(text("SELECT event_type, extract(year FROM published_date)::int AS year, count(*) AS n, "
                                 "count(cash_pct) AS with_cash, count(bonus_pct) AS with_bonus FROM announcement_events WHERE version = :v "
                                 "GROUP BY 1, 2 ORDER BY 1, 2"), connection, params={"v": VERSION})
    table = frame.pivot(index="event_type", columns="year", values="n").fillna(0).astype(int)
    return {"by_type_year": {k: {str(y): int(n) for y, n in v.items()} for k, v in table.to_dict("index").items()},
            "dividend_rows_with_percent": int(frame[frame.event_type.str.startswith("dividend")][["with_cash", "with_bonus"]].max(axis=1).sum())}


if __name__ == "__main__":
    from src.database.connection import engine
    from src.database.holdout_guard import research_engine

    print(json.dumps(rebuild(engine), indent=1))
    print(json.dumps(coverage(research_engine()), indent=1))
