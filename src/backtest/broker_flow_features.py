from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import duckdb
import pandas as pd

from src.backtest import broker_flow_spec as spec

logger = logging.getLogger(__name__)

FLOORSHEET_ROOT = Path(os.environ.get("ARTHASIGNAL_FLOORSHEET_DIR", "~/Desktop/arthasignal-ai/raw/floorsheet")).expanduser()
DERIVED_ROOT = Path(os.environ.get("ARTHASIGNAL_DERIVED_DIR", "~/Desktop/arthasignal-ai/derived")).expanduser() / "broker_flow"
FILE_PATTERN = re.compile(r"year=(\d{4})/month=(\d{1,2})/day=(\d{1,2})\.parquet$")
PRICE_ADJUSTING_ACTIONS = ("BONUS", "RIGHT")
H5_ACTION_WINDOW = 20


@dataclass(frozen=True)
class FeatureInputs:
    files: Sequence[tuple[date, Path]]
    prices: pd.DataFrame
    actions: pd.DataFrame
    companies: pd.DataFrame


def floorsheet_files(root: Path = FLOORSHEET_ROOT, end: date = spec.DEVELOPMENT_END) -> list[tuple[date, Path]]:
    found = []
    for path in root.glob("year=*/month=*/day=*.parquet"):
        match = FILE_PATTERN.search(path.as_posix())
        if match is None:
            continue
        day = date(*(int(part) for part in match.groups()))
        if spec.DEVELOPMENT_START <= day <= end:
            found.append((day, path))
    return sorted(found)


def load_database_inputs(end: date = spec.DEVELOPMENT_END) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    from sqlalchemy import text

    from src.database.connection import engine

    with engine.connect() as connection:
        prices = pd.read_sql(
            text("SELECT symbol, date, close FROM daily_prices WHERE date >= :start AND date <= :end"),
            connection,
            params={"start": spec.DEVELOPMENT_START, "end": end},
        )
        actions = pd.read_sql(
            text(
                "SELECT symbol, action_date FROM corporate_actions "
                "WHERE action_type::text = ANY(:types) AND action_date <= :end"
            ),
            connection,
            params={"types": list(PRICE_ADJUSTING_ACTIONS), "end": end},
        )
        companies = pd.read_sql(text("SELECT symbol, instrument_type FROM companies"), connection)
    prices["close"] = prices["close"].astype(float)
    return prices, actions, companies


def _connect(work_dir: Path, memory_limit: str) -> duckdb.DuckDBPyConnection:
    work_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(work_dir / "work.duckdb"))
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute(f"SET temp_directory='{(work_dir / 'spill').as_posix()}'")
    con.execute("SET preserve_insertion_order=false")
    return con


