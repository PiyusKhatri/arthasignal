from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from src.ops import capture, daily, health, report
from src.scorecard.calendar import NPT


def test_capture_classifies_sessions_holidays_and_incomplete_data() -> None:
    assert capture.classify({"price_rows": 250, "nepse_index_rows": 1}, True) == "session"
    assert capture.classify({"price_rows": 0, "nepse_index_rows": 0}, False) == "no_session"
    assert capture.classify({"price_rows": 0, "nepse_index_rows": 0}, True) == "incomplete"
    assert capture.classify({"price_rows": 250, "nepse_index_rows": 0}, True) == "incomplete"


def test_capture_retries_until_the_cutoff_then_fails(monkeypatch) -> None:
    monkeypatch.setattr(capture, "attempt", lambda day: {"prices": {"error": "boom"}})
    monkeypatch.setattr(capture, "session_rows", lambda day: {"price_rows": 0, "nepse_index_rows": 0})
    monkeypatch.setattr("src.pipeline.backfill_calendar.is_trading_day", lambda day=None: True)
    sleeps = []
    cutoff = datetime.now(tz=NPT)
    code, out = capture.run(date(2026, 10, 5), cutoff, sleep=sleeps.append)
    assert code == capture.EXIT_FAILED and len(out["attempts"]) == 1 and out["attempts"][0]["errors"] == {"prices": "boom"}


def test_daily_chain_writes_calls_before_collectors_and_soft_checks() -> None:
    assert [s.name for s in daily.STEPS] == ["capture", "integrity", "league", "avoid_writer", "tips", "corporate_actions", "sectors",
                                            "quarterly_capture", "news", "grading", "metrics"]
    gates = {s.name: s.gate for s in daily.STEPS}
    assert gates["corporate_actions"] == gates["sectors"] == gates["quarterly_capture"] == gates["news"] == "soft"
    assert [s.name for s in daily.plan("league", None)][:2] == ["league", "avoid_writer"]
    with pytest.raises(SystemExit):
        daily.plan("nope", None)


@pytest.fixture
def fake_chain(monkeypatch, tmp_path):
    recorded, alerts = [], []
    monkeypatch.setattr(daily.runlog, "apply_schema", lambda engine: None)
    monkeypatch.setattr(daily.runlog, "record", lambda engine, job, key, step, status, code, started, detail=None: recorded.append((step, status)))
    monkeypatch.setattr(daily.runlog, "alert_once", lambda engine, fp, msg, severity="failure": alerts.append(fp) or True)
    monkeypatch.chdir(tmp_path)
    return recorded, alerts


def _outcomes(monkeypatch, outcomes):
    def fake(step, log_dir, env):
        assert env["ARTHASIGNAL_EXCLUSIONS"].endswith(daily.EXCLUSIONS_FILE)
        return outcomes.get(step.name, ("ok", 0, {}))
    monkeypatch.setattr(daily, "run_step", fake)


def test_no_session_skips_everything_but_news(monkeypatch, fake_chain) -> None:
    recorded, _ = fake_chain
    _outcomes(monkeypatch, {"capture": ("no_session", 3, {})})
    assert daily.run(report=False) == 0
    status = dict(recorded)
    assert status.pop("capture") == "no_session" and status.pop("news") == "ok"
    assert set(status.values()) == {"skipped"}


def test_flagged_symbols_are_excluded_but_calls_are_still_written(monkeypatch, fake_chain) -> None:
    recorded, alerts = fake_chain
    _outcomes(monkeypatch, {"integrity": ("flagged", 3, {})})
    assert daily.run(report=False) == 0
    status = dict(recorded)
    assert status["integrity"] == "flagged" and status["league"] == status["avoid_writer"] == status["tips"] == "ok"
    assert alerts == [next(a for a in alerts if a.startswith("integrity-flagged:"))]


def test_feed_wide_price_problem_is_the_only_integrity_outcome_that_stops_the_writers(monkeypatch, fake_chain) -> None:
    recorded, alerts = fake_chain
    _outcomes(monkeypatch, {"integrity": ("failed", 4, {})})
    assert daily.run(report=False) == 1
    status = dict(recorded)
    assert status["league"] == status["avoid_writer"] == status["tips"] == "skipped"
    assert status["sectors"] == status["grading"] == status["metrics"] == "ok"
    assert alerts[0].startswith("integrity-feed:")


def test_a_crashed_integrity_checker_does_not_stop_the_calls(monkeypatch, fake_chain) -> None:
    recorded, alerts = fake_chain
    _outcomes(monkeypatch, {"integrity": ("failed", 1, {})})
    assert daily.run(report=False) == 1
    status = dict(recorded)
    assert status["league"] == status["avoid_writer"] == status["tips"] == "ok"
    assert alerts[0].startswith("integrity-error:")


def test_failing_sector_and_collector_checks_never_block_anything(monkeypatch, fake_chain) -> None:
    recorded, _ = fake_chain
    _outcomes(monkeypatch, {"sectors": ("failed", 1, {}), "quarterly_capture": ("timeout", None, {}), "news": ("failed", 1, {})})
    assert daily.run(report=False) == 1
    status = dict(recorded)
    assert status["league"] == status["avoid_writer"] == status["tips"] == status["grading"] == status["metrics"] == "ok"


def test_capture_failure_stops_the_writers(monkeypatch, fake_chain) -> None:
    recorded, _ = fake_chain
    _outcomes(monkeypatch, {"capture": ("failed", 2, {})})
    assert daily.run(report=False) == 1
    status = dict(recorded)
    assert status["league"] == "skipped" and status["news"] == "ok"


