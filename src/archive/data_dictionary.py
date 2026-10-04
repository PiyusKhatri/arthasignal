from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs" / "data_dictionary_coverage.json"
YEARS = list(range(2014, 2026))
FLOORSHEET_ROOT = Path("~/Desktop/arthasignal-ai/raw/floorsheet").expanduser()
DERIVED_BARS = Path("~/Desktop/arthasignal-ai/derived/floorsheet_ohlc/bars.parquet").expanduser()
BROKER_FEATURES = Path("~/Desktop/arthasignal-ai/derived/broker_flow/features.parquet").expanduser()

QUERIES = {
    "daily_prices.close (equity symbol-days)": "SELECT extract(year FROM p.date)::int y, count(*) n FROM daily_prices p JOIN companies c ON c.symbol = p.symbol WHERE c.instrument_type = 'Equity' GROUP BY 1",
    "daily_prices.open real (equity symbol-days with open <> previous close)": "SELECT extract(year FROM date)::int y, count(*) n FROM (SELECT p.date, p.open, lag(p.close) OVER (PARTITION BY p.symbol ORDER BY p.date) prev FROM daily_prices p JOIN companies c ON c.symbol = p.symbol WHERE c.instrument_type = 'Equity') x WHERE open <> prev GROUP BY 1",
    "daily_prices.turnover (equity symbol-days)": "SELECT extract(year FROM p.date)::int y, count(*) n FROM daily_prices p JOIN companies c ON c.symbol = p.symbol WHERE c.instrument_type = 'Equity' AND p.turnover > 0 GROUP BY 1",
    "technical_signals (symbol-days)": "SELECT extract(year FROM date)::int y, count(*) n FROM technical_signals GROUP BY 1",
    "market_index NEPSE (sessions)": "SELECT extract(year FROM date)::int y, count(*) n FROM market_index WHERE index_name = 'NEPSE Index' GROUP BY 1",
    "trading_calendar (sessions)": "SELECT extract(year FROM date)::int y, count(*) n FROM trading_calendar GROUP BY 1",
    "quarterly_report_announcements sharesansar (reports)": "SELECT extract(year FROM published_date)::int y, count(*) n FROM quarterly_report_announcements WHERE source = 'sharesansar' GROUP BY 1",
    "quarterly_report_announcements net_profit headline": "SELECT extract(year FROM published_date)::int y, count(*) n FROM quarterly_report_announcements WHERE source = 'sharesansar' AND net_profit IS NOT NULL GROUP BY 1",
    "quarterly_report_announcements merolagani (reports)": "SELECT extract(year FROM published_date)::int y, count(*) n FROM quarterly_report_announcements WHERE source = 'merolagani' GROUP BY 1",
    "archive_documents report images": "SELECT extract(year FROM published_date)::int y, count(*) n FROM archive_documents WHERE source = 'sharesansar' AND (url LIKE '%/announcement/%' OR url LIKE '%/wp-content/%') GROUP BY 1",
    "report_field_values accepted": "SELECT extract(year FROM published_date)::int y, count(*) n FROM report_field_values WHERE accepted GROUP BY 1",
    "dividend_declarations (announcement-dated)": "SELECT extract(year FROM announcement_date)::int y, count(*) n FROM dividend_declarations GROUP BY 1",
    "corporate_actions (ex/book-close dated)": "SELECT extract(year FROM action_date)::int y, count(*) n FROM corporate_actions GROUP BY 1",
    "corporate_announcements (all types)": "SELECT extract(year FROM published_date)::int y, count(*) n FROM corporate_announcements GROUP BY 1",
    "announcement_events dividend_proposal": "SELECT extract(year FROM published_date)::int y, count(*) n FROM announcement_events WHERE event_type = 'dividend_proposal' GROUP BY 1",
    "announcement_events agm": "SELECT extract(year FROM published_date)::int y, count(*) n FROM announcement_events WHERE event_type = 'agm' GROUP BY 1",
    "announcement_events right_share_issue": "SELECT extract(year FROM published_date)::int y, count(*) n FROM announcement_events WHERE event_type = 'right_share_issue' GROUP BY 1",
    "announcement_events book_close": "SELECT extract(year FROM published_date)::int y, count(*) n FROM announcement_events WHERE event_type = 'book_close' GROUP BY 1",
    "company_event_records agm (meeting-dated)": "SELECT extract(year FROM reference_date)::int y, count(*) n FROM company_event_records WHERE record_type = 'agm' GROUP BY 1",
    "company_event_records dividend": "SELECT extract(year FROM reference_date)::int y, count(*) n FROM company_event_records WHERE record_type = 'dividend' GROUP BY 1",
    "news_articles sharesansar": "SELECT extract(year FROM published_at AT TIME ZONE 'Asia/Kathmandu')::int y, count(*) n FROM news_articles GROUP BY 1",
    "news_articles with a symbol mention": "SELECT extract(year FROM published_at AT TIME ZONE 'Asia/Kathmandu')::int y, count(DISTINCT article_id) n FROM news_symbol_mentions GROUP BY 1",
    "text_items (live news capture)": "SELECT extract(year FROM first_seen_at)::int y, count(*) n FROM text_items GROUP BY 1",
    "policy_events NRB": "SELECT extract(year FROM announced_date)::int y, count(*) n FROM policy_events GROUP BY 1",
    "sentiment market_turnover (sessions)": "SELECT extract(year FROM period_end)::int y, count(*) n FROM sentiment_observations WHERE series = 'market_turnover' GROUP BY 1",
    "sentiment market_breadth (sessions)": "SELECT extract(year FROM period_end)::int y, count(*) n FROM sentiment_observations WHERE series = 'market_breadth' GROUP BY 1",
    "sentiment issue_oversubscription (observations)": "SELECT extract(year FROM period_end)::int y, count(*) n FROM sentiment_observations WHERE series LIKE 'issue_oversubscription%%' GROUP BY 1",
    "sentiment NRB macro (observations)": "SELECT extract(year FROM published_date)::int y, count(*) n FROM sentiment_observations WHERE series LIKE 'nrb_%%' GROUP BY 1",
    "archive_documents NRB macro reports": "SELECT extract(year FROM published_date)::int y, count(*) n FROM archive_documents WHERE source = 'nrb:macro_situation' GROUP BY 1",
}


