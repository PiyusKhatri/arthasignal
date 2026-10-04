from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.backtest.ledger import DatabaseLedger
from src.league import bots as league_bots
from src.league.bots import BOTS, Bot
from src.league.leaderboard import leaderboard, markdown, suspended
from src.scorecard import spec, v2
from src.scorecard.daily import (
    EXIT_NO_SESSION,
    EXIT_TOO_LATE,
    NPT,
    build_state,
    code_commit,
    entry_deadline,
    quarantined_symbols,
)
from src.scorecard.grading import build_cube
from src.scorecard.ledger import feature_hash
from src.scorecard.schema import apply_schema
from src.scorecard.situations import labels_for, market_state_labels, situation_matrix

logger = logging.getLogger(__name__)

LOCK_KEY = 820_260_003
LEAGUE_START = date(2025, 9, 30)

DDL = """
CREATE TABLE IF NOT EXISTS {schema}.league_leaderboard (
    id            BIGSERIAL PRIMARY KEY,
    as_of         DATE NOT NULL,
    bot           VARCHAR(80) NOT NULL,
    bot_version   VARCHAR(20) NOT NULL,
    horizon       SMALLINT NOT NULL,
    rank          SMALLINT NOT NULL,
    metrics       JSONB NOT NULL,
    code_commit   VARCHAR(40) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (as_of, bot, bot_version, horizon)
);

CREATE TABLE IF NOT EXISTS {schema}.league_runs (
    id            BIGSERIAL PRIMARY KEY,
    as_of         DATE NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    code_commit   VARCHAR(40) NOT NULL,
    report        JSONB NOT NULL
);

DROP TRIGGER IF EXISTS league_leaderboard_immutable ON {schema}.league_leaderboard;
CREATE TRIGGER league_leaderboard_immutable BEFORE UPDATE OR DELETE ON {schema}.league_leaderboard
    FOR EACH ROW EXECUTE FUNCTION {schema}.scorecard_reject_change();
DROP TRIGGER IF EXISTS league_runs_immutable ON {schema}.league_runs;
CREATE TRIGGER league_runs_immutable BEFORE UPDATE OR DELETE ON {schema}.league_runs
    FOR EACH ROW EXECUTE FUNCTION {schema}.scorecard_reject_change();
"""


def apply_league_schema(engine: Engine, schema: str = "public") -> None:
    apply_schema(engine, schema)
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(DDL.format(schema=schema))
        raw.commit()
    finally:
        raw.close()


def register_bots(engine: Engine, schema: str = "public", ledger: Any = None) -> list[dict[str, Any]]:
    out = []
    commit = code_commit()
    for bot in BOTS:
        parameters = league_bots.bot_parameters(bot)
        digest = feature_hash(parameters)
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO {schema}.scorecard_models (model_name, model_version, description, parameters, parameters_hash, code_commit) "
                    "VALUES (:n, :v, :d, CAST(:p AS jsonb), :h, :c) ON CONFLICT (model_name, model_version) DO NOTHING"
                ),
                {"n": bot.name, "v": bot.version, "d": bot.description, "p": json.dumps(parameters), "h": digest, "c": commit},
            )
            row = connection.execute(
                text(f"SELECT id, parameters_hash, code_commit, registered_at FROM {schema}.scorecard_models WHERE model_name = :n AND model_version = :v"),
                {"n": bot.name, "v": bot.version},
            ).one()
        if row.parameters_hash != digest:
            raise SystemExit(f"{bot.name} {bot.version} parameters changed after registration; register a new version instead")
        if ledger is not None:
            ledger.register_variant(
                v2.VARIANT_FAMILY,
                {"protocol": v2.PROTOCOL_VERSION, "strategy": bot.name, "version": bot.version, **parameters},
                f"{v2.PROTOCOL_VERSION} strategy {bot.name} {bot.version}",
            )
        out.append({"bot": bot.name, "version": bot.version, "model_id": int(row.id), "parameters_hash": digest,
                    "code_commit": row.code_commit, "registered_at": str(row.registered_at)})
    return out


