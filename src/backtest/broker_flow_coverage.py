from __future__ import annotations

import argparse
import json
import logging
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from src.backtest import broker_flow_spec as spec
from src.backtest.broker_flow_features import (
    DERIVED_ROOT,
    FLOORSHEET_ROOT,
    FeatureInputs,
    build_feature_store,
    floorsheet_files,
    load_database_inputs,
)

logger = logging.getLogger(__name__)

FEATURES = [h.lower() for h in spec.HYPOTHESES]
DEFAULT_OUTPUT = Path("docs/broker_flow_coverage.json")
SENSITIVITY_YEAR = 2016
SENSITIVITY_DROP = 0.02


def _file_stats(files: list[tuple[date, Path]]) -> pd.DataFrame:
    con = duckdb.connect()
    paths = [path.as_posix() for _, path in files]
    frame = con.execute(
        f"""
        SELECT filename, count(symbol) AS trades, max(data_quality_gap_pct) AS gap_pct
        FROM read_parquet({paths!r}, union_by_name=true, filename=true)
        GROUP BY filename
        """
    ).df()
    by_path = {path.as_posix(): day for day, path in files}
    all_files = pd.DataFrame({"filename": paths})
    frame = all_files.merge(frame, on="filename", how="left").fillna({"trades": 0})
    frame["date"] = frame["filename"].map(by_path)
    frame["year"] = frame["date"].map(lambda d: d.year)
    return frame


def yearly_coverage(features: pd.DataFrame, files: list[tuple[date, Path]], prices: pd.DataFrame) -> list[dict[str, Any]]:
    stats = _file_stats(files)
    features = features.assign(year=features["date"].map(lambda d: d.year))
    sessions = prices.assign(year=prices["date"].map(lambda d: d.year)).groupby("year")["date"].nunique()
    rows = []
    for year, group in features.groupby("year"):
        eligible = group[group["eligible"]]
        files_year = stats[stats["year"] == year]
        missing = group[~group["in_companies"]]
        rows.append(
            {
                "year": int(year),
                "price_sessions": int(sessions.get(year, 0)),
                "floorsheet_files": int(len(files_year)),
                "empty_files": int((files_year["trades"] == 0).sum()),
                "mean_gap_pct": round(float(files_year["gap_pct"].mean()), 2) if files_year["gap_pct"].notna().any() else None,
                "symbol_days": int(len(group)),
                "eligible_symbol_days": int(len(eligible)),
                "eligible_symbols": int(eligible["symbol"].nunique()),
                "defined_on_eligible_pct": {
                    feature: round(float(eligible[feature].notna().mean() * 100), 1) if len(eligible) else None
                    for feature in FEATURES
                },
                "symbols_missing_from_companies": int(missing["symbol"].nunique()),
                "missing_symbol_days": int(len(missing)),
                "missing_trade_share_pct": round(float(missing["trades"].sum() / group["trades"].sum() * 100), 2),
                "missing_qty_share_pct": round(float(missing["qty"].sum() / group["qty"].sum() * 100), 2),
                "non_equity_symbol_days": int((group["in_companies"] & ~group["in_companies_equity"].fillna(False)).sum()),
            }
        )
    return rows


def missing_symbol_effect(features: pd.DataFrame) -> dict[str, Any]:
    missing = features[~features["in_companies"]]
    top = (
        missing.groupby("symbol")
        .agg(symbol_days=("date", "size"), first_date=("date", "min"), last_date=("date", "max"), trades=("trades", "sum"))
        .sort_values("symbol_days", ascending=False)
    )
    would_pass = missing[(missing["trades"] >= spec.MIN_TRADES_ON_SIGNAL_DAY) & missing["close"].notna()]
    return {
        "symbols": int(top.shape[0]),
        "symbol_days": int(len(missing)),
        "trade_share_pct": round(float(missing["trades"].sum() / features["trades"].sum() * 100), 3),
        "symbol_days_with_price_row_and_5_trades": int(len(would_pass)),
        "share_of_eligible_universe_if_admitted_pct": round(
            float(len(would_pass) / max(int(features["eligible"].sum()), 1) * 100), 3
        ),
        "largest": [
            {"symbol": symbol, "symbol_days": int(row.symbol_days), "first": str(row.first_date), "last": str(row.last_date)}
            for symbol, row in top.head(15).iterrows()
        ],
    }


