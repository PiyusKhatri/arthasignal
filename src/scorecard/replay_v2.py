from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from sqlalchemy import text

from src.backtest import event_tables as et
from src.backtest.event_data import load_inputs, load_panel
from src.backtest.ledger import DatabaseLedger
from src.scorecard import spec, v2
from src.scorecard.audit import lookahead_audit
from src.scorecard.grading import build_cube, build_market, unresolved_steps_mask
from src.scorecard.ledger import graded_call_ids, load_graded, record_calls, record_grades
from src.scorecard.replay import _existing_calls, generate_calls
from src.scorecard.schema import apply_schema
from src.scorecard.situations import situation_matrix
from src.scorecard.strategies import STRATEGIES, Strategy

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("docs/scorecard_baselines_v2.json")


def build_context() -> dict[str, Any]:
    inputs = load_inputs(spec.DEVELOPMENT_END)
    invalid = sorted({s for s in inputs["prices"]["symbol"].unique() if not spec.valid_symbol(s)})
    inputs["prices"] = inputs["prices"][inputs["prices"]["symbol"].map(spec.valid_symbol)]
    panel = load_panel(inputs)
    if panel.sessions[-1] > spec.DEVELOPMENT_END:
        raise SystemExit("panel extends past the development window")
    actions = inputs["actions"][inputs["actions"]["action_type"].isin(["BONUS", "DIVIDEND", "RIGHT"])]
    mask, steps = unresolved_steps_mask(inputs["prices"], actions, panel)
    market = build_market(panel, actions, inputs["index"], unresolved=mask)
    last_index = len(panel.sessions) - 1
    cubes = {h: build_cube(market, h, last_index) for h in spec.HORIZONS}
    return {
        "inputs": inputs,
        "invalid": invalid,
        "panel": panel,
        "actions": actions,
        "mask": mask,
        "steps": steps,
        "market": market,
        "cubes": cubes,
        "cumulative": v2.cumulative_event_returns(market),
        "situations": situation_matrix(market, inputs["index"], inputs["rates"], et._merger_symbols(inputs)),
    }


