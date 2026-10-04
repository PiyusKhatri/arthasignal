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


def test_daily_chain_order_matches_the_runbook() -> None:
    assert [s.name for s in daily.STEPS] == ["capture", "quarterly_capture", "news", "integrity", "league", "avoid_writer", "tips",
                                            "grading", "metrics"]
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


def test_no_session_skips_everything_but_news(monkeypatch, fake_chain) -> None:
    recorded, _ = fake_chain
    monkeypatch.setattr(daily, "run_step", lambda step, log_dir: ("no_session", 3, {}) if step.name == "capture" else ("ok", 0, {}))
    assert daily.run(report=False) == 0
    assert dict(recorded) == {"capture": "no_session", "quarterly_capture": "skipped", "news": "ok", "integrity": "skipped", "league": "skipped",
                              "avoid_writer": "skipped", "tips": "skipped", "grading": "skipped", "metrics": "skipped"}


def test_integrity_failure_blocks_writers_but_not_grading_and_alerts(monkeypatch, fake_chain) -> None:
    recorded, alerts = fake_chain
    monkeypatch.setattr(daily, "run_step", lambda step, log_dir: ("failed", 1, {}) if step.name == "integrity" else ("ok", 0, {}))
    assert daily.run(report=False) == 1
    status = dict(recorded)
    assert status["league"] == status["avoid_writer"] == status["tips"] == "skipped"
    assert status["grading"] == status["metrics"] == "ok"
    assert alerts and alerts[0].startswith("integrity:")


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
