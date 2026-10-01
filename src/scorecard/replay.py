from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.backtest import event_tables as et
from src.backtest.event_data import load_inputs, load_panel
from src.backtest.ledger import DatabaseLedger
from src.scorecard import spec
from src.scorecard.audit import lookahead_audit
from src.scorecard.grading import build_cube, build_market, cumulative_flags, grade_calls
from src.scorecard.ledger import feature_hash, graded_call_ids, load_graded, record_calls, record_grades
from src.scorecard.metrics import build_matrix, rolling_monitor
from src.scorecard.schema import apply_schema
from src.scorecard.situations import labels_for, situation_matrix
from src.scorecard.strategies import STRATEGIES, Strategy

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("docs/scorecard_baselines.json")


def generate_calls(strategy: Strategy, panel, situations: dict[str, np.ndarray], batch_id: str) -> pd.DataFrame:
    frames = []
    for t in range(len(panel.sessions)):
        picks = strategy.select(panel, t)
        if picks.empty:
            continue
        picks = picks.copy()
        picks["signal_date"] = panel.sessions[t]
        picks["t"] = t
        frames.append(picks)
    calls = pd.concat(frames, ignore_index=True)
    rows = calls["symbol"].map(panel.row).to_numpy()
    calls["situations"] = [labels_for(situations, int(r), int(t)) for r, t in zip(rows, calls["t"])]
    calls["mode"] = "replay"
    calls["strategy"] = strategy.name
    calls["model_version"] = strategy.version
    calls["batch_id"] = batch_id
    calls["probability"] = strategy.probability
    base = {"strategy": strategy.name, "version": strategy.version, **strategy.parameters}
    calls["feature_hash"] = [
        feature_hash({**base, "symbol": s, "date": d, "score": None if pd.isna(x) else round(float(x), 10)})
        for s, d, x in zip(calls["symbol"], calls["signal_date"], calls["score"])
    ]
    calls["call_uid"] = [str(uuid.uuid4()) for _ in range(len(calls))]
    return calls.drop(columns=["t"])


def _existing_calls(engine, strategy: Strategy) -> pd.DataFrame:
    with engine.connect() as connection:
        return pd.read_sql(
            text("SELECT id AS call_id, symbol, signal_date FROM scorecard_calls WHERE strategy = :s AND model_version = :v"),
            connection,
            params={"s": strategy.name, "v": strategy.version},
        )


def run(output: Path = DEFAULT_OUTPUT, strategies: tuple[Strategy, ...] = STRATEGIES) -> dict[str, Any]:
    from src.database.connection import engine

    started = time.perf_counter()
    apply_schema(engine)
    inputs = load_inputs(spec.DEVELOPMENT_END)
    panel = load_panel(inputs)
    if panel.sessions[-1] > spec.DEVELOPMENT_END:
        raise SystemExit("panel extends past the development window")
    actions = inputs["actions"][inputs["actions"]["action_type"].isin(["BONUS", "DIVIDEND", "RIGHT"])]
    market = build_market(panel, actions, inputs["index"])
    last_index = len(panel.sessions) - 1
    cubes = {h: build_cube(market, h, last_index) for h in spec.HORIZONS}
    cumulative = cumulative_flags(market)
    situations = situation_matrix(market, inputs["index"], inputs["rates"], et._merger_symbols(inputs))
    logger.info("market, cubes and situations built in %.0fs", time.perf_counter() - started)
    sectors = dict(zip(inputs["companies"]["symbol"], inputs["companies"]["sector"]))
    ledger = DatabaseLedger()
    for strategy in strategies:
        ledger.register_variant(
            spec.VARIANT_FAMILY,
            {"protocol": spec.PROTOCOL_VERSION, "strategy": strategy.name, "version": strategy.version, **strategy.parameters},
            f"{spec.PROTOCOL_VERSION} replay strategy {strategy.name} {strategy.version}",
        )
    with engine.connect() as connection:
        versions = int(
            connection.execute(
                text("SELECT count(*) FROM backtest_variant_trials WHERE model_family = :f"), {"f": spec.VARIANT_FAMILY}
            ).scalar_one()
        )
    session_index = {d: i for i, d in enumerate(panel.sessions)}
    universe_summary = {}
    for h, cube in cubes.items():
        traded = market.traded
        status = cube.status[traded]
        universe_summary[str(h)] = {
            "universe_symbol_days": int(traded.sum()),
            "status_share": {name: round(float((status == code).mean()), 4) for code, name in enumerate(spec.STATUSES)},
            "mean_baseline_share": round(float(np.nanmean(cube.baseline_share)), 4),
            "mean_universe_mean_return": round(float(np.nanmean(cube.universe_mean)), 5),
            "mean_universe_median_return": round(float(np.nanmean(cube.universe_median)), 5),
            "mean_nepse_return": round(float(np.nanmean(cube.nepse)), 5),
        }
    report: dict[str, Any] = {
        "protocol": spec.PROTOCOL_VERSION,
        "generated_on": date.today().isoformat(),
        "sessions": len(panel.sessions),
        "first_session": panel.sessions[0].isoformat(),
        "last_session": panel.sessions[-1].isoformat(),
        "strategy_versions_in_family": versions,
        "universe": universe_summary,
        "strategies": {},
    }
    batch_id = f"replay-{date.today().isoformat()}"
    audits = lookahead_audit(strategies, panel, inputs["prices"], actions, sectors)
    for strategy in strategies:
        step = time.perf_counter()
        audit = audits[strategy.name]
        existing = _existing_calls(engine, strategy)
        if existing.empty:
            calls = generate_calls(strategy, panel, situations, batch_id)
            record_calls(engine, calls)
            existing = _existing_calls(engine, strategy)
        done = graded_call_ids(engine, strategy.name, strategy.version)
        grades = grade_calls(market, cubes, existing, cumulative)
        if done:
            keys = list(zip(grades["call_id"], grades["horizon"]))
            grades = grades[[key not in done for key in keys]]
        if not grades.empty:
            record_grades(engine, grades)
        graded = load_graded(engine, strategy.name, strategy.version)
        if (graded["exit_date"].dropna() >= spec.HOLDOUT_START).any() or (graded["exit_date"].dropna() > spec.DEVELOPMENT_END).any():
            raise SystemExit("a graded exit reached past the development window")
        matrix = build_matrix(graded, session_index, len(panel.sessions), versions, leaky=audit["leaky"])
        monitors = {}
        for h in spec.HORIZONS:
            monitor = rolling_monitor(graded, h, panel.sessions)
            monitors[str(h)] = {
                "points": int(len(monitor)),
                "suspend_points": int(monitor["suspend"].sum()) if len(monitor) else 0,
                "first_suspend": str(monitor.loc[monitor["suspend"], "as_of"].min()) if len(monitor) and monitor["suspend"].any() else None,
            }
        report["strategies"][strategy.name] = {
            "version": strategy.version,
            "calls": int(len(existing)),
            "grades": int(len(graded)),
            "audit": audit,
            "matrix": matrix,
            "monitoring": monitors,
        }
        logger.info("%s done in %.0fs", strategy.name, time.perf_counter() - step)
    report["runtime_seconds"] = round(time.perf_counter() - started, 1)
    output.write_text(json.dumps(report, indent=1, default=str))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.output)
    for name, result in report["strategies"].items():
        cells = result["matrix"]["all"]
        print(name, result["audit"]["leaky"], {h: (c.get("win_rate"), c.get("baseline"), c.get("verdict")) for h, c in cells.items()})


if __name__ == "__main__":
    main()