def database_coverage(research: Any) -> dict[str, dict[str, Any]]:
    out = {}
    for name, sql in QUERIES.items():
        try:
            with research.connect() as connection:
                frame = pd.read_sql(text(sql), connection)
            counts = {int(y): int(n) for y, n in zip(frame["y"], frame["n"]) if pd.notna(y)}
            out[name] = {"by_year": {str(y): counts.get(y, 0) for y in YEARS}, "first_year_with_rows": min(counts) if counts else None}
        except Exception as exc:
            out[name] = {"error": str(exc).splitlines()[0]}
    return out


def file_coverage() -> dict[str, dict[str, Any]]:
    out = {}
    files = list(FLOORSHEET_ROOT.glob("year=*/month=*/day=*.parquet"))
    counts: dict[int, int] = {}
    for path in files:
        year = int(path.parts[-3].split("=")[1])
        counts[year] = counts.get(year, 0) + 1
    out["floorsheet parquet (session files)"] = {"by_year": {str(y): counts.get(y, 0) for y in YEARS}, "first_year_with_rows": min(counts) if counts else None}
    for name, path in (("floorsheet-derived OHLC bars (symbol-days)", DERIVED_BARS), ("broker-flow features H1-H5 (eligible symbol-days)", BROKER_FEATURES)):
        if not path.exists():
            out[name] = {"error": "file missing"}
            continue
        column = "eligible" if "broker" in name else None
        where = f"WHERE {column}" if column else ""
        try:
            frame = duckdb.sql(f"SELECT year(CAST(date AS DATE)) y, count(*) n FROM read_parquet('{path.as_posix()}') {where} GROUP BY 1").df()
        except duckdb.Error:
            frame = duckdb.sql(f"SELECT year(CAST(date AS DATE)) y, count(*) n FROM read_parquet('{path.as_posix()}') GROUP BY 1").df()
        counts = {int(y): int(n) for y, n in zip(frame["y"], frame["n"])}
        out[name] = {"by_year": {str(y): counts.get(y, 0) for y in YEARS}, "first_year_with_rows": min(counts) if counts else None}
    return out


def main() -> None:
    from src.database.holdout_guard import research_engine

    report = {"as_of": date.today().isoformat(), "role": "arthasignal_research", "note": "rows visible to the research role (dates before 2025-09-30)",
              "database": database_coverage(research_engine()), "files": file_coverage()}
    OUTPUT.write_text(json.dumps(report, indent=1) + "\n")
    for name, value in {**report["database"], **report["files"]}.items():
        print(name, value.get("by_year") or value)


if __name__ == "__main__":
    main()
