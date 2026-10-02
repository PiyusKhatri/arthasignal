from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.backtest import event_tables as et
from src.backtest.event_data import load_inputs, load_panel
from src.backtest.ledger import DatabaseLedger
from src.scorecard import model_v0, spec, v2
from src.scorecard.grading import build_cube, build_market, unresolved_steps_mask
from src.scorecard.ledger import feature_hash
from src.scorecard.schema import apply_schema
from src.scorecard.situations import labels_for, market_state_labels, situation_matrix

logger = logging.getLogger(__name__)

NPT = ZoneInfo("Asia/Kathmandu")
MARKET_OPEN = time(11, 0)
LOCK_KEY = 820_260_002
EXIT_TOO_LATE = 2
EXIT_NO_SESSION = 3


def entry_deadline(signal_date: date) -> datetime:
    return datetime.combine(signal_date + timedelta(days=1), MARKET_OPEN, tzinfo=NPT)


def code_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()[:40]
    except Exception:
        return "unknown"


def register_model(engine: Engine, schema: str = "public") -> dict[str, Any]:
    parameters = dict(model_v0.PARAMETERS)
    digest = feature_hash(parameters)
    with engine.begin() as connection:
        connection.execute(
            text(
                f"INSERT INTO {schema}.scorecard_models (model_name, model_version, description, parameters, parameters_hash, code_commit) "
                "VALUES (:n, :v, :d, CAST(:p AS jsonb), :h, :c) ON CONFLICT (model_name, model_version) DO NOTHING"
            ),
            {"n": model_v0.NAME, "v": model_v0.VERSION, "d": model_v0.DESCRIPTION, "p": json.dumps(parameters), "h": digest, "c": code_commit()},
        )
        row = connection.execute(
            text(f"SELECT id, parameters_hash, code_commit, registered_at FROM {schema}.scorecard_models WHERE model_name = :n AND model_version = :v"),
            {"n": model_v0.NAME, "v": model_v0.VERSION},
        ).one()
    if row.parameters_hash != digest:
        raise SystemExit("model v0 parameters changed after registration; register a new version instead")
    DatabaseLedger().register_variant(
        v2.VARIANT_FAMILY,
        {"protocol": v2.PROTOCOL_VERSION, "strategy": model_v0.NAME, "version": model_v0.VERSION, **model_v0.PARAMETERS},
        f"{v2.PROTOCOL_VERSION} strategy {model_v0.NAME} {model_v0.VERSION}",
    )
    return {"id": int(row.id), "parameters_hash": row.parameters_hash, "code_commit": row.code_commit, "registered_at": str(row.registered_at)}


def build_state(as_of: date) -> dict[str, Any]:
    inputs = load_inputs(as_of)
    inputs["prices"] = inputs["prices"][inputs["prices"]["symbol"].map(spec.valid_symbol)]
    panel = load_panel(inputs)
    if not panel.sessions or panel.sessions[-1] != as_of:
        raise LookupError(f"no price session on {as_of}; latest is {panel.sessions[-1] if panel.sessions else None}")
    actions = inputs["actions"][inputs["actions"]["action_type"].isin(["BONUS", "DIVIDEND", "RIGHT"])]
    mask, steps = unresolved_steps_mask(inputs["prices"], actions, panel)
    market = build_market(panel, actions, inputs["index"], unresolved=mask)
    mergers = et._merger_symbols(inputs)
    return {"inputs": inputs, "panel": panel, "actions": actions, "market": market, "mergers": mergers}


def compute_calls(state: dict[str, Any]) -> pd.DataFrame:
    panel = state["panel"]
    t = len(panel.sessions) - 1
    strategy = model_v0.strategy(state["inputs"]["index"], state["actions"], state["mergers"])
    picks = strategy.select(panel, t)
    if picks.empty:
        return pd.DataFrame(columns=["symbol", "signal_date", "score", "situations", "feature_hash"])
    situations = situation_matrix(state["market"], state["inputs"]["index"], state["inputs"]["rates"], state["mergers"])
    rows = picks["symbol"].map(panel.row).to_numpy()
    params_hash = feature_hash(model_v0.PARAMETERS)
    picks = picks.copy()
    picks["signal_date"] = panel.sessions[t]
    picks["situations"] = [labels_for(situations, int(r), t) for r in rows]
    picks["feature_hash"] = [
        feature_hash({"model": model_v0.NAME, "version": model_v0.VERSION, "parameters_hash": params_hash, "symbol": s,
                      "date": panel.sessions[t], "score": round(float(x), 10), "situations": sit})
        for s, x, sit in zip(picks["symbol"], picks["score"], picks["situations"])
    ]
    return picks


