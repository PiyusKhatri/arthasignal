from __future__ import annotations

from datetime import date, datetime

import src.pipeline.backfill_daily_index as refresh
from src.pipeline.repair_index_dates import build_repair_plan
from src.scrapers.sharesansar_scraper import parse_as_of_date

FRIDAY_SESSION = date(2026, 9, 18)
SUNDAY_RUN = date(2026, 9, 20)


def _patch_sources(monkeypatch, nepse_rows, sub_rows) -> list[dict]:
    written: list[dict] = []

    def fake_upsert(rows):
        written.extend(rows)
        return len(rows), 0

    monkeypatch.setattr(refresh.nepse_api, "get_nepse_index", lambda: nepse_rows)
    monkeypatch.setattr(refresh.sharesansar_scraper, "scrape_sub_indices", lambda: sub_rows)
    monkeypatch.setattr(refresh, "upsert_recent_market_index_rows", fake_upsert)
    monkeypatch.setattr(refresh, "_recent_price_sessions", lambda count: [])
    return written


def _nepse_row(generated_time):
    return {
        "index": "NEPSE Index",
        "generatedTime": generated_time,
        "high": 2647.48,
        "low": 2614.12,
        "currentValue": 2647.22,
        "change": 22.85,
        "perChange": 0.87,
    }


def _sub_row(as_of):
    return {
        "indexName": "Banking SubIndex",
        "asOfDate": as_of,
        "open": 1500.0,
        "high": 1515.0,
        "low": 1498.0,
        "close": 1511.16,
        "pointChange": 9.5,
        "percentChange": 0.63,
    }


def test_rows_are_stamped_with_the_source_session_date_not_the_run_date(monkeypatch) -> None:
    written = _patch_sources(monkeypatch, [_nepse_row("2026-09-18T15:00:02.113")], [_sub_row(FRIDAY_SESSION)])

    summary = refresh.run_daily_index_refresh(today=SUNDAY_RUN)

    assert [row["date"] for row in written] == [FRIDAY_SESSION, FRIDAY_SESSION]
    assert all(row["date"] != SUNDAY_RUN for row in written)
    assert summary["session_dates"] == [FRIDAY_SESSION]
    assert summary["run_date"] == SUNDAY_RUN
    assert summary["failures"] == 0


def test_row_without_a_source_date_is_refused_rather_than_run_dated(monkeypatch) -> None:
    written = _patch_sources(monkeypatch, [_nepse_row(None)], [_sub_row(None), _sub_row(FRIDAY_SESSION)])

    summary = refresh.run_daily_index_refresh(today=SUNDAY_RUN)

    assert [row["date"] for row in written] == [FRIDAY_SESSION]
    assert summary["rows_without_source_session_date"] == 2
    assert summary["failures"] == 2
    assert summary["rows_upserted"] == 1


def test_sub_index_rows_keep_source_open_high_low(monkeypatch) -> None:
    written = _patch_sources(monkeypatch, [], [_sub_row(FRIDAY_SESSION)])
    refresh.run_daily_index_refresh(today=SUNDAY_RUN)
    assert (written[0]["open"], written[0]["high"], written[0]["low"], written[0]["close"]) == (
        1500.0,
        1515.0,
        1498.0,
        1511.16,
    )


def test_parse_session_date_accepts_source_formats_only() -> None:
    assert refresh.parse_session_date("2026-09-18T15:00:02.113") == FRIDAY_SESSION
    assert refresh.parse_session_date("2026-09-18") == FRIDAY_SESSION
    assert refresh.parse_session_date(datetime(2026, 9, 18, 15, 0)) == FRIDAY_SESSION
    assert refresh.parse_session_date(FRIDAY_SESSION) == FRIDAY_SESSION
    assert refresh.parse_session_date(None) is None
    assert refresh.parse_session_date("") is None
    assert refresh.parse_session_date("not a date") is None


def test_as_of_date_is_read_from_the_sub_indices_block() -> None:
    assert parse_as_of_date("Sub Indices As of 2026-09-30 Sub-Indices Open High") == date(2026, 9, 30)
    assert parse_as_of_date("Sub Indices Sub-Indices Open High") is None
    assert parse_as_of_date("As of 2026-13-45") is None


def _source_row(day: date, close: float) -> dict:
    return {
        "index_name": "Banking SubIndex",
        "date": day,
        "open": close - 1,
        "high": close + 2,
        "low": close - 3,
        "close": close,
        "points_change": 5.0,
        "percent_change": 0.3,
    }


def _db_row(row_id: int, day: date, close: float) -> dict:
    return {
        "id": row_id,
        "index_name": "Banking SubIndex",
        "date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "points_change": 5.0,
        "percent_change": 0.3,
    }


def test_repair_plan_moves_misdated_rows_and_removes_stale_copies() -> None:
    thursday, friday, tuesday = date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 22)
    sunday, monday_holiday = date(2026, 9, 20), date(2026, 9, 21)
    source = {
        "Banking SubIndex": {
            thursday: _source_row(thursday, 1500.0),
            friday: _source_row(friday, 1511.16),
            tuesday: _source_row(tuesday, 1520.0),
        }
    }
    db_rows = [
        _db_row(1, thursday, 1500.0),
        _db_row(2, sunday, 1511.16),
        _db_row(3, monday_holiday, 1511.16),
    ]

    plan = build_repair_plan(db_rows, source)

    assert [(m["id"], m["stored_date"], m["session_date"]) for m in plan["moves"]] == [(2, sunday, friday)]
    assert [(d["id"], d["stored_date"], d["session_date"]) for d in plan["deletes"]] == [(3, monday_holiday, friday)]
    assert plan["unmatched"] == []
    assert [(i["date"], i["close"]) for i in plan["inserts"]] == [(tuesday, 1520.0)]
    assert sorted(u["date"] for u in plan["value_updates"]) == [thursday, friday]
    assert all(u["close_differs"] is False for u in plan["value_updates"])


def test_repair_plan_never_deletes_a_row_it_cannot_explain() -> None:
    thursday, sunday = date(2026, 9, 17), date(2026, 9, 20)
    source = {"Banking SubIndex": {thursday: _source_row(thursday, 1500.0)}}
    db_rows = [_db_row(1, thursday, 1500.0), _db_row(2, sunday, 1777.77)]

    plan = build_repair_plan(db_rows, source)

    assert plan["moves"] == [] and plan["deletes"] == []
    assert [(u["id"], u["date"]) for u in plan["unmatched"]] == [(2, sunday)]
