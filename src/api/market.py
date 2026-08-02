from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from src.api.cache import cached, market_pulse_cache
from src.api.rate_limit import PUBLIC_RATE_LIMIT, limiter
from src.pipeline.market_pulse import compute_overall_market_pulse

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/pulse")
@limiter.limit(PUBLIC_RATE_LIMIT)
@cached(market_pulse_cache, key_fn=lambda **kwargs: "pulse")
def get_market_pulse(request: Request) -> dict[str, Any]:
    return compute_overall_market_pulse()
