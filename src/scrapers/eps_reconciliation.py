from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

DEFAULT_OUTPUT = Path("docs/eps_reconciliation.json")
EXACT = 0.01
REL = 0.005

MATCH = "match"
BONUS_RESCALED = "merolagani_rescaled_for_bonus"
REPORTED_WEIGHTED = "sharesansar_reported_basic_eps"
NEITHER = "unexplained"
NO_SHARES = "no_share_count"
BONUS_RATIO_ONLY = "bonus_ratio_only_no_share_count"


def classify(eps_ml: float, eps_ss: float, profit_thousands: float | None, shares: float | None, bonus_pct: float | None) -> str:
    if abs(eps_ml - eps_ss) <= EXACT:
        return MATCH
    def close(a: float, b: float) -> bool:
        return abs(a - b) <= max(EXACT, REL * abs(b))

    if profit_thousands is None or shares is None or pd.isna(profit_thousands) or pd.isna(shares) or not shares:
        if bonus_pct and close(eps_ml * (1 + bonus_pct / 100.0), eps_ss):
            return BONUS_RATIO_ONLY
        return NO_SHARES
    simple = profit_thousands * 1000.0 / shares

    if bonus_pct and close(eps_ss, simple) and close(eps_ml * (1 + bonus_pct / 100.0), simple):
        return BONUS_RESCALED
    if close(eps_ml, simple) and not close(eps_ss, simple):
        return REPORTED_WEIGHTED
    return NEITHER


def run(engine: Any) -> dict[str, Any]:
    with engine.connect() as connection:
        figures = pd.read_sql(
            text("SELECT symbol, fiscal_year, quarter, eps::float AS eps_ss, profit_for_period_thousands::float AS profit, statement "
                 "FROM quarterly_report_figures WHERE source = 'sharesansar'"),
            connection,
        )
        history = pd.read_sql(text("SELECT symbol, reported_date, fiscal_year, eps::float AS eps FROM fundamentals ORDER BY symbol, reported_date"), connection)
        dividends = pd.read_sql(
            text("SELECT symbol, fiscal_year, bonus_pct::float AS bonus, bookclose_date FROM dividend_declarations"), connection
        )
    figures["shares"] = [(s.get("keymetrics") or {}).get("No. of Outstanding Shares") for s in figures["statement"]]
    latest = history.drop_duplicates("symbol", keep="last")
    pairs = latest.merge(figures, on="symbol", suffixes=("_ml", "_ss"))
    pairs = pairs[(pairs["fiscal_year_ml"] == pairs["fiscal_year_ss"]) & pairs["eps"].notna() & pairs["eps_ss"].notna()]
    bonus = dividends.sort_values("bookclose_date").drop_duplicates(["symbol", "fiscal_year"], keep="last")
    pairs = pairs.merge(bonus, left_on=["symbol", "fiscal_year_ss"], right_on=["symbol", "fiscal_year"], how="left")
    rows = []
    for r in pairs.itertuples(index=False):
        bonus_applies = r.bonus if pd.notna(r.bonus) and pd.notna(r.bookclose_date) and r.bookclose_date <= r.reported_date else None
        category = classify(r.eps, r.eps_ss, r.profit, r.shares, bonus_applies)
        changes = history[(history["symbol"] == r.symbol) & (history["fiscal_year"] == r.fiscal_year_ss)].drop_duplicates("eps")
        rows.append({
            "symbol": r.symbol, "fiscal_year": r.fiscal_year_ss, "quarter": int(r.quarter), "eps_merolagani": r.eps, "eps_sharesansar": r.eps_ss,
            "profit_over_outstanding_shares": round(r.profit * 1000.0 / r.shares, 2) if pd.notna(r.profit) and pd.notna(r.shares) and r.shares else None,
            "bonus_pct": None if pd.isna(r.bonus) else r.bonus, "bonus_book_close": None if pd.isna(r.bookclose_date) else str(r.bookclose_date),
            "merolagani_eps_history": [[str(d), e] for d, e in zip(changes["reported_date"], changes["eps"])],
            "category": category,
        })
    frame = pd.DataFrame(rows)
    return {
        "pairs": int(len(frame)),
        "by_category": frame["category"].value_counts().to_dict(),
        "mismatches": frame[frame["category"] != MATCH].to_dict("records"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify EPS differences between Merolagani snapshots and Sharesansar statements")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    from src.database.connection import engine

    result = run(engine)
    args.output.write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps({"pairs": result["pairs"], "by_category": result["by_category"]}, indent=2))
    for row in result["mismatches"]:
        print(row["symbol"], row["category"], row["eps_merolagani"], row["eps_sharesansar"], row["profit_over_outstanding_shares"], row["bonus_pct"], row["bonus_book_close"])


if __name__ == "__main__":
    main()