def _load_base_tables(con: duckdb.DuckDBPyConnection, inputs: FeatureInputs) -> None:
    file_frame = pd.DataFrame(
        {"file_date": [day for day, _ in inputs.files], "path": [path.as_posix() for _, path in inputs.files]}
    )
    con.register("file_frame", file_frame)
    paths = file_frame["path"].tolist()
    if paths:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE trades AS
            SELECT t.symbol, ff.file_date AS d, t.buyer_broker_code AS buyer, t.seller_broker_code AS seller,
                   t.contract_quantity::DOUBLE AS q, t.contract_rate AS rate
            FROM read_parquet({paths!r}, union_by_name=true, filename=true) t
            JOIN file_frame ff ON ff.path = t.filename
            WHERE t.contract_quantity > 0 AND t.symbol IS NOT NULL
              AND t.buyer_broker_code IS NOT NULL AND t.seller_broker_code IS NOT NULL
            """
        )
    else:
        con.execute(
            "CREATE OR REPLACE TABLE trades (symbol VARCHAR, d DATE, buyer VARCHAR, seller VARCHAR, q DOUBLE, rate DOUBLE)"
        )
    con.execute(
        """
        CREATE OR REPLACE TABLE fs_sessions AS
        SELECT d, (row_number() OVER (ORDER BY d) - 1)::INTEGER AS sidx FROM (SELECT DISTINCT d FROM trades)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE broker_day AS
        SELECT symbol, d, broker,
               sum(buy) AS buy, sum(sell) AS sell, sum(buy_amt) AS buy_amt, sum(buy_priced) AS buy_priced
        FROM (
            SELECT symbol, d, buyer AS broker, q AS buy, 0.0 AS sell,
                   CASE WHEN rate > 0 THEN q * rate ELSE 0 END AS buy_amt,
                   CASE WHEN rate > 0 THEN q ELSE 0 END AS buy_priced
            FROM trades
            UNION ALL
            SELECT symbol, d, seller AS broker, 0.0, q, 0.0, 0.0 FROM trades
        )
        GROUP BY ALL
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE broker_day AS
        SELECT b.*, s.sidx FROM broker_day b JOIN fs_sessions s USING (d)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE symbol_day AS
        SELECT symbol, d, s.sidx, count(*) AS trades, sum(q) AS qty
        FROM trades JOIN fs_sessions s USING (d)
        GROUP BY ALL
        """
    )

    prices = inputs.prices[["symbol", "date", "close"]].rename(columns={"date": "d"})
    con.register("prices_frame", prices)
    con.execute("CREATE OR REPLACE TABLE prices AS SELECT symbol, d::DATE AS d, close::DOUBLE AS close FROM prices_frame")
    con.execute(
        """
        CREATE OR REPLACE TABLE market_sessions AS
        SELECT d, (row_number() OVER (ORDER BY d) - 1)::INTEGER AS midx FROM (SELECT DISTINCT d FROM prices)
        """
    )
    con.execute("CREATE OR REPLACE TABLE prices AS SELECT p.*, m.midx FROM prices p JOIN market_sessions m USING (d)")
    actions = inputs.actions[["symbol", "action_date"]].rename(columns={"action_date": "d"})
    con.register("actions_frame", actions)
    con.execute("CREATE OR REPLACE TABLE actions AS SELECT symbol, d::DATE AS d FROM actions_frame")
    con.register("companies_frame", inputs.companies[["symbol", "instrument_type"]])
    con.execute("CREATE OR REPLACE TABLE companies AS SELECT * FROM companies_frame")


def _eligibility(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE first_price AS
        SELECT symbol, min(d) AS first_d, min(midx) AS first_midx FROM prices GROUP BY symbol
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE price_activity AS
        SELECT symbol, d, midx, close,
               count(*) OVER (PARTITION BY symbol ORDER BY midx RANGE BETWEEN 19 PRECEDING AND CURRENT ROW) AS traded_20
        FROM prices
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE eligibility AS
        SELECT sd.symbol, sd.d, sd.sidx, pa.midx, sd.trades, sd.qty, pa.close,
               c.instrument_type = 'Equity' AS in_companies_equity,
               c.symbol IS NOT NULL AS in_companies,
               (c.instrument_type = 'Equity')
               AND sd.trades >= {spec.MIN_TRADES_ON_SIGNAL_DAY}
               AND pa.symbol IS NOT NULL
               AND pa.traded_20 >= {spec.MIN_TRADED_SESSIONS_OF_LAST_20}
               AND (fp.first_d <= DATE '{spec.SEASONED_IF_FIRST_PRICE_ON_OR_BEFORE}'
                    OR pa.midx - fp.first_midx >= {spec.NEW_LISTING_SESSIONS}) AS eligible
        FROM symbol_day sd
        LEFT JOIN price_activity pa ON pa.symbol = sd.symbol AND pa.d = sd.d
        LEFT JOIN first_price fp ON fp.symbol = sd.symbol
        LEFT JOIN companies c ON c.symbol = sd.symbol
        """
    )
    con.execute("UPDATE eligibility SET eligible = false WHERE eligible IS NULL")


