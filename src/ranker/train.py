from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
import warnings
from datetime import date
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sqlalchemy import text

from src.backtest import event_tables as et
from src.backtest.event_data import load_panel
from src.league.bots import League
from src.ranker import features as feat
from src.ranker import spec
from src.scorecard import v2
from src.scorecard.grading import FILLED
from src.scorecard.ledger import feature_hash, load_calls, load_graded, record_calls, record_grades
from src.scorecard.situations import labels_for

logger = logging.getLogger(__name__)

OUTPUT = Path("docs/ranker_results.json")
ARTIFACTS = Path("~/Desktop/arthasignal-ai/derived/ranker_r1").expanduser()
AUDIT_DATES = 20
AUDIT_SEED = 20261004


def build() -> dict[str, Any]:
    from src.database.holdout_guard import engine
    from src.scorecard.replay_v2 import build_context

    ctx = build_context()
    if ctx["panel"].sessions[-1] > spec.WINDOW_END:
        raise SystemExit("panel passes the development window")
    ctx["extras"] = feat.load_extras(engine, spec.WINDOW_END)
    started = time.perf_counter()
    ctx["raw"] = feat.compute(ctx["panel"], ctx["inputs"], ctx["extras"])
    ctx["ranked"] = feat.rank_transform(ctx["raw"])
    logger.info("features computed in %.0fs", time.perf_counter() - started)
    ctx["mergers"] = et._merger_symbols(ctx["inputs"])
    return ctx


def cells(ctx: dict[str, Any]) -> pd.DataFrame:
    panel = ctx["panel"]
    traded = ~np.isnan(panel.close)
    rows, cols = np.nonzero(traded)
    keep = cols >= spec.WARMUP_SESSIONS
    rows, cols = rows[keep], cols[keep]
    frame = pd.DataFrame({"r": rows.astype(np.int32), "t": cols.astype(np.int32)})
    for name in spec.FEATURES:
        frame[name] = ctx["ranked"][name][rows, cols]
    return frame


def labels(ctx: dict[str, Any], frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    cube = ctx["cubes"][horizon]
    r, t = frame["r"].to_numpy(), frame["t"].to_numpy()
    filled = cube.status[r, t] == FILLED
    excess = cube.gross[r, t] - cube.universe_mean[t]
    out = pd.DataFrame({"t": t, "excess": np.where(filled, excess, np.nan), "exit": cube.exit_index[r, t]})
    out["label"] = out.groupby("t")["excess"].rank(pct=True)
    counts = out.groupby("t")["excess"].transform("count")
    out.loc[counts < spec.MIN_CROSS_SECTION, "label"] = np.nan
    return out


def daily_ic(t: np.ndarray, prediction: np.ndarray, target: np.ndarray) -> float:
    frame = pd.DataFrame({"t": t, "p": prediction, "y": target}).dropna()
    values = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for _, group in frame.groupby("t"):
            if len(group) >= spec.MIN_CROSS_SECTION:
                values.append(spearmanr(group["p"], group["y"]).statistic)
    values = [v for v in values if np.isfinite(v)]
    return float(np.mean(values)) if values else float("nan")


def fit(params: dict[str, Any], x: pd.DataFrame, y: np.ndarray) -> lgb.LGBMRegressor:
    model = lgb.LGBMRegressor(**spec.FIXED_PARAMS, **params)
    model.fit(x, y)
    return model


def fold_bounds(sessions: list[date]) -> list[tuple[int, int]]:
    out = []
    for start, end in spec.FOLDS:
        a = next(i for i, d in enumerate(sessions) if d >= start)
        b = max(i for i, d in enumerate(sessions) if d <= end)
        out.append((a, b))
    return out


def walk_forward(ctx: dict[str, Any], frame: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, float]]:
    lab = labels(ctx, frame, horizon)
    x = frame[list(spec.FEATURES)]
    sessions = list(ctx["panel"].sessions)
    predictions = []
    folds = []
    importance = np.zeros(len(spec.FEATURES))
    for k, (a, b) in enumerate(fold_bounds(sessions)):
        train = lab["label"].notna() & (lab["exit"] < a - spec.EMBARGO_SESSIONS) & (lab["t"] < a)
        train_dates = np.sort(lab.loc[train, "t"].unique())
        split = int(train_dates[int(len(train_dates) * (1 - spec.INNER_VALIDATION_SHARE))])
        inner_train = train & (lab["t"] < split) & (lab["exit"] < split - spec.EMBARGO_SESSIONS)
        inner_val = train & (lab["t"] >= split)
        scores = {}
        for g, params in enumerate(spec.GRID):
            model = fit(params, x[inner_train], lab.loc[inner_train, "label"].to_numpy())
            scores[g] = daily_ic(lab.loc[inner_val, "t"].to_numpy(), model.predict(x[inner_val]), lab.loc[inner_val, "label"].to_numpy())
        best = max(scores, key=lambda g: (scores[g], -g))
        model = fit(spec.GRID[best], x[train], lab.loc[train, "label"].to_numpy())
        importance += model.booster_.feature_importance("gain")
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        model.booster_.save_model(str(ARTIFACTS / f"h{horizon}_fold{k + 1}.txt"))
        test = (frame["t"] >= a) & (frame["t"] <= b)
        pred = model.predict(x[test])
        predictions.append(pd.DataFrame({"r": frame.loc[test, "r"].to_numpy(), "t": frame.loc[test, "t"].to_numpy(), "score": pred}))
        test_lab = lab.loc[test, "label"].to_numpy()
        folds.append({
            "fold": k + 1, "test": [sessions[a].isoformat(), sessions[b].isoformat()],
            "train_rows": int(train.sum()), "train_last_exit": sessions[int(lab.loc[train, "exit"].max())].isoformat(),
            "inner_ic": {json.dumps(spec.GRID[g]): round(v, 4) for g, v in scores.items()}, "selected": spec.GRID[best],
            "test_ic": round(daily_ic(frame.loc[test, "t"].to_numpy(), pred, test_lab), 4),
            "purge_ok": bool(lab.loc[train, "exit"].max() < a - spec.EMBARGO_SESSIONS),
        })
        logger.info("h%d fold %d: grid %s inner %s test IC %.4f", horizon, k + 1, spec.GRID[best], folds[-1]["inner_ic"], folds[-1]["test_ic"])
    total = importance.sum() or 1.0
    return pd.concat(predictions, ignore_index=True), folds, {n: round(float(v / total), 4) for n, v in zip(spec.FEATURES, importance)}


