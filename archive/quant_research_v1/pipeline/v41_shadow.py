from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import CorporateAction, DailyPrice, MarketIndex
from src.database.quant_models import QuantV41ShadowSignal
from src.services.nepse_quant_research import NEPSE_INDEX_NAME
from archive.quant_research_v1.services.quant_residual_alpha import V4_EXECUTION_HURDLE_PERCENT, matched_baseline_selection, selection_counts
from archive.quant_research_v1.services.quant_v41_artifact import (
    V41_FROZEN_MODEL_VERSION,
    V41_HORIZON_DAYS,
    load_frozen_v41_model,
    score_v41_candidate_rows,
    train_frozen_v41_model,
)
from archive.quant_research_v1.services.quant_v41_heartbeat import record_v41_shadow_heartbeat
from archive.quant_research_v1.services.quant_v41_live import build_current_v41_rows
from archive.quant_research_v1.services.quant_v41_validation import build_v41_forward_validation_status

logger = logging.getLogger(__name__)
VOID_SEARCH_CAP_TRADING_DAYS = 3


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _market_trading_dates(session) -> list[date]:
    return list(
        session.execute(
            select(MarketIndex.date)
            .where(MarketIndex.index_name == NEPSE_INDEX_NAME)
            .order_by(MarketIndex.date)
        ).scalars().all()
    )


def _target_date(entry_date: date, horizon_days: int, trading_dates: list[date]) -> date | None:
    start = bisect.bisect_right(trading_dates, entry_date)
    target_index = start + horizon_days - 1
    if target_index >= len(trading_dates):
        return None
    return trading_dates[target_index]


def _cutoff_date(target_date: date, trading_dates: list[date]) -> date | None:
    index = bisect.bisect_left(trading_dates, target_date)
    cutoff_index = index + VOID_SEARCH_CAP_TRADING_DAYS
    if cutoff_index >= len(trading_dates):
        return None
    return trading_dates[cutoff_index]


