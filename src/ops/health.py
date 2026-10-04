from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import text

from src.ops import runlog
from src.ops.report import latest_backup
from src.scorecard.calendar import NPT, load

NEWS_SOURCES = ("sharesansar_news", "merolagani_news", "arthasarokar", "bizmandu", "kathmandupost_money")
NEWS_STALE_HOURS = 3
BACKUP_STALE_HOURS = 30
DISK_LIMIT_PCT = 85
SESSION_READY = time(20, 45)
WRITERS = ("league", "avoid_writer")


def expected_latest_session(now: datetime, known_latest: date | None) -> date:
    calendar = load()
    day = now.date() if now.time() >= SESSION_READY else now.date() - timedelta(days=1)
    for _ in range(30):
        if calendar.rule_session(day) or (known_latest is not None and day == known_latest):
            return day
        day -= timedelta(days=1)
    return day


def checks(connection: Any, now: datetime) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    latest = connection.execute(text("SELECT max(date) FROM daily_prices")).scalar()
    expected = expected_latest_session(now, latest)
    if latest is None or latest < expected:
        issues.append((f"stale_prices:{expected}", f"STALE PRICES: latest session in daily_prices is {latest}, expected {expected}. "
                       "If NEPSE was closed that day, add it to config/nepse_calendar.json holidays; otherwise run "
                       f"`python -m src.ops.backfill --since {latest}`."))
    for row in connection.execute(text(
        "SELECT id, step, status, run_key, finished_at FROM ops_runs WHERE status IN ('failed', 'timeout') AND finished_at > now() - interval '26 hours'"
    )):
        issues.append((f"ops_failed:{row.id}", f"JOB FAILED: {row.run_key} step {row.step} ({row.status}) at {row.finished_at:%Y-%m-%d %H:%M}. "
                       f"See logs/daily/{row.run_key.removeprefix('daily-')}/{row.step}.log"))
    if latest == now.date() and now.time() >= time(21, 30):
        done = {r[0] for r in connection.execute(text(
            "SELECT DISTINCT step FROM ops_runs WHERE job = 'daily' AND run_key = :k AND status = 'ok'"), {"k": f"daily-{now.date()}"})}
        missing = [w for w in WRITERS if w not in done]
        if missing:
            deadline = load().next_open(now.date())
            issues.append((f"writers_missing:{now.date()}", f"WRITERS NOT DONE for {now.date()}: {', '.join(missing)}. "
                           f"Calls must be written before {deadline:%Y-%m-%d %H:%M} NPT: sudo systemctl start arthasignal-daily-resume.service"))
    seen = dict(connection.execute(text(
        "SELECT source, max(finished_at) FROM text_collector_runs WHERE status = 'ok' GROUP BY source")).all())
    for source in NEWS_SOURCES:
        last = seen.get(source)
        if last is None or now - last > timedelta(hours=NEWS_STALE_HOURS):
            issues.append((f"news_stale:{source}:{now.date()}", f"NEWS COLLECTOR STALE: {source} last succeeded at {last}"))
    backup = latest_backup()
    if backup.get("file") is None or backup["age_hours"] > BACKUP_STALE_HOURS:
        issues.append((f"backup_stale:{now.date()}", f"BACKUP STALE: newest encrypted backup is {backup.get('file')} "
                       f"({backup.get('age_hours')} h old)"))
    usage = shutil.disk_usage("/")
    pct = 100 * usage.used / usage.total
    if pct > DISK_LIMIT_PCT:
        issues.append((f"disk:{now.date()}", f"DISK {pct:.0f}% used on /"))
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Check jobs and data freshness; alert Discord once per issue")
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args()
    from src.database.connection import engine

    runlog.apply_schema(engine)
    now = datetime.now(tz=NPT)
    with engine.connect() as connection:
        issues = checks(connection, now)
    sent = [] if args.print_only else [fp for fp, message in issues if runlog.alert_once(engine, fp, message)]
    print(json.dumps({"at": now.isoformat(), "issues": [m for _, m in issues], "alerts_sent": sent}, indent=2, default=str))


if __name__ == "__main__":
    main()
