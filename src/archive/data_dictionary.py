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


FIELDS = [
    ("technical", "close, high, low (equities)", "daily_prices.close (equity symbol-days)", "daily_prices (Sharesansar session pages, NEPSE API live)",
     "known at the session close (15:00); used from the next session", "2014-2016 lows/highs before 2018-02-18 are stored values of unknown quality; 18 sector-less equities fixed to 9",
     "tests/test_holdout_guard.py, tests/test_backtest_leakage_guard.py"),
    ("technical", "real open (equities)", "daily_prices.open real (equity symbol-days with open <> previous close)", "daily_prices",
     "session close", "before 2018-02-18 the stored open equals the previous close; protocol uses floorsheet-derived opens instead", "tests/test_floorsheet_ohlc.py"),
    ("technical", "floorsheet-derived open/high/low", "floorsheet-derived OHLC bars (symbol-days)", "Merolagani floorsheet Parquet (board lots, quantity >= 10)",
     "session close", "1-2% missing pages in 2015-2018; files end 2025-01-19; source terms flagged", "tests/test_floorsheet_ohlc.py"),
    ("technical", "indicators (RSI, SMA, Bollinger, ...)", "technical_signals (symbol-days)", "computed from daily_prices", "session close",
     "recomputed on full history 2026-09; weekly/monthly rows stale", "tests/test_indicators_golden.py and other golden tests"),
    ("volume", "volume, turnover (equities)", "daily_prices.turnover (equity symbol-days)", "daily_prices", "session close", "none known on traded days",
     "tests/test_holdout_guard.py"),
    ("floorsheet_broker", "broker-flow H1-H5", "broker-flow features H1-H5 (eligible symbol-days)", "floorsheet Parquet via src/backtest/broker_flow_features.py",
     "after the session close", "files end 2025-01-19 (VM backfill not merged); Merolagani terms flagged", "tests/test_broker_flow_leakage.py"),
    ("floorsheet_broker", "floorsheet files", "floorsheet parquet (session files)", "Merolagani floorsheet", "after the session close",
     "2025 has 13 files; 2025-01-20 to 2026-08-30 hole", "tests/test_holdout_guard.py (file listing refuses holdout dates)"),
    ("fundamentals", "quarterly report publication + net profit headline", "quarterly_report_announcements net_profit headline",
     "Sharesansar announcements (Merolagani index for dates)", "earliest item-verified source date, next session (docs/POINT_IN_TIME.md)",
     "headline rounded to 2-3 significant digits; group vs standalone basis varies", "tests/test_knowledge_time.py, src/ranker/leak_audit.py"),
    ("fundamentals", "report images for OCR", "archive_documents report images", "Sharesansar announcement attachments",
     "announcement date (image upload timestamp kept)", "collection running; images only, no text PDFs", "tests/test_archive_holdout.py"),
    ("fundamentals", "EPS, net worth, reserves, NPL, CAR from reports", "report_field_values accepted", "OCR of report images",
     "announcement date of the report", "not extracted yet; engine accuracy too low (docs/OCR_COMPARISON.md)", "tests/test_archive_holdout.py"),
    ("corporate_events", "dividend declarations (Sharesansar table)", "dividend_declarations (announcement-dated)", "Sharesansar dividend table",
     "announcement_date; 8.3% are after their own book close (late, not early)", "starts 2018", "tests/test_holdout_guard.py"),
    ("corporate_events", "dividend proposals from AGM records", "company_event_records agm (meeting-dated)", "Sharesansar AGM table + AGM announcements",
     "earlier of AGM announcement and book close (dividend_proposals_pit)", "collection running", "tests/test_archive_holdout.py"),
    ("corporate_events", "AGM announcements", "announcement_events agm", "Sharesansar company announcements", "announcement date",
     "collection running", "tests/test_archive_holdout.py"),
    ("corporate_events", "right share announcements", "announcement_events right_share_issue", "Sharesansar company announcements", "announcement date",
     "ratio parsed only when in the title", "tests/test_archive_holdout.py"),
    ("corporate_events", "book close / ex dates", "corporate_actions (ex/book-close dated)", "corporate_actions", "action date is an event date, not a knowledge date",
     "announcement timing comes from announcements", "tests/test_holdout_guard.py"),
    ("news", "Sharesansar news archive", "news_articles sharesansar", "Sharesansar news (category latest)", "published minute (Nepal time), next session",
     "collection running backward from 2025-09-30", "tests/test_archive_holdout.py"),
    ("news", "symbol mentions", "news_articles with a symbol mention", "company names and tickers", "article publication time",
     "name aliases only for distinctive names", "tests/test_archive_parsers.py"),
    ("news", "live text capture", "text_items (live news capture)", "live collectors since 2026", "first_seen_at", "all rows are holdout-era", "tests/test_holdout_guard.py"),
    ("market_state", "NEPSE index", "market_index NEPSE (sessions)", "Sharesansar/Merolagani index history", "session close", "none known", "tests/test_index_session_dates.py"),
    ("market_state", "turnover, breadth series", "sentiment market_breadth (sessions)", "daily_prices", "session close", "none known", "tests/test_archive_holdout.py"),
    ("market_state", "NRB policy rates, CRR, SLR, CD/CCD, margin rules", "policy_events NRB", "NRB monetary policy documents",
     "announcement date (pre-2020 dates from secondary sources, unconfirmed)", "2017/18 and 2019/20 announcement dates missing", "tests/test_archive_holdout.py"),
    ("historical_sentiment", "NRB macro (T-bill, rates, margin loan growth)", "sentiment NRB macro (observations)", "NRB Current Macroeconomic and Financial Situation",
     "NRB listing upload date", "no demat-account counts in these reports", "tests/test_archive_parsers.py"),
    ("historical_sentiment", "IPO/right oversubscription", "sentiment issue_oversubscription (observations)", "Sharesansar news headlines",
     "headline publication time", "grows with the news archive", "tests/test_archive_parsers.py"),
]


def render(coverage: dict[str, Any]) -> str:
    merged = {**coverage["database"], **coverage["files"]}
    pillars: dict[str, tuple[list[str], list[str]]] = {}
    for pillar, field, key, source, rule, gaps, test in FIELDS:
        data = merged.get(key, {})
        counts = data.get("by_year", {})
        first = data.get("first_year_with_rows")
        fields, years = pillars.setdefault(pillar, ([], []))
        fields.append(f"| {field} | {source} | {first if first else 'none yet'} | {rule} | {gaps} | {test} |")
        years.append(f"| {field} | " + " | ".join(f"{counts.get(str(y), 0):,}" for y in YEARS) + " |")
    parts = []
    for pillar, (fields, years) in pillars.items():
        parts.append(
            f"### {pillar}\n\n| Field | Source | First year with rows | Point-in-time rule | Known gaps | Leak test |\n|---|---|---|---|---|---|\n"
            + "\n".join(fields)
            + "\n\nRows per year (research role, before 2025-09-30):\n\n| Field | " + " | ".join(str(y) for y in YEARS) + " |\n|---|" + "---:|" * len(YEARS) + "\n"
            + "\n".join(years))
    return "\n\n".join(parts)
