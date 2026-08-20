from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from src.api.rate_limit import PUBLIC_RATE_LIMIT, limiter
from src.database.connection import get_session
from src.services.stock_intelligence import build_stock_intelligence

router = APIRouter(prefix="/stocks", tags=["stock-intelligence"])


@router.get("/{symbol}/intelligence")
@limiter.limit(PUBLIC_RATE_LIMIT)
def get_stock_intelligence(symbol: str, request: Request) -> dict:
    del request
    normalized = symbol.strip().upper()

    with get_session() as session:
        result = build_stock_intelligence(session, normalized)

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown symbol: {normalized}",
        )

    return result
