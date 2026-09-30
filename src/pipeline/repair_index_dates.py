from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.database.connection import get_session
from src.database.models import DailyPrice, MarketIndex
from src.scrapers.index_scraper import INDEX_NAME_TO_ID, get_index_history

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CLOSE_TOLERANCE = 0.011
VALUE_COLUMNS = ("open", "high", "low", "close", "points_change", "percent_change")


def _load_db_rows(start_date: date, end_date: date) -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            select(
                MarketIndex.id,
                MarketIndex.index_name,
                MarketIndex.date,
                MarketIndex.open,
                MarketIndex.high,
                MarketIndex.low,
                MarketIndex.close,
                MarketIndex.points_change,
                MarketIndex.percent_change,
            )
            .where(MarketIndex.date >= start_date)
            .where(MarketIndex.date <= end_date)
            .order_by(MarketIndex.index_name, MarketIndex.date)
        ).all()
    return [
        {
            "id": row.id,
            "index_name": row.index_name,
            "date": row.date,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "points_change": float(row.points_change),
            "percent_change": float(row.percent_change),
        }
        for row in rows
    ]


def fetch_source_rows(index_names: list[str], start_date: date, end_date: date) -> dict[str, dict[date, dict[str, Any]]]:
    years = max(0.1, ((date.today() - start_date).days + 7) / 365)
    source: dict[str, dict[date, dict[str, Any]]] = {}
    for index_name in index_names:
        if index_name not in INDEX_NAME_TO_ID:
            continue
        rows = get_index_history(index_name, years=years)
        source[index_name] = {
            row["date"]: row
            for row in rows
            if start_date <= row["date"] <= end_date and row.get("close") is not None
        }
    return source


def _differs(db_row: dict[str, Any], source_row: dict[str, Any]) -> bool:
    for column in VALUE_COLUMNS:
        source_value = source_row.get(column)
        if source_value is None:
            continue
        if abs(float(db_row[column]) - float(source_value)) > CLOSE_TOLERANCE:
            return True
    return False


def build_repair_plan(
    db_rows: list[dict[str, Any]],
    source: dict[str, dict[date, dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    moves: list[dict[str, Any]] = []
    deletes: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    inserts: list[dict[str, Any]] = []
    value_updates: list[dict[str, Any]] = []

    by_index: dict[str, list[dict[str, Any]]] = {}
    for row in db_rows:
        by_index.setdefault(row["index_name"], []).append(row)

    for index_name, sessions in source.items():
        rows = sorted(by_index.get(index_name, []), key=lambda row: row["date"])
        occupied = {row["date"]: row for row in rows if row["date"] in sessions}
        claimed: set[date] = set(occupied)

        for row in rows:
            if row["date"] in sessions:
                continue
            candidates = [
                session_date
                for session_date, source_row in sessions.items()
                if session_date < row["date"] and abs(float(source_row["close"]) - row["close"]) <= CLOSE_TOLERANCE
            ]
            if not candidates:
                unmatched.append({"id": row["id"], "index_name": index_name, "date": row["date"], "close": row["close"]})
                continue
            target = max(candidates)
            entry = {
                "id": row["id"],
                "index_name": index_name,
                "stored_date": row["date"],
                "session_date": target,
                "close": row["close"],
            }
            if target in claimed:
                deletes.append(entry)
            else:
                claimed.add(target)
                moves.append(entry)
                occupied[target] = {**row, "date": target}

        for session_date in sorted(sessions):
            source_row = sessions[session_date]
            existing = occupied.get(session_date)
            if existing is None:
                inserts.append({"index_name": index_name, "date": session_date, **{c: source_row.get(c) for c in VALUE_COLUMNS}})
            elif _differs(existing, source_row):
                value_updates.append(
                    {
                        "id": existing["id"],
                        "index_name": index_name,
                        "date": session_date,
                        "stored_close": existing["close"],
                        "source_close": float(source_row["close"]),
                        "close_differs": abs(existing["close"] - float(source_row["close"])) > CLOSE_TOLERANCE,
                        "values": {c: source_row.get(c) for c in VALUE_COLUMNS if source_row.get(c) is not None},
                    }
                )

    return {
        "moves": moves,
        "deletes": deletes,
        "unmatched": unmatched,
        "inserts": inserts,
        "value_updates": value_updates,
    }


def apply_repair_plan(plan: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    with get_session() as session:
        if plan["deletes"]:
            session.execute(delete(MarketIndex).where(MarketIndex.id.in_([row["id"] for row in plan["deletes"]])))
        for row in plan["moves"]:
            session.execute(update(MarketIndex).where(MarketIndex.id == row["id"]).values(date=row["session_date"]))
        session.flush()
        for row in plan["value_updates"]:
            session.execute(update(MarketIndex).where(MarketIndex.id == row["id"]).values(**row["values"]))
        inserted = 0
        if plan["inserts"]:
            complete = [row for row in plan["inserts"] if all(row.get(c) is not None for c in VALUE_COLUMNS)]
            if complete:
                statement = pg_insert(MarketIndex).values(complete)
                statement = statement.on_conflict_do_nothing(index_elements=["index_name", "date"]).returning(MarketIndex.id)
                inserted = len(session.execute(statement).fetchall())
    return {
        "deleted": len(plan["deletes"]),
        "moved": len(plan["moves"]),
        "values_updated": len(plan["value_updates"]),
        "inserted": inserted,
    }


def repair_index_dates(start_date: date, end_date: date | None = None, apply: bool = False) -> dict[str, Any]:
    with get_session() as session:
        last_price_date = session.execute(select(func.max(DailyPrice.date))).scalar()
        index_names = list(session.execute(select(MarketIndex.index_name).distinct().order_by(MarketIndex.index_name)).scalars())
    end_date = end_date or last_price_date or date.today()

    db_rows = _load_db_rows(start_date, end_date + timedelta(days=7))
    source = fetch_source_rows(index_names, start_date, end_date)
    plan = build_repair_plan(db_rows, source)

    result: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "series_in_database": len(index_names),
        "series_fetched": len(source),
        "source_sessions": {name: len(rows) for name, rows in source.items()},
        "plan_counts": {key: len(value) for key, value in plan.items()},
        "plan": plan,
        "applied": None,
    }
    if apply:
        result["applied"] = apply_repair_plan(plan)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Move index rows to the session date they represent")
    parser.add_argument("--from", dest="start_date", required=True)
    parser.add_argument("--to", dest="end_date", default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = repair_index_dates(
        date.fromisoformat(args.start_date),
        date.fromisoformat(args.end_date) if args.end_date else None,
        apply=args.apply,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
