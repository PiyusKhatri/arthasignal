from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from src.pipeline.market_pulse import compute_overall_market_pulse

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/pulse")
def get_market_pulse() -> dict[str, Any]:
    return compute_overall_market_pulse()