def compute_bot_calls(state: dict[str, Any], quarantined: Any = ()) -> dict[str, pd.DataFrame]:
    panel = state["panel"]
    t = len(panel.sessions) - 1
    league = league_bots.League(state["inputs"]["index"], state["actions"], state["mergers"])
    situations = situation_matrix(state["market"], state["inputs"]["index"], state["inputs"]["rates"], state["mergers"])
    blocked = set(quarantined)
    out = {}
    for bot in BOTS:
        picks = league.selector(bot.name)(panel, t)
        picks = picks[~picks["symbol"].isin(blocked)].reset_index(drop=True)
        params_hash = feature_hash(league_bots.bot_parameters(bot))
        if picks.empty:
            out[bot.name] = pd.DataFrame(columns=["strategy", "model_version", "symbol", "signal_date", "score", "situations", "feature_hash"])
            continue
        picks = picks.copy()
        picks["strategy"] = bot.name
        picks["model_version"] = bot.version
        picks["signal_date"] = panel.sessions[t]
        picks["situations"] = [labels_for(situations, int(panel.row[s]), t) for s in picks["symbol"]]
        picks["feature_hash"] = [
            feature_hash({"bot": bot.name, "version": bot.version, "parameters_hash": params_hash, "symbol": s,
                          "date": panel.sessions[t], "score": round(float(x), 10), "situations": sit})
            for s, x, sit in zip(picks["symbol"], picks["score"], picks["situations"])
        ]
        out[bot.name] = picks
    return out


def write_calls(engine: Engine, calls: pd.DataFrame, now: datetime, schema: str = "public") -> dict[str, int]:
    if calls.empty:
        return {"attempted": 0, "inserted": 0}
    signal_date = calls["signal_date"].iloc[0]
    if now >= entry_deadline(signal_date):
        raise TimeoutError(f"calls for {signal_date} must be written before {entry_deadline(signal_date).isoformat()}")
    batch = f"league-{signal_date.isoformat()}"
    inserted = 0
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
        for row in calls.itertuples(index=False):
            result = connection.execute(
                text(
                    f"INSERT INTO {schema}.scorecard_calls (call_uid, mode, strategy, model_version, feature_hash, batch_id, "
                    "symbol, signal_date, probability, score, situations, created_at) VALUES (gen_random_uuid(), 'live', :s, :v, "
                    ":h, :b, :sym, :d, NULL, :score, :sit, :c) "
                    "ON CONFLICT (strategy, model_version, symbol, signal_date) DO NOTHING RETURNING id"
                ),
                {"s": row.strategy, "v": row.model_version, "h": row.feature_hash, "b": batch, "sym": row.symbol,
                 "d": row.signal_date, "score": None if pd.isna(row.score) else float(row.score), "sit": list(row.situations), "c": now},
            ).first()
            inserted += int(result is not None)
    return {"attempted": int(len(calls)), "inserted": inserted}


def _league_filter() -> tuple[list[str], list[str]]:
    return [b.name for b in BOTS], [b.version for b in BOTS]


def grade_matured(engine: Engine, state: dict[str, Any], dry_run: bool, schema: str = "public",
                  pairs: set[tuple[str, str]] | None = None) -> dict[str, Any]:
    pairs = pairs if pairs is not None else {(b.name, b.version) for b in BOTS}
    names = sorted({name for name, _ in pairs})
    panel, market = state["panel"], state["market"]
    with engine.connect() as connection:
        calls = pd.read_sql(
            text(f"SELECT id AS call_id, symbol, signal_date, strategy, model_version FROM {schema}.scorecard_calls "
                 "WHERE mode = 'live' AND strategy = ANY(:s)"),
            connection, params={"s": names},
        )
        done = {
            (int(a), int(b))
            for a, b in connection.execute(
                text(f"SELECT g.call_id, g.horizon FROM {schema}.scorecard_grades g JOIN {schema}.scorecard_calls c ON c.id = g.call_id "
                     "WHERE c.mode = 'live' AND c.strategy = ANY(:s) AND g.grade_version = :g"),
                {"s": names, "g": v2.GRADE_VERSION},
            )
        }
    calls = calls[[(s, v) in pairs for s, v in zip(calls["strategy"], calls["model_version"])]]
    if calls.empty:
        return {"live_calls": 0, "matured_new_grades": 0}
    last_index = len(panel.sessions) - 1
    cubes = {h: build_cube(market, h, last_index) for h in spec.HORIZONS}
    grades = v2.grade_calls_v2(market, cubes, calls[["call_id", "symbol", "signal_date"]], v2.cumulative_event_returns(market))
    if not grades.empty:
        grades = grades[[(int(c), int(h)) not in done for c, h in zip(grades["call_id"], grades["horizon"])]]
    if not dry_run and not grades.empty:
        from src.scorecard.ledger import record_grades

        record_grades(engine, grades, schema)
    return {"live_calls": int(len(calls)), "matured_new_grades": int(len(grades))}


