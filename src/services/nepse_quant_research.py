from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import mean, pstdev
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database.models import (
    ActionType,
    Company,
    CorporateAction,
    DailyPrice,
    Fundamental,
    GdpNepse,
    IpoCalendar,
    MarketIndex,
    PromoterHolding,
    Remittance,
    SectorFundamentalBaseline,
    SectorIndexMapping,
    ShortTermInterestRate,
    SignalTimeframe,
    SymbolLiquidityTier,
    TechnicalSignal,
)
from src.services.quant_features import (
    DEFAULT_HORIZON_DAYS,
    FEATURE_VERSION,
    ROUND_TRIP_COST_PERCENT,
    analyze_historical_analogs,
    build_feature_row,
    build_labeled_feature_rows,
    build_probability_ensemble,
    fit_ridge_logistic,
    predict_ridge_logistic,
)

NEPSE_INDEX_NAME = "NEPSE Index"
MIN_RESEARCH_HISTORY = 140


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _safe_return(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


def _average(values: Sequence[float]) -> float | None:
    return mean(values) if values else None


def _load_stock_series(session: Session, symbol: str) -> dict[str, list[Any]]:
    rows = session.execute(
        select(
            DailyPrice.date,
            DailyPrice.adjusted_close,
            DailyPrice.close,
            DailyPrice.volume,
            DailyPrice.turnover,
        )
        .where(DailyPrice.symbol == symbol)
        .order_by(DailyPrice.date)
    ).all()

    dates: list[Any] = []
    closes: list[float] = []
    raw_closes: list[float] = []
    volumes: list[float] = []
    turnovers: list[float] = []
    for row in rows:
        close = float(row.adjusted_close if row.adjusted_close is not None else row.close)
        if close <= 0:
            continue
        dates.append(row.date)
        closes.append(close)
        raw_closes.append(float(row.close))
        volumes.append(float(row.volume or 0))
        turnovers.append(float(row.turnover or 0))

    return {
        "dates": dates,
        "closes": closes,
        "raw_closes": raw_closes,
        "volumes": volumes,
        "turnovers": turnovers,
    }


def _load_index_series(session: Session, index_name: str | None) -> dict[str, list[Any]]:
    if not index_name:
        return {"dates": [], "closes": []}
    rows = session.execute(
        select(MarketIndex.date, MarketIndex.close)
        .where(MarketIndex.index_name == index_name)
        .order_by(MarketIndex.date)
    ).all()
    return {
        "dates": [row.date for row in rows if row.close is not None],
        "closes": [float(row.close) for row in rows if row.close is not None],
    }


def _annualized_volatility(closes: Sequence[float], lookback: int = 60) -> float | None:
    window = list(closes[-(lookback + 1) :])
    if len(window) < 3:
        return None
    returns = [current / previous - 1.0 for previous, current in zip(window[:-1], window[1:]) if previous]
    if len(returns) < 2:
        return None
    return pstdev(returns) * math.sqrt(252.0) * 100.0


def _classify_regime(
    *,
    dates: Sequence[Any],
    closes: Sequence[float],
    breadth_above_50: float | None,
    breadth_above_200: float | None,
    turnover_ratio: float | None,
) -> dict[str, Any]:
    if len(closes) < 20:
        return {
            "state": "unavailable",
            "risk_level": "unavailable",
            "confidence_score": 0,
            "as_of_date": None,
        }

    latest = float(closes[-1])
    sma20 = mean(closes[-20:]) if len(closes) >= 20 else None
    sma50 = mean(closes[-50:]) if len(closes) >= 50 else None
    sma100 = mean(closes[-100:]) if len(closes) >= 100 else None
    sma200 = mean(closes[-200:]) if len(closes) >= 200 else None
    ret20 = _safe_return(latest, float(closes[-21])) if len(closes) >= 21 else None
    ret60 = _safe_return(latest, float(closes[-61])) if len(closes) >= 61 else None
    peak = max(closes[-252:])
    drawdown = _safe_return(latest, float(peak)) if peak else 0.0
    volatility = _annualized_volatility(closes, 60)

    b50 = breadth_above_50 if breadth_above_50 is not None else 0.50
    b200 = breadth_above_200 if breadth_above_200 is not None else 0.50
    r20 = ret20 or 0.0
    r60 = ret60 or 0.0
    vol = volatility or 0.0

    if sma200 is not None and latest < sma200 and (drawdown or 0.0) <= -18 and vol >= 28:
        state = "high_stress"
    elif sma200 is not None and sma50 is not None and latest > sma50 > sma200 and r60 >= 10 and b50 >= 0.65:
        state = "strong_bull"
    elif sma200 is not None and latest > sma200 and r20 > 0 and b50 >= 0.55:
        state = "bull"
    elif sma50 is not None and latest > sma50 and r20 > 0 and (sma200 is None or latest <= sma200):
        state = "recovery"
    elif sma200 is not None and latest > sma200 and r20 < 0 and b50 < 0.45:
        state = "distribution"
    elif sma200 is not None and latest < sma200 and r20 < 0:
        state = "bear"
    else:
        state = "sideways"

    if state == "high_stress" or vol >= 35 or (drawdown or 0.0) <= -20:
        risk = "high"
    elif state in {"bear", "distribution"} or vol >= 25 or (drawdown or 0.0) <= -10:
        risk = "medium"
    else:
        risk = "low"

    confidence = 40
    if len(closes) >= 200:
        confidence += 25
    elif len(closes) >= 100:
        confidence += 15
    if breadth_above_50 is not None:
        confidence += 15
    if breadth_above_200 is not None:
        confidence += 10
    if turnover_ratio is not None:
        confidence += 5

    return {
        "state": state,
        "risk_level": risk,
        "confidence_score": min(95, confidence),
        "as_of_date": dates[-1].isoformat() if dates and hasattr(dates[-1], "isoformat") else None,
        "latest_close": latest,
        "sma_20": sma20,
        "sma_50": sma50,
        "sma_100": sma100,
        "sma_200": sma200,
        "return_20d_percent": ret20,
        "return_60d_percent": ret60,
        "drawdown_from_252d_high_percent": drawdown,
        "annualized_volatility_60d_percent": volatility,
        "breadth_above_sma50": breadth_above_50,
        "breadth_above_sma200": breadth_above_200,
        "turnover_ratio_20d": turnover_ratio,
    }


def _breadth(session: Session, *, sector: str | None = None) -> tuple[float | None, float | None, int]:
    latest_date = session.execute(
        select(func.max(TechnicalSignal.date)).where(TechnicalSignal.timeframe == SignalTimeframe.DAILY)
    ).scalar_one_or_none()
    if latest_date is None:
        return None, None, 0

    query = (
        select(DailyPrice.close, TechnicalSignal.sma_50, TechnicalSignal.sma_200)
        .join(
            DailyPrice,
            (DailyPrice.symbol == TechnicalSignal.symbol) & (DailyPrice.date == TechnicalSignal.date),
        )
        .join(Company, Company.symbol == TechnicalSignal.symbol)
        .where(
            TechnicalSignal.timeframe == SignalTimeframe.DAILY,
            TechnicalSignal.date == latest_date,
            Company.instrument_type == "Equity",
            Company.status == "A",
        )
    )
    if sector is not None:
        query = query.where(Company.sector == sector)

    rows = session.execute(query).all()
    if not rows:
        return None, None, 0

    eligible50 = [row for row in rows if row.sma_50 is not None and row.close is not None]
    eligible200 = [row for row in rows if row.sma_200 is not None and row.close is not None]
    b50 = (
        sum(1 for row in eligible50 if float(row.close) > float(row.sma_50)) / len(eligible50)
        if eligible50
        else None
    )
    b200 = (
        sum(1 for row in eligible200 if float(row.close) > float(row.sma_200)) / len(eligible200)
        if eligible200
        else None
    )
    return b50, b200, len(rows)


def _turnover_ratio(session: Session, *, sector: str | None = None) -> float | None:
    query = (
        select(DailyPrice.date, func.sum(DailyPrice.turnover).label("turnover"))
        .join(Company, Company.symbol == DailyPrice.symbol)
        .where(Company.instrument_type == "Equity", Company.status == "A")
    )
    if sector is not None:
        query = query.where(Company.sector == sector)
    rows = session.execute(
        query.group_by(DailyPrice.date).order_by(DailyPrice.date.desc()).limit(21)
    ).all()
    if len(rows) < 2:
        return None
    current = float(rows[0].turnover or 0.0)
    trailing = [float(row.turnover or 0.0) for row in rows[1:]]
    avg = mean(trailing) if trailing else 0.0
    return current / avg if avg > 0 else None


def build_market_regime(session: Session) -> dict[str, Any]:
    market = _load_index_series(session, NEPSE_INDEX_NAME)
    b50, b200, breadth_count = _breadth(session)
    turnover_ratio = _turnover_ratio(session)
    result = _classify_regime(
        dates=market["dates"],
        closes=market["closes"],
        breadth_above_50=b50,
        breadth_above_200=b200,
        turnover_ratio=turnover_ratio,
    )
    result["breadth_symbols"] = breadth_count
    return result


def _sector_index_name(session: Session, sector: str | None) -> str | None:
    if not sector:
        return None
    return session.execute(
        select(SectorIndexMapping.market_index_name).where(SectorIndexMapping.companies_sector == sector)
    ).scalar_one_or_none()


def build_sector_regime(session: Session, sector: str | None, market_regime: dict[str, Any]) -> dict[str, Any]:
    if not sector:
        return {"sector": None, "state": "unavailable", "confidence_score": 0}

    index_name = _sector_index_name(session, sector)
    sector_series = _load_index_series(session, index_name)
    b50, b200, breadth_count = _breadth(session, sector=sector)
    turnover_ratio = _turnover_ratio(session, sector=sector)

    if sector_series["closes"]:
        result = _classify_regime(
            dates=sector_series["dates"],
            closes=sector_series["closes"],
            breadth_above_50=b50,
            breadth_above_200=b200,
            turnover_ratio=turnover_ratio,
        )
    else:
        state = "sideways"
        if b50 is not None and b200 is not None:
            if b50 >= 0.68 and b200 >= 0.58:
                state = "strong_bull"
            elif b50 >= 0.55:
                state = "bull"
            elif b50 <= 0.35 and b200 <= 0.40:
                state = "bear"
        result = {
            "state": state,
            "risk_level": "medium" if state == "bear" else "low",
            "confidence_score": 55 if b50 is not None else 20,
            "breadth_above_sma50": b50,
            "breadth_above_sma200": b200,
            "turnover_ratio_20d": turnover_ratio,
        }

    market_return = market_regime.get("return_20d_percent")
    sector_return = result.get("return_20d_percent")
    result.update(
        {
            "sector": sector,
            "index_name": index_name,
            "breadth_symbols": breadth_count,
            "relative_strength_vs_nepse_20d": (
                float(sector_return) - float(market_return)
                if sector_return is not None and market_return is not None
                else None
            ),
        }
    )
    return result


def build_macro_context(session: Session) -> dict[str, Any]:
    rate_rows = session.execute(
        select(ShortTermInterestRate).order_by(ShortTermInterestRate.id.desc()).limit(2)
    ).scalars().all()
    remittance = session.execute(select(Remittance).order_by(Remittance.id.desc()).limit(1)).scalar_one_or_none()
    gdp = session.execute(select(GdpNepse).order_by(GdpNepse.id.desc()).limit(1)).scalar_one_or_none()

    latest_rate = _number(rate_rows[0].interbank_commercial_rate) if rate_rows else None
    previous_rate = _number(rate_rows[1].interbank_commercial_rate) if len(rate_rows) > 1 else None
    rate_delta = latest_rate - previous_rate if latest_rate is not None and previous_rate is not None else None
    remittance_growth = _number(remittance.growth_pct) if remittance else None
    gdp_growth = _number(gdp.gdp_growth_rate) if gdp else None

    supportive = 0
    restrictive = 0
    if rate_delta is not None:
        if rate_delta <= -0.25:
            supportive += 1
        elif rate_delta >= 0.25:
            restrictive += 1
    if remittance_growth is not None:
        if remittance_growth >= 5:
            supportive += 1
        elif remittance_growth < 0:
            restrictive += 1
    if gdp_growth is not None and gdp_growth > 4:
        supportive += 1

    if restrictive >= 2:
        regime = "restrictive"
    elif supportive >= 2 and restrictive == 0:
        regime = "supportive"
    else:
        regime = "neutral"

    return {
        "regime": regime,
        "latest_interbank_commercial_rate": latest_rate,
        "interbank_rate_change": rate_delta,
        "remittance_growth_percent": remittance_growth,
        "gdp_growth_percent": gdp_growth,
        "included_in_probability_model": False,
        "note": (
            "Macro data is current context only in v1. It is intentionally excluded from historical ML features until "
            "every observation has a reliable point-in-time date suitable for walk-forward training."
        ),
    }


def build_event_risk(session: Session, symbol: str, as_of_date: date) -> dict[str, Any]:
    actions = session.execute(
        select(CorporateAction)
        .where(
            CorporateAction.symbol == symbol,
            CorporateAction.action_date >= as_of_date - timedelta(days=45),
            CorporateAction.action_date <= as_of_date + timedelta(days=90),
        )
        .order_by(CorporateAction.action_date)
    ).scalars().all()

    promoter = session.execute(
        select(PromoterHolding)
        .where(PromoterHolding.symbol == symbol)
        .order_by(PromoterHolding.reported_date.desc())
        .limit(1)
    ).scalar_one_or_none()

    issues = session.execute(
        select(IpoCalendar)
        .where(
            IpoCalendar.symbol == symbol,
            IpoCalendar.opening_date.is_not(None),
            IpoCalendar.opening_date >= as_of_date - timedelta(days=15),
            IpoCalendar.opening_date <= as_of_date + timedelta(days=90),
        )
        .order_by(IpoCalendar.opening_date)
    ).scalars().all()

    events: list[dict[str, Any]] = []
    risk_points = 0
    for action in actions:
        action_type = str(getattr(action.action_type, "value", action.action_type))
        future = action.action_date >= as_of_date
        severity = "information"
        if future and action_type == ActionType.RIGHT.value:
            severity = "high"
            risk_points += 45
        elif future and action_type in {ActionType.BONUS.value, ActionType.SPLIT.value}:
            severity = "medium"
            risk_points += 18
        events.append(
            {
                "type": action_type,
                "date": action.action_date.isoformat(),
                "ratio_or_amount": _number(action.ratio_or_amount),
                "future": future,
                "severity": severity,
            }
        )

    if promoter:
        for label, lock_date in (("promoter_lock_expiry", promoter.prom_lock_date), ("mf_lock_expiry", promoter.mf_lock_date)):
            if lock_date and as_of_date <= lock_date <= as_of_date + timedelta(days=90):
                events.append({"type": label, "date": lock_date.isoformat(), "future": True, "severity": "high"})
                risk_points += 45

    for issue in issues:
        issue_type = (issue.issue_type or "issue").lower()
        severity = "high" if "right" in issue_type else "medium"
        risk_points += 40 if severity == "high" else 15
        events.append(
            {
                "type": issue.issue_type,
                "date": issue.opening_date.isoformat() if issue.opening_date else None,
                "future": bool(issue.opening_date and issue.opening_date >= as_of_date),
                "severity": severity,
            }
        )

    risk_points = min(100, risk_points)
    level = "high" if risk_points >= 45 else "medium" if risk_points >= 18 else "low"
    return {"level": level, "risk_score": risk_points, "events": events[:8]}


def build_fundamental_context(session: Session, symbol: str, sector: str | None) -> dict[str, Any]:
    fundamentals = session.execute(
        select(Fundamental)
        .where(Fundamental.symbol == symbol)
        .order_by(Fundamental.reported_date.desc())
        .limit(2)
    ).scalars().all()
    latest = fundamentals[0] if fundamentals else None
    previous = fundamentals[1] if len(fundamentals) > 1 else None
    baseline = None
    if sector:
        baseline = session.execute(
            select(SectorFundamentalBaseline).where(SectorFundamentalBaseline.sector == sector)
        ).scalar_one_or_none()

    if latest is None:
        return {
            "score": 50,
            "coverage": "unavailable",
            "sector_model": "generic_limited",
            "note": "No current fundamental row is available; fundamentals are neutral and excluded from probability training.",
        }

    eps = _number(latest.eps)
    pe = _number(latest.pe_ratio)
    pb = _number(latest.pb_ratio)
    previous_eps = _number(previous.eps) if previous else None
    eps_growth = None
    if eps is not None and previous_eps not in {None, 0}:
        eps_growth = (eps / previous_eps - 1.0) * 100.0

    score = 35
    if eps is not None:
        score += 18 if eps > 0 else -20
    if eps_growth is not None:
        score += 12 if eps_growth > 5 else 5 if eps_growth > 0 else -8

    sector_pe = _number(baseline.avg_pe) if baseline else None
    sector_pb = _number(baseline.avg_pb) if baseline else None
    if pe is not None and pe > 0:
        if sector_pe and pe <= sector_pe:
            score += 18
        elif sector_pe and pe <= sector_pe * 1.25:
            score += 10
        elif pe <= 35:
            score += 7
    if pb is not None and pb > 0:
        if sector_pb and pb <= sector_pb:
            score += 12
        elif pb <= 3.5:
            score += 7

    score = max(0, min(100, score))
    return {
        "score": score,
        "coverage": "current_snapshot",
        "sector_model": "generic_limited",
        "reported_date": latest.reported_date.isoformat(),
        "eps": eps,
        "eps_growth_percent": eps_growth,
        "pe_ratio": pe,
        "pb_ratio": pb,
        "sector_avg_pe": sector_pe,
        "sector_avg_pb": sector_pb,
        "included_in_probability_model": False,
        "note": (
            "Current fundamentals influence confluence only. v1 ML intentionally excludes them until historical fundamental "
            "snapshots are verified point-in-time and sector-specific fields such as NPL/solvency/project debt are available."
        ),
    }


def build_liquidity_context(
    session: Session,
    symbol: str,
    stock_series: dict[str, list[Any]],
    current_features: dict[str, float] | None,
) -> dict[str, Any]:
    tier = session.execute(
        select(SymbolLiquidityTier.liquidity_tier).where(SymbolLiquidityTier.symbol == symbol)
    ).scalar_one_or_none()
    volumes = [float(v) for v in stock_series["volumes"][-20:]]
    turnovers = [float(v) for v in stock_series["turnovers"][-20:]]
    nonzero_fraction = sum(1 for value in volumes if value > 0) / len(volumes) if volumes else 0.0
    avg_turnover = mean(turnovers) if turnovers else None

    normalized_tier = str(tier or "unclassified").lower()
    if "high" in normalized_tier or normalized_tier in {"tier_a", "a"}:
        quality = "high"
    elif "medium" in normalized_tier or normalized_tier in {"tier_b", "b"}:
        quality = "medium"
    elif tier:
        quality = "low"
    else:
        quality = "unavailable"

    return {
        "tier": tier,
        "quality": quality,
        "average_turnover_20d": avg_turnover,
        "nonzero_volume_session_fraction_20d": nonzero_fraction,
        "turnover_expansion_ratio": current_features.get("turnover_ratio_20d") if current_features else None,
        "volume_expansion_ratio": current_features.get("volume_ratio_20d") if current_features else None,
    }


def build_signal_families(
    *,
    technical: TechnicalSignal | None,
    features: dict[str, float] | None,
    ensemble: dict[str, Any],
) -> list[dict[str, Any]]:
    if not features:
        return []

    probability = ensemble.get("probability_outperform_after_cost")
    families: list[dict[str, Any]] = []

    trend_support = bool(
        technical
        and technical.sma_50 is not None
        and technical.sma_200 is not None
        and technical.sma_50 > technical.sma_200
        and features["return_20d"] > 0
        and features["distance_sma50_percent"] > 0
    )
    families.append(
        {
            "family": "trend_continuation",
            "supportive": trend_support,
            "reason": "Price/moving-average structure and 20-day trend agree." if trend_support else "Trend structure is not fully aligned.",
        }
    )

    momentum_support = bool(
        technical
        and technical.rsi_14 is not None
        and 45 <= float(technical.rsi_14) <= 68
        and technical.macd_line is not None
        and technical.macd_signal is not None
        and technical.macd_line > technical.macd_signal
    )
    families.append(
        {
            "family": "momentum",
            "supportive": momentum_support,
            "reason": "RSI and MACD confirm constructive momentum." if momentum_support else "Momentum confirmation is incomplete.",
        }
    )

    mean_reversion_trigger = bool(
        technical
        and (
            (technical.rsi_14 is not None and float(technical.rsi_14) < 30)
            or (
                technical.bollinger_lower is not None
                and features["distance_sma50_percent"] < -5
            )
        )
    )
    mean_reversion_support = bool(mean_reversion_trigger and probability is not None and probability >= 0.56)
    families.append(
        {
            "family": "mean_reversion",
            "supportive": mean_reversion_support,
            "triggered": mean_reversion_trigger,
            "reason": (
                "Exhaustion trigger is present and the probability model supports recovery."
                if mean_reversion_support
                else "No validated mean-reversion confluence is present."
            ),
        }
    )

    volume_support = features["turnover_ratio_20d"] >= 1.35 or features["volume_ratio_20d"] >= 1.35
    families.append(
        {
            "family": "volume_confirmation",
            "supportive": volume_support,
            "reason": "Turnover/volume is expanding versus its recent baseline." if volume_support else "Volume is not confirming the move.",
        }
    )

    rs_support = features["relative_strength_market_20d"] >= 2.0 and features["relative_strength_sector_20d"] >= 0.0
    families.append(
        {
            "family": "relative_strength",
            "supportive": rs_support,
            "reason": "The stock is outperforming NEPSE and is not lagging its sector." if rs_support else "Relative strength is not leading both benchmarks.",
        }
    )
    return families


def _build_confluence(
    *,
    market_regime: dict[str, Any],
    sector_regime: dict[str, Any],
    liquidity: dict[str, Any],
    fundamental: dict[str, Any],
    event_risk: dict[str, Any],
    signal_families: Sequence[dict[str, Any]],
    ensemble: dict[str, Any],
) -> dict[str, Any]:
    family_map = {row["family"]: bool(row.get("supportive")) for row in signal_families}
    probability = ensemble.get("probability_outperform_after_cost")
    dimensions = {
        "market_regime": market_regime.get("state") in {"strong_bull", "bull", "recovery"},
        "sector_regime": sector_regime.get("state") in {"strong_bull", "bull", "recovery"},
        "trend": family_map.get("trend_continuation", False),
        "momentum_or_mean_reversion": family_map.get("momentum", False) or family_map.get("mean_reversion", False),
        "relative_strength": family_map.get("relative_strength", False),
        "liquidity": liquidity.get("quality") in {"high", "medium"},
        "historical_probability": probability is not None and float(probability) >= 0.58,
        "fundamental_quality": int(fundamental.get("score") or 0) >= 60,
        "event_risk_clear": event_risk.get("level") != "high",
    }
    supportive = sum(1 for value in dimensions.values() if value)
    return {
        "supportive_dimensions": supportive,
        "total_dimensions": len(dimensions),
        "support_ratio": supportive / len(dimensions),
        "dimensions": dimensions,
    }


def _decision(
    *,
    ensemble: dict[str, Any],
    analogs: dict[str, Any],
    confluence: dict[str, Any],
    market_regime: dict[str, Any],
    sector_regime: dict[str, Any],
    liquidity: dict[str, Any],
    event_risk: dict[str, Any],
    validation_gate: dict[str, Any],
) -> dict[str, Any]:
    probability = ensemble.get("probability_outperform_after_cost")
    confidence = int(ensemble.get("confidence_score") or 0)
    supports = int(confluence.get("supportive_dimensions") or 0)
    expected_excess = analogs.get("expected_excess_return_percent")

    reasons: list[str] = []
    if probability is None or confidence < 30:
        research_label = "no_qualified_setup"
        reasons.append("The probability model does not yet have enough reliable evidence.")
    elif event_risk.get("level") == "high":
        research_label = "event_risk"
        reasons.append("A material corporate/supply event prevents a high-conviction setup label.")
    elif liquidity.get("quality") in {"low", "unavailable"}:
        research_label = "liquidity_constrained"
        reasons.append("Liquidity quality is too weak for a high-conviction signal.")
    elif (
        probability >= 0.70
        and confidence >= 70
        and supports >= 7
        and market_regime.get("state") not in {"bear", "high_stress"}
        and sector_regime.get("state") != "bear"
        and (expected_excess is None or float(expected_excess) > 1.5)
    ):
        research_label = "strong_candidate"
        reasons.append("Probability, confluence, liquidity and market/sector context align strongly.")
    elif probability >= 0.60 and confidence >= 50 and supports >= 6:
        research_label = "positive_candidate"
        reasons.append("Multiple independent dimensions support a positive setup, but conviction is below the strongest tier.")
    elif probability <= 0.40 and confidence >= 55:
        research_label = "weak_candidate"
        reasons.append("The model sees a low probability of beating NEPSE after costs.")
    else:
        research_label = "neutral"
        reasons.append("Evidence is mixed; ArthaSignal should not force a directional call.")

    public_gate_open = bool(validation_gate.get("public_high_confidence_enabled"))
    if not public_gate_open:
        public_label = "research_only"
    elif research_label == "strong_candidate":
        public_label = "strong_setup"
    elif research_label == "positive_candidate":
        public_label = "positive_setup"
    elif research_label == "weak_candidate":
        public_label = "weak"
    else:
        public_label = "neutral"

    return {
        "research_label": research_label,
        "public_label": public_label,
        "public_eligible": public_gate_open and research_label in {"strong_candidate", "positive_candidate", "weak_candidate"},
        "probability_outperform_nepse_after_cost": probability,
        "confidence_score": confidence,
        "expected_excess_return_20d_percent": expected_excess,
        "horizon_trading_days": DEFAULT_HORIZON_DAYS,
        "round_trip_cost_assumption_percent": ROUND_TRIP_COST_PERCENT,
        "reasons": reasons,
    }


def build_quant_research(session: Session, symbol: str) -> dict[str, Any] | None:
    company = session.execute(select(Company).where(Company.symbol == symbol)).scalar_one_or_none()
    if company is None:
        return None

    stock = _load_stock_series(session, symbol)
    if not stock["closes"]:
        return {
            "feature_version": FEATURE_VERSION,
            "symbol": symbol,
            "status": "insufficient_price_history",
        }

    market = _load_index_series(session, NEPSE_INDEX_NAME)
    market_regime = build_market_regime(session)
    sector_regime = build_sector_regime(session, company.sector, market_regime)
    sector_index = _load_index_series(session, sector_regime.get("index_name"))

    current_index = len(stock["closes"]) - 1
    current_features = build_feature_row(
        dates=stock["dates"],
        closes=stock["closes"],
        volumes=stock["volumes"],
        turnovers=stock["turnovers"],
        index=current_index,
        market_dates=market["dates"],
        market_closes=market["closes"],
        sector_dates=sector_index["dates"],
        sector_closes=sector_index["closes"],
    )

    technical = session.execute(
        select(TechnicalSignal)
        .where(TechnicalSignal.symbol == symbol, TechnicalSignal.timeframe == SignalTimeframe.DAILY)
        .order_by(TechnicalSignal.date.desc())
        .limit(1)
    ).scalar_one_or_none()

    as_of_date = stock["dates"][-1]
    liquidity = build_liquidity_context(session, symbol, stock, current_features)
    macro = build_macro_context(session)
    event_risk = build_event_risk(session, symbol, as_of_date)
    fundamental = build_fundamental_context(session, symbol, company.sector)

    labeled_rows = []
    analog_result: dict[str, Any]
    model = None
    logistic_probability = None
    if current_features and len(stock["closes"]) >= MIN_RESEARCH_HISTORY and len(market["closes"]) >= 80:
        labeled_rows = build_labeled_feature_rows(
            dates=stock["dates"],
            closes=stock["closes"],
            volumes=stock["volumes"],
            turnovers=stock["turnovers"],
            market_dates=market["dates"],
            market_closes=market["closes"],
            sector_dates=sector_index["dates"],
            sector_closes=sector_index["closes"],
            horizon_days=DEFAULT_HORIZON_DAYS,
        )
        analog_result = analyze_historical_analogs(current_features, labeled_rows)
        model = fit_ridge_logistic(labeled_rows)
        logistic_probability = predict_ridge_logistic(model, current_features)
    else:
        analog_result = analyze_historical_analogs(current_features or {}, [])

    ensemble = build_probability_ensemble(
        analog_result=analog_result,
        logistic_probability=logistic_probability,
        training_rows=len(labeled_rows),
    )
    signal_families = build_signal_families(
        technical=technical,
        features=current_features,
        ensemble=ensemble,
    )
    confluence = _build_confluence(
        market_regime=market_regime,
        sector_regime=sector_regime,
        liquidity=liquidity,
        fundamental=fundamental,
        event_risk=event_risk,
        signal_families=signal_families,
        ensemble=ensemble,
    )

    try:
        from src.pipeline.quant_validation import build_quant_validation_status

        validation_gate = build_quant_validation_status(session=session)
    except Exception as exc:
        validation_gate = {
            "gate_status": "unavailable",
            "public_high_confidence_enabled": False,
            "note": f"Quant validation ledger is unavailable: {type(exc).__name__}",
        }

    decision = _decision(
        ensemble=ensemble,
        analogs=analog_result,
        confluence=confluence,
        market_regime=market_regime,
        sector_regime=sector_regime,
        liquidity=liquidity,
        event_risk=event_risk,
        validation_gate=validation_gate,
    )

    relative_strength = None
    if current_features:
        relative_strength = {
            "vs_nepse_20d_percent": current_features["relative_strength_market_20d"],
            "vs_sector_20d_percent": current_features["relative_strength_sector_20d"],
            "stock_return_20d_percent": current_features["return_20d"],
            "stock_return_60d_percent": current_features["return_60d"],
        }

    return {
        "feature_version": FEATURE_VERSION,
        "symbol": symbol,
        "company_name": company.company_name,
        "sector": company.sector,
        "as_of_date": as_of_date.isoformat(),
        "status": "ready" if current_features and labeled_rows else "limited_history",
        "decision": decision,
        "probability_model": {
            **ensemble,
            "training_rows": len(labeled_rows),
            "model_type": model.get("model") if model else None,
            "model_base_rate": model.get("base_rate") if model else None,
            "target": (
                f"20-trading-day stock return minus NEPSE return > {ROUND_TRIP_COST_PERCENT:.2f}% cost hurdle"
            ),
        },
        "historical_analogs": analog_result,
        "market_regime": market_regime,
        "sector_regime": sector_regime,
        "relative_strength": relative_strength,
        "liquidity": liquidity,
        "fundamental_context": fundamental,
        "macro_context": macro,
        "event_risk": event_risk,
        "signal_families": signal_families,
        "confluence": confluence,
        "forward_validation": validation_gate,
        "data_quality": {
            "stock_price_rows": len(stock["closes"]),
            "market_index_rows": len(market["closes"]),
            "historical_labeled_rows": len(labeled_rows),
            "point_in_time_model_features": bool(current_features),
            "fundamentals_in_probability_model": False,
            "macro_in_probability_model": False,
            "note": (
                "Probability features use only price, volume/turnover and market/sector series available on each historical date. "
                "Current fundamentals and macro context stay outside ML until their historical observation timestamps are fully verified."
            ),
        },
    }