def run(
    strategies: Sequence[Strategy] = STRATEGIES,
    output: Path = DEFAULT_OUTPUT,
    context: dict[str, Any] | None = None,
    grade_version: str = v2.GRADE_VERSION,
) -> dict[str, Any]:
    from src.database.holdout_guard import engine

    started = time.perf_counter()
    apply_schema(engine)
    context = context or build_context()
    panel, market, cubes = context["panel"], context["market"], context["cubes"]
    ledger = DatabaseLedger()
    for strategy in strategies:
        ledger.register_variant(
            v2.VARIANT_FAMILY,
            {"protocol": v2.PROTOCOL_VERSION, "strategy": strategy.name, "version": strategy.version, **strategy.parameters},
            f"{v2.PROTOCOL_VERSION} strategy {strategy.name} {strategy.version}",
        )
    with engine.connect() as connection:
        versions = int(
            connection.execute(
                text("SELECT count(*) FROM backtest_variant_trials WHERE model_family = :f"), {"f": v2.VARIANT_FAMILY}
            ).scalar_one()
        )
    sectors = dict(zip(context["inputs"]["companies"]["symbol"], context["inputs"]["companies"]["sector"]))
    audits = lookahead_audit(strategies, panel, context["inputs"]["prices"], context["actions"], sectors)
    session_index = {d: i for i, d in enumerate(panel.sessions)}
    universe = {}
    for h, cube in cubes.items():
        traded = market.traded
        mature = traded & (cube.status != 0)
        excluded = traded & (cube.status == spec.STATUSES.index(spec.STATUS_DATA_ERROR))
        universe[str(h)] = {
            "windows": int(mature.sum()),
            "excluded_unresolved_steps": int(excluded.sum()),
            "excluded_share": round(float(excluded.sum() / max(mature.sum(), 1)), 4),
            "mean_baseline_share": round(float(np.nanmean(cube.baseline_share)), 4),
        }
    report: dict[str, Any] = {
        "protocol": v2.PROTOCOL_VERSION,
        "grade_version": grade_version,
        "generated_on": date.today().isoformat(),
        "sessions": len(panel.sessions),
        "last_session": panel.sessions[-1].isoformat(),
        "excluded_invalid_symbols": context["invalid"],
        "unresolved_step_sessions": int(context["mask"].sum()),
        "strategy_versions_in_family": versions,
        "supportable_horizons": v2.supportable_horizons(len(panel.sessions)),
        "universe": universe,
        "strategies": {},
    }
    batch_id = f"replay-{date.today().isoformat()}"
    for strategy in strategies:
        step = time.perf_counter()
        existing = _existing_calls(engine, strategy)
        if existing.empty:
            record_calls(engine, generate_calls(strategy, panel, context["situations"], batch_id))
            existing = _existing_calls(engine, strategy)
        done = graded_call_ids(engine, strategy.name, strategy.version, grade_version=grade_version)
        grades = v2.grade_calls_v2(market, cubes, existing, context["cumulative"], grade_version)
        if done and not grades.empty:
            keys = list(zip(grades["call_id"], grades["horizon"]))
            grades = grades[[key not in done for key in keys]]
        inserted = 0
        if not grades.empty:
            inserted = record_grades(engine, grades)
        del grades
        audit = audits[strategy.name]
        matrix: dict[str, dict[str, Any]] = {s: {} for s in spec.SITUATIONS}
        monitors = {}
        for h in spec.HORIZONS:
            graded = load_graded(engine, strategy.name, strategy.version, horizon=h, grade_version=grade_version)
            exits = graded["exit_date"].dropna()
            if (exits > spec.DEVELOPMENT_END).any():
                raise SystemExit("a graded exit reached past the development window")
            for situation, cell in v2.matrix_column_v2(graded, h, session_index, len(panel.sessions), versions, audit["leaky"]).items():
                matrix[situation][str(h)] = cell
            monitor = v2.rolling_monitor_v2(graded, h, panel.sessions)
            monitors[str(h)] = {
                "points": int(len(monitor)),
                "suspend_points": int(monitor["suspend"].sum()) if len(monitor) else 0,
                "kill_brier_points": int(monitor["kill_brier"].sum()) if len(monitor) else 0,
                "kill_edge_points": int(monitor["kill_edge"].sum()) if len(monitor) else 0,
            }
        report["strategies"][strategy.name] = {
            "version": strategy.version,
            "calls": int(len(existing)),
            "grades_inserted_now": int(inserted),
            "audit": audit,
            "matrix": matrix,
            "monitoring": monitors,
        }
        logger.info("%s v2 done in %.0fs", strategy.name, time.perf_counter() - step)
    report["runtime_seconds"] = round(time.perf_counter() - started, 1)
    output.write_text(json.dumps(report, indent=1, default=str))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--with-model-v0", action="store_true")
    parser.add_argument("--only-model-v0", action="store_true")
    parser.add_argument("--grade-version", default=v2.GRADE_VERSION)
    args = parser.parse_args()
    context = build_context()
    strategies: list[Strategy] = [] if args.only_model_v0 else list(STRATEGIES)
    if args.with_model_v0 or args.only_model_v0:
        from src.scorecard import model_v0

        strategies.append(model_v0.strategy(context["inputs"]["index"], context["actions"], et._merger_symbols(context["inputs"])))
    report = run(strategies, args.output, context, args.grade_version)
    for name, result in report["strategies"].items():
        verdicts: dict[str, int] = {}
        for row in result["matrix"].values():
            for cell in row.values():
                verdicts[cell["verdict"]] = verdicts.get(cell["verdict"], 0) + 1
        print(name, "leaky" if result["audit"]["leaky"] else "clean", verdicts)


if __name__ == "__main__":
    main()
