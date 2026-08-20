from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.quant_models import QuantModelSnapshot
from src.services.quant_xgboost import calibrate_probability, xgboost_available

ARTHA_XGB_MODEL_VERSION = "artha-xgb-cross-sectional-v1"
SHADOW_PREDICTION_VERSION = "nepse-quant-xgb-v2"


def _load_booster(blob: bytes | None) -> Any | None:
    if not blob or not xgboost_available():
        return None
    import xgboost as xgb

    booster = xgb.Booster()
    booster.load_model(bytearray(blob))
    return booster


def load_latest_quant_model(session: Session) -> dict[str, Any] | None:
    row = session.execute(
        select(QuantModelSnapshot)
        .where(QuantModelSnapshot.model_version == ARTHA_XGB_MODEL_VERSION)
        .order_by(QuantModelSnapshot.trained_through.desc(), QuantModelSnapshot.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None

    calibrator = None
    if row.calibration_intercept is not None and row.calibration_slope is not None:
        calibrator = {
            "intercept": float(row.calibration_intercept),
            "slope": float(row.calibration_slope),
        }

    try:
        metrics = json.loads(row.metrics_json)
    except (TypeError, ValueError):
        metrics = {}

    return {
        "id": row.id,
        "model_version": row.model_version,
        "feature_version": row.feature_version,
        "trained_through": row.trained_through.isoformat(),
        "horizon_days": row.horizon_days,
        "training_rows": row.training_rows,
        "calibration_rows": row.calibration_rows,
        "test_rows": row.test_rows,
        "classifier": _load_booster(row.classifier_blob),
        "ranker": _load_booster(row.ranker_blob),
        "calibrator": calibrator,
        "metrics": metrics,
        "promotable": bool(metrics.get("promotion_gate", {}).get("promotable")),
    }


def predict_persisted_quant_model(
    model: dict[str, Any] | None,
    features: dict[str, float],
) -> dict[str, Any]:
    if not model or not xgboost_available():
        return {
            "available": False,
            "candidate_available": False,
            "promotable": False,
            "model_version": model.get("model_version") if model else ARTHA_XGB_MODEL_VERSION,
            "raw_probability": None,
            "calibrated_probability": None,
            "rank_score": None,
        }

    import xgboost as xgb
    from src.services.quant_features import FEATURE_NAMES, feature_vector

    matrix = xgb.DMatrix([feature_vector(features)], feature_names=list(FEATURE_NAMES))
    classifier = model.get("classifier")
    ranker = model.get("ranker")
    raw_probability = None
    rank_score = None
    if classifier is not None:
        predictions = classifier.predict(matrix)
        if len(predictions):
            raw_probability = float(predictions[0])
    if ranker is not None:
        rankings = ranker.predict(matrix)
        if len(rankings):
            rank_score = float(rankings[0])

    calibrated = calibrate_probability(raw_probability, model.get("calibrator"))
    promotable = bool(model.get("promotable"))
    return {
        "available": promotable and calibrated is not None,
        "candidate_available": calibrated is not None,
        "promotable": promotable,
        "model_version": model.get("model_version"),
        "trained_through": model.get("trained_through"),
        "raw_probability": raw_probability,
        "calibrated_probability": calibrated,
        "rank_score": rank_score,
        "holdout_metrics": model.get("metrics", {}).get("test", {}),
        "promotion_gate": model.get("metrics", {}).get("promotion_gate", {}),
    }
