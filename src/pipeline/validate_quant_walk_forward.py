from __future__ import annotations

import argparse
import json
import logging
from statistics import mean
from typing import Any

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import Company
from src.services.nepse_quant_research import (
    NEPSE_INDEX_NAME,
    _load_index_series,
    _load_stock_series,
    _sector_index_name,
)
from src.services.quant_features import (
    DEFAULT_HORIZON_DAYS,
    FEATURE_VERSION,
    analyze_historical_analogs,
    build_labeled_feature_rows,
    build_probability_ensemble,
    evaluate_predictions,
    fit_ridge_logistic,
    predict_ridge_logistic,
)

logger = logging.getLogger(__name__)
MIN_WALK_FORWARD_TRAIN_ROWS = 80
DEFAULT_TEST_STEP = 4


def walk_forward_symbol(
    symbol: str,
    *,
    test_step: int = DEFAULT_TEST_STEP,
    min_train_rows: int = MIN_WALK_FORWARD_TRAIN_ROWS,
) -> dict[str, Any]:
    with get_session() as session:
        company = session.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
        if company is None:
            return {"symbol": symbol, "status": "unknown_symbol", "samples": 0}

        stock = _load_stock_series(session, symbol)
        market = _load_index_series(session, NEPSE_INDEX_NAME)
        sector_name = _sector_index_name(session, company.sector)
        sector = _load_index_series(session, sector_name)

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
        step=5,
    )

    predictions: list[float] = []
    outcomes: list[bool] = []
    excess_returns: list[float] = []
    prediction_rows: list[dict[str, Any]] = []

    for test_position in range(min_train_rows, len(rows), max(1, test_step)):
        test_row = rows[test_position]

        # Purge every row whose forward outcome would not have been known on the test date.
        train_rows = [
            row
            for row in rows[:test_position]
            if int(row["index"]) + DEFAULT_HORIZON_DAYS < int(test_row["index"])
        ]
        if len(train_rows) < min_train_rows:
            continue

        analogs = analyze_historical_analogs(test_row["features"], train_rows)
        model = fit_ridge_logistic(train_rows)
        logistic_probability = predict_ridge_logistic(model, test_row["features"])
        ensemble = build_probability_ensemble(
            analog_result=analogs,
            logistic_probability=logistic_probability,
            training_rows=len(train_rows),
        )
        probability = ensemble.get("probability_outperform_after_cost")
        if probability is None:
            continue

        predictions.append(float(probability))
        outcomes.append(bool(test_row["success"]))
        excess_returns.append(float(test_row["excess_return_percent"]))
        prediction_rows.append(
            {
                "date": test_row["date"].isoformat(),
                "probability": float(probability),
                "confidence_score": int(ensemble.get("confidence_score") or 0),
                "actual_success": bool(test_row["success"]),
                "actual_excess_return_percent": float(test_row["excess_return_percent"]),
                "train_rows": len(train_rows),
            }
        )

    metrics = evaluate_predictions(predictions, outcomes, excess_returns)
    return {
        "symbol": symbol,
        "sector": company.sector,
        "feature_version": FEATURE_VERSION,
        "status": "ready" if predictions else "insufficient_walk_forward_history",
        "raw_labeled_rows": len(rows),
        "samples": len(predictions),
        "first_test_date": prediction_rows[0]["date"] if prediction_rows else None,
        "last_test_date": prediction_rows[-1]["date"] if prediction_rows else None,
        "metrics": metrics,
        "recent_predictions": prediction_rows[-10:],
        "leakage_controls": {
            "expanding_window": True,
            "purge_forward_horizon": True,
            "future_normalization": False,
            "future_fundamentals": False,
            "future_macro": False,
            "target": "20D stock return minus NEPSE return after 0.50% cost hurdle",
        },
    }


def run_walk_forward_validation(
    symbols: list[str] | None = None,
    *,
    limit: int = 50,
    test_step: int = DEFAULT_TEST_STEP,
) -> dict[str, Any]:
    if symbols is None:
        with get_session() as session:
            symbols = list(
                session.execute(
                    select(Company.symbol)
                    .where(Company.instrument_type == "Equity", Company.status == "A")
                    .order_by(Company.symbol)
                    .limit(limit)
                ).scalars().all()
            )

    results: list[dict[str, Any]] = []
    for index, symbol in enumerate(symbols, 1):
        logger.info("Walk-forward validating %s (%d/%d)", symbol, index, len(symbols))
        try:
            results.append(walk_forward_symbol(symbol, test_step=test_step))
        except Exception as exc:
            logger.exception("Walk-forward validation failed for %s", symbol)
            results.append({"symbol": symbol, "status": "failed", "error": type(exc).__name__, "samples": 0})

    ready = [row for row in results if row.get("samples", 0) > 0]
    total_samples = sum(int(row.get("samples", 0)) for row in ready)
    weighted_brier = None
    weighted_precision = None
    if total_samples:
        brier_rows = [
            (row["metrics"].get("brier_score"), int(row["samples"]))
            for row in ready
            if row["metrics"].get("brier_score") is not None
        ]
        if brier_rows:
            weighted_brier = sum(float(value) * weight for value, weight in brier_rows) / sum(weight for _, weight in brier_rows)

        precision_rows = [
            (row["metrics"].get("high_confidence_precision"), row["metrics"].get("high_confidence_samples", 0))
            for row in ready
            if row["metrics"].get("high_confidence_precision") is not None
            and row["metrics"].get("high_confidence_samples", 0) > 0
        ]
        if precision_rows:
            weighted_precision = sum(float(value) * weight for value, weight in precision_rows) / sum(
                weight for _, weight in precision_rows
            )

    return {
        "feature_version": FEATURE_VERSION,
        "symbols_requested": len(symbols),
        "symbols_with_predictions": len(ready),
        "total_walk_forward_predictions": total_samples,
        "weighted_brier_score": weighted_brier,
        "weighted_high_confidence_precision": weighted_precision,
        "mean_symbol_brier_score": (
            mean(row["metrics"]["brier_score"] for row in ready if row["metrics"].get("brier_score") is not None)
            if any(row["metrics"].get("brier_score") is not None for row in ready)
            else None
        ),
        "results": results,
        "note": (
            "This is research validation only. Each test prediction trains exclusively on observations whose full forward "
            "outcome was already known at that historical date."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Leakage-safe walk-forward validation for the NEPSE quant research model")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols; blank selects active equities")
    parser.add_argument("--limit", type=int, default=50, help="Maximum auto-selected active equities")
    parser.add_argument("--test-step", type=int, default=DEFAULT_TEST_STEP, help="Evaluate every Nth historical feature row")
    args = parser.parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()] or None
    result = run_walk_forward_validation(symbols, limit=args.limit, test_step=args.test_step)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
