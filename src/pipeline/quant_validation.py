from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Any, Iterable

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from src.database.connection import get_session
from src.database.quant_models import QuantShadowSignal
from src.services.quant_features import evaluate_predictions
from src.services.quant_model_store import SHADOW_PREDICTION_VERSION

QUANT_VALIDATION_POLICY_VERSION = "2026-08-21-xgb-v2"
QUANT_VALIDATION_START_DATE = date(2026, 8, 21)
HIGH_CONFIDENCE_THRESHOLD = 0.65
MIN_RESOLVED_CALLS = 80
MIN_INDEPENDENT_DAYS = 20
MIN_HIGH_CONFIDENCE_CALLS = 30
MAX_BRIER_SCORE = 0.235
MIN_HIGH_CONFIDENCE_PRECISION = 0.58
MIN_HIGH_CONFIDENCE_WILSON_LOW = 0.52
MIN_HIGH_CONFIDENCE_MEAN_EXCESS_RETURN = 0.0


def _wilson_lower_bound(successes: int, total: int, z: float = 1.959963984540054) -> float | None:
    if total <= 0:
        return None
    p = successes / total
    denominator = 1.0 + z * z / total
    center = p + z * z / (2.0 * total)
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return (center - margin) / denominator


def _load_rows(session: Session) -> list[QuantShadowSignal]:
    return (
        session.execute(
            select(QuantShadowSignal)
            .where(
                QuantShadowSignal.feature_version == SHADOW_PREDICTION_VERSION,
                QuantShadowSignal.as_of_date >= QUANT_VALIDATION_START_DATE,
            )
            .order_by(QuantShadowSignal.as_of_date, QuantShadowSignal.symbol)
        )
        .scalars()
        .all()
    )


def evaluate_quant_validation_rows(rows: Iterable[Any]) -> dict[str, Any]:
    rows = list(rows)
    resolved = [
        row
        for row in rows
        if row.status == "resolved"
        and row.success_after_cost is not None
        and row.realized_excess_return_percent is not None
    ]
    pending = [row for row in rows if row.status == "pending"]
    voided = [row for row in rows if row.status == "void"]

    predictions = [float(row.probability_outperform) for row in resolved]
    outcomes = [bool(row.success_after_cost) for row in resolved]
    excess_returns = [float(row.realized_excess_return_percent) for row in resolved]
    metrics = evaluate_predictions(
        predictions,
        outcomes,
        excess_returns,
        high_confidence_threshold=HIGH_CONFIDENCE_THRESHOLD,
    )

    high_rows = [row for row in resolved if float(row.probability_outperform) >= HIGH_CONFIDENCE_THRESHOLD]
    high_successes = sum(1 for row in high_rows if row.success_after_cost)
    high_wilson_low = _wilson_lower_bound(high_successes, len(high_rows))
    independent_days = len({row.as_of_date for row in resolved})

    decision_counts: dict[str, int] = defaultdict(int)
    for row in resolved:
        decision_counts[row.decision] += 1

    sample_ready = len(resolved) >= MIN_RESOLVED_CALLS
    days_ready = independent_days >= MIN_INDEPENDENT_DAYS
    high_sample_ready = len(high_rows) >= MIN_HIGH_CONFIDENCE_CALLS
    ready = sample_ready and days_ready and high_sample_ready

    pass_checks = {
        "brier_score": metrics.get("brier_score") is not None and metrics["brier_score"] <= MAX_BRIER_SCORE,
        "high_confidence_precision": (
            metrics.get("high_confidence_precision") is not None
            and metrics["high_confidence_precision"] >= MIN_HIGH_CONFIDENCE_PRECISION
        ),
        "high_confidence_wilson_low": (
            high_wilson_low is not None and high_wilson_low >= MIN_HIGH_CONFIDENCE_WILSON_LOW
        ),
        "high_confidence_mean_excess_return": (
            metrics.get("high_confidence_mean_excess_return_percent") is not None
            and metrics["high_confidence_mean_excess_return_percent"] > MIN_HIGH_CONFIDENCE_MEAN_EXCESS_RETURN
        ),
    }

    if not ready:
        gate_status = "collecting"
    elif all(pass_checks.values()):
        gate_status = "pass"
    else:
        gate_status = "fail"

    return {
        "policy_version": QUANT_VALIDATION_POLICY_VERSION,
        "prediction_version": SHADOW_PREDICTION_VERSION,
        "protocol_start_date": QUANT_VALIDATION_START_DATE.isoformat(),
        "gate_status": gate_status,
        "public_high_confidence_enabled": gate_status == "pass",
        "storage_ready": True,
        "resolved_calls": len(resolved),
        "pending_calls": len(pending),
        "void_calls": len(voided),
        "independent_entry_days": independent_days,
        "high_confidence_calls": len(high_rows),
        "requirements": {
            "min_resolved_calls": MIN_RESOLVED_CALLS,
            "min_independent_entry_days": MIN_INDEPENDENT_DAYS,
            "min_high_confidence_calls": MIN_HIGH_CONFIDENCE_CALLS,
            "high_confidence_probability_threshold": HIGH_CONFIDENCE_THRESHOLD,
            "max_brier_score": MAX_BRIER_SCORE,
            "min_high_confidence_precision": MIN_HIGH_CONFIDENCE_PRECISION,
            "min_high_confidence_wilson_low": MIN_HIGH_CONFIDENCE_WILSON_LOW,
            "min_high_confidence_mean_excess_return_percent": MIN_HIGH_CONFIDENCE_MEAN_EXCESS_RETURN,
        },
        "pass_checks": pass_checks,
        "high_confidence_wilson_low": high_wilson_low,
        "decision_counts": dict(decision_counts),
        "metrics": metrics,
        "note": (
            "Public high-confidence labels remain disabled until enough forward calls from the promoted cross-sectional "
            "XGBoost ensemble resolve and every precommitted calibration/precision gate passes. Historical backtests and "
            "older model versions cannot bypass this gate."
        ),
    }


def _uninitialized_storage_status() -> dict[str, Any]:
    status = evaluate_quant_validation_rows([])
    status.update(
        {
            "gate_status": "unavailable",
            "public_high_confidence_enabled": False,
            "storage_ready": False,
            "note": (
                "Quant shadow-validation storage is not initialized on this database. "
                "Core market intelligence remains available; run `python -m src.database.init_db` "
                "before collecting forward shadow signals."
            ),
        }
    )
    return status


def _storage_ready(session: Session) -> bool:
    bind = session.get_bind()
    return bool(inspect(bind).has_table(QuantShadowSignal.__tablename__))


def build_quant_validation_status(session: Session | None = None) -> dict[str, Any]:
    if session is not None:
        if not _storage_ready(session):
            return _uninitialized_storage_status()
        return evaluate_quant_validation_rows(_load_rows(session))

    with get_session() as owned_session:
        if not _storage_ready(owned_session):
            return _uninitialized_storage_status()
        rows = _load_rows(owned_session)
    return evaluate_quant_validation_rows(rows)
