from __future__ import annotations

import logging
import time
from typing import Any

from src.pipeline.signal_validation_policy import MIN_INDEPENDENT_ENTRY_DAYS, VALIDATION_SIGNAL_SPECS

logger = logging.getLogger(__name__)

UNDER_VALIDATION_TIER = "under_validation"
EVIDENCE_CACHE_SECONDS = 600

_cache: dict[str, Any] = {"loaded_at": 0.0, "evidence": None}


def evidence_from_status(validation_status: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    signals = (validation_status or {}).get("signals", {})
    evidence = {}
    for signal_name, spec in VALIDATION_SIGNAL_SPECS.items():
        row = signals.get(signal_name) or {}
        evidence[signal_name] = {
            "status": row.get("gate_status") or "collecting",
            "graded_calls": int(row.get("graded_calls") or 0),
            "required_calls": int(row.get("min_graded_calls") or spec.min_graded_calls),
            "independent_entry_days": int(row.get("independent_entry_days") or 0),
            "required_entry_days": int(row.get("min_independent_entry_days") or MIN_INDEPENDENT_ENTRY_DAYS),
        }
    return evidence


def load_signal_evidence() -> dict[str, dict[str, Any]]:
    now = time.monotonic()
    if _cache["evidence"] is not None and now - _cache["loaded_at"] < EVIDENCE_CACHE_SECONDS:
        return _cache["evidence"]
    try:
        from src.pipeline.validation_status import build_validation_status

        evidence = evidence_from_status(build_validation_status())
    except Exception:
        logger.exception("Could not load forward validation evidence")
        evidence = evidence_from_status(None)
    _cache.update(loaded_at=now, evidence=evidence)
    return evidence


def public_signal_label(
    signal_name: str,
    evidence: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evidence = evidence if evidence is not None else load_signal_evidence()
    return {
        "tier": UNDER_VALIDATION_TIER,
        "validation": evidence.get(signal_name)
        or {
            "status": "collecting",
            "graded_calls": 0,
            "required_calls": None,
            "independent_entry_days": 0,
            "required_entry_days": MIN_INDEPENDENT_ENTRY_DAYS,
        },
    }
