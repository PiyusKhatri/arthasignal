from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

MONTH_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "annual": 12}
PERIOD = re.compile(r"based[\s-]+on[\s-]+(?:the[\s-]+)?(\w+)[\s-]+months?(?:'s|’s|’|')?[\s-]+data(?:[\s-]+of)?[\s-]+(\d{4})[./](\d{2})", re.I)
VALUE = r"(-?\d+(?:\.\d+)?)"
PATTERNS = {
    "nrb_tbill_91d_rate": (re.compile(r"91-?daytreasurybills?rate[^.]{0,80}?(?:to|at)" + VALUE + r"percent", re.I), "percent"),
    "nrb_interbank_rate_commercial": (re.compile(r"(?:weightedaverage)?interbankrate(?:among|of)commercialbanks[^.]{0,80}?(?:to|at)" + VALUE + r"percent", re.I), "percent"),
    "nrb_wavg_deposit_rate": (re.compile(r"weightedaveragedepositrateandlendingrateofcommercialbanks(?:stood|remained)at" + VALUE + r"percentand", re.I), "percent"),
    "nrb_wavg_lending_rate": (re.compile(r"weightedaveragedepositrateandlendingrateofcommercialbanks(?:stood|remained)at-?\d+(?:\.\d+)?percentand" + VALUE + r"percent", re.I), "percent"),
    "nrb_base_rate_commercial": (re.compile(r"averagebaserateofcommercialbanks(?:increased|decreased|remained|stood)(?:to|at)?" + VALUE + r"percent", re.I), "percent"),
    "nrb_margin_loan_growth": (re.compile(r"marginnatureloan(increased|decreased|grew|declined)" + VALUE + r"percent", re.I), "percent, fiscal year to date"),
    "nrb_market_cap_to_gdp": (re.compile(r"ratioofmarketcapitali[sz]ationtoGDP(?:stood|remained)at" + VALUE + r"percent", re.I), "percent"),
}


def period_end(title: str) -> date | None:
    match = PERIOD.search(title)
    if not match or match.group(1).lower() not in MONTH_WORDS:
        return None
    months = MONTH_WORDS[match.group(1).lower()]
    start = int(match.group(2))
    month = 7 + months
    year = start + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, 15)


def extract(raw: str) -> dict[str, tuple[float, str]]:
    compact = re.sub(r"\s+", "", raw)
    out = {}
    for series, (pattern, _) in PATTERNS.items():
        match = pattern.search(compact)
        if not match:
            continue
        if series == "nrb_margin_loan_growth":
            value = float(match.group(2)) * (-1 if match.group(1).lower() in ("decreased", "declined") else 1)
        else:
            value = float(match.group(1))
        out[series] = (value, compact[max(0, match.start() - 20): match.end() + 10])
    return out


def run(engine: Engine, research: Any) -> dict[str, Any]:
    import pypdf

    from src.archive.schema import apply

    apply(engine)
    with research.connect() as connection:
        docs = pd.read_sql(text("SELECT announcement_source_id, url, path, published_date FROM archive_documents WHERE source = 'nrb:macro_situation'"), connection)
    rows, failures, found = [], [], {k: 0 for k in PATTERNS}
    for doc in docs.itertuples():
        title = doc.announcement_source_id.split("|", 1)[-1]
        end = period_end(title)
        if end is None:
            failures.append({"title": title, "reason": "period not parsed"})
            continue
        try:
            raw = " ".join((page.extract_text() or "") for page in pypdf.PdfReader(doc.path).pages)
        except Exception as exc:
            failures.append({"title": title, "reason": str(exc)[:120]})
            continue
        for series, (value, evidence) in extract(raw).items():
            found[series] += 1
            rows.append({"s": series, "ps": None, "pe": end, "v": value, "u": PATTERNS[series][1], "pd": doc.published_date,
                         "b": "NRB listing upload date of the report", "url": doc.url, "e": evidence})
    added = 0
    with engine.begin() as connection:
        for row in rows:
            added += connection.execute(text(
                "INSERT INTO sentiment_observations (series, period_start, period_end, value, unit, published_date, date_basis, source_url, evidence) "
                "VALUES (:s, :ps, :pe, :v, :u, :pd, :b, :url, :e) ON CONFLICT (series, period_end, source_url) DO NOTHING"), row).rowcount
    return {"documents": len(docs), "observations": len(rows), "added": added, "found_by_series": found, "failures": failures[:20]}


if __name__ == "__main__":
    from src.database.connection import engine
    from src.database.holdout_guard import research_engine

    print(json.dumps(run(engine, research_engine()), indent=1, default=str))
