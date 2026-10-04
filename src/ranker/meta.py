from __future__ import annotations

import argparse
import json
import logging
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from src.ranker import meta_spec, spec, train
from src.scorecard import v2
from src.scorecard.ledger import feature_hash, load_graded, record_calls
from src.scorecard.situations import labels_for

logger = logging.getLogger(__name__)

OUTPUT = Path("docs/meta_results.json")


def primary_table(ctx: dict[str, Any], horizon: int) -> pd.DataFrame:
    from src.database.holdout_guard import engine

    graded = load_graded(engine, spec.strategy_name(horizon), spec.VERSION, horizon=horizon, grade_version=v2.GRADE_VERSION)
    graded = graded[graded["status"] != "data_error"].copy()
    eligible = pd.read_parquet(train.ARTIFACTS / f"h{horizon}_eligible_scores.parquet")
    panel = ctx["panel"]
    index = {d: i for i, d in enumerate(panel.sessions)}
    graded["t"] = graded["signal_date"].map(index)
    graded["r"] = graded["symbol"].map(panel.row)
    eleventh = eligible.sort_values(["t", "score"], ascending=[True, False]).groupby("t")["score"].apply(
        lambda s: s.iloc[spec.PICKS] if len(s) > spec.PICKS else s.iloc[-1]).rename("eleventh")
    merged = graded.merge(eligible[["r", "t", "score_rank"]], on=["r", "t"], how="left").merge(eleventh, left_on="t", right_index=True, how="left")
    merged["gap_to_11th"] = merged["score"] - merged["eleventh"]
    merged["pick_rank"] = merged.groupby("t")["score"].rank(ascending=False, method="first")
    r, t = merged["r"].to_numpy(dtype=int), merged["t"].to_numpy(dtype=int)
    for name in ("state_code", "breadth_sma50", "nepse_ret_20", "e2_bonus_20", "e4_streak_20"):
        merged[name] = ctx["raw"][name][r, t]
    for name in ("vol_20", "turnover_20", "ret_20"):
        merged[name] = ctx["ranked"][name][r, t]
    merged["exit_index"] = ctx["cubes"][horizon].exit_index[r, t]
    merged["label"] = merged["correct"].astype(float)
    return merged


def walk_forward(ctx: dict[str, Any], table: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    sessions = list(ctx["panel"].sessions)
    kept, folds = [], []
    features = list(meta_spec.META_FEATURES)
    for start, end in meta_spec.FOLDS:
        a = next(i for i, d in enumerate(sessions) if d >= start)
        b = max(i for i, d in enumerate(sessions) if d <= end)
        train_rows = table[(table["t"] < a) & (table["exit_index"] < a - meta_spec.EMBARGO_SESSIONS)].dropna(subset=features + ["label"])
        test_rows = table[(table["t"] >= a) & (table["t"] <= b)].dropna(subset=features)
        fold = {"test": [sessions[a].isoformat(), sessions[b].isoformat()], "train_calls": int(len(train_rows)),
                "train_last_exit": sessions[int(train_rows["exit_index"].max())].isoformat() if len(train_rows) else None}
        if len(train_rows) < meta_spec.MIN_TRAINING_CALLS or train_rows["label"].nunique() < 2:
            fold["skipped"] = "fewer than the minimum training calls"
            folds.append(fold)
            continue
        scaler = StandardScaler().fit(train_rows[features])
        model = LogisticRegression(**meta_spec.LOGISTIC).fit(scaler.transform(train_rows[features]), train_rows["label"])
        fitted = model.predict_proba(scaler.transform(train_rows[features]))[:, 1]
        tau = float(np.quantile(fitted, meta_spec.KEEP_QUANTILE))
        p = model.predict_proba(scaler.transform(test_rows[features]))[:, 1] if len(test_rows) else np.array([])
        chosen = test_rows.assign(probability=p)[p >= tau]
        kept.append(chosen)
        fold.update({"tau": round(tau, 4), "train_win_rate": round(float(train_rows["label"].mean()), 4), "test_calls": int(len(test_rows)),
                     "kept": int(len(chosen)), "purge_ok": bool(train_rows["exit_index"].max() < a - meta_spec.EMBARGO_SESSIONS),
                     "coefficients": {f: round(float(c), 4) for f, c in zip(features, model.coef_[0])}})
        folds.append(fold)
    return (pd.concat(kept, ignore_index=True) if kept else pd.DataFrame()), folds


def run(horizons: tuple[int, ...] = meta_spec.HORIZONS, output: Path = OUTPUT) -> dict[str, Any]:
    ctx = train.build()
    primary_leaky = bool(json.loads(train.OUTPUT.read_text())["audit"]["leaky"])
    report: dict[str, Any] = {"protocol": v2.PROTOCOL_VERSION, "declared_at": meta_spec.DECLARED_AT, "models": {},
                              "inherits_primary_audit": {"primary_leaky": primary_leaky}}
    for h in horizons:
        table = primary_table(ctx, h)
        chosen, folds = walk_forward(ctx, table, h)
        name = meta_spec.strategy_name(h)
        calls = pd.DataFrame([{
            "call_uid": str(uuid.uuid4()), "mode": "replay", "strategy": name, "model_version": meta_spec.VERSION,
            "feature_hash": feature_hash({"strategy": name, "version": meta_spec.VERSION, "symbol": s, "date": d, "p": round(float(p), 8)}),
            "batch_id": f"meta-m1-h{h}", "symbol": s, "signal_date": d, "probability": float(p), "score": float(sc),
            "situations": labels_for(ctx["situations"], int(ctx["panel"].row[s]), int(t)),
        } for s, d, p, sc, t in zip(chosen.get("symbol", []), chosen.get("signal_date", []), chosen.get("probability", []),
                                    chosen.get("score", []), chosen.get("t", []))])
        evaluation = train.grade_and_evaluate(ctx, name, meta_spec.VERSION, h, primary_leaky, calls)
        report["models"][str(h)] = {"folds": folds, "evaluation": evaluation}
        output.write_text(json.dumps(report, indent=1, default=str))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Train and evaluate the pre-registered meta-labeler m1")
    parser.add_argument("--horizons", nargs="*", type=int, default=list(meta_spec.HORIZONS))
    report = run(tuple(parser.parse_args().horizons))
    print(json.dumps({h: {k: m["evaluation"]["primary"].get(k) for k in ("graded", "win_rate", "baseline", "edge", "edge_lower_90", "calibration", "verdict")}
                      for h, m in report["models"].items()}, indent=1, default=str))


if __name__ == "__main__":
    main()