def make_calls(ctx: dict[str, Any], predictions: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    panel = ctx["panel"]
    league = League(ctx["inputs"]["index"], ctx["actions"], ctx["mergers"])
    situations = ctx["situations"]
    calls, eligible_scores = [], []
    batch = f"ranker-r1-h{horizon}"
    name = spec.strategy_name(horizon)
    for t, group in predictions.groupby("t"):
        rows, _ = league.eligible(panel, int(t))
        scored = group[group["r"].isin(set(rows.tolist()))]
        if scored.empty:
            continue
        eligible_scores.append(scored.assign(score_rank=scored["score"].rank(pct=True)))
        picks = league._cap(panel, scored["r"].to_numpy(), scored["score"].to_numpy(), spec.PICKS)
        day = panel.sessions[int(t)]
        for symbol, score in zip(picks["symbol"], picks["score"]):
            situation = labels_for(situations, int(panel.row[symbol]), int(t))
            calls.append({"call_uid": str(uuid.uuid4()), "mode": "replay", "strategy": name, "model_version": spec.VERSION,
                          "feature_hash": feature_hash({"strategy": name, "version": spec.VERSION, "symbol": symbol, "date": day, "score": round(float(score), 10)}),
                          "batch_id": batch, "symbol": symbol, "signal_date": day, "probability": None, "score": float(score), "situations": situation})
    return pd.DataFrame(calls), pd.concat(eligible_scores, ignore_index=True)


def ledger_calls(engine: Any, strategy: str, version: str) -> pd.DataFrame:
    frame = load_calls(engine, strategy)
    frame = frame[frame["model_version"] == version]
    return frame.rename(columns={"id": "call_id"})[["call_id", "symbol", "signal_date"]]


def grade_and_evaluate(ctx: dict[str, Any], strategy: str, version: str, horizon: int, leaky: bool, calls: pd.DataFrame | None) -> dict[str, Any]:
    from src.database.holdout_guard import engine

    existing = ledger_calls(engine, strategy, version)
    if existing.empty and calls is not None and not calls.empty:
        record_calls(engine, calls)
        existing = ledger_calls(engine, strategy, version)
    with engine.connect() as connection:
        done = connection.execute(
            text("SELECT count(*) FROM scorecard_grades g JOIN scorecard_calls c ON c.id = g.call_id WHERE c.strategy = :s AND c.model_version = :v AND g.grade_version = :g"),
            {"s": strategy, "v": version, "g": v2.GRADE_VERSION},
        ).scalar_one()
        versions = int(connection.execute(text("SELECT count(*) FROM backtest_variant_trials WHERE model_family = :f"), {"f": v2.VARIANT_FAMILY}).scalar_one())
    if not done:
        grades = v2.grade_calls_v2(ctx["market"], ctx["cubes"], existing, ctx["cumulative"])
        record_grades(engine, grades)
    sessions = list(ctx["panel"].sessions)
    first_signal = min(existing["signal_date"]) if not existing.empty else spec.FIRST_TEST_DAY
    test_sessions = [d for d in sessions if d >= first_signal]
    test_index = {d: i for i, d in enumerate(test_sessions)}
    out: dict[str, Any] = {"strategy": strategy, "version": version, "calls": int(len(existing)), "versions_in_family": versions,
                           "penalty_tests": versions * 104, "cells_all": {}}
    for h in spec.HORIZONS + (80,):
        graded = load_graded(engine, strategy, version, horizon=h, grade_version=v2.GRADE_VERSION)
        if (graded["exit_date"].dropna() > spec.WINDOW_END).any():
            raise SystemExit("a graded exit passed the development window")
        column = v2.matrix_column_v2(graded, h, test_index, len(test_sessions), versions, leaky)
        out["cells_all"][str(h)] = column["all"]
    out["primary"] = out["cells_all"][str(horizon)]
    return out


def audit(ctx: dict[str, Any]) -> dict[str, Any]:
    rng = np.random.default_rng(AUDIT_SEED)
    sessions = list(ctx["panel"].sessions)
    candidates = [i for i, d in enumerate(sessions) if d >= spec.FIRST_TEST_DAY]
    chosen = sorted(rng.choice(candidates, size=AUDIT_DATES, replace=False).tolist())
    league = League(ctx["inputs"]["index"], ctx["actions"], ctx["mergers"])
    mismatches = []
    for t in chosen:
        day = sessions[t]
        cut_inputs, cut_extras = feat.truncate(ctx["inputs"], ctx["extras"], day)
        cut_panel = load_panel(cut_inputs)
        cut = feat.compute(cut_panel, cut_inputs, cut_extras)
        rows, _ = league.eligible(ctx["panel"], t)
        for r in rows:
            symbol = ctx["panel"].symbols[r]
            cr = cut_panel.row.get(symbol)
            for name in spec.FEATURES:
                a = float(ctx["raw"][name][r, t])
                b = float(cut[name][cr, len(cut_panel.sessions) - 1]) if cr is not None else float("nan")
                if not (np.isnan(a) and np.isnan(b)) and not np.isclose(a, b, rtol=1e-4, atol=1e-6):
                    mismatches.append({"date": day.isoformat(), "symbol": symbol, "feature": name, "full": a, "truncated": b})
        logger.info("audit %s: %d eligible checked, %d mismatches so far", day, len(rows), len(mismatches))
    by_feature = pd.Series([m["feature"] for m in mismatches]).value_counts().to_dict() if mismatches else {}
    return {"dates": [sessions[t].isoformat() for t in chosen], "mismatches": len(mismatches), "by_feature": by_feature,
            "examples": mismatches[:10], "leaky": bool(mismatches)}


def run(horizons: tuple[int, ...] = spec.HORIZONS, output: Path = OUTPUT) -> dict[str, Any]:
    started = time.perf_counter()
    ctx = build()
    report: dict[str, Any] = {"protocol": v2.PROTOCOL_VERSION, "declared_at": spec.DECLARED_AT, "window": [spec.WINDOW_START.isoformat(), spec.WINDOW_END.isoformat()]}
    report["audit"] = audit(ctx)
    frame = cells(ctx)
    report["cells"] = int(len(frame))
    report["models"] = {}
    for h in horizons:
        step = time.perf_counter()
        predictions, folds, importance = walk_forward(ctx, frame, h)
        calls, eligible = make_calls(ctx, predictions, h)
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        eligible.to_parquet(ARTIFACTS / f"h{h}_eligible_scores.parquet", index=False)
        evaluation = grade_and_evaluate(ctx, spec.strategy_name(h), spec.VERSION, h, report["audit"]["leaky"], calls)
        report["models"][str(h)] = {"folds": folds, "importance_top": dict(sorted(importance.items(), key=lambda kv: -kv[1])[:12]),
                                    "evaluation": evaluation, "seconds": round(time.perf_counter() - step)}
        output.write_text(json.dumps(report, indent=1, default=str))
    report["runtime_seconds"] = round(time.perf_counter() - started)
    output.write_text(json.dumps(report, indent=1, default=str))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Train and evaluate the pre-registered ranker r1 walk-forward through the scorecard")
    parser.add_argument("--horizons", nargs="*", type=int, default=list(spec.HORIZONS))
    args = parser.parse_args()
    report = run(tuple(args.horizons))
    print(json.dumps({h: {"primary": {k: m["evaluation"]["primary"].get(k) for k in ("graded", "win_rate", "baseline", "edge", "edge_lower_90", "edge_lower_penalized", "verdict")},
                          "test_ic": [f["test_ic"] for f in m["folds"]]} for h, m in report["models"].items()}, indent=1, default=str))


if __name__ == "__main__":
    main()
