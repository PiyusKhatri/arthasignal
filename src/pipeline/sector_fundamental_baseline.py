from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import SectorFundamentalBaseline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIN_SECTOR_SAMPLE_FOR_OUTLIER_FILTER = 5
IQR_MULTIPLIER = Decimal("1.5")


def _load_latest_fundamentals_by_sector() -> dict[str, list[dict[str, Any]]]:
    with get_session() as session:
        rows = session.execute(
            text(
                """
                SELECT DISTINCT ON (f.symbol) f.symbol, c.sector, f.pe_ratio, f.pb_ratio, f.market_capitalization
                FROM fundamentals f
                JOIN companies c ON c.symbol = f.symbol
                WHERE c.instrument_type = 'Equity' AND c.status = 'A' AND c.sector IS NOT NULL
                ORDER BY f.symbol, f.reported_date DESC
                """
            )
        ).all()

    by_sector: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_sector.setdefault(row.sector, []).append(
            {"symbol": row.symbol, "pe": row.pe_ratio, "pb": row.pb_ratio, "market_cap": row.market_capitalization}
        )
    return by_sector


def _iqr_outlier_bounds(values: list[Decimal]) -> tuple[Decimal, Decimal] | None:
    if len(values) < MIN_SECTOR_SAMPLE_FOR_OUTLIER_FILTER:
        return None
    q1, _median, q3 = statistics.quantiles([float(v) for v in values], n=4, method="inclusive")
    iqr = Decimal(str(q3 - q1))
    lower = Decimal(str(q1)) - IQR_MULTIPLIER * iqr
    upper = Decimal(str(q3)) + IQR_MULTIPLIER * iqr
    return lower, upper


def _weighted_average(candidates: list[tuple[str, Decimal, Decimal]]) -> Decimal | None:
    numerator = Decimal(0)
    denominator = Decimal(0)
    for _symbol, value, weight in candidates:
        numerator += value * weight
        denominator += weight
    return numerator / denominator if denominator else None


def _filter_outliers(candidates: list[tuple[str, Decimal, Decimal]]) -> tuple[list[tuple[str, Decimal, Decimal]], set[str]]:
    bounds = _iqr_outlier_bounds([value for _symbol, value, _weight in candidates])
    if bounds is None:
        return candidates, set()

    lower, upper = bounds
    kept = [c for c in candidates if lower <= c[1] <= upper]
    outliers = {symbol for symbol, value, _weight in candidates if not (lower <= value <= upper)}
    return kept, outliers


def compute_sector_fundamental_baselines() -> dict[str, Any]:
    fundamentals_by_sector = _load_latest_fundamentals_by_sector()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    db_rows = []
    sector_reports = []

    for sector, rows in fundamentals_by_sector.items():
        pe_candidates = [
            (r["symbol"], r["pe"], r["market_cap"]) for r in rows if r["pe"] is not None and r["market_cap"] is not None
        ]
        pb_candidates = [
            (r["symbol"], r["pb"], r["market_cap"]) for r in rows if r["pb"] is not None and r["market_cap"] is not None
        ]

        pe_kept, pe_outliers = _filter_outliers(pe_candidates)
        pb_kept, pb_outliers = _filter_outliers(pb_candidates)

        avg_pe = _weighted_average(pe_kept)
        avg_pb = _weighted_average(pb_kept)

        excluded_outliers = pe_outliers | pb_outliers

        db_rows.append(
            {
                "sector": sector,
                "avg_pe": avg_pe,
                "avg_pb": avg_pb,
                "excluded_outlier_count": len(excluded_outliers),
                "computed_at": now,
            }
        )

        sector_reports.append(
            {
                "sector": sector,
                "total_symbols": len(rows),
                "pe_candidates": len(pe_candidates),
                "pb_candidates": len(pb_candidates),
                "pe_missing_data": len(rows) - len(pe_candidates),
                "pb_missing_data": len(rows) - len(pb_candidates),
                "pe_outliers": sorted(pe_outliers),
                "pb_outliers": sorted(pb_outliers),
                "excluded_outlier_count": len(excluded_outliers),
                "avg_pe": avg_pe,
                "avg_pb": avg_pb,
            }
        )

    if db_rows:
        stmt = pg_insert(SectorFundamentalBaseline).values(db_rows)
        update_columns = {c: getattr(stmt.excluded, c) for c in db_rows[0] if c != "sector"}
        stmt = stmt.on_conflict_do_update(index_elements=["sector"], set_=update_columns)
        with get_session() as session:
            session.execute(stmt)

    logger.info("Computed sector fundamental baselines for %d sectors", len(db_rows))
    return {"sectors_processed": len(db_rows), "reports": sector_reports}


if __name__ == "__main__":
    compute_sector_fundamental_baselines()