def trade_loss_sensitivity(
    files: list[tuple[date, Path]],
    prices: pd.DataFrame,
    actions: pd.DataFrame,
    companies: pd.DataFrame,
    work: Path,
    year: int = SENSITIVITY_YEAR,
    drop: float = SENSITIVITY_DROP,
) -> dict[str, Any]:
    chosen = [(day, path) for day, path in files if day.year == year]
    copies = work / "dropped"
    if copies.exists():
        shutil.rmtree(copies)
    rng = np.random.default_rng(20261001)
    dropped_files = []
    for day, path in chosen:
        frame = pd.read_parquet(path)
        kept = frame[rng.random(len(frame)) >= drop]
        target = copies / f"year={day.year}" / f"month={day.month:02d}" / f"day={day.day:02d}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        kept.to_parquet(target, index=False)
        dropped_files.append((day, target))
    base = build_feature_store(FeatureInputs(chosen, prices, actions, companies), work / "base", memory_limit="3GB")
    thin = build_feature_store(FeatureInputs(dropped_files, prices, actions, companies), work / "thin", memory_limit="3GB")
    merged = base.merge(thin, on=["symbol", "date"], suffixes=("", "_thin"))
    merged = merged[merged["eligible"]]
    result: dict[str, Any] = {"year": year, "drop_fraction": drop, "eligible_rows": int(len(merged))}
    v5_base = merged["qty"]
    v5_thin = merged["qty_thin"]
    result["daily_quantity_median_abs_change_pct"] = round(float(((v5_thin / v5_base - 1).abs()).median() * 100), 3)
    result["daily_quantity_mean_change_pct"] = round(float((v5_thin / v5_base - 1).mean() * 100), 3)
    for feature in ("h1", "h2", "h3", "h5"):
        pair = merged[[feature, f"{feature}_thin"]].dropna()
        if pair.empty:
            continue
        scale = pair[feature].abs().median() or 1.0
        rank_base = pair.groupby(merged.loc[pair.index, "date"])[feature].rank(pct=True)
        rank_thin = pair.groupby(merged.loc[pair.index, "date"])[f"{feature}_thin"].rank(pct=True)
        top_base = rank_base > 1 - spec.QUINTILE if spec.HYPOTHESES[feature.upper()]["direction"] == "high" else rank_base <= spec.QUINTILE
        top_thin = rank_thin > 1 - spec.QUINTILE if spec.HYPOTHESES[feature.upper()]["direction"] == "high" else rank_thin <= spec.QUINTILE
        result[feature] = {
            "rows": int(len(pair)),
            "median_abs_change_over_median_abs_value": round(float((pair[feature] - pair[f"{feature}_thin"]).abs().median() / scale), 4),
            "mean_change": float((pair[f"{feature}_thin"] - pair[feature]).mean()),
            "spearman": round(float(pair[feature].rank().corr(pair[f"{feature}_thin"].rank())), 4),
            "portfolio_membership_agreement_pct": round(float((top_base == top_thin).mean() * 100), 2),
        }
    shutil.rmtree(work, ignore_errors=True)
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, default=DERIVED_ROOT / "features.parquet")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--work", type=Path, default=DERIVED_ROOT.parent / "broker_flow_sensitivity")
    args = parser.parse_args()
    if FLOORSHEET_ROOT.resolve() in args.work.resolve().parents:
        raise SystemExit("refusing to write inside the floorsheet directory")
    features = pd.read_parquet(args.features)
    features["date"] = pd.to_datetime(features["date"]).dt.date
    files = floorsheet_files()
    prices, actions, companies = load_database_inputs()
    report = {
        "generated_on": date.today().isoformat(),
        "development_end": spec.DEVELOPMENT_END.isoformat(),
        "files": len(files),
        "rows": int(len(features)),
        "eligible_rows": int(features["eligible"].sum()),
        "by_year": yearly_coverage(features, files, prices),
        "symbols_missing_from_companies": missing_symbol_effect(features),
        "trade_loss_sensitivity": trade_loss_sensitivity(files, prices, actions, companies, args.work),
    }
    early = pd.read_parquet(args.features.with_name("early_brokers.parquet"))
    report["h4_early_sets"] = {
        "refreshes_with_set": int(early["refresh_date"].nunique()),
        "first_refresh": str(early["refresh_date"].min()) if len(early) else None,
        "distinct_brokers_ever_selected": int(early["broker"].nunique()),
        "median_events_in_lookback": float(early.groupby("refresh_date")["n_events"].first().median()) if len(early) else None,
    }
    args.output.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "by_year"}, indent=2, default=str))
    for row in report["by_year"]:
        print(json.dumps(row))


if __name__ == "__main__":
    main()
