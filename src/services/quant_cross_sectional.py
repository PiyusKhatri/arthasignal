from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import Company
from src.services.quant_features import build_feature_row
from src.services.quant_model_store import load_latest_quant_model, predict_persisted_quant_model


def build_cross_sectional_prediction(session: Session, symbol: str) -> dict[str, Any]:
    """Score today's stock state with the latest persisted pooled NEPSE model."""
    from src.services.nepse_quant_research import NEPSE_INDEX_NAME, _load_index_series, _load_stock_series, _sector_index_name

    company = session.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
    if company is None:
        return {"available": False, "candidate_available": False, "reason": "unknown_symbol"}

    model = load_latest_quant_model(session)
    if model is None:
        return {"available": False, "candidate_available": False, "reason": "no_trained_model"}

    stock = _load_stock_series(session, symbol)
    market = _load_index_series(session, NEPSE_INDEX_NAME)
    sector_name = _sector_index_name(session, company.sector)
    sector = _load_index_series(session, sector_name)
    if not stock["closes"] or not market["closes"]:
        return {
            "available": False,
            "candidate_available": False,
            "model_version": model.get("model_version"),
            "reason": "insufficient_current_features",
        }

    features = build_feature_row(
        dates=stock["dates"],
        closes=stock["closes"],
        volumes=stock["volumes"],
        turnovers=stock["turnovers"],
        index=len(stock["closes"]) - 1,
        market_dates=market["dates"],
        market_closes=market["closes"],
        sector_dates=sector["dates"],
        sector_closes=sector["closes"],
    )
    if features is None:
        return {
            "available": False,
            "candidate_available": False,
            "model_version": model.get("model_version"),
            "reason": "insufficient_current_features",
        }

    prediction = predict_persisted_quant_model(model, features)
    prediction["reason"] = "promoted" if prediction.get("available") else "challenger_not_promoted"
    return prediction


def _weighted_probability(components: list[tuple[float | None, float]]) -> float | None:
    usable = [(float(value), weight) for value, weight in components if value is not None]
    if not usable:
        return None
    total_weight = sum(weight for _, weight in usable)
    return sum(value * weight for value, weight in usable) / total_weight


def _relabel_decision(research: dict[str, Any], probability: float, confidence: int) -> None:
    decision = research.get("decision", {})
    confluence = research.get("confluence", {})
    market = research.get("market_regime", {})
    sector = research.get("sector_regime", {})
    liquidity = research.get("liquidity", {})
    event_risk = research.get("event_risk", {})
    analogs = research.get("historical_analogs", {})
    validation = research.get("forward_validation", {})
    supports = int(confluence.get("supportive_dimensions") or 0)
    expected_excess = analogs.get("expected_excess_return_percent")

    reasons: list[str] = []
    if confidence < 30:
        label = "no_qualified_setup"
        reasons.append("The probability ensemble does not yet have enough reliable evidence.")
    elif event_risk.get("level") == "high":
        label = "event_risk"
        reasons.append("A material corporate/supply event prevents a high-conviction setup label.")
    elif liquidity.get("quality") in {"low", "unavailable"}:
        label = "liquidity_constrained"
        reasons.append("Liquidity quality is too weak for a high-conviction signal.")
    elif (
        probability >= 0.70
        and confidence >= 70
        and supports >= 7
        and market.get("state") not in {"bear", "high_stress"}
        and sector.get("state") != "bear"
        and (expected_excess is None or float(expected_excess) > 1.5)
    ):
        label = "strong_candidate"
        reasons.append("Promoted ML, historical evidence and confluence align strongly.")
    elif probability >= 0.60 and confidence >= 50 and supports >= 6:
        label = "positive_candidate"
        reasons.append("Promoted ML and multiple independent dimensions support a positive setup.")
    elif probability <= 0.40 and confidence >= 55:
        label = "weak_candidate"
        reasons.append("The promoted ensemble sees a low probability of beating NEPSE after costs.")
    else:
        label = "neutral"
        reasons.append("Evidence remains mixed; ArthaSignal should not force a directional call.")

    public_gate_open = bool(validation.get("public_high_confidence_enabled"))
    if not public_gate_open:
        public_label = "research_only"
    elif label == "strong_candidate":
        public_label = "strong_setup"
    elif label == "positive_candidate":
        public_label = "positive_setup"
    elif label == "weak_candidate":
        public_label = "weak"
    else:
        public_label = "neutral"

    decision.update(
        {
            "research_label": label,
            "public_label": public_label,
            "public_eligible": public_gate_open and label in {"strong_candidate", "positive_candidate", "weak_candidate"},
            "probability_outperform_nepse_after_cost": probability,
            "confidence_score": confidence,
            "reasons": reasons,
        }
    )


def enhance_quant_research_with_cross_sectional(
    session: Session,
    symbol: str,
    research: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if research is None:
        return None

    cross = build_cross_sectional_prediction(session, symbol)
    research["cross_sectional_ml"] = cross
    if not cross.get("available"):
        return research

    probability_model = research.setdefault("probability_model", {})
    components = probability_model.setdefault("components", {})
    analog_probability = research.get("historical_analogs", {}).get("probability_outperform_after_cost")
    ridge_probability = components.get("ridge_logistic_probability")
    xgb_probability = cross.get("calibrated_probability")

    combined = _weighted_probability(
        [
            (xgb_probability, 0.55),
            (analog_probability, 0.30),
            (ridge_probability, 0.15),
        ]
    )
    if combined is None:
        return research

    present = [
        float(value)
        for value in (xgb_probability, analog_probability, ridge_probability)
        if value is not None
    ]
    disagreement = max(present) - min(present) if len(present) >= 2 else 0.0
    previous_confidence = int(probability_model.get("confidence_score") or 0)
    holdout_precision = (
        cross.get("holdout_metrics", {})
        .get("calibrated_classifier", {})
        .get("high_confidence_precision")
    )
    holdout_confidence = round(float(holdout_precision) * 100) if holdout_precision is not None else 55
    confidence = round(0.55 * max(previous_confidence, 35) + 0.45 * holdout_confidence - disagreement * 70)
    confidence = max(0, min(92, confidence))

    components.update(
        {
            "cross_sectional_xgboost_probability": xgb_probability,
            "cross_sectional_rank_score": cross.get("rank_score"),
            "model_disagreement": disagreement,
        }
    )
    probability_model.update(
        {
            "probability_outperform_after_cost": combined,
            "confidence_score": confidence,
            "model_type": "promoted_cross_sectional_xgboost_analogue_ridge_ensemble",
            "model_version": cross.get("model_version"),
            "trained_through": cross.get("trained_through"),
            "calibration_status": "held_out_platt_plus_forward_shadow_gate",
        }
    )

    confluence = research.get("confluence", {})
    dimensions = confluence.get("dimensions", {})
    if dimensions:
        old = bool(dimensions.get("historical_probability"))
        new = combined >= 0.58
        dimensions["historical_probability"] = new
        supports = int(confluence.get("supportive_dimensions") or 0)
        if old != new:
            supports += 1 if new else -1
        confluence["supportive_dimensions"] = max(0, supports)
        total = int(confluence.get("total_dimensions") or len(dimensions) or 1)
        confluence["support_ratio"] = confluence["supportive_dimensions"] / total

    _relabel_decision(research, combined, confidence)
    return research
