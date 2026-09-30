from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.database.quant_models import QuantV41ShadowRun


def _stable_json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def heartbeat_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def record_v41_shadow_heartbeat(
    session: Session,
    *,
    model: dict[str, Any],
    current: dict[str, Any],
    run_status: str,
    candidate_rows: int = 0,
    v41_selected: int = 0,
    baseline_selected: int = 0,
    rows_inserted: int = 0,
    failure_code: str | None = None,
    extra_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one immutable V4.1 run audit record when a market date exists.

    The fingerprint excludes wall-clock time, so an identical rerun is naturally
    de-duplicated while a materially different retry on the same market date is
    preserved as a separate immutable audit event.
    """
    as_of_date = current.get("as_of_date")
    if as_of_date is None:
        return {"status": "not_recorded", "reason": "missing_as_of_date"}

    details = {
        "market_entry": current.get("market_entry"),
        "rows_before_liquidity_filter": current.get("rows_before_liquidity_filter"),
        "excluded_low_liquidity": current.get("excluded_low_liquidity"),
        "excluded_unclassified_liquidity": current.get("excluded_unclassified_liquidity"),
        "liquidity_rule": current.get("liquidity_rule"),
        **(extra_details or {}),
    }
    immutable = {
        "model_snapshot_id": int(model["snapshot_id"]),
        "model_version": str(model["model_version"]),
        "policy_version": str(model["policy_version"]),
        "artifact_fingerprint": str(model["artifact_fingerprint"]),
        "as_of_date": as_of_date,
        "run_status": run_status,
        "symbols_considered": int(current.get("symbols_considered") or 0),
        "eligible_rows": int(current.get("eligible_rows") or 0),
        "candidate_rows": int(candidate_rows),
        "v41_selected": int(v41_selected),
        "baseline_selected": int(baseline_selected),
        "rows_inserted": int(rows_inserted),
        "skipped_stale_or_untraded": int(current.get("skipped_stale_or_untraded") or 0),
        "skipped_features": int(current.get("skipped_features") or 0),
        "failure_code": failure_code,
        "details": details,
    }
    fingerprint = heartbeat_fingerprint(immutable)
    values = {
        "model_snapshot_id": immutable["model_snapshot_id"],
        "as_of_date": as_of_date,
        "model_version": immutable["model_version"],
        "policy_version": immutable["policy_version"],
        "artifact_fingerprint": immutable["artifact_fingerprint"],
        "run_status": run_status,
        "symbols_considered": immutable["symbols_considered"],
        "eligible_rows": immutable["eligible_rows"],
        "candidate_rows": immutable["candidate_rows"],
        "v41_selected": immutable["v41_selected"],
        "baseline_selected": immutable["baseline_selected"],
        "rows_inserted": immutable["rows_inserted"],
        "skipped_stale_or_untraded": immutable["skipped_stale_or_untraded"],
        "skipped_features": immutable["skipped_features"],
        "failure_code": failure_code,
        "details_json": _stable_json(details),
        "run_fingerprint": fingerprint,
        "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    stmt = pg_insert(QuantV41ShadowRun).values(values)
    stmt = stmt.on_conflict_do_nothing(index_elements=["run_fingerprint"]).returning(QuantV41ShadowRun.id)
    inserted_id = session.execute(stmt).scalar_one_or_none()
    return {
        "status": "recorded" if inserted_id is not None else "already_recorded",
        "heartbeat_id": inserted_id,
        "run_fingerprint": fingerprint,
        "run_status": run_status,
        "as_of_date": str(as_of_date),
    }
