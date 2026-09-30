from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.database.connection import get_session
from src.database.quant_models import QuantV41ModelSnapshot
from src.pipeline.train_quant_models import DEFAULT_SYMBOL_LIMIT, _build_pooled_rows
from src.services.quant_historical_context import attach_exact_regime_context
from src.services.quant_predictable_investability import prepare_predictable_investable_rows
from src.services.quant_residual_alpha import (
    V4_META_FEATURE_NAMES,
    apply_affine_calibrator,
    attach_residual_targets,
    build_baseline_candidate_pool,
    fit_affine_calibrator,
    fit_baseline_expectation,
    fit_v4_classifier,
    fit_v4_regressor,
    predict_baseline_expectation,
    predict_v4_classifier,
    predict_v4_regressor,
    v4_feature_vector,
)
from src.services.quant_residual_stability import (
    V41_POLICY_VERSION,
    apply_monotonic_residual_calibrator,
    build_v41_predictions,
    fit_monotonic_residual_calibrator,
    select_v41_setups,
)
from src.services.quant_xgboost import calibrate_probability, fit_platt_calibrator, xgboost_available

V41_FROZEN_MODEL_VERSION = "artha-residual-alpha-v4.1-shadow-v1"
V41_FEATURE_VERSION = "nepse-v41-residual-stability-v1"
V41_HORIZON_DAYS = 20
V41_CALIBRATION_FRACTION = 0.15
MIN_V41_TRAIN_CANDIDATES = 1000
MIN_V41_CALIBRATION_CANDIDATES = 200


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _encode_baseline(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "detailed": [
            {"market": key[0], "sector": key[1], "bucket": key[2], "value": value}
            for key, value in sorted(model.get("detailed", {}).items())
        ],
        "market_bucket": [
            {"market": key[0], "bucket": key[1], "value": value}
            for key, value in sorted(model.get("market_bucket", {}).items())
        ],
        "bucket_only": model.get("bucket_only", {}),
        "overall": model.get("overall", 0.0),
        "training_candidates": model.get("training_candidates", 0),
        "min_cell_rows": model.get("min_cell_rows", 20),
    }


def _decode_baseline(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "detailed": {
            (str(item["market"]), str(item["sector"]), str(item["bucket"])): float(item["value"])
            for item in payload.get("detailed", [])
        },
        "market_bucket": {
            (str(item["market"]), str(item["bucket"])): float(item["value"])
            for item in payload.get("market_bucket", [])
        },
        "bucket_only": {str(key): float(value) for key, value in payload.get("bucket_only", {}).items()},
        "overall": float(payload.get("overall") or 0.0),
        "training_candidates": int(payload.get("training_candidates") or 0),
        "min_cell_rows": int(payload.get("min_cell_rows") or 20),
    }


def _train_calibration_split(rows: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any | None]:
    dates = sorted({row["date"] for row in rows})
    if len(dates) < 100:
        return [], [], None
    cut_index = max(1, min(len(dates) - 1, int(len(dates) * (1.0 - V41_CALIBRATION_FRACTION))))
    calibration_start = dates[cut_index]
    train = [
        row
        for row in rows
        if row["date"] < calibration_start
        and row.get("label_end_date") is not None
        and row["label_end_date"] < calibration_start
    ]
    calibration = [row for row in rows if row["date"] >= calibration_start]
    return train, calibration, calibration_start


def _raw_model_bytes(model: Any) -> bytes:
    return bytes(model.save_raw(raw_format="json"))


def _load_booster(blob: bytes) -> Any:
    import xgboost as xgb

    model = xgb.Booster()
    model.load_model(bytearray(blob))
    return model


