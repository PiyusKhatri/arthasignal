from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.quant_models import QuantModelSnapshot
from src.services.quant_xgboost import calibrate_probability, xgboost_available

ARTHA_XGB_MODEL_VERSION = "artha-xgb-cross-sectional-v1"
SHADOW_PREDICTION_VERSION = "nepse-quant-xgb-v2"
MODEL_HISTORY_SCAN_LIMIT = 12


def _load_booster(blob: bytes | None) -> Any | None:
    if not blob or not xgboost_available():
        return None
    import xgboost as xgb

    booster = xgb.Booster()
    booster.load_model(bytearray(blob))
    return booster


def _parse_metrics(row: QuantModelSnapshot) -> dict[str, Any]:
    try:
        return json.loads(row.metrics_json)
    except (TypeError, ValueError):
        return {}


def _is_promotable(row: QuantModelSnapshot) -> bool:
    return bool(_parse_metrics(row).get("promotion_gate", {}).get("promotable"))


def load_latest_quant_model(session: Session) -> dict[str, Any] | None:
    """Load the newest proven champion; retain newest snapshot as challenger metadata.

    A failed weekly challenger must never displace the last model that passed the
    untouched holdout promotion gate. If no snapshot has ever passed, the newest
    candidate is returned as non-promotable research only.
    """
    rows = session.execute(
        select(QuantModelSnapshot)
        .where(QuantModelSnapshot.model_version == ARTHA_XGB_MODEL_VERSION)
        .order_by(QuantModelSnapshot.created_at.desc(), QuantModelSnapshot.id.desc())
        .limit(MODEL_HISTORY_SCAN_LIMIT)
    ).scalars().all()
    if not rows:
        return None

    latest_candidate = rows[0]
    champion = next((row for row in rows if _is_promotable(row)), None)
    selected = champion or latest_candidate
    metrics = _parse_metrics(selected)
    candidate_metrics = _parse_metrics(latest_candidate)

    calibrator = None
    if selected.calibration_intercept is not None and selected.calibration_slope is not None:
        calibrator = {
            "intercept": float(selected.calibration_intercept),
            "slope": float(selected.calibration_slope),
        }

    return {
        "id": selected.id,
        "model_version": selected.model_version,
        "feature_version": selected.feature_version,
        "trained_through": selected.trained_through.isoformat(),
        "horizon_days": selected.horizon_days,
        "training_rows": selected.training_rows,
        "calibration_rows": selected.calibration_rows,
        "test_rows": selected.test_rows,
        "classifier": _load_booster(selected.classifier_blob),
        "ranker": _load_booster(selected.ranker_blob),
        "calibrator": calibrator,
        "metrics": metrics,
        "promotable": bool(metrics.get("promotion_gate", {}).get("promotable")),
        "champion_is_latest_candidate": selected.id == latest_candidate.id,
        "latest_candidate": {
            "id": latest_candidate.id,
            "trained_through": latest_candidate.trained_through.isoformat(),
            "created_at": latest_candidate.created_at.isoformat(),
            "promotable": bool(candidate_metrics.get("promotion_gate", {}).get("promotable")),
            "promotion_gate": candidate_metrics.get("promotion_gate", {}),
        },
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
        "champion_is_latest_candidate": model.get("champion_is_latest_candidate"),
        "latest_candidate": model.get("latest_candidate"),
    }