def load_live_graded(engine: Engine, schema: str = "public", names: list[str] | None = None) -> pd.DataFrame:
    names = names if names is not None else _league_filter()[0]
    with engine.connect() as connection:
        frame = pd.read_sql(
            text(
                f"SELECT c.id AS call_id, c.strategy, c.model_version, c.symbol, c.signal_date, c.probability, c.score, c.situations, "
                "g.horizon, g.status, g.exit_date, g.gross_return, g.universe_mean, g.baseline_share, g.correct, g.failure_cause "
                f"FROM {schema}.scorecard_calls c JOIN {schema}.scorecard_grades g ON g.call_id = c.id AND g.grade_version = :g "
                "WHERE c.mode = 'live' AND c.strategy = ANY(:s)"
            ),
            connection, params={"g": v2.GRADE_VERSION, "s": names},
        )
    frame["probability"] = pd.to_numeric(frame["probability"], errors="coerce")
    return frame


def penalty_tests(engine: Engine) -> int:
    with engine.connect() as connection:
        versions = connection.execute(
            text("SELECT count(*) FROM backtest_variant_trials WHERE model_family = :f"), {"f": v2.VARIANT_FAMILY}
        ).scalar_one()
    return max(1, int(versions)) * len(spec.SITUATIONS) * len(spec.HORIZONS)


def write_leaderboard(engine: Engine, board: dict[int, list[dict[str, Any]]], as_of: date, schema: str = "public",
                      table: str = "league_leaderboard") -> int:
    commit = code_commit()
    inserted = 0
    with engine.begin() as connection:
        for horizon, rows in board.items():
            for row in rows:
                result = connection.execute(
                    text(f"INSERT INTO {schema}.{table} (as_of, bot, bot_version, horizon, rank, metrics, code_commit) "
                         "VALUES (:d, :b, :v, :h, :r, CAST(:m AS jsonb), :c) ON CONFLICT (as_of, bot, bot_version, horizon) DO NOTHING RETURNING id"),
                    {"d": as_of, "b": row["bot"], "v": row["version"], "h": horizon, "r": row["rank"],
                     "m": json.dumps(row, default=str), "c": commit},
                ).first()
                inserted += int(result is not None)
    return inserted


def run(as_of: date | None, dry_run: bool, now: datetime | None = None, schema: str = "public",
        markdown_dir: Path | None = None, write_only: bool = False) -> dict[str, Any]:
    from src.database.connection import engine
    from src.database.holdout_guard import HOLDOUT_START, HoldoutQueryViolation, allow

    with engine.connect() as connection:
        latest = connection.execute(text("SELECT max(date) FROM daily_prices")).scalar_one()
    as_of = as_of or latest
    if as_of < HOLDOUT_START:
        if not dry_run:
            raise HoldoutQueryViolation(f"{as_of} is before the league start; only dry runs may use development dates")
        return _run(as_of, dry_run, now, schema, markdown_dir, write_only)
    if as_of != latest:
        raise HoldoutQueryViolation(f"{as_of} is inside the holdout and is not the latest session ({latest}); only live operation may read it")
    with allow("live_ledger"):
        return _run(as_of, dry_run, now, schema, markdown_dir, write_only)


