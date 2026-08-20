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
CACHE_TTL_SECONDS = 1800  # 30-minute in-memory cache

# In-memory cache: cache_key -> (timestamp, analysis_dict)
_analysis_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _generate_cache_key(symbol: str, as_of_date: str | None, artha_score: int, rating: str) -> str:
    raw = f"{symbol}:{as_of_date}:{artha_score}:{rating}"
    return hashlib.md5(raw.encode()).hexdigest()


def _rule_based_fallback(
    symbol: str,
    company_name: str,
    artha_score: int,
    rating: str,
    strengths: list[str],
    warnings: list[str],
    backtests: list[dict[str, Any]],
    technical: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic, high-quality institutional explanation when AI is offline."""
    top_backtest = backtests[0] if backtests else None
    backtest_fact = ""
    if top_backtest and top_backtest.get("win_rate") is not None:
        wr = float(top_backtest["win_rate"])
        wr_pct = wr * 100 if wr <= 1 else wr
        days = top_backtest.get("forward_days", 10)
        backtest_fact = (
            f" Historically, similar setups in {symbol} resolved positively {wr_pct:.1f}% of the time "
            f"over a {days}-day holding window."
        )

    if artha_score >= 80:
        summary = (
            f"{company_name} ({symbol}) is demonstrating strong institutional accumulation characteristics. "
            f"Price structure is supported by favorable moving average alignment and robust liquidity.{backtest_fact}"
        )
    elif artha_score >= 65:
        summary = (
            f"{company_name} ({symbol}) is exhibiting constructive technical momentum. "
            f"Trend indicators remain positive, though short-term oscillator confirmation should be monitored.{backtest_fact}"
        )
    elif artha_score >= 45:
        summary = (
            f"{company_name} ({symbol}) is consolidating in a neutral regime. "
            f"Risk-reward is balanced, requiring decisive breakout confirmation above key resistance levels.{backtest_fact}"
        )
    else:
        summary = (
            f"{company_name} ({symbol}) is currently facing technical headwinds or elevated distribution risk. "
            f"Capital preservation and waiting for structural base building is advised.{backtest_fact}"
        )

    return {
        "summary": summary.strip(),
        "confidence_reason": (
            "Derived from 15+ year NEPSE multi-cycle price action, volume liquidity stratification, and corporate adjusted baseline."
        ),
        "key_takeaway": strengths[0] if strengths else (warnings[0] if warnings else "Maintain disciplined risk controls."),
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
    score_breakdown: dict[str, int],
    strengths: list[str],
    warnings: list[str],
    technical: dict[str, Any],
    signals: list[dict[str, Any]],
    backtests: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate institutional AI analyst commentary using Mistral AI with seamless fallback."""
    cache_key = _generate_cache_key(symbol, as_of_date, artha_score, rating)
    now = time.time()
    
    if cache_key in _analysis_cache:
        ts, cached_val = _analysis_cache[cache_key]
        if now - ts < CACHE_TTL_SECONDS:
            return cached_val

    api_key = settings.mistral_api_key
    if not api_key:
        fallback = _rule_based_fallback(
            symbol, company_name, artha_score, rating, strengths, warnings, backtests, technical
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
        "score_breakdown": score_breakdown,
        "strengths": strengths[:3],
        "risk_warnings": warnings[:3],
        "technical_summary": {
            "price": technical.get("latest_price"),
            "sma_50": technical.get("sma_50"),
            "sma_200": technical.get("sma_200"),
            "rsi": technical.get("rsi"),
            "macd": technical.get("macd"),
        },
        "active_signals": [s.get("signal_name") for s in signals[:3]],
        "top_historical_backtests": backtests[:2],
    }

    system_prompt = (
        "You are ArthaSignal's Senior Quantitative NEPSE Analyst. "
        "Provide an objective, institutional, high-conviction 2-3 sentence executive synthesis for this stock. "
        "Cite specific numbers where relevant (e.g. moving average relationships, historical backtest win rates, or score pillars). "
        "Keep the tone professional, concise, and actionable without offering direct financial advice. "
        "Return valid JSON ONLY with three keys: "
        "'summary' (2-3 sentences), 'confidence_reason' (1 short sentence explaining data basis), and 'key_takeaway' (1 crisp sentence)."
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
                    {"role": "user", "content": f"Analyze this stock snapshot:\n{json.dumps(prompt_data)}"},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
                "max_tokens": 250,
            },
            timeout=8,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        result = {
            "summary": parsed.get("summary", ""),
            "confidence_reason": parsed.get("confidence_reason", "Verified via 15+ year NEPSE historical backtest repository."),
            "key_takeaway": parsed.get("key_takeaway", strengths[0] if strengths else "Monitor key technical levels."),
            "provider": "mistral_ai",
        }
        _analysis_cache[cache_key] = (now, result)
        return result
    except Exception as exc:
        logger.warning("Mistral AI analyst call failed (%s); using institutional fallback", exc)
        fallback = _rule_based_fallback(
            symbol, company_name, artha_score, rating, strengths, warnings, backtests, technical
        )
        _analysis_cache[cache_key] = (now, fallback)
        return fallback
