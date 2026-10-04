from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

from src.scorecard.calendar import NPT

BACKUP_DIR = Path(os.environ.get("ARTHASIGNAL_BACKUP_DIR", "/var/backups/arthasignal"))
LIMIT = 3900


def _rows(connection: Any, sql: str, **params: Any) -> list[Any]:
    return list(connection.execute(text(sql), params).all())


def _table_exists(connection: Any, name: str) -> bool:
    return connection.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{name}"}).scalar() is not None


def latest_backup() -> dict[str, Any]:
    try:
        files = sorted(BACKUP_DIR.glob("arthasignal_*.dump.age")) if BACKUP_DIR.exists() else []
    except PermissionError:
        return {"file": None, "error": f"{BACKUP_DIR} is not readable by this user"}
    if not files:
        return {"file": None}
    newest = files[-1]
    age = datetime.now().timestamp() - newest.stat().st_mtime
    return {"file": newest.name, "age_hours": round(age / 3600, 1), "size_mb": round(newest.stat().st_size / 1e6, 1), "count": len(files)}


def collect_daily(connection: Any, today: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"date": today.isoformat()}
    out["latest_session"] = connection.execute(text("SELECT max(date) FROM daily_prices")).scalar()
    out["price_rows_latest"] = connection.execute(text("SELECT count(*) FROM daily_prices WHERE date = :d"), {"d": out["latest_session"]}).scalar()
    if _table_exists(connection, "ops_runs"):
        out["steps"] = [(r.step, r.status) for r in _rows(connection,
            "SELECT DISTINCT ON (step) step, status, finished_at FROM ops_runs WHERE job = 'daily' AND run_key = :k ORDER BY step, finished_at DESC",
            k=f"daily-{today.isoformat()}")]
    out["calls"] = [(r.strategy, r.model_version, r.n) for r in _rows(connection,
        "SELECT strategy, model_version, count(*) AS n FROM scorecard_calls WHERE mode = 'live' AND signal_date = :d GROUP BY 1, 2 ORDER BY 1",
        d=out["latest_session"])]
    out["graded_today"] = connection.execute(text(
        "SELECT count(*) FROM scorecard_grades g JOIN scorecard_calls c ON c.id = g.call_id WHERE c.mode = 'live' AND g.graded_at >= now() - interval '24 hours'"
    )).scalar()
    if _table_exists(connection, "league_leaderboard"):
        out["leaderboard"] = [(r.horizon, r.bot, r.metrics.get("graded"), r.metrics.get("edge"), r.metrics.get("verdict")) for r in _rows(connection,
            "SELECT horizon, bot, metrics FROM league_leaderboard WHERE as_of = (SELECT max(as_of) FROM league_leaderboard) AND horizon IN (5, 20) "
            "ORDER BY horizon, rank")]
    if _table_exists(connection, "text_items"):
        out["text_items_24h"] = [(r.source, r.n) for r in _rows(connection,
            "SELECT source, count(*) AS n FROM text_items WHERE first_seen_at >= now() - interval '24 hours' GROUP BY 1 ORDER BY 1")]
    if _table_exists(connection, "public_tip_events"):
        out["tips_24h"] = [(r.event, r.n) for r in _rows(connection,
            "SELECT event, count(*) AS n FROM public_tip_events WHERE recorded_at >= now() - interval '24 hours' GROUP BY 1")]
    out["backup"] = latest_backup()
    return out


def collect_weekly(connection: Any, today: Any) -> dict[str, Any]:
    since = today - timedelta(days=7)
    out: dict[str, Any] = {"week_ending": today.isoformat()}
    out["sessions"] = [r[0] for r in _rows(connection, "SELECT DISTINCT date FROM daily_prices WHERE date > :s ORDER BY 1", s=since)]
    out["calls"] = [(r.strategy, r.n) for r in _rows(connection,
        "SELECT strategy, count(*) AS n FROM scorecard_calls WHERE mode = 'live' AND signal_date > :s GROUP BY 1 ORDER BY 1", s=since)]
    out["live_totals"] = [(r.strategy, r.calls, r.graded) for r in _rows(connection,
        "SELECT c.strategy, count(DISTINCT c.id) AS calls, count(g.id) AS graded FROM scorecard_calls c "
        "LEFT JOIN scorecard_grades g ON g.call_id = c.id WHERE c.mode = 'live' GROUP BY 1 ORDER BY 1")]
    if _table_exists(connection, "ops_runs"):
        out["ops_failures"] = [(r.step, r.n) for r in _rows(connection,
            "SELECT step, count(*) AS n FROM ops_runs WHERE status IN ('failed', 'timeout') AND finished_at > now() - interval '7 days' GROUP BY 1")]
    if _table_exists(connection, "text_items"):
        out["text_items_7d"] = [(r.source, r.n) for r in _rows(connection,
            "SELECT source, count(*) AS n FROM text_items WHERE first_seen_at > now() - interval '7 days' GROUP BY 1 ORDER BY 1")]
    if _table_exists(connection, "league_leaderboard"):
        out["leaderboard"] = [(r.horizon, r.bot, r.metrics.get("graded"), r.metrics.get("edge"), r.metrics.get("edge_lower_90"), r.metrics.get("verdict"))
                              for r in _rows(connection,
            "SELECT horizon, bot, metrics FROM league_leaderboard WHERE as_of = (SELECT max(as_of) FROM league_leaderboard) ORDER BY horizon, rank")]
    out["backup"] = latest_backup()
    usage = shutil.disk_usage("/")
    out["disk_used_pct"] = round(100 * usage.used / usage.total, 1)
    return out


