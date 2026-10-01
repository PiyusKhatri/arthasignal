from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.backtest import event_study as es

logger = logging.getLogger(__name__)

DEVELOPMENT_START = date(2014, 6, 1)
DEVELOPMENT_END = date(2025, 1, 19)


def load_inputs(end: date = DEVELOPMENT_END) -> dict[str, pd.DataFrame]:
    from src.database.connection import engine

    with engine.connect() as connection:
        prices = pd.read_sql(
            text(
                "SELECT p.symbol, p.date, p.open, p.high, p.low, p.close, p.volume, p.turnover "
                "FROM daily_prices p JOIN companies c ON c.symbol = p.symbol AND c.instrument_type = 'Equity' "
                "WHERE p.date >= :s AND p.date <= :e"
            ),
            connection,
            params={"s": DEVELOPMENT_START, "e": end},
        )
        sessions = pd.read_sql(
            text("SELECT DISTINCT date FROM daily_prices WHERE date >= :s AND date <= :e ORDER BY date"),
            connection,
            params={"s": DEVELOPMENT_START, "e": end},
        )
        actions = pd.read_sql(
            text(
                "SELECT symbol, action_date, action_type::text AS action_type, ratio_or_amount::float AS ratio_or_amount "
                "FROM corporate_actions WHERE action_date <= :e"
            ),
            connection,
            params={"e": end},
        )
        companies = pd.read_sql(
            text("SELECT symbol, sector, status, instrument_type FROM companies"), connection
        )
        index = pd.read_sql(
            text(
                "SELECT date, open, high, low, close FROM market_index "
                "WHERE index_name = 'NEPSE Index' AND date >= :s AND date <= :e ORDER BY date"
            ),
            connection,
            params={"s": DEVELOPMENT_START, "e": end},
        )
        rates = pd.read_sql(text("SELECT * FROM short_term_interest_rates"), connection)
        ipos = pd.read_sql(text("SELECT * FROM ipo_calendar"), connection)
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        prices[column] = prices[column].astype(float)
    for column in ("open", "high", "low", "close"):
        index[column] = index[column].astype(float)
    return {
        "prices": prices,
        "sessions": sessions,
        "actions": actions,
        "companies": companies,
        "index": index,
        "rates": rates,
        "ipos": ipos,
    }


def load_panel(inputs: dict[str, pd.DataFrame]) -> es.Panel:
    sectors = dict(zip(inputs["companies"]["symbol"], inputs["companies"]["sector"]))
    sessions = list(inputs["sessions"]["date"])
    return es.build_panel(inputs["prices"], inputs["actions"], sectors, sessions=sessions)


def panel_diagnostics(panel: es.Panel, actions: pd.DataFrame) -> dict[str, Any]:
    traded = ~np.isnan(panel.close)
    locked_up = 0
    locked_down = 0
    rows, cols = np.nonzero(traded)
    for r, c in zip(rows, cols):
        if c == 0:
            continue
        if es._locked_up(panel, r, c):
            locked_up += 1
        elif es._locked_down(panel, r, c):
            locked_down += 1
    raw = np.full(panel.close.shape, np.nan)
    for r in range(len(panel.symbols)):
        idx = np.flatnonzero(traded[r])
        raw[r, idx[1:]] = panel.close[r, idx[1:]] / panel.close[r, idx[:-1]] - 1
    checks = {}
    for kind in ("BONUS", "RIGHT", "DIVIDEND"):
        subset = actions[(actions["action_type"] == kind) & (actions["ratio_or_amount"] >= (10 if kind != "DIVIDEND" else 0))]
        adjusted_small = raw_small = n = flagged = 0
        for symbol, day in zip(subset["symbol"], subset["action_date"]):
            r = panel.row.get(symbol)
            c = panel.first_session_on_or_after(day)
            if r is None or c is None or np.isnan(panel.close[r, c]):
                continue
            n += 1
            if panel.corrupt[r, c]:
                flagged += 1
            if not np.isnan(panel.total_return[r, c]) and abs(panel.total_return[r, c]) < 0.06:
                adjusted_small += 1
            if not np.isnan(raw[r, c]) and abs(raw[r, c]) < 0.06:
                raw_small += 1
        checks[kind] = {
            "ex_sessions_with_trade": n,
            "abs_return_below_6pct_raw": raw_small,
            "abs_return_below_6pct_adjusted": adjusted_small,
            "flagged_corrupt": flagged,
        }
    corrupt_rows = np.nonzero(panel.corrupt)
    return {
        "symbols": len(panel.symbols),
        "sessions": len(panel.sessions),
        "first_session": panel.sessions[0].isoformat(),
        "last_session": panel.sessions[-1].isoformat(),
        "traded_symbol_days": int(traded.sum()),
        "returns_valid": int((~np.isnan(panel.total_return)).sum()),
        "returns_flagged_corrupt": int(panel.corrupt.sum()),
        "symbols_with_corrupt_flag": int(len(set(corrupt_rows[0].tolist()))),
        "locked_upper_circuit_days": locked_up,
        "locked_lower_circuit_days": locked_down,
        "action_ex_session_checks": checks,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args()
    inputs = load_inputs()
    panel = load_panel(inputs)
    if args.diagnostics:
        print(json.dumps(panel_diagnostics(panel, inputs["actions"]), indent=2, default=str))


if __name__ == "__main__":
    main()
