from __future__ import annotations

import json
from datetime import date

import src.pipeline.backfill_price_history as history
from src.pipeline.validate_price_history import classify_volume, compare_price_rows


def test_instrument_type_is_inferred_from_name_and_symbol() -> None:
    assert history.infer_instrument_type("GRANDP", "Grand Bank Limited Promoter Share") == "Promoter Shares"
    assert history.infer_instrument_type("TNBLPO", None) == "Promoter Shares"
    assert history.infer_instrument_type("EBLCP", "Everest Bank Limited Con. Pref.") == "Preference Shares"
    assert history.infer_instrument_type("NBF1", "Nabil Balance Fund 1") == "Mutual Funds"
    assert history.infer_instrument_type("SBLD2082", None) == "Non-Convertible Debentures"
    assert history.infer_instrument_type("GBD80/81", None) == "Non-Convertible Debentures"
    assert history.infer_instrument_type("NIB", "Nepal Investment Bank Limited") == "Equity"
    assert history.infer_instrument_type("BOK", None) == "Equity"


def test_candidate_dates_skip_saturdays_only() -> None:
    days = history.candidate_dates(date(2014, 6, 1), date(2014, 6, 8))

    assert date(2014, 6, 7) not in days
    assert date(2014, 6, 6) in days
    assert len(days) == 7


def test_resume_skips_finished_dates_but_retries_failures(tmp_path) -> None:
    state = tmp_path / "state.jsonl"
    records = [
        {"date": "2014-06-01", "status": "inserted"},
        {"date": "2014-06-06", "status": "no_session"},
        {"date": "2014-06-02", "status": "failed"},
        {"date": "2014-06-03", "status": "date_mismatch"},
    ]
    state.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    assert history.completed_dates(state) == {date(2014, 6, 1), date(2014, 6, 6)}
    assert history.completed_dates(tmp_path / "missing.jsonl") == set()


def test_duplicate_symbols_in_one_session_keep_the_first_row() -> None:
    rows = [{"symbol": "A", "close": 1}, {"symbol": "B", "close": 2}, {"symbol": "A", "close": 3}]

    unique, duplicates = history.dedupe_rows(rows)

    assert unique == [{"symbol": "A", "close": 1}, {"symbol": "B", "close": 2}]
    assert duplicates == 1


def test_source_date_mismatch_is_recorded_and_nothing_is_written(monkeypatch) -> None:
    monkeypatch.setattr(history, "_fetch_with_retry", lambda day, client: (date(2014, 6, 5), [{"symbol": "A"}]))
    monkeypatch.setattr(history, "insert_price_rows_only_new", lambda rows: (_ for _ in ()).throw(AssertionError))

    record = history.backfill_day(date(2014, 6, 6), {}, {})

    assert record["status"] == "date_mismatch"
    assert record["source_as_of"] == "2014-06-05"


def test_empty_source_day_is_a_no_session(monkeypatch) -> None:
    monkeypatch.setattr(history, "_fetch_with_retry", lambda day, client: (day, []))

    assert history.backfill_day(date(2014, 6, 6), {}, {})["status"] == "no_session"


def test_price_comparison_flags_each_differing_field() -> None:
    local = {"open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000}

    assert compare_price_rows(local, {**local}) == []
    assert compare_price_rows(local, {**local, "close": 105.005}) == []
    assert compare_price_rows(local, {**local, "close": 106, "volume": 999}) == ["close", "volume"]


def test_volume_classification() -> None:
    assert classify_volume(1000, 1000) == "exact"
    assert classify_volume(1000, 995) == "within_1pct"
    assert classify_volume(1000, 900) == "mismatch"
    assert classify_volume(1000, 0) == "mismatch"