def _fmt(value: Any) -> str:
    return "-" if value is None else f"{value:+.3f}" if isinstance(value, float) else str(value)


def render_daily(data: dict[str, Any]) -> tuple[str, str]:
    failed = [s for s, st in data.get("steps", []) if st in ("failed", "timeout")]
    severity = "failure" if failed else "success"
    lines = [f"**Daily report {data['date']}** (latest session {data['latest_session']}, {data['price_rows_latest']} price rows)"]
    if data.get("steps"):
        lines.append("Steps: " + ", ".join(f"{s}={st}" for s, st in data["steps"]))
    lines.append("Live calls on latest session: " + (", ".join(f"{s} {v}: {n}" for s, v, n in data["calls"]) or "none"))
    lines.append(f"Grades written in 24h: {data['graded_today']}")
    if data.get("leaderboard"):
        lines.append("Leaderboard (graded / edge / verdict):")
        lines += [f"  {h}d {b}: {g or 0} / {_fmt(e)} / {v}" for h, b, g, e, v in data["leaderboard"]]
    if data.get("text_items_24h") is not None:
        lines.append("Text items 24h: " + (", ".join(f"{s} {n}" for s, n in data["text_items_24h"]) or "none"))
    if data.get("tips_24h"):
        lines.append("Tip events 24h: " + ", ".join(f"{e} {n}" for e, n in data["tips_24h"]))
    backup = data["backup"]
    lines.append(f"Backup: {backup.get('file') or 'none found'}" + (f", {backup['age_hours']} h old" if backup.get("file") else ""))
    lines.append("No result here is evidence until a cell passes protocol v2.1 on live calls.")
    return "\n".join(lines)[:LIMIT], severity


def render_weekly(data: dict[str, Any]) -> tuple[str, str]:
    severity = "warning" if data.get("ops_failures") or data["disk_used_pct"] > 85 else "success"
    lines = [f"**Weekly report, week ending {data['week_ending']}**", f"Sessions: {len(data['sessions'])} ({', '.join(str(d) for d in data['sessions'])})",
             "Calls this week: " + (", ".join(f"{s} {n}" for s, n in data["calls"]) or "none"),
             "Live totals (calls/grades): " + (", ".join(f"{s} {c}/{g}" for s, c, g in data["live_totals"]) or "none")]
    if data.get("ops_failures") is not None:
        lines.append("Job failures: " + (", ".join(f"{s} {n}" for s, n in data["ops_failures"]) or "none"))
    if data.get("text_items_7d") is not None:
        lines.append("Text items: " + (", ".join(f"{s} {n}" for s, n in data["text_items_7d"]) or "none"))
    if data.get("leaderboard"):
        lines.append("Leaderboard (graded / edge / lower 90 / verdict):")
        lines += [f"  {h}d {b}: {g or 0} / {_fmt(e)} / {_fmt(lo)} / {v}" for h, b, g, e, lo, v in data["leaderboard"]]
    backup = data["backup"]
    lines.append(f"Backups: {backup.get('count', 0)} kept, newest {backup.get('file')}; disk used {data['disk_used_pct']}%")
    return "\n".join(lines)[:LIMIT], severity


def main() -> None:
    parser = argparse.ArgumentParser(description="Send the daily or weekly report to Discord")
    parser.add_argument("kind", choices=("daily", "weekly"))
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args()
    from src.database.connection import engine
    from src.notifications.discord_alert import send_discord_alert

    today = datetime.now(tz=NPT).date()
    with engine.connect() as connection:
        data = collect_daily(connection, today) if args.kind == "daily" else collect_weekly(connection, today)
    message, severity = render_daily(data) if args.kind == "daily" else render_weekly(data)
    sent = False if args.print_only else send_discord_alert(message, severity)
    print(json.dumps({"sent": sent, "severity": severity, "message": message}, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