def _window_aggregate(con: duckdb.DuckDBPyConnection, name: str, window: int, years: Sequence[int]) -> None:
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {name} (symbol VARCHAR, d DATE, broker VARCHAR,
            buy DOUBLE, sell DOUBLE, buy_amt DOUBLE, buy_priced DOUBLE)
        """
    )
    for year in years:
        con.execute(
            f"""
            INSERT INTO {name}
            SELECT t.symbol, t.d, b.broker, sum(b.buy), sum(b.sell), sum(b.buy_amt), sum(b.buy_priced)
            FROM (SELECT symbol, d, sidx FROM symbol_day WHERE year(d) = {year}) t
            JOIN broker_day b ON b.symbol = t.symbol AND b.sidx BETWEEN t.sidx - {window - 1} AND t.sidx
            GROUP BY ALL
            """
        )


def _top_net_buyers(con: duckdb.DuckDBPyConnection, source: str, top: int) -> str:
    return f"""
        SELECT symbol, d, broker, buy, sell, buy_amt, buy_priced, buy - sell AS net
        FROM (
            SELECT *, row_number() OVER (PARTITION BY symbol, d ORDER BY buy - sell DESC, broker) AS rk
            FROM {source}
        )
        WHERE rk <= {top} AND buy - sell > 0
    """


def _h1_h2(con: duckdb.DuckDBPyConnection) -> None:
    top = spec.HYPOTHESES["H1"]["top_brokers"]
    con.execute(
        f"""
        CREATE OR REPLACE TABLE f_h1_h2 AS
        WITH totals AS (
            SELECT symbol, d, sum(buy) AS v5 FROM a5 GROUP BY ALL
        ), hhi AS (
            SELECT a.symbol, a.d,
                   sum(pow(a.buy / t.v5, 2)) - sum(pow(a.sell / t.v5, 2)) AS h2
            FROM a5 a JOIN totals t USING (symbol, d) GROUP BY ALL
        ), top_net AS (
            SELECT symbol, d, sum(net) AS top_net FROM ({_top_net_buyers(con, 'a5', top)}) GROUP BY ALL
        )
        SELECT t.symbol, t.d, t.v5,
               coalesce(n.top_net, 0) / t.v5 AS h1,
               h.h2
        FROM totals t JOIN hhi h USING (symbol, d) LEFT JOIN top_net n USING (symbol, d)
        WHERE t.v5 > 0
        """
    )


def _h3(con: duckdb.DuckDBPyConnection) -> None:
    baseline = spec.HYPOTHESES["H3"]["baseline_sessions"]
    con.execute(
        f"""
        CREATE OR REPLACE TABLE f_h3 AS
        WITH daily AS (
            SELECT b.symbol, b.d, b.sidx, sum(pow(b.buy / sd.qty, 2)) AS hhi
            FROM broker_day b JOIN symbol_day sd USING (symbol, d)
            GROUP BY ALL
        ), rolled AS (
            SELECT symbol, d, hhi,
                   avg(hhi) OVER w AS hhi_prev_mean,
                   count(hhi) OVER w AS hhi_prev_days
            FROM daily
            WINDOW w AS (PARTITION BY symbol ORDER BY sidx RANGE BETWEEN {baseline} PRECEDING AND 1 PRECEDING)
        )
        SELECT symbol, d, hhi AS buy_hhi_day,
               CASE WHEN hhi_prev_days >= {spec.H3_MIN_HISTORY_DAYS} THEN hhi - hhi_prev_mean END AS h3
        FROM rolled
        """
    )


def _h5(con: duckdb.DuckDBPyConnection) -> None:
    top = spec.HYPOTHESES["H5"]["top_brokers"]
    con.execute(
        f"""
        CREATE OR REPLACE TABLE f_h5 AS
        WITH top_buyers AS (
            SELECT symbol, d, sum(buy_amt) AS amt, sum(buy_priced) AS qty
            FROM ({_top_net_buyers(con, 'a20', top)}) GROUP BY ALL
        ), action_hit AS (
            SELECT DISTINCT e.symbol, e.d
            FROM eligibility e
            JOIN market_sessions lo ON lo.midx = e.midx - {H5_ACTION_WINDOW - 1}
            JOIN actions a ON a.symbol = e.symbol AND a.d BETWEEN lo.d AND e.d
        ), early_action_hit AS (
            SELECT DISTINCT e.symbol, e.d
            FROM eligibility e
            JOIN actions a ON a.symbol = e.symbol AND a.d <= e.d
            WHERE e.midx < {H5_ACTION_WINDOW - 1}
        )
        SELECT e.symbol, e.d,
               CASE WHEN tb.qty > 0 AND e.close > 0 AND ah.symbol IS NULL AND eh.symbol IS NULL
                    THEN e.close / (tb.amt / tb.qty) - 1 END AS h5
        FROM eligibility e
        LEFT JOIN top_buyers tb USING (symbol, d)
        LEFT JOIN action_hit ah USING (symbol, d)
        LEFT JOIN early_action_hit eh USING (symbol, d)
        """
    )


def _h4(con: duckdb.DuckDBPyConnection) -> None:
    window = spec.HYPOTHESES["H4"]["window_sessions"]
    con.execute(
        f"""
        CREATE OR REPLACE TABLE h4_complete AS
        SELECT e.symbol, e.d, e.midx
        FROM eligibility e
        JOIN fs_sessions s ON s.d = e.d
        JOIN fs_sessions s0 ON s0.sidx = s.sidx - {window - 1}
        JOIN market_sessions m0 ON m0.midx = e.midx - {window - 1}
        WHERE e.eligible AND s0.d = m0.d
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE h4_events AS
        SELECT c.symbol, c.d, c.midx,
               (p20.close / p0.close - 1 >= {spec.H4_MOVE_THRESHOLD}) AS is_event
        FROM h4_complete c
        JOIN prices p0 ON p0.symbol = c.symbol AND p0.d = c.d
        JOIN market_sessions m20 ON m20.midx = c.midx + {spec.HORIZON_SESSIONS}
        JOIN prices p20 ON p20.symbol = c.symbol AND p20.d = m20.d
        WHERE p0.close > 0
          AND NOT EXISTS (SELECT 1 FROM actions a WHERE a.symbol = c.symbol AND a.d BETWEEN c.d AND m20.d)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE h4_day_counts AS
        SELECT midx, count(*) AS n_all, count(*) FILTER (WHERE is_event) AS n_events
        FROM h4_events GROUP BY midx
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE h4_day_broker AS
        WITH shares AS (
            SELECT a.symbol, a.d, a.broker, (a.buy - a.sell) / t.v5 AS ns
            FROM a5 a JOIN f_h1_h2 t USING (symbol, d)
        )
        SELECT ev.midx, sh.broker,
               sum(sh.ns) AS sum_all,
               sum(sh.ns) FILTER (WHERE ev.is_event) AS sum_events
        FROM h4_events ev JOIN shares sh ON sh.symbol = ev.symbol AND sh.d = ev.d
        GROUP BY ALL
        """
    )
    lookback = spec.H4_LOOKBACK_SESSIONS
    known_lag = spec.HORIZON_SESSIONS + spec.H4_EMBARGO_SESSIONS
    con.execute(
        f"""
        CREATE OR REPLACE TABLE h4_refresh AS
        SELECT DISTINCT (midx // {spec.H4_REFRESH_SESSIONS}) * {spec.H4_REFRESH_SESSIONS} AS ref_midx
        FROM eligibility WHERE midx IS NOT NULL
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE h4_scores AS
        WITH bounds AS (
            SELECT ref_midx, ref_midx - {lookback} AS lo, ref_midx - {known_lag} AS hi FROM h4_refresh
        ), counts AS (
            SELECT b.ref_midx, sum(c.n_all) AS n_all, sum(c.n_events) AS n_events
            FROM bounds b JOIN h4_day_counts c ON c.midx BETWEEN b.lo AND b.hi
            GROUP BY ALL
        ), sums AS (
            SELECT b.ref_midx, x.broker, sum(x.sum_all) AS sum_all, coalesce(sum(x.sum_events), 0) AS sum_events
            FROM bounds b JOIN h4_day_broker x ON x.midx BETWEEN b.lo AND b.hi
            GROUP BY ALL
        )
        SELECT s.ref_midx, s.broker, c.n_events, c.n_all,
               s.sum_events / c.n_events - s.sum_all / c.n_all AS score
        FROM sums s JOIN counts c USING (ref_midx)
        WHERE c.n_events >= {spec.H4_MIN_EVENTS}
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE h4_early AS
        SELECT ref_midx, broker, score, n_events, rk FROM (
            SELECT *, row_number() OVER (PARTITION BY ref_midx ORDER BY score DESC, broker) AS rk
            FROM h4_scores WHERE score > 0
        ) WHERE rk <= {spec.H4_TOP_BROKERS}
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE f_h4 AS
        WITH keyed AS (
            SELECT e.symbol, e.d, (e.midx // {spec.H4_REFRESH_SESSIONS}) * {spec.H4_REFRESH_SESSIONS} AS ref_midx, t.v5
            FROM eligibility e JOIN f_h1_h2 t USING (symbol, d)
            WHERE e.midx IS NOT NULL
        ), has_set AS (
            SELECT DISTINCT ref_midx FROM h4_early
        ), early_net AS (
            SELECT k.symbol, k.d, sum(a.buy - a.sell) AS net
            FROM keyed k
            JOIN h4_early he ON he.ref_midx = k.ref_midx
            JOIN a5 a ON a.symbol = k.symbol AND a.d = k.d AND a.broker = he.broker
            GROUP BY ALL
        )
        SELECT k.symbol, k.d, coalesce(n.net, 0) / k.v5 AS h4
        FROM keyed k JOIN has_set hs USING (ref_midx) LEFT JOIN early_net n USING (symbol, d)
        """
    )


def build_feature_store(
    inputs: FeatureInputs,
    out_dir: Path,
    memory_limit: str = "4GB",
    keep_work: bool = False,
) -> pd.DataFrame:
    started = time.perf_counter()
    work_dir = out_dir / "_work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    con = _connect(work_dir, memory_limit)
    try:
        _load_base_tables(con, inputs)
        logger.info("base tables loaded in %.0fs", time.perf_counter() - started)
        _eligibility(con)
        years = [row[0] for row in con.execute("SELECT DISTINCT year(d) FROM symbol_day ORDER BY 1").fetchall()]
        _window_aggregate(con, "a5", spec.HYPOTHESES["H1"]["window_sessions"], years)
        logger.info("5-session broker windows built in %.0fs", time.perf_counter() - started)
        _h1_h2(con)
        _h3(con)
        _window_aggregate(con, "a20", spec.HYPOTHESES["H5"]["window_sessions"], years)
        logger.info("20-session broker windows built in %.0fs", time.perf_counter() - started)
        _h5(con)
        _h4(con)
        logger.info("features built in %.0fs", time.perf_counter() - started)
        features = con.execute(
            """
            SELECT e.symbol, e.d AS date, e.sidx, e.midx, e.trades, e.qty, e.close,
                   e.in_companies, e.in_companies_equity, e.eligible,
                   f12.h1, f12.h2, f3.h3, f4.h4, f5.h5
            FROM eligibility e
            LEFT JOIN f_h1_h2 f12 USING (symbol, d)
            LEFT JOIN f_h3 f3 USING (symbol, d)
            LEFT JOIN f_h4 f4 USING (symbol, d)
            LEFT JOIN f_h5 f5 USING (symbol, d)
            ORDER BY e.d, e.symbol
            """
        ).df()
        early = con.execute(
            """
            SELECT m.d AS refresh_date, h.ref_midx, h.broker, h.score, h.n_events, h.rk
            FROM h4_early h JOIN market_sessions m ON m.midx = h.ref_midx ORDER BY 1, rk
            """
        ).df()
        out_dir.mkdir(parents=True, exist_ok=True)
        features.to_parquet(out_dir / "features.parquet", index=False)
        early.to_parquet(out_dir / "early_brokers.parquet", index=False)
        con.execute(f"COPY symbol_day TO '{(out_dir / 'symbol_day.parquet').as_posix()}' (FORMAT PARQUET)")
    finally:
        con.close()
        if not keep_work:
            shutil.rmtree(work_dir, ignore_errors=True)
    features["date"] = pd.to_datetime(features["date"]).dt.date
    return features


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Build point-in-time broker-flow features from the floorsheet Parquet")
    parser.add_argument("--out", type=Path, default=DERIVED_ROOT)
    parser.add_argument("--end", type=date.fromisoformat, default=spec.DEVELOPMENT_END)
    parser.add_argument("--memory-limit", default="4GB")
    args = parser.parse_args()
    if args.end > spec.DEVELOPMENT_END:
        raise SystemExit(f"--end may not be after {spec.DEVELOPMENT_END}")
    if FLOORSHEET_ROOT.resolve() in args.out.resolve().parents or args.out.resolve() == FLOORSHEET_ROOT.resolve():
        raise SystemExit("refusing to write inside the floorsheet directory")
    files = floorsheet_files(end=args.end)
    prices, actions, companies = load_database_inputs(args.end)
    features = build_feature_store(FeatureInputs(files, prices, actions, companies), args.out, args.memory_limit)
    summary: dict[str, Any] = {
        "files": len(files),
        "first_file": files[0][0].isoformat() if files else None,
        "last_file": files[-1][0].isoformat() if files else None,
        "rows": len(features),
        "eligible_rows": int(features["eligible"].sum()),
        "defined": {h: int(features[h.lower()].notna().sum()) for h in spec.HYPOTHESES},
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
