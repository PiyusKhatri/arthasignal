from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from statistics import mean
from typing import Any, Mapping, Sequence

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import Company, CorporateAction, DailyPrice, MarketIndex
from src.pipeline.train_quant_models import DEFAULT_SYMBOL_LIMIT
from archive.quant_research_v1.pipeline.validate_residual_stability_v41 import (
    _calibrate_affine_target,
    _calibrate_monotonic_residual,
    _calibrate_residual_probability,
)
from src.services.nepse_quant_research import (
    NEPSE_INDEX_NAME,
    _load_index_series,
    _sector_index_name,
)
from archive.quant_research_v1.services.quant_execution_aware import expanding_nested_folds
from archive.quant_research_v1.services.quant_decision_policy import consensus_market_regime, consensus_sector_regime
from archive.quant_research_v1.services.quant_e1_forward import shift_signal_decisions_to_next_session
from archive.quant_research_v1.services.quant_execution_policy_e1 import simulate_execution_policy_e1
from src.services.quant_features import build_feature_row
from archive.quant_research_v1.services.quant_historical_context import index_context_as_of
from archive.quant_research_v1.services.quant_residual_alpha import (
    V4_FINAL_CAPACITY,
    attach_residual_targets,
    build_baseline_candidate_pool,
    fit_baseline_expectation,
    fit_v4_classifier,
    fit_v4_regressor,
    fixed_multifactor_score,
    predict_v4_classifier,
    selection_counts,
)
from archive.quant_research_v1.services.quant_residual_stability import build_v41_predictions, select_v41_setups
from archive.quant_research_v1.services.quant_v5_dataset import build_v5_dataset_with_diagnostics
from archive.quant_research_v1.services.quant_v5_model import ScoreObservation, score_v5_policy
from archive.quant_research_v1.services.quant_v5_training import (
    V5TrainingError,
    fit_platt_calibrator as fit_v5_platt_calibrator,
    fit_v5_model_bundle,
    predict_v5_rows,
)
from archive.quant_research_v1.services.quant_v5_validation import (
    ReplayMetrics,
    build_historical_validation_report,
    derive_historical_gate_metrics,
    evaluate_historical_gate,
)

