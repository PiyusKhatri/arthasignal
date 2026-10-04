from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.scrapers.quarterly_reports_collector import (
    COMPANY_ID_PATTERN,
    COMPANY_URL,
    DEFAULT_DELAY,
    QUARTERLY_URL,
    SECTOR_PATTERN,
    TOKEN_PATTERN,
    Collector,
    parse_quarterly_tab,
)

logger = logging.getLogger(__name__)

DEFAULT_LOG = Path("logs/quarterly_capture.log")

DDL = (
    """
    CREATE TABLE IF NOT EXISTS quarterly_figure_captures (
        id BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        fiscal_year VARCHAR(20) NOT NULL,
        quarter SMALLINT NOT NULL CHECK (quarter BETWEEN 1 AND 4),
        column_label TEXT NOT NULL,
        eps NUMERIC(14,4),
        net_worth_per_share NUMERIC(14,4),
        profit_for_period_thousands NUMERIC(22,2),
        reserves_thousands NUMERIC(22,2),
        retained_earnings_thousands NUMERIC(22,2),
        total_equity_thousands NUMERIC(22,2),
        share_capital_thousands NUMERIC(22,2),
        statement JSONB NOT NULL,
        statement_sha256 CHAR(64) NOT NULL,
        captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (source, symbol, statement_sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quarterly_capture_runs (
        id BIGSERIAL PRIMARY KEY,
        started_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ NOT NULL,
        symbols INTEGER NOT NULL,
        captured INTEGER NOT NULL,
        unchanged INTEGER NOT NULL,
        no_data INTEGER NOT NULL,
        errors INTEGER NOT NULL,
        detail JSONB NOT NULL
    )
    """,
    """
    CREATE OR REPLACE FUNCTION quarterly_capture_reject_change() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'quarterly captures are append-only: % on % rejected', TG_OP, TG_TABLE_NAME;
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS quarterly_figure_captures_immutable ON quarterly_figure_captures",
    "CREATE TRIGGER quarterly_figure_captures_immutable BEFORE UPDATE OR DELETE ON quarterly_figure_captures "
    "FOR EACH ROW EXECUTE FUNCTION quarterly_capture_reject_change()",
)


def statement_hash(figures: dict[str, Any]) -> str:
    payload = {"label": figures["column_label"], "statement": figures["statement"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def apply_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        for statement in DDL:
            connection.execute(text(statement))


def capture_symbol(collector: Collector, engine: Engine, symbol: str) -> str:
    page = collector.get(COMPANY_URL.format(slug=symbol.lower()))
    token = TOKEN_PATTERN.search(page.text)
    company = COMPANY_ID_PATTERN.search(page.text)
    if not token or not company:
        return "no_page"
    sector = SECTOR_PATTERN.search(page.text)
    headers = {"X-CSRF-Token": token.group(1), "X-Requested-With": "XMLHttpRequest", "Referer": COMPANY_URL.format(slug=symbol.lower())}
    tab = collector.post(QUARTERLY_URL, {"company": company.group(1), "symbol": symbol, "sector": sector.group(1) if sector else ""}, headers)
    figures = parse_quarterly_tab(tab.text)
    if not figures or not figures["fiscal_year"] or not figures["quarter"]:
        return "no_data"
    with engine.begin() as connection:
        inserted = connection.execute(
            text(
                "INSERT INTO quarterly_figure_captures (source, symbol, fiscal_year, quarter, column_label, eps, net_worth_per_share, "
                "profit_for_period_thousands, reserves_thousands, retained_earnings_thousands, total_equity_thousands, share_capital_thousands, "
                "statement, statement_sha256) VALUES ('sharesansar', :sym, :fy, :q, :label, :eps, :nw, :pp, :res, :ret, :eq, :cap, "
                "CAST(:st AS jsonb), :h) ON CONFLICT (source, symbol, statement_sha256) DO NOTHING RETURNING id"
            ),
            {"sym": symbol, "fy": figures["fiscal_year"], "q": figures["quarter"], "label": figures["column_label"], "eps": figures["eps"],
             "nw": figures["net_worth_per_share"], "pp": figures["profit_for_period_thousands"], "res": figures["reserves_thousands"],
             "ret": figures["retained_earnings_thousands"], "eq": figures["total_equity_thousands"], "cap": figures["share_capital_thousands"],
             "st": json.dumps(figures["statement"]), "h": statement_hash(figures)},
        ).first()
    return "captured" if inserted is not None else "unchanged"


def active_symbols(engine: Engine) -> list[str]:
    from src.scorecard.spec import valid_symbol

    with engine.connect() as connection:
        rows = connection.execute(text("SELECT symbol FROM companies WHERE instrument_type = 'Equity' AND status = 'A' ORDER BY symbol")).all()
    return [r[0] for r in rows if valid_symbol(r[0])]


def run(engine: Engine, symbols: list[str], delay: float) -> dict[str, Any]:
    apply_schema(engine)
    collector = Collector(engine, delay)
    started = datetime.now().astimezone()
    outcome: dict[str, list[str]] = {"captured": [], "unchanged": [], "no_data": [], "no_page": [], "error": []}
    for k, symbol in enumerate(symbols, 1):
        try:
            result = capture_symbol(collector, engine, symbol)
        except Exception as error:
            logger.warning("%s: %s", symbol, error)
            result = "error"
        outcome[result].append(symbol)
        logger.info("[%d/%d] %s %s", k, len(symbols), symbol, result)
    finished = datetime.now().astimezone()
    summary = {k: len(v) for k, v in outcome.items()}
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO quarterly_capture_runs (started_at, finished_at, symbols, captured, unchanged, no_data, errors, detail) "
                "VALUES (:s, :f, :n, :c, :u, :nd, :e, CAST(:d AS jsonb))"
            ),
            {"s": started, "f": finished, "n": len(symbols), "c": summary["captured"], "u": summary["unchanged"],
             "nd": summary["no_data"] + summary["no_page"], "e": summary["error"],
             "d": json.dumps({"no_page": outcome["no_page"], "error": outcome["error"], "no_data": outcome["no_data"]})},
        )
    return {"started": started.isoformat(), "finished": finished.isoformat(), "symbols": len(symbols), **summary}


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture the latest-quarter statement of every active symbol with a timestamp")
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args()
    if args.delay < 2.0:
        raise SystemExit("--delay below 2 seconds is not allowed")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(args.log), logging.StreamHandler()])
    from src.database.connection import engine

    print(json.dumps(run(engine, args.symbols or active_symbols(engine), args.delay), indent=2))


if __name__ == "__main__":
    main()
