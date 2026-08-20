from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.api.cache import cached, market_pulse_cache, stock_intelligence_cache
from src.api.db_readonly import get_readonly_session
from src.api.rate_limit import PUBLIC_RATE_LIMIT, limiter
from src.pipeline.quant_validation import build_quant_validation_status
from src.services.nepse_quant_research import build_market_regime, build_quant_research
from src.services.stock_intelligence import build_market_intelligence, build_stock_intelligence

router = APIRouter(tags=["intelligence"])


@router.get("/stocks/{symbol}/intelligence")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(
    stock_intelligence_cache,
    key_fn=lambda **kwargs: f"stock-intelligence:{str(kwargs.get('symbol', '')).strip().upper()}",
)
def get_stock_intelligence(symbol: str, request: Request) -> dict[str, Any]:
    del request
    normalized = symbol.strip().upper()

    with get_readonly_session() as session:
        result = build_stock_intelligence(session, normalized)
        quant_research = build_quant_research(session, normalized) if result is not None else None

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown symbol: {normalized}",
        )

    result["quant_research"] = quant_research
    return result


@router.get("/stocks/{symbol}/quant-research")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(
    stock_intelligence_cache,
    key_fn=lambda **kwargs: f"quant-research:{str(kwargs.get('symbol', '')).strip().upper()}",
)
def get_stock_quant_research(symbol: str, request: Request) -> dict[str, Any]:
    del request
    normalized = symbol.strip().upper()
    with get_readonly_session() as session:
        result = build_quant_research(session, normalized)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown symbol: {normalized}",
        )
    return result


@router.get("/market/quant-regime")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(market_pulse_cache, key_fn=lambda **kwargs: "market-quant-regime")
def get_market_quant_regime(request: Request) -> dict[str, Any]:
    del request
    with get_readonly_session() as session:
        return build_market_regime(session)


@router.get("/quant/validation")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(market_pulse_cache, key_fn=lambda **kwargs: "quant-validation")
def get_quant_validation(request: Request) -> dict[str, Any]:
    del request
    with get_readonly_session() as session:
        return build_quant_validation_status(session=session)


@router.get("/market/intelligence")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(market_pulse_cache, key_fn=lambda **kwargs: f"market-intelligence:{kwargs.get('limit', 200)}")
def get_market_intelligence(
    request: Request,
    limit: int = Query(200, ge=25, le=300),
) -> dict[str, Any]:
    del request
    with get_readonly_session() as session:
        result = build_market_intelligence(session, limit=limit)
        result["quant_regime"] = build_market_regime(session)
        result["quant_validation"] = build_quant_validation_status(session=session)
        return result
