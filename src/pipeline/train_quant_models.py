from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import Company
from src.database.quant_models import QuantModelSnapshot
from src.services.nepse_quant_research import NEPSE_INDEX_NAME, _load_index_series, _load_stock_series, _sector_index_name
from src.services.quant_features import (
    DEFAULT_HORIZON_DAYS,
    FEATURE_NAMES,
    FEATURE_VERSION,
    ROUND_TRIP_COST_PERCENT,
    build_labeled_feature_rows,
    evaluate_predictions,
    feature_vector,
    fit_ridge_logistic,
    predict_ridge_logistic,
)
from src.services.quant_model_store import ARTHA_XGB_MODEL_VERSION
from src.services.quant_xgboost import (
    calibrate_probability,
    chronological_three_way_split,
    fit_platt_calibrator,
    fit_xgb_classifier,
    fit_xgb_ranker,
    precision_at_k_by_date,
    predict_xgb_rows,
    xgboost_available,
)

logger = logging.getLogger(__name__)
DEFAULT_SYMBOL_LIMIT = 400
HISTORICAL_STEP = 5
MIN_POOLED_ROWS = 2500
MIN_TEST_ROWS = 500
MIN_TEST_DATES = 20
RETRAIN_INTERVAL_DAYS = 7


def model_training_due(now: datetime | None = None) -> dict[str, Any]:
    """Keep predictions daily while limiting model-parameter churn to a weekly cadence."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    with get_session() as session:
        latest = session.execute(
            select(QuantModelSnapshot)
            .where(QuantModelSnapshot.model_version == ARTHA_XGB_MODEL_VERSION)
            .order_by(QuantModelSnapshot.created_at.desc(), QuantModelSnapshot.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    if latest is None:
        return {
            "due": True,
            "reason": "no_existing_snapshot",
            "last_trained_at": None,
            "minimum_interval_days": RETRAIN_INTERVAL_DAYS,
        }

    last_created = latest.created_at
    due_at = last_created + timedelta(days=RETRAIN_INTERVAL_DAYS)
    return {
        "due": now >= due_at,
        "reason": "weekly_interval_elapsed" if now >= due_at else "recent_snapshot_still_current",
        "last_trained_at": last_created.isoformat(),
        "next_due_at": due_at.isoformat(),
        "minimum_interval_days": RETRAIN_INTERVAL_DAYS,
        "current_snapshot_promotable": bool(
            json.loads(latest.metrics_json).get("promotion_gate", {}).get("promotable")
            if latest.metrics_json
            else False
        ),
    }


def _build_pooled_rows(limit: int = DEFAULT_SYMBOL_LIMIT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with get_session() as session:
        companies = session.execute(
            select(Company)
            .where(Company.instrument_type == "Equity")
            .order_by(Company.symbol)
            .limit(limit)
        ).scalars().all()
        market = _load_index_series(session, NEPSE_INDEX_NAME)

        pooled: list[dict[str, Any]] = []
        symbols_used = 0
        symbols_skipped = 0
        failures: list[str] = []
        sector_cache: dict[str, dict[str, list[Any]]] = {}

        for index, company in enumerate(companies, 1):
            try:
                stock = _load_stock_series(session, company.symbol)
                if len(stock["closes"]) < 120:
                    symbols_skipped += 1
                    continue
                sector_name = _sector_index_name(session, company.sector)
                cache_key = sector_name or ""
                if cache_key not in sector_cache:
                    sector_cache[cache_key] = _load_index_series(session, sector_name)
                sector = sector_cache[cache_key]

                rows = build_labeled_feature_rows(
                    dates=stock["dates"],
                    closes=stock["closes"],
                    volumes=stock["volumes"],
                    turnovers=stock["turnovers"],
                    market_dates=market["dates"],
                    market_closes=market["closes"],
                    sector_dates=sector["dates"],
                    sector_closes=sector["closes"],
                    horizon_days=DEFAULT_HORIZON_DAYS,
                    step=HISTORICAL_STEP,
                )
                for row in rows:
                    end_index = int(row["index"]) + DEFAULT_HORIZON_DAYS
                    if end_index >= len(stock["dates"]):
                        continue
                    pooled.append(
                        {
                            **row,
                            "symbol": company.symbol,
                            "sector": company.sector,
                            "label_end_date": stock["dates"][end_index],
                        }
                    )
                if rows:
                    symbols_used += 1
            except Exception as exc:
                logger.exception("Failed to build pooled ML rows for %s", company.symbol)
                failures.append(f"{company.symbol}:{type(exc).__name__}")

            if index % 25 == 0:
                logger.info("Prepared pooled rows for %d/%d symbols (%d rows)", index, len(companies), len(pooled))

    pooled.sort(key=lambda row: (row["date"], row["symbol"]))
    return pooled, {
        "symbols_considered": len(companies),
        "symbols_used": symbols_used,
        "symbols_skipped": symbols_skipped,
        "failures": len(failures),
        "failure_samples": failures[:10],
        "market_index_rows": len(market["closes"]),
        "survivorship_note": (
            "The trainer includes every Equity company currently present in the local company master, not only active names. "
            "Historical delisted/suspended coverage is still incomplete, so holdout metrics must retain the repository's known survivor-bias caveat."
        ),
    }


def _rank_scores(model: Any | None, rows: list[dict[str, Any]]) -> list[float]:
    if model is None or not rows or not xgboost_available():
        return []
    import xgboost as xgb

    matrix = xgb.DMatrix(
        [feature_vector(row["features"]) for row in rows],
        feature_names=list(FEATURE_NAMES),
    )
    return [float(value) for value in model.predict(matrix)]


def _baseline_metrics(train: list[dict[str, Any]], test: list[dict[str, Any]]) -> dict[str, Any]:
    ridge = fit_ridge_logistic(train)
    ridge_predictions = [predict_ridge_logistic(ridge, row["features"]) for row in test]
    valid = [(row, probability) for row, probability in zip(test, ridge_predictions) if probability is not None]
    if valid:
        ridge_metrics = evaluate_predictions(
            [float(probability) for _, probability in valid],
            [bool(row["success"]) for row, _ in valid],
            [float(row["excess_return_percent"]) for row, _ in valid],
        )
    else:
        ridge_metrics = evaluate_predictions([], [])

    momentum_scores = [float(row["features"].get("relative_strength_market_20d", 0.0)) for row in test]
    momentum_rank = precision_at_k_by_date(test, momentum_scores, k=10)
    return {"ridge_logistic": ridge_metrics, "momentum_rank_p10": momentum_rank}


def train_quant_models(limit: int = DEFAULT_SYMBOL_LIMIT) -> dict[str, Any]:
    if not xgboost_available():
        return {
            "status": "xgboost_unavailable",
            "model_version": ARTHA_XGB_MODEL_VERSION,
            "note": "Install requirements.txt before training the cross-sectional model.",
        }

    pooled, universe = _build_pooled_rows(limit=limit)
    if len(pooled) < MIN_POOLED_ROWS:
        return {
            "status": "insufficient_pooled_history",
            "model_version": ARTHA_XGB_MODEL_VERSION,
            "pooled_rows": len(pooled),
            "universe": universe,
        }

    split = chronological_three_way_split(pooled)
    train_rows = split["train"]
    calibration_rows = split["calibration"]
    test_rows = split["test"]
    if min(len(train_rows), len(calibration_rows), len(test_rows)) == 0:
        return {
            "status": "insufficient_chronological_split",
            "model_version": ARTHA_XGB_MODEL_VERSION,
            "pooled_rows": len(pooled),
            "split_sizes": {key: len(value) for key, value in split.items()},
            "universe": universe,
        }

    logger.info(
        "Training candidate model on %d rows; calibration=%d test=%d",
        len(train_rows),
        len(calibration_rows),
        len(test_rows),
    )
    candidate_classifier = fit_xgb_classifier(train_rows)
    candidate_ranker = fit_xgb_ranker(train_rows)
    if candidate_classifier is None or candidate_ranker is None:
        return {
            "status": "candidate_training_failed",
            "model_version": ARTHA_XGB_MODEL_VERSION,
            "split_sizes": {key: len(value) for key, value in split.items()},
            "universe": universe,
        }

    calibration_raw = predict_xgb_rows(candidate_classifier, calibration_rows)
    calibrator = fit_platt_calibrator(calibration_raw, [bool(row["success"]) for row in calibration_rows])

    test_raw = predict_xgb_rows(candidate_classifier, test_rows)
    test_calibrated = [calibrate_probability(value, calibrator) for value in test_raw]
    calibrated_pairs = [
        (row, probability)
        for row, probability in zip(test_rows, test_calibrated)
        if probability is not None
    ]
    test_metrics = evaluate_predictions(
        [float(probability) for _, probability in calibrated_pairs],
        [bool(row["success"]) for row, _ in calibrated_pairs],
        [float(row["excess_return_percent"]) for row, _ in calibrated_pairs],
    )
    raw_metrics = evaluate_predictions(
        test_raw,
        [bool(row["success"]) for row in test_rows],
        [float(row["excess_return_percent"]) for row in test_rows],
    )

    rank_scores = _rank_scores(candidate_ranker, test_rows)
    rank_metrics = precision_at_k_by_date(test_rows, rank_scores, k=10)
    baselines = _baseline_metrics(train_rows, test_rows)
    ridge_brier = baselines["ridge_logistic"].get("brier_score")
    model_brier = test_metrics.get("brier_score")
    momentum_p10 = baselines["momentum_rank_p10"].get("p_at_k")
    model_p10 = rank_metrics.get("p_at_k")
    high_precision = test_metrics.get("high_confidence_precision")
    unique_test_dates = len({row["date"] for row in test_rows})

    promotion_checks = {
        "enough_test_rows": len(test_rows) >= MIN_TEST_ROWS,
        "enough_test_dates": unique_test_dates >= MIN_TEST_DATES,
        "calibration_available": calibrator is not None,
        "brier_not_worse_than_ridge": (
            model_brier is not None and (ridge_brier is None or float(model_brier) <= float(ridge_brier))
        ),
        "rank_p10_not_worse_than_momentum": (
            model_p10 is not None and (momentum_p10 is None or float(model_p10) >= float(momentum_p10))
        ),
        "high_confidence_precision_at_least_58pct": (
            high_precision is not None and float(high_precision) >= 0.58
        ),
    }
    promotable = all(promotion_checks.values())

    # After untouched evaluation, fit the stored candidate on every matured historical observation.
    # Live inference still ignores it unless the untouched holdout promotion gate above passed.
    production_classifier = fit_xgb_classifier(pooled)
    production_ranker = fit_xgb_ranker(pooled)
    if production_classifier is None or production_ranker is None:
        return {
            "status": "production_training_failed",
            "model_version": ARTHA_XGB_MODEL_VERSION,
            "promotion_checks": promotion_checks,
        }

    trained_through = max(row["date"] for row in pooled)
    metrics = {
        "model_version": ARTHA_XGB_MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "target": (
            f"{DEFAULT_HORIZON_DAYS}D stock excess return vs NEPSE > "
            f"{ROUND_TRIP_COST_PERCENT:.2f}% cost hurdle"
        ),
        "split": {
            "train_rows": len(train_rows),
            "calibration_rows": len(calibration_rows),
            "test_rows": len(test_rows),
            "test_dates": unique_test_dates,
            "train_last_label_end": max(row["label_end_date"] for row in train_rows).isoformat(),
            "calibration_first_date": min(row["date"] for row in calibration_rows).isoformat(),
            "calibration_last_label_end": max(row["label_end_date"] for row in calibration_rows).isoformat(),
            "test_first_date": min(row["date"] for row in test_rows).isoformat(),
        },
        "test": {
            "calibrated_classifier": test_metrics,
            "raw_classifier": raw_metrics,
            "rank_p10": rank_metrics,
        },
        "baselines": baselines,
        "promotion_gate": {"promotable": promotable, "checks": promotion_checks},
        "universe": universe,
        "leakage_controls": {
            "chronological_split": True,
            "purged_forward_labels": True,
            "held_out_calibration_period": True,
            "untouched_test_period": True,
            "point_in_time_price_volume_features": True,
            "fundamentals_excluded_until_timestamp_verified": True,
            "macro_excluded_until_timestamp_verified": True,
        },
    }

    classifier_blob = bytes(production_classifier.save_raw(raw_format="json"))
    ranker_blob = bytes(production_ranker.save_raw(raw_format="json"))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    values = {
        "model_version": ARTHA_XGB_MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "trained_through": trained_through,
        "horizon_days": DEFAULT_HORIZON_DAYS,
        "training_rows": len(pooled),
        "calibration_rows": len(calibration_rows),
        "test_rows": len(test_rows),
        "classifier_blob": classifier_blob,
        "ranker_blob": ranker_blob,
        "calibration_intercept": calibrator.get("intercept") if calibrator else None,
        "calibration_slope": calibrator.get("slope") if calibrator else None,
        "metrics_json": json.dumps(metrics, default=str, sort_keys=True),
        "created_at": now,
    }

    with get_session() as session:
        stmt = pg_insert(QuantModelSnapshot).values(values)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["model_version", "trained_through"]
        ).returning(QuantModelSnapshot.id)
        inserted = session.execute(stmt).scalar_one_or_none()

    return {
        "status": "trained",
        "model_version": ARTHA_XGB_MODEL_VERSION,
        "trained_through": trained_through.isoformat(),
        "pooled_rows": len(pooled),
        "snapshot_inserted": inserted is not None,
        "snapshot_id": inserted,
        "promotion_gate": metrics["promotion_gate"],
        "test": metrics["test"],
        "baselines": metrics["baselines"],
        "universe": universe,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train leakage-safe pooled XGBoost models for ArthaSignal")
    parser.add_argument("--limit", type=int, default=DEFAULT_SYMBOL_LIMIT, help="Maximum equity symbols to include")
    parser.add_argument(
        "--if-due",
        action="store_true",
        help="Skip the expensive retrain when a snapshot was created less than seven days ago",
    )
    args = parser.parse_args()

    due_check = model_training_due() if args.if_due else None
    if due_check is not None and not due_check["due"]:
        print(
            json.dumps(
                {
                    "status": "not_due",
                    "model_version": ARTHA_XGB_MODEL_VERSION,
                    "retrain": due_check,
                },
                indent=2,
                default=str,
            )
        )
        return

    result = train_quant_models(limit=args.limit)
    if due_check is not None:
        result["retrain"] = due_check
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
