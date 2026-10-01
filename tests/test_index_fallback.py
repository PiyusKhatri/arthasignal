from __future__ import annotations

from datetime import date

import pytest

import src.pipeline.backfill_daily_index as refresh
import src.pipeline.data_quality as data_quality

S1 = date(2026, 9, 28)
S2 = date(2026, 9, 29)


def _history_row(index_name: str, day: date, close: float) -> dict:
    return {
        "index_name": index_name,
        "date": day,
        "open": close - 5,
        "high": close + 5,
        "low": close - 10,
        "close": close,
        "points_change": 1.0,
        "percent_change": 0.1,
    }


def _patch_fallback(monkeypatch, stored: set, history: dict) -> tuple[list, list]:
    written: list[dict] = []
    requested: list[tuple] = []

    def fake_history(index_name, start_date=None, end_date=None, **_):
        requested.append((index_name, start_date, end_date))
        if index_name not in history:
            raise ConnectionError("source down")
        return history[index_name]

    def fake_upsert(rows):
        written.extend(rows)
        return len(rows), 0

    monkeypatch.setattr(refresh, "_recent_price_sessions", lambda count: [S1, S2])
    monkeypatch.setattr(refresh, "_stored_index_keys", lambda start: set(stored))
    monkeypatch.setattr(refresh, "get_index_history", fake_history)
    monkeypatch.setattr(refresh, "upsert_recent_market_index_rows", fake_upsert)
    return written, requested


def _all_stored(except_keys: set) -> set:
    return {(name, day) for name in refresh.TRACKED_INDEX_NAMES for day in (S1, S2)} - except_keys


def test_missing_sessions_are_listed_per_index() -> None:
    present = {("NEPSE Index", S1), ("Float Index", S1), ("Float Index", S2)}

    missing = refresh.find_missing_index_sessions([S1, S2], present, ["NEPSE Index", "Float Index", "Sensitive Index"])

    assert missing == {"NEPSE Index": [S2], "Sensitive Index": [S1, S2]}


def test_fallback_fills_broad_index_when_primary_source_failed(monkeypatch) -> None:
    stored = _all_stored({("NEPSE Index", S2), ("Sensitive Index", S2)})
    history = {
        "NEPSE Index": [_history_row("NEPSE Index", S2, 2603.71)],
        "Sensitive Index": [_history_row("Sensitive Index", S2, 466.65)],
    }
    written, requested = _patch_fallback(monkeypatch, stored, history)

    summary = refresh.fill_missing_index_rows([])

    assert sorted((row["index_name"], row["date"], row["close"]) for row in written) == [
        ("NEPSE Index", S2, 2603.71),
        ("Sensitive Index", S2, 466.65),
    ]
    assert sorted(name for name, _, _ in requested) == ["NEPSE Index", "Sensitive Index"]
    assert summary["fallback_rows_upserted"] == 2
    assert summary["still_missing"] == {}


def test_nothing_is_fetched_when_every_index_is_present(monkeypatch) -> None:
    written, requested = _patch_fallback(monkeypatch, _all_stored(set()), {})

    summary = refresh.fill_missing_index_rows([])

    assert written == [] and requested == []
    assert summary["missing_before_fallback"] == {}


def test_rows_the_fallback_cannot_find_are_reported_not_hidden(monkeypatch) -> None:
    stored = _all_stored({("NEPSE Index", S2), ("Float Index", S1)})
    written, _ = _patch_fallback(monkeypatch, stored, {"NEPSE Index": [_history_row("NEPSE Index", S1, 2600.0)]})

    summary = refresh.fill_missing_index_rows([])

    assert written == []
    assert summary["still_missing"] == {"Float Index": [S1.isoformat()], "NEPSE Index": [S2.isoformat()]}


def test_refresh_counts_unfilled_index_rows_as_failures(monkeypatch) -> None:
    monkeypatch.setattr(refresh.nepse_api, "get_nepse_index", lambda: (_ for _ in ()).throw(ConnectionError("blocked")))
    monkeypatch.setattr(refresh.sharesansar_scraper, "scrape_sub_indices", lambda: [])
    stored = _all_stored({(name, S2) for name in ("NEPSE Index", "Sensitive Index", "Float Index", "Sensitive Float Index")})
    _patch_fallback(monkeypatch, stored, {})

    summary = refresh.run_daily_index_refresh(today=S2)

    assert summary["failures"] == 4
    assert set(summary["fallback"]["still_missing"]) == {"NEPSE Index", "Sensitive Index", "Float Index", "Sensitive Float Index"}


def test_discontinued_index_is_not_tracked() -> None:
    assert "Insurance" not in refresh.TRACKED_INDEX_NAMES
    assert "NEPSE Index" in refresh.TRACKED_INDEX_NAMES


def test_coverage_check_fails_loudly_when_a_session_lacks_the_benchmark(monkeypatch) -> None:
    monkeypatch.setattr(data_quality, "missing_benchmark_index_sessions", lambda: [S2])

    result = data_quality._check_benchmark_index_coverage(S2)

    assert result == {"flagged": True, "missing_sessions": [S2]}
    with pytest.raises(data_quality.MissingIndexSessionsError, match="2026-09-29"):
        data_quality.assert_benchmark_index_coverage()


def test_coverage_check_passes_when_every_session_has_the_benchmark(monkeypatch) -> None:
    monkeypatch.setattr(data_quality, "missing_benchmark_index_sessions", lambda: [])

    assert data_quality._check_benchmark_index_coverage(S2) == {"flagged": False, "missing_sessions": []}
    data_quality.assert_benchmark_index_coverage()


def test_coverage_query_runs_against_the_database() -> None:
    assert data_quality.missing_benchmark_index_sessions() == []
