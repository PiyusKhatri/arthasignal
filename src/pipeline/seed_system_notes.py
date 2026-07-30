from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import SystemNote

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FORWARD_DAYS_COST_VIABILITY_NOTE = (
    "FORWARD_DAYS COST-VIABILITY CAVEAT: this backtest framework computes win_rate/mean_return at "
    "forward_days=[5,10,20] throughout backtest_results, confluence_backtest_results, "
    "liquidity_stratified_backtest_results, and mtf_agreement_backtest_results. Realistic NEPSE "
    "round-trip transaction costs (~0.74% official fees only, ~1.24% including an estimated bid-ask "
    "spread) exceed the raw mean_return at 5 and 10 days for every signal tested so far. As of this "
    "analysis, no signal has been confirmed cost-viable at 5 or 10 days. The 5-day and 10-day columns "
    "across all backtest tables should be treated as diagnostic/research values - useful for "
    "understanding signal behavior and validating consistency across horizons - not as direct trading "
    "recommendations. Only 20-day forward_days figures currently represent actionable guidance, and "
    "only for signals with a recommended_holding_period of '20-day minimum' in signal_confidence. See "
    "transaction_cost_adjusted_returns for the underlying per-signal cost analysis."
)


def _upsert_system_note(note_key: str, note_text: str) -> None:
    stmt = pg_insert(SystemNote).values(
        note_key=note_key, note_text=note_text, updated_at=datetime.utcnow()
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["note_key"],
        set_={"note_text": stmt.excluded.note_text, "updated_at": stmt.excluded.updated_at},
    )
    with get_session() as session:
        session.execute(stmt)


def seed_system_notes() -> dict[str, Any]:
    _upsert_system_note("forward_days_cost_viability_caveat", FORWARD_DAYS_COST_VIABILITY_NOTE)
    logger.info("Seeded system_notes")
    return {"seeded": 1}


if __name__ == "__main__":
    seed_system_notes()