def _run(as_of: date, dry_run: bool, now: datetime | None, schema: str, markdown_dir: Path | None,
         write_only: bool = False) -> dict[str, Any]:
    from src.database.connection import engine

    started = datetime.now(tz=NPT)
    now = now or started
    report: dict[str, Any] = {"as_of": as_of.isoformat(), "now": now.isoformat(), "dry_run": dry_run,
                              "deadline": entry_deadline(as_of).isoformat(), "deadline_passed": now >= entry_deadline(as_of)}
    if not dry_run:
        apply_league_schema(engine, schema)
        report["bots_registered"] = register_bots(engine, schema)
    state = build_state(as_of)
    quarantine = quarantined_symbols(engine)
    report["market_state"] = market_state_labels(state["inputs"]["index"], state["panel"].sessions).iloc[-1]
    graded = load_live_graded(engine, schema) if not dry_run else pd.DataFrame()
    live_sessions = [d for d in state["panel"].sessions if d >= LEAGUE_START]
    calls = compute_bot_calls(state, quarantine)
    bots_report = {}
    to_write = []
    for bot in BOTS:
        halted = suspended(graded, bot, state["panel"].sessions) if not graded.empty else {h: False for h in bot.primary_horizons}
        frame = calls[bot.name]
        entry: dict[str, Any] = {"version": bot.version, "side": bot.side, "calls": int(len(frame)),
                                 "symbols": frame["symbol"].tolist(), "suspended": halted}
        if all(halted.values()) and halted:
            entry["write"] = "suspended under the v2 kill rule: no new calls"
        else:
            to_write.append(frame)
        bots_report[bot.name] = entry
    report["bots"] = bots_report
    batch = pd.concat([f for f in to_write if not f.empty], ignore_index=True) if any(not f.empty for f in to_write) else pd.DataFrame()
    if dry_run:
        report["write"] = "dry run: nothing written" + (" (a live write would be refused: deadline passed)" if report["deadline_passed"] else "")
        report["grading"] = "dry run: not graded"
    else:
        report["write"] = write_calls(engine, batch, now, schema)
        if write_only:
            report["grading"] = "write-only run: grading and leaderboard are separate steps"
            _log_run(engine, as_of, started, report, schema)
            return report
        report["grading"] = grade_matured(engine, state, dry_run, schema)
        graded = load_live_graded(engine, schema)
    tests = penalty_tests(engine)
    board = leaderboard(graded, live_sessions, tests)
    report["penalty_tests"] = tests
    report["leaderboard"] = {h: [{k: r.get(k) for k in ("rank", "bot", "graded", "win_rate", "baseline", "edge", "verdict")} for r in rows]
                             for h, rows in board.items()}
    if not dry_run:
        report["leaderboard_rows_written"] = write_leaderboard(engine, board, as_of, schema)
    if markdown_dir is not None:
        markdown_dir.mkdir(parents=True, exist_ok=True)
        path = markdown_dir / f"leaderboard_{as_of.isoformat()}{'_dry_run' if dry_run else ''}.md"
        path.write_text(markdown(board, as_of))
        report["markdown"] = str(path)
    if not dry_run:
        _log_run(engine, as_of, started, report, schema)
    return report


def _log_run(engine: Engine, as_of: date, started: datetime, report: dict[str, Any], schema: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(f"INSERT INTO {schema}.league_runs (as_of, started_at, code_commit, report) VALUES (:d, :s, :c, CAST(:r AS jsonb))"),
            {"d": as_of, "s": started, "c": code_commit(), "r": json.dumps(report, default=str)},
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Paper-bot league: write frozen bot calls after the close, grade matured calls, rank bots")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--schema", default="public")
    parser.add_argument("--markdown-dir", type=Path)
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--write-only", action="store_true")
    args = parser.parse_args()
    if args.register_only:
        from src.database.connection import engine

        apply_league_schema(engine, args.schema)
        print(json.dumps(register_bots(engine, args.schema, DatabaseLedger()), indent=2, default=str))
        return
    try:
        report = run(args.date, args.dry_run, schema=args.schema, markdown_dir=args.markdown_dir, write_only=args.write_only)
    except TimeoutError as error:
        logger.error("%s", error)
        sys.exit(EXIT_TOO_LATE)
    except LookupError as error:
        logger.error("%s", error)
        sys.exit(EXIT_NO_SESSION)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