logger = logging.getLogger(__name__)
REQUIRED_OUTER_FOLDS = 4
V5_RESEARCH_MODEL_VERSION = "artha-pit-stability-v5-research-v1"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _annotate_turnover_percentiles(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return copies with tie-aware same-date turnover percentile and tercile."""
    result = [dict(row) for row in rows]
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in result:
        if row.get("date") is not None and row.get("trailing_turnover_20d") is not None:
            grouped[row["date"]].append(row)

    for date_rows in grouped.values():
        ordered = sorted(date_rows, key=lambda row: float(row["trailing_turnover_20d"]))
        if len(ordered) < 6:
            for row in ordered:
                row["turnover_percentile"] = None
                row["liquidity_bucket"] = "unclassified"
            continue
        denominator = len(ordered) - 1
        positions: dict[float, list[int]] = defaultdict(list)
        for position, row in enumerate(ordered):
            positions[float(row["trailing_turnover_20d"])].append(position)
        for row in ordered:
            tied = positions[float(row["trailing_turnover_20d"])]
            percentile = mean(tied) / denominator
            row["turnover_percentile"] = percentile
            row["liquidity_bucket"] = (
                "low"
                if percentile <= 1.0 / 3.0
                else "medium"
                if percentile <= 2.0 / 3.0
                else "high"
            )
    return result


def _trailing_market_session_turnover(
    *,
    trading_date: Any,
    market_dates: Sequence[Any],
    market_position: Mapping[Any, int],
    turnover_by_date: Mapping[Any, float],
    window: int = 20,
) -> float | None:
    """Return the exact trailing-market-session mean, zero-filling no-trade days."""
    position = market_position.get(trading_date)
    if position is None or window < 1 or position + 1 < window:
        return None
    sessions = market_dates[position - window + 1 : position + 1]
    values = [
        max(0.0, float(turnover_by_date.get(session) or 0.0))
        for session in sessions
    ]
    return mean(values) if any(value > 0.0 for value in values) else None


def _build_full_baseline_rank_history(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Rank every outcome-free eligible row, including names below the candidate cut."""
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for source in rows:
        row = dict(source)
        if row.get("date") is not None:
            grouped[row["date"]].append(row)

    history: list[dict[str, Any]] = []
    for trading_date in sorted(grouped):
        date_rows = grouped[trading_date]
        market_regime = consensus_market_regime(date_rows)
        if V4_FINAL_CAPACITY.get(market_regime, 0) <= 0:
            continue
        sector_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in date_rows:
            sector_groups[str(row.get("sector") or "unknown")].append(row)
        sector_labels = {
            sector: consensus_sector_regime(sector_rows)
            for sector, sector_rows in sector_groups.items()
        }
        eligible = [
            row
            for row in date_rows
            if sector_labels[str(row.get("sector") or "unknown")] != "lagging"
            and not (
                market_regime == "sideways"
                and sector_labels[str(row.get("sector") or "unknown")] != "leading"
            )
        ]
        ranked = sorted(eligible, key=fixed_multifactor_score, reverse=True)
        denominator = max(1, len(ranked) - 1)
        for position, row in enumerate(ranked, start=1):
            sector = str(row.get("sector") or "unknown")
            history.append(
                {
                    **row,
                    "v4_market_regime": market_regime,
                    "v4_sector_regime": sector_labels[sector],
                    "baseline_score": fixed_multifactor_score(row),
                    "baseline_rank": position,
                    "baseline_percentile": 1.0 - (position - 1) / denominator,
                }
            )
    return history


def _matched_percentile_baseline_selection(
    rows: Sequence[Mapping[str, Any]],
    selected_counts: Mapping[Any, int],
) -> dict[str, Any]:
    """Match V4.1 breadth using the protocol's frozen percentile ordering."""
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for source in rows:
        row = dict(source)
        grouped[row["date"]].append(row)

    selected: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    for trading_date in sorted(grouped):
        count = max(0, int(selected_counts.get(trading_date, 0)))
        ranked = sorted(
            grouped[trading_date],
            key=lambda row: (-float(row.get("baseline_percentile") or 0.0), str(row["symbol"])),
        )
        chosen = ranked[:count]
        selected.extend(
            {"row": row, "market_regime": row["v4_market_regime"]}
            for row in chosen
        )
        days.append(
            {
                "date": trading_date,
                "market_regime": (
                    ranked[0]["v4_market_regime"] if ranked else "high_stress"
                ),
                "capacity": count,
                "selected": len(chosen),
            }
        )
    return {"selected": selected, "days": days}


def _regime_context(
    market_series: dict[str, list[Any]],
    sector_series: dict[str, list[Any]],
    trading_date: Any,
) -> dict[str, Any]:
    market = index_context_as_of(market_series, trading_date)
    sector = index_context_as_of(sector_series, trading_date)
    market_return = market.get("return_20d_percent")
    sector_return = sector.get("return_20d_percent")
    return {
        "market_close": market.get("close"),
        "market_return_20d_percent": market_return,
        "market_return_60d_percent": market.get("return_60d_percent"),
        "market_sma50": market.get("sma50"),
        "market_sma200": market.get("sma200"),
        "market_drawdown_252d_percent": market.get("drawdown_252d_percent"),
        "market_annualized_volatility_60d_percent": market.get(
            "annualized_volatility_60d_percent"
        ),
        "sector_return_20d_percent": sector_return,
        "sector_relative_strength_20d_percent": (
            float(sector_return) - float(market_return)
            if sector_return is not None and market_return is not None
            else None
        ),
    }


def _load_v5_research_rows(
    *,
    limit: int = DEFAULT_SYMBOL_LIMIT,
) -> tuple[list[dict[str, Any]], tuple[Any, ...], dict[str, Any]]:
    """Build daily PIT candidates and aligned labels without using current status.

    The company master lacks a point-in-time delisting history. All Equity rows are
    therefore considered after their listed date, and the limitation is reported
    rather than leaking today's company status into historical eligibility.
    """
    with get_session() as session:
        companies = session.execute(
            select(Company)
            .where(Company.instrument_type == "Equity")
            .order_by(Company.symbol)
            .limit(limit)
        ).scalars().all()
        market_records = session.execute(
            select(MarketIndex)
            .where(MarketIndex.index_name == NEPSE_INDEX_NAME)
            .order_by(MarketIndex.date)
        ).scalars().all()
        market_bars = [
            {
                "date": row.date,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
            }
            for row in market_records
        ]
        market_series = {
            "dates": [row["date"] for row in market_bars],
            "closes": [row["close"] for row in market_bars],
        }
        market_position = {
            trading_date: index
            for index, trading_date in enumerate(market_series["dates"])
        }

        raw_signal_rows: list[dict[str, Any]] = []
        stock_bars: dict[str, list[dict[str, Any]]] = {}
        sector_series_cache: dict[str, dict[str, list[Any]]] = {}
        failures: list[str] = []

        for company in companies:
            try:
                prices = session.execute(
                    select(DailyPrice)
                    .where(DailyPrice.symbol == company.symbol)
                    .order_by(DailyPrice.date)
                ).scalars().all()
                bars = [
                    {
                        "symbol": company.symbol,
                        "date": row.date,
                        "open": float(row.open),
                        "high": float(row.high),
                        "low": float(row.low),
                        "close": float(row.close),
                        "adjusted_close": (
                            float(row.adjusted_close)
                            if row.adjusted_close is not None
                            else None
                        ),
                        "volume": float(row.volume or 0.0),
                        "turnover": float(row.turnover or 0.0),
                    }
                    for row in prices
                    if float(row.close) > 0.0
                ]
                stock_bars[company.symbol] = bars
                if len(bars) < 81:
                    continue

                sector_name = _sector_index_name(session, company.sector)
                cache_key = sector_name or ""
                if cache_key not in sector_series_cache:
                    sector_series_cache[cache_key] = _load_index_series(session, sector_name)
                sector_series = sector_series_cache[cache_key]

                dates = [row["date"] for row in bars]
                closes = [float(row["adjusted_close"] or row["close"]) for row in bars]
                volumes = [float(row["volume"]) for row in bars]
                turnovers = [float(row["turnover"]) for row in bars]
                turnover_by_date = {
                    row["date"]: float(row["turnover"]) for row in bars
                }
                for index in range(60, len(bars)):
                    trading_date = dates[index]
                    if company.listed_date is not None and trading_date < company.listed_date:
                        continue
                    features = build_feature_row(
                        dates=dates,
                        closes=closes,
                        volumes=volumes,
                        turnovers=turnovers,
                        index=index,
                        market_dates=market_series["dates"],
                        market_closes=market_series["closes"],
                        sector_dates=sector_series.get("dates"),
                        sector_closes=sector_series.get("closes"),
                    )
                    if features is None:
                        continue
                    trailing_turnover = _trailing_market_session_turnover(
                        trading_date=trading_date,
                        market_dates=market_series["dates"],
                        market_position=market_position,
                        turnover_by_date=turnover_by_date,
                    )
                    if trailing_turnover is None:
                        continue
                    raw_signal_rows.append(
                        {
                            "date": trading_date,
                            "index": index,
                            "symbol": company.symbol,
                            "sector": company.sector,
                            "instrument_type": "Equity",
                            "listed_date": company.listed_date,
                            "features": features,
                            "trailing_turnover_20d": trailing_turnover,
                            "regime_context": _regime_context(
                                market_series,
                                sector_series,
                                trading_date,
                            ),
                        }
                    )
            except Exception as error:  # pragma: no cover - DB-specific audit path
                logger.exception("Failed to build V5 PIT rows for %s", company.symbol)
                failures.append(f"{company.symbol}:{type(error).__name__}")

        annotated = _annotate_turnover_percentiles(raw_signal_rows)
        investable = [
            row for row in annotated if row.get("liquidity_bucket") in {"medium", "high"}
        ]
        baseline_history = _build_full_baseline_rank_history(investable)
        candidates = build_baseline_candidate_pool(investable)
        membership = [
            {"date": row["date"], "symbol": row["symbol"], "is_candidate": True}
            for row in candidates
        ]
        actions = [
            {
                "symbol": row.symbol,
                "action_date": row.action_date,
                "action_type": row.action_type.value,
            }
            for row in session.execute(
                select(CorporateAction).where(
                    CorporateAction.symbol.in_([company.symbol for company in companies])
                )
            ).scalars().all()
        ]

    labeled, label_diagnostics = build_v5_dataset_with_diagnostics(
        signal_rows=candidates,
        stock_bars=stock_bars,
        market_bars=market_bars,
        corporate_actions=actions,
        membership_history=membership,
        baseline_history=baseline_history,
        step=1,
    )
    mark_history = {
        symbol: {
            row["date"]: float(row["close"])
            for row in bars
            if row.get("close") is not None and float(row["close"]) > 0.0
        }
        for symbol, bars in stock_bars.items()
    }
    execution_history = {
        symbol: {
            row["date"]: float(row["open"])
            for row in bars
            if row.get("open") is not None and float(row["open"]) > 0.0
        }
        for symbol, bars in stock_bars.items()
    }
    return (
        labeled,
        tuple(row["date"] for row in market_bars),
        {
            "companies_considered": len(companies),
            "raw_feature_rows": len(raw_signal_rows),
            "investable_rows": len(investable),
            "baseline_rank_rows": len(baseline_history),
            "candidate_rows": len(candidates),
            "labeled_rows": len(labeled),
            "failures": len(failures),
            "failure_samples": failures[:10],
            "label_diagnostics": label_diagnostics,
            "survivorship_note": (
                "Historical delisting/suspension state is incomplete. V5 includes every Equity "
                "company after listed_date and deliberately ignores today's status field."
            ),
            "_replay": {
                "mark_history": mark_history,
                "execution_history": execution_history,
            },
        },
    )


def _aligned_v41_fold_predictions(
    train: list[dict[str, Any]],
    calibration: list[dict[str, Any]],
    test: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[tuple[Any, str], float]]:
    residual_model = fit_v4_regressor(
        train,
        target_key="residual_alpha_percent",
        clip_low=-25.0,
        clip_high=25.0,
    )
    residual_classifier = fit_v4_classifier(train)
    mae_model = fit_v4_regressor(
        train,
        target_key="mae_magnitude_percent",
        clip_low=0.0,
        clip_high=25.0,
    )
    mfe_model = fit_v4_regressor(
        train,
        target_key="mfe_percent",
        clip_low=0.0,
        clip_high=35.0,
    )
    if any(model is None for model in (residual_model, residual_classifier, mae_model, mfe_model)):
        raise V5TrainingError("V4.1 comparator head failed")

    residual, _ = _calibrate_monotonic_residual(residual_model, calibration, test)
    probability, _ = _calibrate_residual_probability(
        residual_classifier,
        calibration,
        test,
    )
    mae, _ = _calibrate_affine_target(
        mae_model,
        calibration,
        test,
        target_key="mae_magnitude_percent",
        floor=0.0,
        ceiling=25.0,
    )
    mfe, _ = _calibrate_affine_target(
        mfe_model,
        calibration,
        test,
        target_key="mfe_percent",
        floor=0.0,
        ceiling=35.0,
    )
    if not (len(test) == len(residual) == len(probability) == len(mae) == len(mfe)):
        raise V5TrainingError("V4.1 comparator prediction count mismatch")
    predictions = build_v41_predictions(test, residual, probability, mae, mfe)

    calibration_raw = predict_v4_classifier(residual_classifier, calibration)
    same_target_calibrator = fit_v5_platt_calibrator(
        calibration_raw,
        [bool(row["success_after_cost"]) for row in calibration],
    )
    test_raw = predict_v4_classifier(residual_classifier, test)
    same_target = {
        (row["date"], str(row["symbol"])): same_target_calibrator.calibrate(raw)
        for row, raw in zip(test, test_raw)
    }
    return select_v41_setups(predictions), same_target


def _validation_row(
    row: Mapping[str, Any],
    *,
    fold: int,
    probability: float | None = None,
    recalibrated_probability: float | None = None,
) -> dict[str, Any]:
    return {
        "date": row["date"],
        "symbol": str(row["symbol"]),
        "fold": fold,
        "gross_excess_return_percent": float(row["excess_return_percent"]),
        "mae_percent": float(row["max_adverse_percent"]),
        "success_probability": probability,
        "recalibrated_success_probability": recalibrated_probability,
        "status": "resolved",
        "void": False,
    }


def _evaluate_fold(fold: Mapping[str, Any]) -> dict[str, Any]:
    fold_number = int(fold["fold"])
    baseline_model = fit_baseline_expectation(fold["train"])
    train = attach_residual_targets(fold["train"], baseline_model)
    calibration = attach_residual_targets(fold["calibration"], baseline_model)
    test = attach_residual_targets(fold["test"], baseline_model)

    bundle = fit_v5_model_bundle(train, calibration, stability_age_sessions=0)
    v5_heads = predict_v5_rows(bundle, test, stability_age_sessions=0)
    head_by_key = {(item.date, item.symbol): item for item in v5_heads}

    v41_selection, v41_success_probabilities = _aligned_v41_fold_predictions(
        train,
        calibration,
        test,
    )
    counts = selection_counts(v41_selection)
    baseline_selection = _matched_percentile_baseline_selection(test, counts)
    market_sessions = tuple(fold.get("market_sessions", ()))
    market_position = {session: index for index, session in enumerate(market_sessions)}

    test_by_date: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in test:
        test_by_date[row["date"]].append(row)
    prior_scores: list[ScoreObservation] = []
    v5_selected_rows: list[dict[str, Any]] = []
    v5_probability_by_key: dict[tuple[Any, str], float] = {}
    v5_decision_by_key: dict[tuple[Any, str], Any] = {}
    for trading_date in sorted(test_by_date):
        date_rows = test_by_date[trading_date]
        outputs = [
            head_by_key[(row["date"], str(row["symbol"]))].calibrated_outputs
            for row in date_rows
        ]
        decisions = score_v5_policy(
            date_rows,
            outputs,
            stability_age_sessions=0,
            prior_scores=prior_scores,
            prior_market_dates=(
                market_sessions[max(0, market_position[trading_date] - 5) : market_position[trading_date]]
                if trading_date in market_position
                else ()
            ),
        )
        row_by_symbol = {str(row["symbol"]): row for row in date_rows}
        scored = [decision for decision in decisions if decision.status == "scored"]
        v5_decision_by_key.update(
            {(decision.date, decision.symbol): decision for decision in scored}
        )
        breadth = int(counts.get(trading_date, 0))
        for decision in scored[:breadth]:
            row = row_by_symbol[decision.symbol]
            v5_selected_rows.append(row)
            v5_probability_by_key[(trading_date, decision.symbol)] = (
                decision.calibrated_outputs.success_after_cost_probability
            )
        for decision in scored:
            prior_scores.append(
                ScoreObservation(
                    date=trading_date,
                    symbol=decision.symbol,
                    composite_score=float(decision.current_composite_score),
                )
            )

    v41_rows = [candidate["row"] for candidate in v41_selection.get("selected", [])]
    baseline_rows = [candidate["row"] for candidate in baseline_selection.get("selected", [])]
    v41_probability_by_key = {
        (candidate["row"]["date"], str(candidate["row"]["symbol"])): float(
            candidate["probability_positive_residual"]
        )
        for candidate in v41_selection.get("selected", [])
    }
    v5_replay_by_date: dict[Any, list[dict[str, Any]]] = {
        trading_date: [] for trading_date in test_by_date
    }
    for row in v5_selected_rows:
        key = (row["date"], str(row["symbol"]))
        decision = v5_decision_by_key[key]
        v5_replay_by_date[row["date"]].append(
            {
                "row": row,
                "final_score": float(decision.shrunk_score),
                "override_action": "promote",
                "expected_excess_return_percent": 0.0,
                "market_regime": row["v4_market_regime"],
                "sector_regime": row["v4_sector_regime"],
            }
        )
    v41_replay_by_date: dict[Any, list[dict[str, Any]]] = {
        trading_date: [] for trading_date in test_by_date
    }
    for candidate in v41_selection.get("selected", []):
        v41_replay_by_date[candidate["row"]["date"]].append(candidate)
    baseline_replay_by_date: dict[Any, list[dict[str, Any]]] = {
        trading_date: [] for trading_date in test_by_date
    }
    for candidate in baseline_selection.get("selected", []):
        baseline_replay_by_date[candidate["row"]["date"]].append(candidate)
    climatology = mean(1.0 if bool(row["success_after_cost"]) else 0.0 for row in train)
    return {
        "fold": fold_number,
        "v5_rows": [
            _validation_row(
                row,
                fold=fold_number,
                probability=v5_probability_by_key[(row["date"], str(row["symbol"]))],
            )
            for row in v5_selected_rows
        ],
        "v41_rows": [
            _validation_row(
                row,
                fold=fold_number,
                probability=v41_probability_by_key.get((row["date"], str(row["symbol"]))),
                recalibrated_probability=v41_success_probabilities.get(
                    (row["date"], str(row["symbol"]))
                ),
            )
            for row in v41_rows
        ],
        "baseline_rows": [
            _validation_row(row, fold=fold_number) for row in baseline_rows
        ],
        "training_climatology": climatology,
        "replay_runs": {
            "v5": sorted(v5_replay_by_date.items()),
            "v41": sorted(v41_replay_by_date.items()),
            "baseline": sorted(baseline_replay_by_date.items()),
        },
        "diagnostics": {
            "v41_comparator": "architecture_locked_aligned_oos_reference",
            "train_rows": len(train),
            "calibration_rows": len(calibration),
            "test_rows": len(test),
            "v5_selected": len(v5_selected_rows),
            "v41_selected": len(v41_rows),
            "baseline_selected": len(baseline_rows),
        },
    }


def _split_manifest_fingerprint(
    rows: Sequence[Mapping[str, Any]],
    folds: Sequence[Mapping[str, Any]],
) -> str:
    """Hash every source row and its purged assignments before evaluation."""
    assignments: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fold in folds:
        fold_number = int(fold["fold"])
        for segment in ("train", "calibration", "test"):
            for row in fold[segment]:
                row_payload = _jsonable(row)
                row_fingerprint = hashlib.sha256(
                    json.dumps(
                        row_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                assignments[row_fingerprint].append(
                    {
                        "fold": fold_number,
                        "segment": segment,
                    }
                )
    manifest: list[dict[str, Any]] = []
    for row in rows:
        row_payload = _jsonable(row)
        row_fingerprint = hashlib.sha256(
            json.dumps(
                row_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        row_assignments = sorted(
            assignments.get(row_fingerprint, ()),
            key=lambda item: (item["fold"], item["segment"]),
        )
        manifest.append(
            {
                "date": _jsonable(row.get("date")),
                "label_end_date": _jsonable(row.get("label_end_date")),
                "symbol": str(row.get("symbol") or ""),
                "assignments": row_assignments,
                "exclusion_reason": None if row_assignments else "not_assigned_to_any_outer_fold",
                "row_fingerprint": row_fingerprint,
            }
        )
    manifest.sort(
        key=lambda item: (
            item["date"],
            item["symbol"],
            item["row_fingerprint"],
        )
    )
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _replay_market_dates(
    market_sessions: Sequence[Any],
    signal_dates: Sequence[Any],
) -> list[Any]:
    sessions = list(market_sessions)
    positions = {session: index for index, session in enumerate(sessions)}
    if not signal_dates or any(session not in positions for session in signal_dates):
        return []
    first = positions[min(signal_dates)] + 1
    last = min(len(sessions) - 1, positions[max(signal_dates)] + 21)
    return sessions[first : last + 1] if first <= last else []


def _build_replay_metrics(
    *,
    fold_results: Sequence[Mapping[str, Any]],
    market_sessions: Sequence[Any],
    replay_inputs: Mapping[str, Any] | None,
) -> tuple[ReplayMetrics, dict[str, Any]]:
    if not replay_inputs:
        return ReplayMetrics(), {
            "status": "missing_replay_data_fail_closed",
            "note": "No price payload was available for the frozen next-open replay.",
        }

    signal_runs: dict[str, list[tuple[Any, list[dict[str, Any]]]]] = {
        "v5": [],
        "v41": [],
        "baseline": [],
    }
    for fold in fold_results:
        replay_runs = fold.get("replay_runs")
        if not isinstance(replay_runs, Mapping):
            return ReplayMetrics(), {
                "status": "missing_fold_replay_fail_closed",
                "note": f"Fold {fold.get('fold')} did not emit replay selections.",
            }
        for strategy in signal_runs:
            signal_runs[strategy].extend(replay_runs.get(strategy, ()))

    signal_dates = [session for session, _ in signal_runs["v5"]]
    if len(signal_dates) != len(set(signal_dates)):
        return ReplayMetrics(), {
            "status": "duplicate_replay_date_fail_closed",
            "note": "A replay signal date appeared in more than one outer test fold.",
        }
    replay_dates = _replay_market_dates(market_sessions, signal_dates)
    if not replay_dates:
        return ReplayMetrics(), {
            "status": "missing_replay_calendar_fail_closed",
            "note": "The market calendar could not resolve the next-session replay window.",
        }

    simulations: dict[str, dict[str, Any]] = {}
    for strategy in signal_runs:
        predictions = shift_signal_decisions_to_next_session(
            sorted(signal_runs[strategy]),
            list(market_sessions),
        )
        simulations[strategy] = simulate_execution_policy_e1(
            market_dates=replay_dates,
            predictions_by_date={
                session: predictions.get(session, []) for session in replay_dates
                if session in predictions
            },
            price_history=dict(replay_inputs.get("mark_history") or {}),
            execution_price_history=dict(replay_inputs.get("execution_history") or {}),
            mode="baseline" if strategy == "baseline" else "v41",
            terminal_liquidation=True,
        )
    if any(result.get("status") != "simulated" for result in simulations.values()):
        return ReplayMetrics(), {
            "status": "replay_simulation_failed_closed",
            "simulator_status": {
                strategy: result.get("status") for strategy, result in simulations.items()
            },
        }

    def metric(strategy: str, key: str) -> float | None:
        value = simulations[strategy].get(key)
        return float(value) if value is not None else None

    metrics = ReplayMetrics(
        v5_compounded_return_percent=metric("v5", "total_return_percent"),
        v41_compounded_return_percent=metric("v41", "total_return_percent"),
        v5_annualized_turnover=metric("v5", "annualized_turnover_x"),
        v5_mean_holding_sessions=metric("v5", "mean_completed_holding_sessions"),
        v5_max_drawdown_percent=metric("v5", "max_drawdown_percent"),
        v41_max_drawdown_percent=metric("v41", "max_drawdown_percent"),
        baseline_compounded_return_percent=metric("baseline", "total_return_percent"),
        baseline_annualized_turnover=metric("baseline", "annualized_turnover_x"),
        baseline_mean_holding_sessions=metric(
            "baseline", "mean_completed_holding_sessions"
        ),
        baseline_max_drawdown_percent=metric("baseline", "max_drawdown_percent"),
    )
    return metrics, {
        "status": "complete",
        "signal_dates": len(signal_dates),
        "market_sessions": len(replay_dates),
        "execution_convention": "signal close D; execute on next NEPSE-session open",
        "simulator_status": {
            strategy: result["status"] for strategy, result in simulations.items()
        },
    }


def validate_quant_v5(
    *,
    limit: int = DEFAULT_SYMBOL_LIMIT,
    folds: int = REQUIRED_OUTER_FOLDS,
) -> dict[str, Any]:
    if folds != REQUIRED_OUTER_FOLDS:
        return {
            "status": "invalid_protocol",
            "model_version": V5_RESEARCH_MODEL_VERSION,
            "requested_folds": folds,
            "required_folds": REQUIRED_OUTER_FOLDS,
        }

    rows, market_sessions, dataset = _load_v5_research_rows(limit=limit)
    dataset_public = dict(dataset)
    replay_inputs = dataset_public.pop("_replay", None)
    fold_specs = expanding_nested_folds(rows, folds=folds)
    if len(fold_specs) != REQUIRED_OUTER_FOLDS:
        return {
            "status": "insufficient_valid_folds",
            "model_version": V5_RESEARCH_MODEL_VERSION,
            "valid_folds": len(fold_specs),
            "required_folds": REQUIRED_OUTER_FOLDS,
            "dataset": _jsonable(dataset_public),
        }
    split_manifest_fingerprint = _split_manifest_fingerprint(rows, fold_specs)
    outer_test_dates = sorted(
        {row["date"] for fold in fold_specs for row in fold["test"]}
    )
    evaluation_sessions = tuple(
        session
        for session in market_sessions
        if outer_test_dates[0] <= session <= outer_test_dates[-1]
    )

    fold_results: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for fold in fold_specs:
        try:
            fold_results.append(
                _evaluate_fold({**fold, "market_sessions": market_sessions})
            )
        except (V5TrainingError, ValueError) as error:
            invalid.append(
                {
                    "fold": fold.get("fold"),
                    "error": type(error).__name__,
                    "reason": str(error),
                }
            )
    if invalid or len(fold_results) != REQUIRED_OUTER_FOLDS:
        return {
            "status": "invalid_fold_results",
            "model_version": V5_RESEARCH_MODEL_VERSION,
            "valid_folds": len(fold_results),
            "required_folds": REQUIRED_OUTER_FOLDS,
            "invalid_folds": invalid,
            "dataset": _jsonable(dataset_public),
            "split_manifest_fingerprint": split_manifest_fingerprint,
        }

    report = build_historical_validation_report(
        v5_rows=[row for fold in fold_results for row in fold["v5_rows"]],
        v41_rows=[row for fold in fold_results for row in fold["v41_rows"]],
        baseline_rows=[row for fold in fold_results for row in fold["baseline_rows"]],
        market_sessions=evaluation_sessions,
        training_climatology_by_fold={
            fold["fold"]: fold["training_climatology"] for fold in fold_results
        },
        valid_purged_folds=tuple(fold["fold"] for fold in fold_results),
    )
    replay, replay_status = _build_replay_metrics(
        fold_results=fold_results,
        market_sessions=market_sessions,
        replay_inputs=replay_inputs,
    )
    metrics = derive_historical_gate_metrics(report, replay)
    gate = evaluate_historical_gate(metrics)
    return {
        "status": "ready",
        "model_version": V5_RESEARCH_MODEL_VERSION,
        "development_verdict": "historical_admissible" if gate.status == "pass" else "continue_research",
        "dataset": _jsonable(dataset_public),
        "split_manifest_fingerprint": split_manifest_fingerprint,
        "evaluation_market_sessions": len(evaluation_sessions),
        "fold_diagnostics": [_jsonable(fold["diagnostics"]) for fold in fold_results],
        "historical_report": _jsonable(report),
        "historical_gate_metrics": _jsonable(metrics),
        "historical_gate": _jsonable(gate),
        "replay": {**_jsonable(replay_status), "metrics": _jsonable(replay)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the frozen V5 research challenger")
    parser.add_argument("--limit", type=int, default=DEFAULT_SYMBOL_LIMIT)
    parser.add_argument("--folds", type=int, default=REQUIRED_OUTER_FOLDS)
    args = parser.parse_args()
    print(json.dumps(validate_quant_v5(limit=args.limit, folds=args.folds), indent=2, default=str))


if __name__ == "__main__":
    main()