def capture_v41_shadow_signals(*, limit: int = 300, train_if_missing: bool = False) -> dict[str, Any]:
    with get_session() as session:
        model = load_frozen_v41_model(session)
    training = None
    if model is None and train_if_missing:
        training = train_frozen_v41_model(limit=max(limit, 400))
        with get_session() as session:
            model = load_frozen_v41_model(session)
    if model is None:
        return {
            "status": "no_frozen_model",
            "model_version": V41_FROZEN_MODEL_VERSION,
            "training": training,
            "note": "Run this command once with --train-if-missing after initializing the V4.1 tables.",
        }

    with get_session() as session:
        current = build_current_v41_rows(session, limit=limit)
        candidates = current.get("candidates", [])
        if not candidates:
            heartbeat = record_v41_shadow_heartbeat(
                session,
                model=model,
                current=current,
                run_status="abstained",
                candidate_rows=0,
                v41_selected=0,
                baseline_selected=0,
                rows_inserted=0,
                extra_details={"reason": current.get("reason") or "no_current_candidates"},
            )
            return {
                "status": "no_current_candidates",
                "model_version": model["model_version"],
                "artifact_fingerprint": model["artifact_fingerprint"],
                "heartbeat": heartbeat,
                "current": {key: value for key, value in current.items() if key not in {"rows", "candidates"}},
            }

        scored = score_v41_candidate_rows(model, candidates)
        predictions = scored.get("predictions", [])
        selection = scored.get("selection", {"selected": [], "days": []})
        if not predictions:
            heartbeat = record_v41_shadow_heartbeat(
                session,
                model=model,
                current=current,
                run_status="failed",
                candidate_rows=len(candidates),
                failure_code="scoring_failed",
            )
            return {
                "status": "scoring_failed",
                "model_version": model["model_version"],
                "heartbeat": heartbeat,
            }

        baseline = matched_baseline_selection(candidates, selection_counts(selection))
        v41_selected = {candidate["row"]["symbol"] for candidate in selection.get("selected", [])}
        baseline_selected = {candidate["row"]["symbol"] for candidate in baseline.get("selected", [])}
        ranked = sorted(predictions, key=lambda item: float(item["final_score"]), reverse=True)
        v41_rank = {candidate["row"]["symbol"]: index for index, candidate in enumerate(ranked, start=1)}
        created_at = datetime.now(timezone.utc).replace(tzinfo=None)

        rows: list[dict[str, Any]] = []
        for prediction in predictions:
            row = prediction["row"]
            symbol = str(row["symbol"])
            immutable = {
                "artifact_fingerprint": model["artifact_fingerprint"],
                "model_version": model["model_version"],
                "policy_version": model["policy_version"],
                "as_of_date": row["date"],
                "symbol": symbol,
                "baseline_rank": int(row["baseline_rank"]),
                "v41_rank": int(v41_rank[symbol]),
                "selected_v41": symbol in v41_selected,
                "selected_baseline": symbol in baseline_selected,
                "override_action": prediction["override_action"],
                "baseline_score": float(row["baseline_score"]),
                "final_score": float(prediction["final_score"]),
                "predicted_residual_alpha_percent": float(prediction["predicted_residual_alpha_percent"]),
                "probability_positive_residual": float(prediction["probability_positive_residual"]),
                "predicted_mae_percent": float(prediction["predicted_mae_percent"]),
                "predicted_mfe_percent": float(prediction["predicted_mfe_percent"]),
                "predicted_payoff_ratio": float(prediction["predicted_payoff_ratio"]),
                "expected_excess_return_percent": float(prediction["expected_excess_return_percent"]),
                "market_regime": prediction["market_regime"],
                "sector_regime": prediction["sector_regime"],
                "liquidity_bucket": row["liquidity_bucket"],
                "entry_price": float(row["entry_price"]),
                "market_entry": float(row["market_entry"]),
            }
            rows.append(
                {
                    "model_snapshot_id": model["snapshot_id"],
                    "symbol": symbol,
                    "as_of_date": row["date"],
                    "horizon_days": V41_HORIZON_DAYS,
                    "model_version": model["model_version"],
                    "policy_version": model["policy_version"],
                    "prediction_fingerprint": _fingerprint(immutable),
                    "baseline_rank": immutable["baseline_rank"],
                    "v41_rank": immutable["v41_rank"],
                    "selected_v41": immutable["selected_v41"],
                    "selected_baseline": immutable["selected_baseline"],
                    "override_action": immutable["override_action"],
                    "baseline_score": immutable["baseline_score"],
                    "final_score": immutable["final_score"],
                    "predicted_residual_alpha_percent": immutable["predicted_residual_alpha_percent"],
                    "probability_positive_residual": immutable["probability_positive_residual"],
                    "predicted_mae_percent": immutable["predicted_mae_percent"],
                    "predicted_mfe_percent": immutable["predicted_mfe_percent"],
                    "predicted_payoff_ratio": immutable["predicted_payoff_ratio"],
                    "expected_excess_return_percent": immutable["expected_excess_return_percent"],
                    "market_regime": immutable["market_regime"],
                    "sector_regime": immutable["sector_regime"],
                    "liquidity_bucket": immutable["liquidity_bucket"],
                    "entry_price": immutable["entry_price"],
                    "market_entry": immutable["market_entry"],
                    "status": "pending",
                    "created_at": created_at,
                }
            )

        stmt = pg_insert(QuantV41ShadowSignal).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["model_snapshot_id", "symbol", "as_of_date"]
        ).returning(QuantV41ShadowSignal.id)
        inserted = len(session.execute(stmt).fetchall())
        heartbeat = record_v41_shadow_heartbeat(
            session,
            model=model,
            current=current,
            run_status="captured",
            candidate_rows=len(predictions),
            v41_selected=len(v41_selected),
            baseline_selected=len(baseline_selected),
            rows_inserted=inserted,
            extra_details={
                "prepared_prediction_rows": len(rows),
                "existing_prediction_rows": max(0, len(rows) - inserted),
            },
        )

    return {
        "status": "captured",
        "model_version": model["model_version"],
        "policy_version": model["policy_version"],
        "artifact_fingerprint": model["artifact_fingerprint"],
        "as_of_date": str(current.get("as_of_date")),
        "candidate_rows": len(predictions),
        "v41_selected": len(v41_selected),
        "baseline_selected": len(baseline_selected),
        "rows_inserted": inserted,
        "heartbeat": heartbeat,
        "training": training,
        "current": {key: value for key, value in current.items() if key not in {"rows", "candidates"}},
    }


