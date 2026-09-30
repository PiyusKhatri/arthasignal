from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import requests

from src.config import settings

logger = logging.getLogger(__name__)

MISTRAL_CHAT_API_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_MODEL = "mistral-small-latest"
CACHE_TTL_SECONDS = 1800

_analysis_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _generate_cache_key(
    symbol: str,
    as_of_date: str | None,
    artha_score: int,
    rating: str,
    confidence_score: int,
    evidence: dict[str, Any] | None,
    market_regime: dict[str, Any] | None,
    forward_validation: dict[str, Any] | None,
) -> str:
    payload = {
        "symbol": symbol,
        "as_of_date": as_of_date,
        "artha_score": artha_score,
        "rating": rating,
        "confidence_score": confidence_score,
        "evidence": evidence or {},
        "market_regime": market_regime or {},
        "forward_validation": forward_validation or {},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _format_evidence_fact(evidence: dict[str, Any] | None) -> str:
    evidence = evidence or {}
    if evidence.get("scope") != "market_wide_signal_backtest":
        return ""

    sample = int(evidence.get("min_sample_size") or 0)
    edge = evidence.get("average_edge_vs_baseline")
    avg_return = evidence.get("weighted_average_return")
    parts: list[str] = []

    if edge is not None:
        parts.append(f"historical edge versus baseline is {float(edge):+.2f} percentage points")
    if sample:
        parts.append(f"minimum supporting sample is {sample}")
    if avg_return is not None:
        parts.append(f"weighted average forward return is {float(avg_return):+.2f}%")

    if not parts:
        return ""
    return " Market-wide signal evidence: " + "; ".join(parts) + "."


def _rule_based_fallback(
    symbol: str,
    company_name: str,
    artha_score: int,
    rating: str,
    strengths: list[str],
    warnings: list[str],
    backtests: list[dict[str, Any]],
    technical: dict[str, Any],
    *,
    confidence_score: int = 0,
    evidence: dict[str, Any] | None = None,
    market_regime: dict[str, Any] | None = None,
    forward_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del backtests, technical
    evidence_fact = _format_evidence_fact(evidence)
    regime_state = str((market_regime or {}).get("state") or "unavailable")
    forward_state = str((forward_validation or {}).get("state") or "unavailable")

    if rating == "strong_setup":
        summary = (
            f"{company_name} ({symbol}) has a strong evidence-weighted setup score of {artha_score}/100 "
            f"with {confidence_score}/100 evidence confidence."
        )
    elif rating == "positive_setup":
        summary = (
            f"{company_name} ({symbol}) has a constructive evidence-weighted setup score of {artha_score}/100 "
            f"with {confidence_score}/100 evidence confidence."
        )
    elif rating == "neutral":
        summary = (
            f"{company_name} ({symbol}) is currently a neutral setup at {artha_score}/100. "
            f"The evidence confidence is {confidence_score}/100, so the score should not be read as a probability of profit."
        )
    else:
        summary = (
            f"{company_name} ({symbol}) currently has a weak evidence-weighted setup score of {artha_score}/100 "
            f"with {confidence_score}/100 evidence confidence."
        )

    if regime_state != "unavailable":
        summary += f" The broader NEPSE regime is classified as {regime_state}."
    summary += evidence_fact

    if forward_state == "collecting":
        summary += " Forward paper-trade validation is still collecting, so the historical edge is not yet confirmed live."
    elif forward_state == "failed":
        summary += " At least one active signal has not passed the forward-validation gate."

    confidence_reason_parts: list[str] = []
    if evidence and evidence.get("scope") == "market_wide_signal_backtest":
        confidence_reason_parts.append("historical evidence is market-wide signal evidence, not stock-specific backtesting")
    if forward_state:
        confidence_reason_parts.append(f"forward validation is {forward_state}")
    if regime_state != "unavailable":
        confidence_reason_parts.append(f"market regime is {regime_state}")
    confidence_reason = "; ".join(confidence_reason_parts) or "Evidence is limited; treat the score as a descriptive snapshot"

    key_takeaway = strengths[0] if strengths else (warnings[0] if warnings else "No high-conviction evidence is established.")

    return {
        "summary": summary.strip(),
        "confidence_reason": confidence_reason.capitalize() + ".",
        "key_takeaway": key_takeaway,
        "provider": "rule_engine_fallback",
    }


def generate_ai_analyst_commentary(
    *,
    symbol: str,
    company_name: str,
    sector: str | None,
    as_of_date: str | None,
    artha_score: int,
    rating: str,
    confidence_score: int,
    score_breakdown: dict[str, int],
    strengths: list[str],
    warnings: list[str],
    technical: dict[str, Any],
    signals: list[dict[str, Any]],
    backtests: list[dict[str, Any]],
    evidence: dict[str, Any],
    market_regime: dict[str, Any],
    forward_validation: dict[str, Any],
) -> dict[str, Any]:
    """Explain structured quantitative evidence; the LLM never creates the score or signal."""
    cache_key = _generate_cache_key(
        symbol,
        as_of_date,
        artha_score,
        rating,
        confidence_score,
        evidence,
        market_regime,
        forward_validation,
    )
    now = time.time()

    if cache_key in _analysis_cache:
        ts, cached_val = _analysis_cache[cache_key]
        if now - ts < CACHE_TTL_SECONDS:
            return cached_val

    api_key = settings.mistral_api_key
    if not api_key:
        fallback = _rule_based_fallback(
            symbol,
            company_name,
            artha_score,
            rating,
            strengths,
            warnings,
            backtests,
            technical,
            confidence_score=confidence_score,
            evidence=evidence,
            market_regime=market_regime,
            forward_validation=forward_validation,
        )
        _analysis_cache[cache_key] = (now, fallback)
        return fallback

    prompt_data = {
        "symbol": symbol,
        "company_name": company_name,
        "sector": sector,
        "as_of_date": as_of_date,
        "artha_score": artha_score,
        "rating": rating,
        "evidence_confidence_score": confidence_score,
        "score_breakdown": score_breakdown,
        "strengths": strengths[:4],
        "risk_warnings": warnings[:4],
        "technical_summary": {
            "price": technical.get("latest_price"),
            "sma_50": technical.get("sma_50"),
            "sma_200": technical.get("sma_200"),
            "rsi": technical.get("rsi"),
            "macd": technical.get("macd"),
        },
        "active_signals": [s.get("signal_name") for s in signals[:4]],
        "historical_evidence": evidence,
        "market_regime": market_regime,
        "forward_validation": forward_validation,
        "historical_backtest_rows": backtests[:4],
    }

    system_prompt = (
        "You are ArthaSignal's quantitative evidence explainer for NEPSE. "
        "The deterministic engine has already produced the score; you must only explain the supplied structured facts. "
        "Do not invent probabilities, target prices, support/resistance, accumulation/distribution, or stock-specific backtest claims. "
        "Historical BacktestResult evidence is market-wide signal evidence unless the payload explicitly says otherwise. "
        "If forward validation is collecting or failed, state that limitation clearly. "
        "Do not tell the user to buy, sell, hold, enter, exit, or allocate capital. "
        "Write a concise 2-3 sentence synthesis and return valid JSON only with keys "
        "'summary', 'confidence_reason', and 'key_takeaway'."
    )

    try:
        response = requests.post(
            MISTRAL_CHAT_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEFAULT_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Explain this deterministic stock snapshot:\n{json.dumps(prompt_data)}"},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
                "max_tokens": 260,
            },
            timeout=8,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        result = {
            "summary": str(parsed.get("summary", "")).strip(),
            "confidence_reason": str(parsed.get("confidence_reason", "")).strip(),
            "key_takeaway": str(parsed.get("key_takeaway", "")).strip(),
            "provider": "mistral_ai",
        }
        if not result["summary"] or not result["confidence_reason"] or not result["key_takeaway"]:
            raise ValueError("AI response omitted required evidence-explanation fields")
        _analysis_cache[cache_key] = (now, result)
        return result
    except Exception as exc:
        logger.warning("Mistral AI analyst call failed (%s); using deterministic fallback", exc)
        fallback = _rule_based_fallback(
            symbol,
            company_name,
            artha_score,
            rating,
            strengths,
            warnings,
            backtests,
            technical,
            confidence_score=confidence_score,
            evidence=evidence,
            market_regime=market_regime,
            forward_validation=forward_validation,
        )
        _analysis_cache[cache_key] = (now, fallback)
        return fallback
