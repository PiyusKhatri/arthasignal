from __future__ import annotations

import bisect
import hashlib
import json
from datetime import date, datetime, timezone
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.e1_models import QuantE1ForwardDecision, QuantE1ForwardRun
from src.database.models import DailyPrice, MarketIndex
from src.database.quant_models import QuantV41ShadowRun, QuantV41ShadowSignal
from src.services.nepse_quant_research import NEPSE_INDEX_NAME
from archive.quant_research_v1.services.quant_execution_policy_e1 import (
    E1_MAX_HOLDING_SESSIONS,
    E1_MAX_POSITIONS,
    E1_POLICY_VERSION,
    E1_REPLACEMENT_SCORE_MARGIN,
    E1_ROUND_TRIP_COST_PERCENT,
    _entry_eligible,
    _hold_eligible,
    simulate_execution_policy_e1,
)
from archive.quant_research_v1.services.quant_portfolio_simulator import SIM_BLOCK_LENGTH, block_bootstrap_incremental_returns
from archive.quant_research_v1.services.quant_v41_artifact import load_frozen_v41_model, score_v41_candidate_rows
from archive.quant_research_v1.services.quant_v41_live import build_current_v41_rows

E1_FORWARD_POLICY_VERSION = "2026-08-21-e1-forward-v1"
E1_FORWARD_EXECUTION_CONVENTION = "signal on completed session D; execute E1 decisions at next tradable session open"
E1_FORWARD_MIN_SIGNAL_DATES = 120
E1_FORWARD_MIN_COMPLETED_V41_TRADES = 40
E1_FORWARD_MAX_ANNUALIZED_TURNOVER_X = 30.0
E1_FORWARD_MIN_MEAN_HOLDING_SESSIONS = 5.0


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _successful_v41_run(session: Session, as_of_date: date) -> QuantV41ShadowRun | None:
    return session.execute(
        select(QuantV41ShadowRun)
        .where(
            QuantV41ShadowRun.as_of_date == as_of_date,
            QuantV41ShadowRun.run_status.in_(["captured", "abstained"]),
        )
        .order_by(QuantV41ShadowRun.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def _existing_successful_e1_run(
    session: Session,
    *,
    model_snapshot_id: int,
    as_of_date: date,
) -> QuantE1ForwardRun | None:
    return session.execute(
        select(QuantE1ForwardRun)
        .where(
            QuantE1ForwardRun.model_snapshot_id == model_snapshot_id,
            QuantE1ForwardRun.as_of_date == as_of_date,
            QuantE1ForwardRun.execution_policy_version == E1_POLICY_VERSION,
            QuantE1ForwardRun.run_status.in_(["captured", "abstained"]),
        )
        .order_by(QuantE1ForwardRun.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def _record_run(
    session: Session,
    *,
    model: dict[str, Any],
    as_of_date: date,
    source_run: QuantV41ShadowRun,
    run_status: str,
    candidate_rows: int,
    decision_rows: int,
    failure_code: str | None,
    details: dict[str, Any],
) -> QuantE1ForwardRun:
    payload = {
        "model_snapshot_id": model["snapshot_id"],
        "as_of_date": as_of_date,
        "model_version": model["model_version"],
        "predictive_policy_version": model["policy_version"],
        "execution_policy_version": E1_POLICY_VERSION,
        "artifact_fingerprint": model["artifact_fingerprint"],
        "source_v41_run_fingerprint": source_run.run_fingerprint,
        "run_status": run_status,
        "candidate_rows": candidate_rows,
        "decision_rows": decision_rows,
        "failure_code": failure_code,
        "details": details,
    }
    run_fingerprint = _fingerprint(payload)
    existing = session.execute(
        select(QuantE1ForwardRun)
        .where(QuantE1ForwardRun.run_fingerprint == run_fingerprint)
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = QuantE1ForwardRun(
        model_snapshot_id=int(model["snapshot_id"]),
        as_of_date=as_of_date,
        model_version=str(model["model_version"]),
        predictive_policy_version=str(model["policy_version"]),
        execution_policy_version=E1_POLICY_VERSION,
        artifact_fingerprint=str(model["artifact_fingerprint"]),
        source_v41_run_fingerprint=str(source_run.run_fingerprint),
        run_status=run_status,
        candidate_rows=int(candidate_rows),
        decision_rows=int(decision_rows),
        failure_code=failure_code,
        details_json=json.dumps(details, default=str, sort_keys=True),
        run_fingerprint=run_fingerprint,
        created_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def _shadow_prediction_map(
    session: Session,
    *,
    model_snapshot_id: int,
    as_of_date: date,
) -> dict[str, QuantV41ShadowSignal]:
    rows = session.execute(
        select(QuantV41ShadowSignal)
        .where(
            QuantV41ShadowSignal.model_snapshot_id == model_snapshot_id,
            QuantV41ShadowSignal.as_of_date == as_of_date,
        )
        .order_by(QuantV41ShadowSignal.symbol)
    ).scalars().all()
    return {str(row.symbol): row for row in rows}


def _prediction_mismatch(
    predictions: list[dict[str, Any]],
    stored: dict[str, QuantV41ShadowSignal],
) -> str | None:
    predicted_symbols = {str(item["row"]["symbol"]) for item in predictions}
    if predicted_symbols != set(stored):
        return "candidate_symbol_set_mismatch"
    for prediction in predictions:
        symbol = str(prediction["row"]["symbol"])
        shadow = stored[symbol]
        if str(prediction.get("override_action")) != str(shadow.override_action):
            return f"override_action_mismatch:{symbol}"
        if abs(float(prediction.get("final_score") or 0.0) - float(shadow.final_score)) > 1e-5:
            return f"final_score_mismatch:{symbol}"
        if abs(
            float(prediction.get("expected_excess_return_percent") or 0.0)
            - float(shadow.expected_excess_return_percent)
        ) > 1e-4:
            return f"expected_excess_mismatch:{symbol}"
    return None


def capture_e1_forward_decisions(session: Session, *, limit: int = 300) -> dict[str, Any]:
    model = load_frozen_v41_model(session)
    if model is None:
        return {"status": "no_frozen_v41_model", "policy_version": E1_FORWARD_POLICY_VERSION}

    current = build_current_v41_rows(session, limit=limit)
    as_of_date = current.get("as_of_date")
    if as_of_date is None:
        return {"status": "no_current_market_session", "policy_version": E1_FORWARD_POLICY_VERSION}

    source_run = _successful_v41_run(session, as_of_date)
    if source_run is None:
        return {
            "status": "waiting_for_v41_heartbeat",
            "as_of_date": as_of_date.isoformat(),
            "policy_version": E1_FORWARD_POLICY_VERSION,
        }

    existing = _existing_successful_e1_run(
        session,
        model_snapshot_id=int(model["snapshot_id"]),
        as_of_date=as_of_date,
    )
    if existing is not None:
        return {
            "status": "already_captured",
            "as_of_date": as_of_date.isoformat(),
            "run_id": existing.id,
            "run_status": existing.run_status,
            "run_fingerprint": existing.run_fingerprint,
        }

    candidates = list(current.get("candidates", []))
    if source_run.run_status == "abstained":
        if candidates:
            failed = _record_run(
                session,
                model=model,
                as_of_date=as_of_date,
                source_run=source_run,
                run_status="failed",
                candidate_rows=len(candidates),
                decision_rows=0,
                failure_code="v41_abstention_candidate_mismatch",
                details={"source_candidate_rows": source_run.candidate_rows},
            )
            return {"status": "failed", "failure_code": failed.failure_code, "run_id": failed.id}
        run = _record_run(
            session,
            model=model,
            as_of_date=as_of_date,
            source_run=source_run,
            run_status="abstained",
            candidate_rows=0,
            decision_rows=0,
            failure_code=None,
            details={"source_v41_run_status": "abstained", "execution_convention": E1_FORWARD_EXECUTION_CONVENTION},
        )
        return {
            "status": "abstained",
            "as_of_date": as_of_date.isoformat(),
            "run_id": run.id,
            "run_fingerprint": run.run_fingerprint,
            "decision_rows": 0,
        }

    scored = score_v41_candidate_rows(model, candidates)
    predictions = list(scored.get("predictions", []))
    stored = _shadow_prediction_map(
        session,
        model_snapshot_id=int(model["snapshot_id"]),
        as_of_date=as_of_date,
    )
    mismatch = _prediction_mismatch(predictions, stored)
    if mismatch is None and len(predictions) != int(source_run.candidate_rows):
        mismatch = "candidate_count_mismatch"
    if mismatch is not None:
        failed = _record_run(
            session,
            model=model,
            as_of_date=as_of_date,
            source_run=source_run,
            run_status="failed",
            candidate_rows=len(predictions),
            decision_rows=0,
            failure_code=mismatch,
            details={
                "source_candidate_rows": source_run.candidate_rows,
                "stored_prediction_rows": len(stored),
                "recomputed_prediction_rows": len(predictions),
            },
        )
        return {"status": "failed", "failure_code": failed.failure_code, "run_id": failed.id}

    prepared: list[dict[str, Any]] = []
    for prediction in predictions:
        candidate = prediction["row"]
        symbol = str(candidate["symbol"])
        source = stored[symbol]
        immutable = {
            "as_of_date": as_of_date,
            "symbol": symbol,
            "source_prediction_fingerprint": source.prediction_fingerprint,
            "baseline_rank": int(candidate["baseline_rank"]),
            "baseline_percentile": float(candidate["baseline_percentile"]),
            "baseline_score": float(candidate["baseline_score"]),
            "final_score": float(prediction["final_score"]),
            "override_action": str(prediction["override_action"]),
            "expected_excess_return_percent": float(prediction["expected_excess_return_percent"]),
            "market_regime": str(prediction["market_regime"]),
            "sector_regime": str(prediction["sector_regime"]),
            "liquidity_bucket": str(candidate["liquidity_bucket"]),
            "entry_eligible_v41": bool(_entry_eligible(prediction, "v41")),
            "hold_eligible_v41": bool(_hold_eligible(prediction, "v41")),
            "entry_eligible_baseline": bool(_entry_eligible(prediction, "baseline")),
            "execution_policy_version": E1_POLICY_VERSION,
        }
        prepared.append({**immutable, "decision_fingerprint": _fingerprint(immutable)})

    run = _record_run(
        session,
        model=model,
        as_of_date=as_of_date,
        source_run=source_run,
        run_status="captured",
        candidate_rows=len(predictions),
        decision_rows=len(prepared),
        failure_code=None,
        details={
            "source_v41_run_status": source_run.run_status,
            "execution_convention": E1_FORWARD_EXECUTION_CONVENTION,
            "replacement_score_margin": E1_REPLACEMENT_SCORE_MARGIN,
            "round_trip_cost_percent": E1_ROUND_TRIP_COST_PERCENT,
        },
    )
    created_at = _now()
    for item in prepared:
        session.add(
            QuantE1ForwardDecision(
                run_id=run.id,
                symbol=item["symbol"],
                as_of_date=as_of_date,
                source_prediction_fingerprint=item["source_prediction_fingerprint"],
                baseline_rank=item["baseline_rank"],
                baseline_percentile=item["baseline_percentile"],
                baseline_score=item["baseline_score"],
                final_score=item["final_score"],
                override_action=item["override_action"],
                expected_excess_return_percent=item["expected_excess_return_percent"],
                market_regime=item["market_regime"],
                sector_regime=item["sector_regime"],
                liquidity_bucket=item["liquidity_bucket"],
                entry_eligible_v41=item["entry_eligible_v41"],
                hold_eligible_v41=item["hold_eligible_v41"],
                entry_eligible_baseline=item["entry_eligible_baseline"],
                decision_fingerprint=item["decision_fingerprint"],
                created_at=created_at,
            )
        )

    return {
        "status": "captured",
        "as_of_date": as_of_date.isoformat(),
        "run_id": run.id,
        "run_fingerprint": run.run_fingerprint,
        "candidate_rows": len(predictions),
        "decision_rows": len(prepared),
        "source_v41_run_fingerprint": source_run.run_fingerprint,
    }


def shift_signal_decisions_to_next_session(
    runs: list[tuple[date, list[dict[str, Any]]]],
    market_dates: list[date],
) -> dict[date, list[dict[str, Any]]]:
    """Map immutable close-of-D decisions onto the next tradable market session."""
    shifted: dict[date, list[dict[str, Any]]] = {}
    for signal_date, candidates in runs:
        index = bisect.bisect_right(market_dates, signal_date)
        if index >= len(market_dates):
            continue
        execution_date = market_dates[index]
        shifted[execution_date] = [
            {
                **candidate,
                "row": {
                    **candidate["row"],
                    "signal_date": signal_date,
                    "date": execution_date,
                },
            }
            for candidate in candidates
        ]
    return shifted


def _decision_candidate(row: QuantE1ForwardDecision) -> dict[str, Any]:
    return {
        "row": {
            "symbol": str(row.symbol),
            "date": row.as_of_date,
            "baseline_rank": int(row.baseline_rank),
            "baseline_percentile": float(row.baseline_percentile),
            "baseline_score": float(row.baseline_score),
            "v4_market_regime": str(row.market_regime),
            "liquidity_bucket": str(row.liquidity_bucket),
        },
        "final_score": float(row.final_score),
        "override_action": str(row.override_action),
        "expected_excess_return_percent": float(row.expected_excess_return_percent),
        "market_regime": str(row.market_regime),
        "sector_regime": str(row.sector_regime),
    }


def _successful_e1_runs(session: Session) -> list[QuantE1ForwardRun]:
    rows = session.execute(
        select(QuantE1ForwardRun)
        .where(
            QuantE1ForwardRun.execution_policy_version == E1_POLICY_VERSION,
            QuantE1ForwardRun.run_status.in_(["captured", "abstained"]),
        )
        .order_by(QuantE1ForwardRun.as_of_date, QuantE1ForwardRun.id)
    ).scalars().all()
    first_by_date: dict[date, QuantE1ForwardRun] = {}
    for row in rows:
        first_by_date.setdefault(row.as_of_date, row)
    return [first_by_date[key] for key in sorted(first_by_date)]


def _compact_portfolio(result: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "sessions",
        "decision_dates",
        "ending_wealth",
        "total_return_percent",
        "approx_cagr_percent",
        "annualized_volatility_percent",
        "sharpe_like",
        "max_drawdown_percent",
        "total_transaction_cost_percent_of_initial_capital",
        "annualized_turnover_x",
        "buy_transactions",
        "sell_transactions",
        "replacements",
        "blocked_buy_attempts",
        "blocked_sell_attempts",
        "mean_positions",
        "mean_completed_holding_sessions",
        "median_completed_holding_sessions",
    )
    return {key: result.get(key) for key in keys}


def build_e1_forward_validation_status(session: Session) -> dict[str, Any]:
    runs = _successful_e1_runs(session)
    if not runs:
        return {
            "policy_version": E1_FORWARD_POLICY_VERSION,
            "execution_policy_version": E1_POLICY_VERSION,
            "status": "collecting",
            "signal_dates": 0,
            "note": "No successful E1 forward decision dates have been captured yet.",
        }

    decisions = session.execute(
        select(QuantE1ForwardDecision)
        .where(QuantE1ForwardDecision.run_id.in_([row.id for row in runs]))
        .order_by(QuantE1ForwardDecision.as_of_date, QuantE1ForwardDecision.baseline_rank)
    ).scalars().all()
    by_run: dict[int, list[dict[str, Any]]] = {row.id: [] for row in runs}
    for decision in decisions:
        by_run.setdefault(decision.run_id, []).append(_decision_candidate(decision))

    first_date = runs[0].as_of_date
    latest_market_date = session.execute(
        select(MarketIndex.date)
        .where(MarketIndex.index_name == NEPSE_INDEX_NAME, MarketIndex.date >= first_date)
        .order_by(MarketIndex.date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest_market_date is None:
        return {"policy_version": E1_FORWARD_POLICY_VERSION, "status": "collecting", "reason": "missing_market_data"}

    market_dates = list(
        session.execute(
            select(MarketIndex.date)
            .where(
                MarketIndex.index_name == NEPSE_INDEX_NAME,
                MarketIndex.date >= first_date,
                MarketIndex.date <= latest_market_date,
            )
            .order_by(MarketIndex.date)
        ).scalars().all()
    )
    signal_runs = [(row.as_of_date, by_run.get(row.id, [])) for row in runs]
    predictions_by_execution_date = shift_signal_decisions_to_next_session(signal_runs, market_dates)

    symbols = sorted({str(row.symbol) for row in decisions})
    mark_history: dict[str, dict[date, float]] = {symbol: {} for symbol in symbols}
    execution_history: dict[str, dict[date, float]] = {symbol: {} for symbol in symbols}
    if symbols:
        price_rows = session.execute(
            select(DailyPrice.symbol, DailyPrice.date, DailyPrice.open, DailyPrice.close)
            .where(
                DailyPrice.symbol.in_(symbols),
                DailyPrice.date >= first_date,
                DailyPrice.date <= latest_market_date,
            )
            .order_by(DailyPrice.symbol, DailyPrice.date)
        ).all()
        for row in price_rows:
            symbol = str(row.symbol)
            if row.close is not None and float(row.close) > 0:
                mark_history.setdefault(symbol, {})[row.date] = float(row.close)
            if row.open is not None and float(row.open) > 0:
                execution_history.setdefault(symbol, {})[row.date] = float(row.open)

    v41 = simulate_execution_policy_e1(
        market_dates=market_dates,
        predictions_by_date=predictions_by_execution_date,
        price_history=mark_history,
        execution_price_history=execution_history,
        terminal_liquidation=False,
        mode="v41",
    )
    baseline = simulate_execution_policy_e1(
        market_dates=market_dates,
        predictions_by_date=predictions_by_execution_date,
        price_history=mark_history,
        execution_price_history=execution_history,
        terminal_liquidation=False,
        mode="baseline",
    )
    bootstrap = block_bootstrap_incremental_returns(
        v41.get("daily_returns", []),
        baseline.get("daily_returns", []),
        block_length=SIM_BLOCK_LENGTH,
    )

    source_dates = set(
        session.execute(
            select(QuantV41ShadowRun.as_of_date)
            .where(
                QuantV41ShadowRun.as_of_date >= first_date,
                QuantV41ShadowRun.run_status.in_(["captured", "abstained"]),
            )
        ).scalars().all()
    )
    e1_dates = {row.as_of_date for row in runs}
    missing_e1_dates = sorted(source_dates - e1_dates)
    completed_v41_trades = sum(
        trade.get("side") == "sell" and trade.get("reason") != "terminal"
        for trade in v41.get("trade_log", [])
    )
    enough_sample = len(runs) >= E1_FORWARD_MIN_SIGNAL_DATES and completed_v41_trades >= E1_FORWARD_MIN_COMPLETED_V41_TRADES
    bootstrap_low = bootstrap.get("annualized_incremental_return_95", {}).get("low")
    v41_return = v41.get("total_return_percent")
    baseline_return = baseline.get("total_return_percent")
    v41_drawdown = v41.get("max_drawdown_percent")
    baseline_drawdown = baseline.get("max_drawdown_percent")
    checks = {
        "no_missing_e1_capture_dates": not missing_e1_dates,
        "enough_successful_signal_dates": len(runs) >= E1_FORWARD_MIN_SIGNAL_DATES,
        "enough_completed_v41_trades": completed_v41_trades >= E1_FORWARD_MIN_COMPLETED_V41_TRADES,
        "v41_total_return_positive": v41_return is not None and float(v41_return) > 0.0,
        "v41_total_return_better_than_e1_momentum": (
            v41_return is not None and baseline_return is not None and float(v41_return) > float(baseline_return)
        ),
        "annualized_turnover_at_most_30x": (
            v41.get("annualized_turnover_x") is not None
            and float(v41["annualized_turnover_x"]) <= E1_FORWARD_MAX_ANNUALIZED_TURNOVER_X
        ),
        "mean_holding_at_least_5_sessions": (
            v41.get("mean_completed_holding_sessions") is not None
            and float(v41["mean_completed_holding_sessions"]) >= E1_FORWARD_MIN_MEAN_HOLDING_SESSIONS
        ),
        "max_drawdown_not_worse_than_e1_momentum": (
            v41_drawdown is not None and baseline_drawdown is not None and float(v41_drawdown) >= float(baseline_drawdown)
        ),
        "block_bootstrap_lower_bound_positive": bootstrap_low is not None and float(bootstrap_low) > 0.0,
    }
    passed = enough_sample and all(checks.values())
    status = "pass" if passed else ("review" if enough_sample else "collecting")

    return {
        "policy_version": E1_FORWARD_POLICY_VERSION,
        "execution_policy_version": E1_POLICY_VERSION,
        "model_version": runs[0].model_version,
        "status": status,
        "public_promotion_automatic": False,
        "execution_convention": E1_FORWARD_EXECUTION_CONVENTION,
        "evidence": {
            "successful_signal_dates": len(runs),
            "captured_dates": sum(row.run_status == "captured" for row in runs),
            "abstained_dates": sum(row.run_status == "abstained" for row in runs),
            "completed_v41_trades": completed_v41_trades,
            "missing_e1_capture_dates": [value.isoformat() for value in missing_e1_dates[:20]],
            "latest_signal_date": runs[-1].as_of_date.isoformat(),
            "latest_market_date": latest_market_date.isoformat(),
        },
        "portfolio": {
            "v41_e1": _compact_portfolio(v41),
            "momentum_e1": _compact_portfolio(baseline),
            "v41_minus_momentum_total_return_percent": (
                float(v41_return) - float(baseline_return)
                if v41_return is not None and baseline_return is not None
                else None
            ),
        },
        "block_bootstrap": bootstrap,
        "checks": checks,
        "note": (
            "E1 forward evidence is replayed only from immutable daily E1 decisions tied to frozen V4.1 prediction "
            "fingerprints. Decisions are formed after session D and executed at the next tradable session open. A pass "
            "earns manual review only and cannot change the live champion automatically."
        ),
    }
