from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from math import sqrt
from statistics import pstdev
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import (
    BacktestResult,
    Company,
    DailyPrice,
    Fundamental,
    MarketIndex,
    SignalCall,
    SignalConfidence,
    SignalTimeframe,
    SymbolLiquidityTier,
    TechnicalSignal,
)
from src.pipeline.run_signal_backtests import build_signal_conditions
from src.pipeline.signal_validation_policy import VALIDATION_SIGNAL_SPECS
from src.pipeline.validation_status import build_validation_status

NEPSE_INDEX_NAME = "NEPSE Index"
PREFERRED_BACKTEST_HORIZON = 20

SCORE_MAX = {
    "trend": 20,
    "momentum": 15,
    "liquidity": 10,
    "reliability": 30,
    "risk_adjustment": 15,
    "valuation": 10,
}


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _probability(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if number > 1:
        number /= 100.0
    return max(0.0, min(1.0, number))


def _enum_value(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _rating(score: int, confidence_score: int = 100, has_signal: bool = True) -> str:
    """Keep strong labels gated by current signal evidence and uncertainty."""
    if score >= 80 and confidence_score >= 70 and has_signal:
        return "strong_setup"
    if score >= 65 and confidence_score >= 45 and has_signal:
        return "positive_setup"
    if score >= 50:
        return "neutral"
    return "weak"


def _liquidity_score(tier: Any) -> int:
    if not tier:
        return 0
    value = str(tier).lower()
    if "high" in value or value in {"a", "tier_a"}:
        return 10
    if "medium" in value or value in {"b", "tier_b"}:
        return 7
    return 3


def _confidence_score(rows: Iterable[Any], signal_names: set[str]) -> int:
    """Compatibility helper: classify historical evidence quality on 0-100."""
    score = 0
    for row in rows:
        if row.signal_name not in signal_names:
            continue
        tier = _enum_value(row.tier).lower()
        edge = _number(getattr(row, "avg_win_rate_minus_baseline", None))
        if "high_confidence" in tier and (edge is None or edge > 0):
            score = max(score, 80)
        elif "unreliable_low_sample" in tier:
            score = max(score, 30)
        elif "weak_or_no_edge" in tier or "decayed" in tier or "inconsistent" in tier:
            score = max(score, 20)
        else:
            score = max(score, 40)
    return score


def _confidence_level(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _trend_strength(score: int) -> str:
    if score >= 16:
        return "strong"
    if score >= 9:
        return "moderate"
    return "weak"


def _signal_quality(reliability_score: int, signal_names: set[str]) -> str:
    if reliability_score >= 22 and signal_names:
        return "high"
    if reliability_score >= 8 and signal_names:
        return "medium"
    return "low"


def _signal_direction(signal_name: str) -> str:
    """
    v1 validation is long-only: a win means future price > entry price.
    Therefore an overbought observation is not represented as a short trade.
    """
    if signal_name in {"rsi_14 < 30 (oversold)", "close < bollinger_lower"}:
        return "bullish"
    return "neutral"


def _active_validation_signal_names(
    technical: TechnicalSignal | None,
    price: Any,
    liquidity: Any,
) -> set[str]:
    """Evaluate today's signal state from the latest snapshot, never from an open validation call."""
    if technical is None or price is None:
        return set()

    conditions = build_signal_conditions()
    active: set[str] = set()
    liquidity_value = str(liquidity) if liquidity is not None else None

    for signal_name, spec in VALIDATION_SIGNAL_SPECS.items():
        condition = conditions.get(signal_name)
        if condition is None:
            continue
        if not bool(condition(technical, price, None, None)):
            continue
        if spec.required_liquidity_tier and liquidity_value != spec.required_liquidity_tier:
            continue
        active.add(signal_name)

    return active


def _backtest_summary(rows: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "signal_name": row.signal_name,
            "forward_days": row.forward_days,
            "win_rate": _number(row.win_rate),
            "sample_size": row.sample_size,
            "average_return": _number(row.mean_return),
        }
        for row in rows
    ]


def _confidence_summary(rows: Iterable[Any], signal_names: set[str]) -> list[dict[str, Any]]:
    relevant = [row for row in rows if row.signal_name in signal_names]
    relevant.sort(
        key=lambda row: (
            0 if "high_confidence" in _enum_value(row.tier).lower() else 1,
            -(row.min_sample_size or 0),
            row.signal_name,
        )
    )
    return [
        {
            "signal_name": row.signal_name,
            "tier": _enum_value(row.tier),
            "edge_vs_baseline": _number(row.avg_win_rate_minus_baseline),
            "min_sample_size": row.min_sample_size,
            "recommended_holding_period": row.recommended_holding_period,
            "cost_viability_note": row.cost_viability_note,
        }
        for row in relevant
    ]


def _valuation_score(fundamental: Fundamental | None) -> tuple[int, list[str]]:
    """Conservative fundamental-health layer (0-10); not a sector-relative valuation model."""
    if not fundamental:
        return 5, ["Fundamental data is unavailable; valuation contribution is held at a neutral baseline."]

    score = 0
    insights: list[str] = []
    pe = _number(fundamental.pe_ratio)
    eps = _number(fundamental.eps)
    pb = _number(fundamental.pb_ratio)

    if eps is None:
        score += 2
    elif eps > 25:
        score += 4
        insights.append(f"EPS is strongly positive at NPR {eps:.2f}.")
    elif eps > 10:
        score += 3
    elif eps > 0:
        score += 2
    else:
        insights.append(f"EPS is negative at NPR {eps:.2f}, weakening fundamental quality.")

    if pe is None:
        score += 2
    elif 0 < pe <= 18:
        score += 4
        insights.append(f"P/E is {pe:.1f}x; this is not elevated on an absolute basis.")
    elif 18 < pe <= 35:
        score += 3
    elif pe > 50:
        score += 1
        insights.append(f"P/E is elevated at {pe:.1f}x; sector-relative context is required.")
    elif pe > 0:
        score += 2

    if pb is None:
        score += 1
    elif 0 < pb <= 3.5:
        score += 2
    else:
        score += 1

    return min(10, max(0, score)), insights


def _select_preferred_backtests(rows: Iterable[Any], signal_names: set[str]) -> list[Any]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        if row.signal_name in signal_names:
            grouped[row.signal_name].append(row)

    selected: list[Any] = []
    for signal_name in sorted(signal_names):
        candidates = grouped.get(signal_name, [])
        if not candidates:
            continue
        preferred = [row for row in candidates if row.forward_days == PREFERRED_BACKTEST_HORIZON]
        pool = preferred or candidates
        selected.append(max(pool, key=lambda row: row.sample_size or 0))
    return selected


def _historical_evidence(
    backtests: Iterable[Any],
    confidence_rows: Iterable[Any],
    signal_names: set[str],
) -> dict[str, Any]:
    """
    Convert market-wide signal backtests into evidence points.

    These tables do not contain stock-specific nearest-neighbour setups, so the
    payload states their scope explicitly and the AI/UI must not claim they do.
    """
    if not signal_names:
        return {
            "scope": "none",
            "score": 0,
            "confidence_score": 0,
            "signals_covered": 0,
            "signals_requested": 0,
            "min_sample_size": 0,
            "weighted_win_rate": None,
            "weighted_average_return": None,
            "average_edge_vs_baseline": None,
        }

    selected = _select_preferred_backtests(backtests, signal_names)
    relevant_conf = [row for row in confidence_rows if row.signal_name in signal_names]

    total_weight = sum(max(int(row.sample_size or 0), 0) for row in selected)
    weighted_win_rate = None
    weighted_average_return = None
    if total_weight > 0:
        win_numerator = 0.0
        win_weight = 0
        return_numerator = 0.0
        return_weight = 0
        for row in selected:
            weight = max(int(row.sample_size or 0), 0)
            win_rate = _probability(row.win_rate)
            mean_return = _number(row.mean_return)
            if win_rate is not None and weight > 0:
                win_numerator += win_rate * weight
                win_weight += weight
            if mean_return is not None and weight > 0:
                return_numerator += mean_return * weight
                return_weight += weight
        if win_weight:
            weighted_win_rate = win_numerator / win_weight
        if return_weight:
            weighted_average_return = return_numerator / return_weight

    edge_rows: list[tuple[float, int]] = []
    for row in relevant_conf:
        edge = _number(getattr(row, "avg_win_rate_minus_baseline", None))
        if edge is None:
            continue
        edge_rows.append((edge, max(int(getattr(row, "min_sample_size", 0) or 0), 1)))

    average_edge = None
    if edge_rows:
        edge_weight = sum(weight for _, weight in edge_rows)
        average_edge = sum(edge * weight for edge, weight in edge_rows) / edge_weight

    sample_candidates = [
        int(getattr(row, "min_sample_size", 0) or 0)
        for row in relevant_conf
        if int(getattr(row, "min_sample_size", 0) or 0) > 0
    ]
    if not sample_candidates:
        sample_candidates = [int(row.sample_size or 0) for row in selected if int(row.sample_size or 0) > 0]
    min_sample_size = min(sample_candidates) if sample_candidates else 0

    edge_points = 0
    if average_edge is not None:
        if average_edge >= 5:
            edge_points = 14
        elif average_edge >= 3:
            edge_points = 11
        elif average_edge >= 1:
            edge_points = 7
        elif average_edge > 0:
            edge_points = 4

    if min_sample_size >= 1000:
        sample_points = 8
    elif min_sample_size >= 500:
        sample_points = 7
    elif min_sample_size >= 250:
        sample_points = 5
    elif min_sample_size >= 100:
        sample_points = 3
    elif min_sample_size > 0:
        sample_points = 1
    else:
        sample_points = 0

    tiers_by_signal = {row.signal_name: _enum_value(row.tier).lower() for row in relevant_conf}
    high_confidence_signals = {
        signal for signal, tier in tiers_by_signal.items() if "high_confidence" in tier
    }
    if signal_names and high_confidence_signals == signal_names:
        tier_points = 5
    elif high_confidence_signals:
        tier_points = 3
    elif any("unreliable_low_sample" in tier for tier in tiers_by_signal.values()):
        tier_points = 1
    else:
        tier_points = 0

    return_points = 0
    if weighted_average_return is not None:
        if weighted_average_return >= 5:
            return_points = 3
        elif weighted_average_return >= 2:
            return_points = 2
        elif weighted_average_return > 0:
            return_points = 1

    reliability_score = min(30, edge_points + sample_points + tier_points + return_points)
    coverage_ratio = len(set(tiers_by_signal) & signal_names) / max(len(signal_names), 1)
    sample_confidence = min(1.0, min_sample_size / 500.0) if min_sample_size else 0.0
    edge_confidence = min(1.0, max(average_edge or 0.0, 0.0) / 5.0)
    confidence_score = round(100 * (0.40 * coverage_ratio + 0.35 * sample_confidence + 0.25 * edge_confidence))

    return {
        "scope": "market_wide_signal_backtest",
        "score": reliability_score,
        "confidence_score": confidence_score,
        "signals_covered": len(set(tiers_by_signal) & signal_names),
        "signals_requested": len(signal_names),
        "min_sample_size": min_sample_size,
        "weighted_win_rate": weighted_win_rate,
        "weighted_average_return": weighted_average_return,
        "average_edge_vs_baseline": average_edge,
    }


def _classify_market_regime(closes: list[float], as_of_date: str | None = None) -> dict[str, Any]:
    clean = [float(value) for value in closes if value is not None and float(value) > 0]
    if len(clean) < 20:
        return {
            "state": "unavailable",
            "risk_level": "unavailable",
            "confidence": 0,
            "score_adjustment": 0,
            "as_of_date": as_of_date,
            "latest_close": clean[-1] if clean else None,
            "sma_50": None,
            "sma_200": None,
            "return_20d_percent": None,
            "drawdown_percent": None,
            "annualized_volatility_percent": None,
        }

    latest = clean[-1]
    sma_50 = sum(clean[-50:]) / 50 if len(clean) >= 50 else None
    sma_200 = sum(clean[-200:]) / 200 if len(clean) >= 200 else None
    return_20d = (latest / clean[-21] - 1.0) if len(clean) >= 21 else 0.0
    rolling_peak = max(clean[-252:])
    drawdown = latest / rolling_peak - 1.0 if rolling_peak else 0.0

    returns: list[float] = []
    for previous, current in zip(clean[-61:-1], clean[-60:]):
        if previous:
            returns.append(current / previous - 1.0)
    annualized_vol = pstdev(returns) * sqrt(252) if len(returns) >= 2 else 0.0

    if sma_200 is not None and sma_50 is not None:
        if latest > sma_200 and sma_50 > sma_200 and return_20d > 0:
            state = "bullish"
        elif latest < sma_200 and sma_50 < sma_200 and return_20d < 0:
            state = "bearish"
        elif latest > sma_50 and latest < sma_200 and return_20d > 0:
            state = "recovery"
        else:
            state = "mixed"
    elif sma_50 is not None:
        if latest > sma_50 and return_20d > 0:
            state = "bullish"
        elif latest < sma_50 and return_20d < 0:
            state = "bearish"
        else:
            state = "mixed"
    else:
        state = "mixed"

    if drawdown <= -0.15 or annualized_vol >= 0.25:
        risk_level = "high"
    elif drawdown <= -0.08 or annualized_vol >= 0.18:
        risk_level = "medium"
    else:
        risk_level = "low"

    adjustment = {"bullish": 3, "recovery": 1, "mixed": 0, "bearish": -3}.get(state, 0)
    if risk_level == "high":
        adjustment -= 2
    elif risk_level == "medium":
        adjustment -= 1
    adjustment = max(-5, min(3, adjustment))

    if len(clean) >= 200:
        confidence = 90
    elif len(clean) >= 100:
        confidence = 75
    elif len(clean) >= 50:
        confidence = 60
    else:
        confidence = 45

    return {
        "state": state,
        "risk_level": risk_level,
        "confidence": confidence,
        "score_adjustment": adjustment,
        "as_of_date": as_of_date,
        "latest_close": latest,
        "sma_50": sma_50,
        "sma_200": sma_200,
        "return_20d_percent": return_20d * 100.0,
        "drawdown_percent": drawdown * 100.0,
        "annualized_volatility_percent": annualized_vol * 100.0,
    }


def _load_market_regime(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(MarketIndex.date, MarketIndex.close)
        .where(MarketIndex.index_name == NEPSE_INDEX_NAME)
        .order_by(MarketIndex.date.desc())
        .limit(260)
    ).all()
    if not rows:
        return _classify_market_regime([])
    ordered = list(reversed(rows))
    return _classify_market_regime(
        [float(row.close) for row in ordered if row.close is not None],
        ordered[-1].date.isoformat() if ordered else None,
    )


def _forward_validation_for_signals(
    validation_status: dict[str, Any] | None,
    signal_names: set[str],
) -> dict[str, Any]:
    if not signal_names:
        return {
            "state": "no_active_signal",
            "confidence_cap": 44,
            "policy_version": validation_status.get("policy_version") if validation_status else None,
            "signals": {},
        }

    if not validation_status:
        return {
            "state": "unavailable",
            "confidence_cap": 59,
            "policy_version": None,
            "signals": {},
        }

    scoped = {
        signal_name: validation_status.get("signals", {}).get(signal_name)
        for signal_name in signal_names
        if validation_status.get("signals", {}).get(signal_name) is not None
    }
    statuses = {row.get("gate_status") for row in scoped.values() if row}

    if statuses & {"fail", "invalid_logic_change"}:
        state = "failed"
        confidence_cap = 35
    elif scoped and statuses == {"pass"} and len(scoped) == len(signal_names):
        state = "pass"
        confidence_cap = 100
    else:
        state = "collecting"
        confidence_cap = 69

    return {
        "state": state,
        "confidence_cap": confidence_cap,
        "policy_version": validation_status.get("policy_version"),
        "signals": {
            name: {
                "gate_status": row.get("gate_status"),
                "graded_calls": row.get("graded_calls"),
                "min_graded_calls": row.get("min_graded_calls"),
                "independent_entry_days": row.get("independent_entry_days"),
                "min_independent_entry_days": row.get("min_independent_entry_days"),
            }
            for name, row in scoped.items()
            if row
        },
    }


def _build_payload(
    *,
    symbol: str,
    company_name: str,
    sector: str | None,
    technical: TechnicalSignal | None,
    price: Any,
    liquidity: Any,
    calls: Iterable[SignalCall],
    confidence_rows: Iterable[SignalConfidence],
    backtests: Iterable[BacktestResult],
    fundamental: Fundamental | None = None,
    market_regime: dict[str, Any] | None = None,
    validation_status: dict[str, Any] | None = None,
    active_signal_names: set[str] | None = None,
    include_ai: bool = True,
) -> dict[str, Any]:
    calls_list = list(calls)
    if active_signal_names is None:
        signal_names = {str(call.signal_name) for call in calls_list if getattr(call, "signal_name", None)}
    else:
        signal_names = set(active_signal_names)

    confidence_list = list(confidence_rows)
    backtest_list = list(backtests)
    market_regime = market_regime or _classify_market_regime([])

    warnings: list[str] = []
    strengths: list[str] = []
    explanation: list[str] = []

    price_number = _number(price)
    sma_50 = _number(technical.sma_50) if technical else None
    sma_200 = _number(technical.sma_200) if technical else None
    rsi = _number(technical.rsi_14) if technical else None

    price_vs_sma_200 = "unavailable"
    sma_50_vs_sma_200 = "unavailable"
    macd_state = "neutral"
    rsi_state = "unavailable"

    trend_score = 0
    if technical:
        if technical.sma_50 is not None and technical.sma_200 is not None:
            if technical.sma_50 > technical.sma_200:
                trend_score += 10
                sma_50_vs_sma_200 = "above"
                strengths.append("50-day SMA is above the 200-day SMA.")
                explanation.append("Long-term moving-average structure is constructive.")
            else:
                sma_50_vs_sma_200 = "below"
                warnings.append("50-day SMA is below the 200-day SMA.")

        if price is not None and technical.sma_200 is not None:
            if Decimal(str(price)) > Decimal(str(technical.sma_200)):
                trend_score += 10
                price_vs_sma_200 = "above"
                strengths.append("Price is above its 200-day SMA.")
            else:
                price_vs_sma_200 = "below"
                warnings.append("Price is below its 200-day SMA.")

    momentum_score = 0
    if technical:
        if technical.rsi_14 is not None:
            rsi_value = float(technical.rsi_14)
            if 42 <= rsi_value <= 68:
                momentum_score += 8
                rsi_state = "healthy"
                strengths.append("RSI is in a balanced momentum range.")
            elif rsi_value < 30:
                momentum_score += 6
                rsi_state = "oversold"
                explanation.append("RSI is oversold; mean-reversion evidence is evaluated separately in the signal backtest.")
            elif rsi_value > 70:
                rsi_state = "overbought"
                warnings.append("RSI is overbought; near-term reversal risk is elevated.")
            else:
                rsi_state = "neutral"
                momentum_score += 4

        if technical.macd_line is not None and technical.macd_signal is not None:
            if technical.macd_line > technical.macd_signal:
                momentum_score += 7
                macd_state = "bullish"
                strengths.append("MACD is above its signal line.")
            elif technical.macd_line < technical.macd_signal:
                macd_state = "bearish"
                warnings.append("MACD is below its signal line.")

    liquidity_pts = _liquidity_score(liquidity)
    liquidity_label = str(liquidity) if liquidity is not None else None
    if liquidity_pts >= 10:
        strengths.append("High-liquidity tier reduces execution risk relative to thinner names.")
    elif 0 < liquidity_pts <= 3:
        warnings.append("Low liquidity can increase slippage and execution risk.")

    evidence = _historical_evidence(backtest_list, confidence_list, signal_names)
    reliability_pts = int(evidence["score"])
    if signal_names:
        edge = evidence.get("average_edge_vs_baseline")
        if reliability_pts >= 22 and edge is not None:
            strengths.append(
                f"Current signal set has positive market-wide historical edge "
                f"({edge:+.2f} percentage points vs baseline)."
            )
        elif edge is not None and edge <= 0:
            warnings.append("Current signal set has not demonstrated positive historical edge versus the market baseline.")
        elif reliability_pts == 0:
            warnings.append("Current signal set lacks sufficient historical evidence for a reliability contribution.")
    else:
        explanation.append("No active v1 validation signal is present, so historical reliability contributes zero points.")

    risk_pts = 10 + int(market_regime.get("score_adjustment") or 0)
    if rsi_state == "overbought":
        risk_pts -= 3
    if price_vs_sma_200 == "below":
        risk_pts -= 2
    risk_pts = max(0, min(15, risk_pts))

    regime_state = str(market_regime.get("state") or "unavailable")
    if regime_state != "unavailable":
        explanation.append(
            f"NEPSE regime is {regime_state}; 20-day index return is "
            f"{(market_regime.get('return_20d_percent') or 0):+.2f}%."
        )
    if regime_state == "bullish":
        strengths.append("Broader NEPSE regime is supportive.")
    elif regime_state == "bearish":
        warnings.append("Broader NEPSE regime is bearish and reduces the risk-control contribution.")
    if market_regime.get("risk_level") == "high":
        warnings.append("NEPSE volatility/drawdown regime is elevated.")

    fundamental_pts, fund_insights = _valuation_score(fundamental)
    for item in fund_insights:
        if "negative" in item.lower() or "elevated" in item.lower() or "unavailable" in item.lower():
            warnings.append(item)
        else:
            strengths.append(item)

    score = max(
        0,
        min(
            100,
            trend_score
            + momentum_score
            + liquidity_pts
            + reliability_pts
            + risk_pts
            + fundamental_pts,
        ),
    )

    data_quality_points = 0
    if technical is not None:
        data_quality_points += 8
    if price_number is not None:
        data_quality_points += 5
    if liquidity is not None:
        data_quality_points += 4
    if fundamental is not None:
        data_quality_points += 4
    if int(market_regime.get("confidence") or 0) >= 60:
        data_quality_points += 4

    historical_confidence = int(evidence.get("confidence_score") or 0)
    raw_confidence_score = min(100, round(historical_confidence * 0.75 + data_quality_points))
    forward_validation = _forward_validation_for_signals(validation_status, signal_names)
    confidence_score = min(raw_confidence_score, int(forward_validation["confidence_cap"]))

    if forward_validation["state"] == "collecting" and signal_names:
        warnings.append(
            "Forward paper-trade validation is still collecting; historical edge is not yet confirmed as a live edge."
        )
    elif forward_validation["state"] == "failed":
        warnings.append("Forward validation has not passed for at least one active signal.")
    elif forward_validation["state"] == "pass":
        strengths.append("The active signal set has passed the pre-committed forward-validation gate.")

    confidence_level = _confidence_level(confidence_score)
    rating = _rating(score, confidence_score, bool(signal_names))

    if market_regime.get("risk_level") == "high" or risk_pts <= 5:
        risk_level = "high"
    elif market_regime.get("risk_level") == "medium" or risk_pts <= 9:
        risk_level = "medium"
    else:
        risk_level = "low"

    if not explanation:
        explanation.append("Available evidence does not establish a strong directional setup.")

    backtest_data = _backtest_summary(backtest_list)
    signals_data = [
        {
            "signal_name": signal_name,
            "status": "active",
            "entry_date": None,
            "forward_days_horizon": VALIDATION_SIGNAL_SPECS[signal_name].horizon_trading_days,
            "direction": _signal_direction(signal_name),
        }
        for signal_name in sorted(signal_names)
        if signal_name in VALIDATION_SIGNAL_SPECS
    ]

    scores_breakdown = {
        "trend": trend_score,
        "momentum": momentum_score,
        "liquidity": liquidity_pts,
        "reliability": reliability_pts,
        "risk_adjustment": risk_pts,
        "valuation": fundamental_pts,
    }

    technical_summary = {
        "rsi": rsi,
        "rsi_state": rsi_state,
        "macd": macd_state,
        "latest_price": price_number,
        "sma_50": sma_50,
        "sma_200": sma_200,
        "price_vs_sma_200": price_vs_sma_200,
        "sma_50_vs_sma_200": sma_50_vs_sma_200,
    }

    warnings = _unique(warnings)
    strengths = _unique(strengths)
    explanation = _unique(explanation)

    ai_commentary = None
    if include_ai:
        from src.services.ai_analyst import generate_ai_analyst_commentary

        ai_commentary = generate_ai_analyst_commentary(
            symbol=symbol,
            company_name=company_name,
            sector=sector,
            as_of_date=technical.date.isoformat() if technical and technical.date else None,
            artha_score=score,
            rating=rating,
            confidence_score=confidence_score,
            score_breakdown=scores_breakdown,
            strengths=strengths,
            warnings=warnings,
            technical=technical_summary,
            signals=signals_data,
            backtests=backtest_data,
            evidence=evidence,
            market_regime=market_regime,
            forward_validation=forward_validation,
        )

    return {
        "symbol": symbol,
        "company_name": company_name,
        "sector": sector,
        "as_of_date": technical.date.isoformat() if technical and technical.date else None,
        "artha_score": score,
        "rating": rating,
        "confidence_score": confidence_score,
        "confidence_level": confidence_level,
        "signal_quality": _signal_quality(reliability_pts, signal_names),
        "trend_strength": _trend_strength(trend_score),
        "scores": scores_breakdown,
        "score_max": dict(SCORE_MAX),
        "technical": technical_summary,
        "fundamental": {
            "pe_ratio": _number(fundamental.pe_ratio) if fundamental else None,
            "pb_ratio": _number(fundamental.pb_ratio) if fundamental else None,
            "eps": _number(fundamental.eps) if fundamental else None,
            "book_value": _number(fundamental.book_value) if fundamental else None,
            "score": fundamental_pts,
        },
        "liquidity": {"tier": liquidity_label, "score": liquidity_pts},
        "confidence": _confidence_summary(confidence_list, signal_names),
        "evidence": evidence,
        "forward_validation": forward_validation,
        "market_regime": market_regime,
        "backtest_summary": backtest_data,
        "signals": signals_data,
        "risk": {"level": risk_level, "warnings": warnings},
        "strengths": strengths,
        "explanation": explanation,
        "ai_analysis": ai_commentary,
    }


def build_stock_intelligence(session: Session, symbol: str) -> dict[str, Any] | None:
    company = session.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
    if not company:
        return None

    technical = session.execute(
        select(TechnicalSignal)
        .where(TechnicalSignal.symbol == symbol, TechnicalSignal.timeframe == SignalTimeframe.DAILY)
        .order_by(TechnicalSignal.date.desc())
        .limit(1)
    ).scalar_one_or_none()

    fundamental = session.execute(
        select(Fundamental)
        .where(Fundamental.symbol == symbol)
        .order_by(Fundamental.reported_date.desc())
        .limit(1)
    ).scalar_one_or_none()

    price = session.execute(
        select(DailyPrice.close).where(DailyPrice.symbol == symbol).order_by(DailyPrice.date.desc()).limit(1)
    ).scalar_one_or_none()

    liquidity = session.execute(
        select(SymbolLiquidityTier.liquidity_tier).where(SymbolLiquidityTier.symbol == symbol)
    ).scalar_one_or_none()

    signal_names = _active_validation_signal_names(technical, price, liquidity)
    if signal_names:
        confidence = (
            session.execute(select(SignalConfidence).where(SignalConfidence.signal_name.in_(signal_names)))
            .scalars()
            .all()
        )
        backtests = (
            session.execute(
                select(BacktestResult)
                .where(BacktestResult.signal_name.in_(signal_names))
                .order_by(BacktestResult.signal_name, BacktestResult.forward_days)
            )
            .scalars()
            .all()
        )
    else:
        confidence = []
        backtests = []

    market_regime = _load_market_regime(session)
    validation_status = build_validation_status(session=session)

    return _build_payload(
        symbol=symbol,
        company_name=company.company_name,
        sector=company.sector,
        technical=technical,
        fundamental=fundamental,
        price=price,
        liquidity=liquidity,
        calls=[],
        active_signal_names=signal_names,
        confidence_rows=confidence,
        backtests=backtests,
        market_regime=market_regime,
        validation_status=validation_status,
        include_ai=True,
    )


def build_market_intelligence(session: Session, limit: int = 200) -> dict[str, Any]:
    companies = (
        session.execute(
            select(Company)
            .where(Company.instrument_type == "Equity", Company.status == "A")
            .order_by(Company.symbol)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    if not companies:
        return {
            "as_of_date": None,
            "market": {"condition": "unavailable", "trend": "unavailable", "risk_level": "unavailable"},
            "top_opportunities": [],
            "high_confidence_signals": [],
            "sector_insights": [],
            "stocks": [],
        }

    symbols = [company.symbol for company in companies]

    technical_rows = (
        session.execute(
            select(TechnicalSignal)
            .where(TechnicalSignal.symbol.in_(symbols), TechnicalSignal.timeframe == SignalTimeframe.DAILY)
            .order_by(TechnicalSignal.symbol, TechnicalSignal.date.desc())
            .distinct(TechnicalSignal.symbol)
        )
        .scalars()
        .all()
    )
    technical_by_symbol = {row.symbol: row for row in technical_rows}

    price_rows = (
        session.execute(
            select(DailyPrice)
            .where(DailyPrice.symbol.in_(symbols))
            .order_by(DailyPrice.symbol, DailyPrice.date.desc())
            .distinct(DailyPrice.symbol)
        )
        .scalars()
        .all()
    )
    price_by_symbol = {row.symbol: row.close for row in price_rows}

    fundamental_rows = (
        session.execute(
            select(Fundamental)
            .where(Fundamental.symbol.in_(symbols))
            .order_by(Fundamental.symbol, Fundamental.reported_date.desc())
            .distinct(Fundamental.symbol)
        )
        .scalars()
        .all()
    )
    fundamental_by_symbol = {row.symbol: row for row in fundamental_rows}

    liquidity_rows = session.execute(
        select(SymbolLiquidityTier.symbol, SymbolLiquidityTier.liquidity_tier).where(
            SymbolLiquidityTier.symbol.in_(symbols)
        )
    ).all()
    liquidity_by_symbol = {row.symbol: row.liquidity_tier for row in liquidity_rows}

    confidence_rows = session.execute(select(SignalConfidence)).scalars().all()
    confidence_by_signal: dict[str, list[SignalConfidence]] = defaultdict(list)
    for row in confidence_rows:
        confidence_by_signal[row.signal_name].append(row)

    backtest_rows = session.execute(select(BacktestResult)).scalars().all()
    backtests_by_signal: dict[str, list[BacktestResult]] = defaultdict(list)
    for row in backtest_rows:
        backtests_by_signal[row.signal_name].append(row)

    market_regime = _load_market_regime(session)
    validation_status = build_validation_status(session=session)

    stocks: list[dict[str, Any]] = []
    latest_dates: list[str] = []
    for company in companies:
        technical = technical_by_symbol.get(company.symbol)
        price = price_by_symbol.get(company.symbol)
        liquidity = liquidity_by_symbol.get(company.symbol)
        current_signal_names = _active_validation_signal_names(technical, price, liquidity)

        scoped_confidence = [
            row for signal_name in current_signal_names for row in confidence_by_signal.get(signal_name, [])
        ]
        scoped_backtests = [
            row for signal_name in current_signal_names for row in backtests_by_signal.get(signal_name, [])
        ]

        payload = _build_payload(
            symbol=company.symbol,
            company_name=company.company_name,
            sector=company.sector,
            technical=technical,
            fundamental=fundamental_by_symbol.get(company.symbol),
            price=price,
            liquidity=liquidity,
            calls=[],
            active_signal_names=current_signal_names,
            confidence_rows=scoped_confidence,
            backtests=scoped_backtests,
            market_regime=market_regime,
            validation_status=validation_status,
            include_ai=False,
        )
        if payload["as_of_date"]:
            latest_dates.append(payload["as_of_date"])
        stocks.append(
            {
                "symbol": payload["symbol"],
                "company_name": payload["company_name"],
                "sector": payload["sector"],
                "artha_score": payload["artha_score"],
                "rating": payload["rating"],
                "confidence_score": payload["confidence_score"],
                "confidence_level": payload["confidence_level"],
                "liquidity_tier": payload["liquidity"]["tier"],
                "trend_strength": payload["trend_strength"],
                "signal_quality": payload["signal_quality"],
                "risk_level": payload["risk"]["level"],
                "trend_score": payload["scores"]["trend"],
                "momentum_score": payload["scores"]["momentum"],
                "reliability_score": payload["scores"]["reliability"],
                "active_signals": [signal["signal_name"] for signal in payload["signals"]],
                "strengths": payload["strengths"][:2],
            }
        )

    stocks.sort(
        key=lambda row: (
            row["artha_score"],
            row["confidence_score"],
            row["reliability_score"],
            row["trend_score"],
        ),
        reverse=True,
    )

    analyzed = len(stocks)
    average_score = sum(row["artha_score"] for row in stocks) / analyzed if analyzed else 0.0
    positive_share = (
        sum(1 for row in stocks if row["rating"] in {"positive_setup", "strong_setup"}) / analyzed
        if analyzed
        else 0.0
    )
    weak_share = sum(1 for row in stocks if row["rating"] == "weak") / analyzed if analyzed else 0.0
    high_confidence_share = (
        sum(1 for row in stocks if row["confidence_level"] == "high" and row["active_signals"]) / analyzed
        if analyzed
        else 0.0
    )

    regime_state = market_regime.get("state", "unavailable")
    if regime_state == "bullish":
        condition = "bullish"
        market_trend = "bullish"
    elif regime_state == "recovery":
        condition = "constructive"
        market_trend = "improving"
    elif regime_state == "bearish":
        condition = "defensive"
        market_trend = "bearish"
    elif regime_state == "mixed":
        condition = "mixed"
        market_trend = "neutral"
    else:
        condition = "unavailable"
        market_trend = "unavailable"

    regime_risk = market_regime.get("risk_level", "unavailable")
    if regime_risk == "high" or weak_share >= 0.45:
        market_risk = "high"
    elif regime_risk == "medium" or weak_share >= 0.25:
        market_risk = "medium"
    elif regime_risk == "low":
        market_risk = "low"
    else:
        market_risk = "unavailable"

    sector_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in stocks:
        sector_groups[row["sector"] or "Other"].append(row)

    sector_insights = []
    for sector, rows in sector_groups.items():
        rows.sort(key=lambda item: (item["artha_score"], item["confidence_score"]), reverse=True)
        avg_score = sum(item["artha_score"] for item in rows) / len(rows)
        avg_trend = sum(item["trend_score"] for item in rows) / len(rows)
        sector_insights.append(
            {
                "sector": sector,
                "average_artha_score": round(avg_score, 1),
                "trend": "bullish" if avg_trend >= 16 else "neutral" if avg_trend >= 9 else "bearish",
                "high_confidence_count": sum(
                    1 for item in rows if item["confidence_level"] == "high" and item["active_signals"]
                ),
                "stock_count": len(rows),
                "top_symbol": rows[0]["symbol"],
                "top_score": rows[0]["artha_score"],
            }
        )
    sector_insights.sort(key=lambda row: row["average_artha_score"], reverse=True)

    opportunities = [
        row
        for row in stocks
        if row["active_signals"] and row["rating"] in {"positive_setup", "strong_setup"}
    ]
    high_confidence = [
        row for row in stocks if row["confidence_level"] == "high" and row["active_signals"]
    ]

    return {
        "as_of_date": max(latest_dates) if latest_dates else None,
        "market": {
            "condition": condition,
            "trend": market_trend,
            "risk_level": market_risk,
            "average_artha_score": round(average_score, 1),
            "positive_setup_share": round(positive_share, 4),
            "high_confidence_share": round(high_confidence_share, 4),
            "stocks_analyzed": analyzed,
            "regime_confidence": market_regime.get("confidence"),
            "index_20d_return_percent": market_regime.get("return_20d_percent"),
            "index_drawdown_percent": market_regime.get("drawdown_percent"),
            "annualized_volatility_percent": market_regime.get("annualized_volatility_percent"),
            "forward_validation_status": validation_status.get("overall_status"),
        },
        "top_opportunities": opportunities[:8],
        "high_confidence_signals": high_confidence[:8],
        "sector_insights": sector_insights,
        "stocks": stocks,
    }