def grade_v41_shadow_signals(*, as_of: date | None = None) -> dict[str, Any]:
    with get_session() as session:
        trading_dates = _market_trading_dates(session)
        if not trading_dates:
            return {"status": "missing_market_calendar"}
        as_of = as_of or trading_dates[-1]
        pending = session.execute(
            select(QuantV41ShadowSignal)
            .where(
                QuantV41ShadowSignal.status == "pending",
                QuantV41ShadowSignal.model_version == V41_FROZEN_MODEL_VERSION,
            )
            .order_by(QuantV41ShadowSignal.as_of_date, QuantV41ShadowSignal.symbol)
        ).scalars().all()

        resolved = 0
        voided = 0
        not_ready = 0
        for row in pending:
            target = _target_date(row.as_of_date, row.horizon_days, trading_dates)
            if target is None or target > as_of:
                not_ready += 1
                continue
            cutoff = _cutoff_date(target, trading_dates)
            effective_cutoff = min(cutoff, as_of) if cutoff is not None else as_of
            resolution = session.execute(
                select(DailyPrice.date, DailyPrice.close)
                .where(
                    DailyPrice.symbol == row.symbol,
                    DailyPrice.date >= target,
                    DailyPrice.date <= effective_cutoff,
                )
                .order_by(DailyPrice.date)
                .limit(1)
            ).first()
            if resolution is None:
                if cutoff is None or cutoff > as_of:
                    not_ready += 1
                    continue
                row.status = "void"
                row.void_reason = "No stock trade within the allowed resolution window."
                voided += 1
                continue

            action_exists = session.execute(
                select(CorporateAction.id)
                .where(
                    CorporateAction.symbol == row.symbol,
                    CorporateAction.action_date > row.as_of_date,
                    CorporateAction.action_date <= resolution.date,
                )
                .limit(1)
            ).scalar_one_or_none()
            if action_exists is not None:
                row.status = "void"
                row.void_reason = "Corporate action occurred during the shadow holding window."
                voided += 1
                continue

            market_resolution = session.execute(
                select(MarketIndex.close)
                .where(MarketIndex.index_name == NEPSE_INDEX_NAME, MarketIndex.date <= resolution.date)
                .order_by(MarketIndex.date.desc())
                .limit(1)
            ).scalar_one_or_none()
            if market_resolution is None:
                not_ready += 1
                continue

            path = session.execute(
                select(DailyPrice.date, DailyPrice.close)
                .where(
                    DailyPrice.symbol == row.symbol,
                    DailyPrice.date > row.as_of_date,
                    DailyPrice.date <= resolution.date,
                )
                .order_by(DailyPrice.date)
            ).all()
            entry_price = float(row.entry_price)
            path_returns = [
                (float(item.close) / entry_price - 1.0) * 100.0
                for item in path
                if item.close is not None and entry_price > 0
            ]

            resolution_price = float(resolution.close)
            market_resolution_value = float(market_resolution)
            stock_return = (resolution_price / entry_price - 1.0) * 100.0
            market_return = (market_resolution_value / float(row.market_entry) - 1.0) * 100.0
            excess = stock_return - market_return

            row.status = "resolved"
            row.resolution_date = resolution.date
            row.resolution_price = resolution_price
            row.market_resolution = market_resolution_value
            row.realized_stock_return_percent = stock_return
            row.realized_market_return_percent = market_return
            row.realized_excess_return_percent = excess
            row.realized_mae_percent = min(path_returns) if path_returns else 0.0
            row.realized_mfe_percent = max(path_returns) if path_returns else 0.0
            row.success_after_cost = excess > V4_EXECUTION_HURDLE_PERCENT
            resolved += 1

    return {
        "status": "graded",
        "model_version": V41_FROZEN_MODEL_VERSION,
        "pending_before_run": len(pending),
        "resolved": resolved,
        "voided": voided,
        "not_ready": not_ready,
    }


def run_v41_shadow_cycle(*, limit: int = 300, train_if_missing: bool = False) -> dict[str, Any]:
    grading = grade_v41_shadow_signals()
    capture = capture_v41_shadow_signals(limit=limit, train_if_missing=train_if_missing)
    with get_session() as session:
        validation = build_v41_forward_validation_status(session)
    return {"grading": grading, "capture": capture, "validation": validation}


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade and capture the frozen V4.1 forward-shadow ledger")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument(
        "--train-if-missing",
        action="store_true",
        help="Train and freeze the V4.1 shadow artifact only when this exact version has never been created.",
    )
    args = parser.parse_args()
    print(json.dumps(run_v41_shadow_cycle(limit=args.limit, train_if_missing=args.train_if_missing), indent=2, default=str))


if __name__ == "__main__":
    main()
