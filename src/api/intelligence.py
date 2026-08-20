from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.api.cache import cached, market_pulse_cache
from src.api.db_readonly import get_readonly_session
from src.api.rate_limit import PUBLIC_RATE_LIMIT, limiter
from src.services.stock_intelligence import build_market_intelligence, build_stock_intelligence

router = APIRouter(tags=["intelligence"])


@router.get("/stocks/{symbol}/intelligence")
@limiter.limit(PUBLIC_RATE_LIMIT)
def get_stock_intelligence(symbol: str, request: Request) -> dict[str, Any]:
    del request
    normalized = symbol.strip().upper()

    with get_readonly_session() as session:
        result = build_stock_intelligence(session, normalized)

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown symbol: {normalized}",
        )

    return result


@router.get("/market/intelligence")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(market_pulse_cache, key_fn=lambda **kwargs: f"market-intelligence:{kwargs.get('limit', 200)}")
def get_market_intelligence(
    request: Request,
    limit: int = Query(200, ge=25, le=300),
) -> dict[str, Any]:
    del request
    with get_readonly_session() as session:
        return build_market_intelligence(session, limit=limit)