def _fingerprint(parts: Sequence[bytes | str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        payload = part if isinstance(part, bytes) else part.encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _existing_snapshot(session: Session) -> QuantV41ModelSnapshot | None:
    return session.execute(
        select(QuantV41ModelSnapshot)
        .where(QuantV41ModelSnapshot.model_version == V41_FROZEN_MODEL_VERSION)
        .limit(1)
    ).scalar_one_or_none()


def train_frozen_v41_model(limit: int = DEFAULT_SYMBOL_LIMIT) -> dict[str, Any]:
    """Train the one write-once V4.1 artifact used by the forward ledger.

    This command is intentionally idempotent. Once the model version exists it
    is returned unchanged; changing parameters requires a new version constant.
    """
    if not xgboost_available():
        return {"status": "xgboost_unavailable", "model_version": V41_FROZEN_MODEL_VERSION}

    with get_session() as session:
        existing = _existing_snapshot(session)
        if existing is not None:
            return {
                "status": "already_frozen",
                "snapshot_id": existing.id,
                "model_version": existing.model_version,
                "trained_through": existing.trained_through.isoformat(),
                "artifact_fingerprint": existing.artifact_fingerprint,
            }

    pooled, universe = _build_pooled_rows(limit=limit)
    attach_exact_regime_context(pooled)
    investable, investability = prepare_predictable_investable_rows(pooled)
    raw_train, raw_calibration, calibration_start = _train_calibration_split(investable)
    if calibration_start is None:
        return {"status": "insufficient_history", "pooled_rows": len(pooled), "universe": universe}

    train_candidates = build_baseline_candidate_pool(raw_train)
    calibration_candidates = build_baseline_candidate_pool(raw_calibration)
    if len(train_candidates) < MIN_V41_TRAIN_CANDIDATES or len(calibration_candidates) < MIN_V41_CALIBRATION_CANDIDATES:
        return {
            "status": "insufficient_candidates",
            "train_candidates": len(train_candidates),
            "calibration_candidates": len(calibration_candidates),
            "investability": investability,
        }

    baseline_model = fit_baseline_expectation(train_candidates)
    train = attach_residual_targets(train_candidates, baseline_model)
    calibration = attach_residual_targets(calibration_candidates, baseline_model)

    residual_model = fit_v4_regressor(train, target_key="residual_alpha_percent", clip_low=-25.0, clip_high=25.0)
    residual_classifier = fit_v4_classifier(train)
    mae_model = fit_v4_regressor(train, target_key="mae_magnitude_percent", clip_low=0.0, clip_high=25.0)
    mfe_model = fit_v4_regressor(train, target_key="mfe_percent", clip_low=0.0, clip_high=35.0)
    if any(model is None for model in (residual_model, residual_classifier, mae_model, mfe_model)):
        return {"status": "training_failed", "model_version": V41_FROZEN_MODEL_VERSION}

    residual_raw = predict_v4_regressor(residual_model, calibration)
    residual_calibrator = fit_monotonic_residual_calibrator(
        residual_raw,
        [float(row["residual_alpha_percent"]) for row in calibration],
    )
    probability_raw = predict_v4_classifier(residual_classifier, calibration)
    probability_calibrator = fit_platt_calibrator(
        probability_raw,
        [bool(row["residual_positive"]) for row in calibration],
    )
    mae_raw = predict_v4_regressor(mae_model, calibration)
    mae_calibrator = fit_affine_calibrator(mae_raw, [float(row["mae_magnitude_percent"]) for row in calibration])
    mfe_raw = predict_v4_regressor(mfe_model, calibration)
    mfe_calibrator = fit_affine_calibrator(mfe_raw, [float(row["mfe_percent"]) for row in calibration])
    if any(value is None for value in (residual_calibrator, probability_calibrator, mae_calibrator, mfe_calibrator)):
        return {"status": "calibration_failed", "model_version": V41_FROZEN_MODEL_VERSION}

    residual_blob = _raw_model_bytes(residual_model)
    classifier_blob = _raw_model_bytes(residual_classifier)
    mae_blob = _raw_model_bytes(mae_model)
    mfe_blob = _raw_model_bytes(mfe_model)
    encoded_baseline = _encode_baseline(baseline_model)
    metadata = {
        "model_version": V41_FROZEN_MODEL_VERSION,
        "policy_version": V41_POLICY_VERSION,
        "feature_version": V41_FEATURE_VERSION,
        "frozen": True,
        "historical_model_changed": False,
        "calibration_start": calibration_start.isoformat(),
        "core_training_through": max(row["date"] for row in train).isoformat(),
        "calibration_through": max(row["date"] for row in calibration).isoformat(),
        "train_candidates": len(train),
        "calibration_candidates": len(calibration),
        "candidate_first_training": True,
        "training_only_baseline_expectation": True,
        "calibration_only_monotonic_residual_map": True,
        "future_shadow_only": True,
        "universe": universe,
        "investability": investability,
    }
    baseline_json = _json_dumps(encoded_baseline)
    residual_calibrator_json = _json_dumps(residual_calibrator)
    probability_calibrator_json = _json_dumps(probability_calibrator)
    mae_calibrator_json = _json_dumps(mae_calibrator)
    mfe_calibrator_json = _json_dumps(mfe_calibrator)
    metadata_json = _json_dumps(metadata)
    fingerprint = _fingerprint(
        [
            residual_blob,
            classifier_blob,
            mae_blob,
            mfe_blob,
            baseline_json,
            residual_calibrator_json,
            probability_calibrator_json,
            mae_calibrator_json,
            mfe_calibrator_json,
            metadata_json,
        ]
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    trained_through = max(row["date"] for row in calibration)
    values = {
        "model_version": V41_FROZEN_MODEL_VERSION,
        "policy_version": V41_POLICY_VERSION,
        "feature_version": V41_FEATURE_VERSION,
        "trained_through": trained_through,
        "core_training_rows": len(train),
        "calibration_rows": len(calibration),
        "residual_regressor_blob": residual_blob,
        "residual_classifier_blob": classifier_blob,
        "mae_regressor_blob": mae_blob,
        "mfe_regressor_blob": mfe_blob,
        "baseline_expectation_json": baseline_json,
        "residual_calibrator_json": residual_calibrator_json,
        "probability_calibrator_json": probability_calibrator_json,
        "mae_calibrator_json": mae_calibrator_json,
        "mfe_calibrator_json": mfe_calibrator_json,
        "metadata_json": metadata_json,
        "artifact_fingerprint": fingerprint,
        "created_at": now,
    }
    with get_session() as session:
        stmt = pg_insert(QuantV41ModelSnapshot).values(values)
        stmt = stmt.on_conflict_do_nothing(index_elements=["model_version"]).returning(QuantV41ModelSnapshot.id)
        snapshot_id = session.execute(stmt).scalar_one_or_none()
        if snapshot_id is None:
            existing = _existing_snapshot(session)
            snapshot_id = existing.id if existing is not None else None

    return {
        "status": "frozen" if snapshot_id is not None else "freeze_failed",
        "snapshot_id": snapshot_id,
        "model_version": V41_FROZEN_MODEL_VERSION,
        "policy_version": V41_POLICY_VERSION,
        "trained_through": trained_through.isoformat(),
        "core_training_rows": len(train),
        "calibration_rows": len(calibration),
        "artifact_fingerprint": fingerprint,
        "investability": investability,
        "universe": universe,
    }


def load_frozen_v41_model(session: Session) -> dict[str, Any] | None:
    row = _existing_snapshot(session)
    if row is None or not xgboost_available():
        return None
    return {
        "snapshot_id": row.id,
        "model_version": row.model_version,
        "policy_version": row.policy_version,
        "feature_version": row.feature_version,
        "trained_through": row.trained_through,
        "artifact_fingerprint": row.artifact_fingerprint,
        "residual_model": _load_booster(row.residual_regressor_blob),
        "residual_classifier": _load_booster(row.residual_classifier_blob),
        "mae_model": _load_booster(row.mae_regressor_blob),
        "mfe_model": _load_booster(row.mfe_regressor_blob),
        "baseline_model": _decode_baseline(json.loads(row.baseline_expectation_json)),
        "residual_calibrator": json.loads(row.residual_calibrator_json),
        "probability_calibrator": json.loads(row.probability_calibrator_json),
        "mae_calibrator": json.loads(row.mae_calibrator_json),
        "mfe_calibrator": json.loads(row.mfe_calibrator_json),
        "metadata": json.loads(row.metadata_json),
    }


def score_v41_candidate_rows(model: dict[str, Any], candidate_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Score already feature-only baseline candidates with the frozen artifact."""
    baseline_model = model["baseline_model"]
    rows: list[dict[str, Any]] = []
    for candidate in candidate_rows:
        expected = predict_baseline_expectation(baseline_model, candidate)
        rows.append({**candidate, "baseline_expected_excess_percent": expected})
    if not rows:
        return {"predictions": [], "selection": {"selected": [], "days": []}}

    residual_raw = predict_v4_regressor(model["residual_model"], rows)
    probability_raw = predict_v4_classifier(model["residual_classifier"], rows)
    mae_raw = predict_v4_regressor(model["mae_model"], rows)
    mfe_raw = predict_v4_regressor(model["mfe_model"], rows)
    if not (len(rows) == len(residual_raw) == len(probability_raw) == len(mae_raw) == len(mfe_raw)):
        return {"predictions": [], "selection": {"selected": [], "days": []}}

    residual = [
        apply_monotonic_residual_calibrator(value, model["residual_calibrator"])
        for value in residual_raw
    ]
    probability = [
        calibrate_probability(value, model["probability_calibrator"])
        for value in probability_raw
    ]
    if any(value is None for value in probability):
        return {"predictions": [], "selection": {"selected": [], "days": []}}
    mae = [max(0.0, apply_affine_calibrator(value, model["mae_calibrator"])) for value in mae_raw]
    mfe = [max(0.0, apply_affine_calibrator(value, model["mfe_calibrator"])) for value in mfe_raw]

    predictions = build_v41_predictions(
        rows,
        residual,
        [float(value) for value in probability if value is not None],
        mae,
        mfe,
    )
    return {"predictions": predictions, "selection": select_v41_setups(predictions)}