def test_exclusion_file_reaches_the_writers(monkeypatch, tmp_path) -> None:
    from src.ops import exclusions

    path = tmp_path / "x.json"
    path.write_text('{"symbols": {"NLG": "unresolved price step"}}')
    monkeypatch.setenv(exclusions.ENV, str(path))
    assert exclusions.excluded_symbols() == {"NLG": "unresolved price step"}
    monkeypatch.setenv(exclusions.ENV, str(tmp_path / "missing.json"))
    assert exclusions.excluded_symbols() == {}


def test_health_expects_the_last_rule_session(monkeypatch) -> None:
    saturday_noon = datetime(2026, 10, 3, 12, tzinfo=NPT)
    assert health.expected_latest_session(saturday_noon, date(2026, 10, 2)) == date(2026, 10, 2)
    monday_morning = datetime(2026, 10, 5, 9, tzinfo=NPT)
    assert health.expected_latest_session(monday_morning, date(2026, 10, 2)) == date(2026, 10, 2)
    monday_night = datetime(2026, 10, 5, 21, tzinfo=NPT)
    assert health.expected_latest_session(monday_night, date(2026, 10, 2)) == date(2026, 10, 5)


def test_reports_render_within_discord_limits() -> None:
    data = {"date": "2026-10-05", "latest_session": date(2026, 10, 5), "price_rows_latest": 300,
            "steps": [("capture", "ok"), ("league", "failed")], "calls": [("bot_momentum", "b2", 10)], "graded_today": 4,
            "leaderboard": [(5, "bot_ranker_spec", 0, None, "INSUFFICIENT SAMPLE")] * 400, "text_items_24h": [("bizmandu", 12)],
            "tips_24h": [], "backup": {"file": None}}
    message, severity = report.render_daily(data)
    assert severity == "failure" and len(message) <= report.LIMIT and "bot_momentum b2: 10" in message


def test_deploy_kit_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("arthasignal-daily.service", "arthasignal-daily.timer", "arthasignal-health.timer", "arthasignal-backup.timer",
                 "arthasignal-weekly.timer", "arthasignal-notify@.service"):
        assert (root / "deploy" / "systemd" / name).exists()
    timer = (root / "deploy" / "systemd" / "arthasignal-daily.timer").read_text()
    assert "Mon..Fri" in timer and "Asia/Kathmandu" in timer
    assert "listen_addresses = 'localhost'" in (root / "deploy" / "config" / "postgresql-arthasignal.conf").read_text()


def test_every_chain_step_has_a_write_free_rehearsal_variant() -> None:
    assert set(daily.REHEARSAL) == {s.name for s in daily.STEPS}
    for name, command in daily.REHEARSAL.items():
        joined = " ".join(command)
        assert "--dry-run" in joined or "--as-of" in joined or "--rehearse" in joined or name == "sectors", name
        assert "{as_of}" in joined or name in ("sectors", "corporate_actions", "quarterly_capture", "news"), name


def test_rehearsal_refuses_holdout_dates() -> None:
    with pytest.raises(SystemExit, match="development-window"):
        daily.rehearse("2025-09-30")


def test_rehearsal_engine_is_read_only_research_role_with_guard() -> None:
    import subprocess
    import sys

    code = (
        "from sqlalchemy import text\n"
        "from src.database.connection import engine\n"
        "with engine.connect() as c:\n"
        "    print(c.execute(text('select current_user')).scalar(), c.execute(text('show default_transaction_read_only')).scalar())\n"
        "try:\n"
        "    with engine.begin() as c: c.execute(text('update companies set sector = sector where false'))\n"
        "    print('write-allowed')\n"
        "except Exception as e: print('write-refused')\n"
    )
    import os

    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "ARTHASIGNAL_REHEARSAL": "1"}, timeout=120)
    if "could not connect" in result.stderr or "Connection refused" in result.stderr:
        pytest.skip("database not reachable")
    assert "arthasignal_research on" in result.stdout and "write-refused" in result.stdout, result.stderr[-500:]


def test_corporate_action_refresh_inserts_only_live_era_rows_and_survives_errors() -> None:
    from src.ops import corporate_actions

    def fetch(symbol):
        if symbol == "BAD":
            raise ConnectionError("down")
        return [
            {"symbol": symbol, "action_date": date(2026, 9, 24), "action_type": "bonus", "ratio_or_amount": 10.0, "fiscal_year": "2025/2026"},
            {"symbol": symbol, "action_date": date(2025, 9, 29), "action_type": "dividend", "ratio_or_amount": 5.0, "fiscal_year": "2024/2025"},
        ]

    inserted, sleeps = [], []
    report = corporate_actions.refresh(["AAA", "BAD", "BBB"], fetch, lambda rows: inserted.extend(rows) or (len(rows), 0), sleep=sleeps.append)
    assert [r["symbol"] for r in inserted] == ["AAA", "BBB"] and all(r["action_date"] >= date(2025, 9, 30) for r in inserted)
    assert report["inserted"] == 2 and report["older_rows_not_inserted"] == 2 and report["errors"] == {"BAD": "ConnectionError"}
    assert len(sleeps) == 2
    dry = corporate_actions.refresh(["AAA"], fetch, None, sleep=sleeps.append)
    assert dry["dry_run"] and dry["inserted"] == 0 and dry["live_rows"] == 1


def test_corporate_actions_are_collected_by_the_chain_and_never_block_it() -> None:
    step = next(s for s in daily.STEPS if s.name == "corporate_actions")
    assert step.gate == "soft" and step.command[-1] == "src.ops.corporate_actions"
    from src.ops.write_check import WRITERS

    assert WRITERS["corporate_actions"] == ("corporate_actions",)