def write_live_calls(engine: Engine, calls: pd.DataFrame, now: datetime, schema: str = "public") -> dict[str, int]:
    if calls.empty:
        return {"attempted": 0, "inserted": 0}
    signal_date = calls["signal_date"].iloc[0]
    if now >= entry_deadline(signal_date):
        raise TimeoutError(f"calls for {signal_date} must be written before {entry_deadline(signal_date).isoformat()}")
    batch = f"live-{signal_date.isoformat()}"
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
                {"s": model_v0.NAME, "v": model_v0.VERSION, "h": row.feature_hash, "b": batch, "sym": row.symbol,
                 "d": row.signal_date, "score": float(row.score), "sit": list(row.situations), "c": now},
            ).first()
            inserted += int(result is not None)
    return {"attempted": int(len(calls)), "inserted": inserted}


def grade_matured(engine: Engine, state: dict[str, Any], dry_run: bool, schema: str = "public") -> dict[str, Any]:
    panel, market = state["panel"], state["market"]
    with engine.connect() as connection:
        calls = pd.read_sql(
            text(
                f"SELECT c.id AS call_id, c.symbol, c.signal_date FROM {schema}.scorecard_calls c "
                "WHERE c.mode = 'live' AND c.strategy = :s AND c.model_version = :v"
            ),
            connection,
            params={"s": model_v0.NAME, "v": model_v0.VERSION},
        )
        done = {
            (int(a), int(b))
            for a, b in connection.execute(
                text(
                    f"SELECT g.call_id, g.horizon FROM {schema}.scorecard_grades g JOIN {schema}.scorecard_calls c ON c.id = g.call_id "
                    "WHERE c.mode = 'live' AND c.strategy = :s AND g.grade_version = :g"
                ),
                {"s": model_v0.NAME, "g": v2.GRADE_VERSION},
            )
        }
    if calls.empty:
        return {"live_calls": 0, "matured_new_grades": 0}
    last_index = len(panel.sessions) - 1
    cubes = {h: build_cube(market, h, last_index) for h in spec.HORIZONS}
    grades = v2.grade_calls_v2(market, cubes, calls, v2.cumulative_event_returns(market))
    if not grades.empty:
        grades = grades[[(int(c), int(h)) not in done for c, h in zip(grades["call_id"], grades["horizon"])]]
    if not dry_run and not grades.empty:
        from src.scorecard.ledger import record_grades

        record_grades(engine, grades, schema)
    return {"live_calls": int(len(calls)), "matured_new_grades": int(len(grades))}


def run(as_of: date | None, dry_run: bool, now: datetime | None = None, schema: str = "public") -> dict[str, Any]:
    from src.database.connection import engine

    now = now or datetime.now(tz=NPT)
    if as_of is None:
        with engine.connect() as connection:
            as_of = connection.execute(text("SELECT max(date) FROM daily_prices")).scalar_one()
    report: dict[str, Any] = {"as_of": as_of.isoformat(), "now": now.isoformat(), "dry_run": dry_run,
                              "deadline": entry_deadline(as_of).isoformat()}
    if not dry_run:
        apply_schema(engine, schema)
        report["model"] = register_model(engine, schema)
    state = build_state(as_of)
    calls = compute_calls(state)
    states = market_state_labels(state["inputs"]["index"], state["panel"].sessions)
    report["market_state"] = states.iloc[-1]
    report["calls"] = calls[["symbol", "score", "situations", "feature_hash"]].assign(score=lambda f: f["score"].round(4)).to_dict("records")
    report["deadline_passed"] = now >= entry_deadline(as_of)
    if dry_run:
        report["write"] = "dry run: nothing written" + (" (a live write would be refused: deadline passed)" if report["deadline_passed"] else "")
    else:
        report["write"] = write_live_calls(engine, calls, now, schema)
    report["grading"] = grade_matured(engine, state, dry_run, schema)
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Write frozen model v0 calls after the close and grade matured live calls")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--schema", default="public")
    args = parser.parse_args()
    try:
        report = run(args.date, args.dry_run, schema=args.schema)
    except TimeoutError as error:
        logger.error("%s", error)
        sys.exit(EXIT_TOO_LATE)
    except LookupError as error:
        logger.error("%s", error)
        sys.exit(EXIT_NO_SESSION)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
