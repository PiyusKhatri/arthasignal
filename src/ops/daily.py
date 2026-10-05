from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from src.ops import runlog
from src.scorecard.calendar import NPT

logger = logging.getLogger(__name__)

PY = sys.executable
JOB = "daily"
NO_SESSION_EXIT = 3
INTEGRITY_FLAGGED = 3
INTEGRITY_FEED_WIDE = 4
EXCLUSIONS_FILE = "exclude_symbols.json"


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...]
    timeout_minutes: int
    gate: str
    why: str


STEPS: tuple[Step, ...] = (
    Step("capture", (PY, "-m", "src.ops.capture"), 360, "session",
         "Today's prices and NEPSE index are the input of every call. Without them no call can be computed, so failure stops every later step except news"),
    Step("integrity", (PY, "-m", "src.backtest.price_integrity", "--live", "--exclusions-out", "{log_dir}/" + EXCLUSIONS_FILE), 30, "integrity",
         "Flags unresolved price steps. Flagged symbols are excluded from today's calls and the other calls are still written; only a feed-wide "
         "problem (10% or more of traded equities flagged) stops the writers. A crash of the checker itself does not stop them"),
    Step("league", (PY, "-m", "src.league.run", "--write-only", "--markdown-dir", "logs/league"), 45, "writer",
         "Paper-bot calls; must be written before the next session opens"),
    Step("avoid_writer", (PY, "-m", "src.scorecard.daily", "--write-only"), 45, "writer",
         "Model v0.1 calls and avoid observations; must be written before the next session opens"),
    Step("tips", (PY, "-m", "src.tips.run", "cycle"), 30, "writer", "Public tips to the ledger; same deadline"),
    Step("sectors", (PY, "-m", "src.pipeline.data_quality", "--sectors"), 10, "soft",
         "Checks non-equity sector labels. Calls use only the Equity panel, so a failure cannot make a call invalid: alert, never block"),
    Step("quarterly_capture", (PY, "-m", "src.scrapers.quarterly_capture"), 120, "soft",
         "Collects data for future features; no call today uses it, so it runs after the writers and never blocks"),
    Step("news", (PY, "-m", "src.collectors.run", "news"), 30, "soft", "Collects text; no call uses it; never blocks"),
    Step("grading", (PY, "-m", "src.ops.scoring", "grade"), 60, "soft", "Grades matured calls; a failure is retried tomorrow and never touches new calls"),
    Step("metrics", (PY, "-m", "src.ops.scoring", "metrics", "--markdown-dir", "logs/league"), 60, "soft", "Leaderboards; never blocks"),
)
SESSION_INDEPENDENT = {"news"}
REHEARSAL: dict[str, tuple[str, ...]] = {
    "capture": (PY, "-m", "src.ops.capture", "--rehearse", "--date", "{as_of}"),
    "integrity": (PY, "-m", "src.backtest.price_integrity", "--live", "--as-of", "{as_of}", "--exclusions-out", "{log_dir}/" + EXCLUSIONS_FILE),
    "league": (PY, "-m", "src.league.run", "--date", "{as_of}", "--dry-run", "--write-only", "--markdown-dir", "{log_dir}"),
    "avoid_writer": (PY, "-m", "src.scorecard.daily", "--date", "{as_of}", "--dry-run", "--write-only"),
    "tips": (PY, "-m", "src.tips.run", "cycle", "--as-of", "{as_of}"),
    "sectors": (PY, "-m", "src.pipeline.data_quality", "--sectors"),
    "quarterly_capture": (PY, "-m", "src.scrapers.quarterly_capture", "--dry-run", "--symbols", "NABIL", "EBL", "--log", "{log_dir}/quarterly.log"),
    "news": (PY, "-m", "src.collectors.run", "news", "--dry-run"),
    "grading": (PY, "-m", "src.ops.scoring", "grade", "--as-of", "{as_of}"),
    "metrics": (PY, "-m", "src.ops.scoring", "metrics", "--as-of", "{as_of}", "--markdown-dir", "{log_dir}"),
}


def plan(start_from: str | None, only: Sequence[str] | None) -> list[Step]:
    names = [s.name for s in STEPS]
    if start_from and start_from not in names:
        raise SystemExit(f"unknown step {start_from}; steps are {names}")
    steps = list(STEPS[names.index(start_from):]) if start_from else list(STEPS)
    return [s for s in steps if not only or s.name in only]


def run_step(step: Step, log_dir: Path, env: dict[str, str], command: tuple[str, ...] | None = None) -> tuple[str, int | None, dict]:
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path, err_path = log_dir / f"{step.name}.json", log_dir / f"{step.name}.log"
    command = tuple(part.replace("{log_dir}", str(log_dir)) for part in (command or step.command))
    with out_path.open("w") as out, err_path.open("a") as err:
        try:
            result = subprocess.run(command, stdout=out, stderr=err, timeout=step.timeout_minutes * 60, env=env)
        except subprocess.TimeoutExpired:
            return "timeout", None, {"stdout": str(out_path), "stderr": str(err_path)}
    detail = {"stdout": str(out_path), "stderr": str(err_path)}
    code = result.returncode
    if step.gate == "session" and code == NO_SESSION_EXIT:
        return "no_session", code, detail
    if step.gate == "integrity" and code == INTEGRITY_FLAGGED:
        return "flagged", code, detail
    return ("ok" if code == 0 else "failed"), code, detail


def decide(step: Step, blocked: str | None) -> str | None:
    if blocked in ("no_session", "capture_failure") and step.name not in SESSION_INDEPENDENT:
        return blocked
    if blocked == "feed_wide_price_steps" and step.gate == "writer":
        return blocked
    return None


def run(start_from: str | None = None, only: Sequence[str] | None = None, report: bool = True) -> int:
    from src.database.connection import engine

    runlog.apply_schema(engine)
    today = datetime.now(tz=NPT).date()
    run_key = f"daily-{today.isoformat()}"
    log_dir = Path("logs/daily") / today.isoformat()
    env = {**os.environ, "ARTHASIGNAL_EXCLUSIONS": str(log_dir / EXCLUSIONS_FILE)}
    blocked: str | None = None
    failures: list[str] = []
    results = []
    for step in plan(start_from, only):
        started = runlog.utcnow()
        reason = decide(step, blocked)
        if reason:
            status, code, detail = "skipped", None, {"reason": f"blocked by {reason}"}
        else:
            status, code, detail = run_step(step, log_dir, env)
        runlog.record(engine, JOB, run_key, step.name, status, code, started, detail)
        results.append({"step": step.name, "status": status, "exit_code": code})
        logger.info("step %s: %s (exit %s)", step.name, status, code)
        if status == "no_session":
            blocked = "no_session"
        elif status == "flagged":
            runlog.alert_once(engine, f"integrity-flagged:{today}", (
                f"PRICE INTEGRITY: unresolved price steps on {today}. The flagged symbols were EXCLUDED from today's calls; the "
                f"other calls were written. Symbols: logs/daily/{today}/{EXCLUSIONS_FILE}. Resolve or quarantine them."), "warning")
        elif status in ("failed", "timeout"):
            failures.append(step.name)
            if step.gate == "session":
                blocked = "capture_failure"
            elif step.gate == "integrity" and code == INTEGRITY_FEED_WIDE:
                blocked = "feed_wide_price_steps"
                runlog.alert_once(engine, f"integrity-feed:{today}", (
                    f"PRICE INTEGRITY: a feed-wide problem on {today} (10% or more of traded equities flagged). No calls were written. "
                    f"Check today's prices, then before the next session opens run: sudo systemctl start arthasignal-daily-resume.service"))
            elif step.gate == "integrity":
                runlog.alert_once(engine, f"integrity-error:{today}", (
                    f"PRICE INTEGRITY CHECK DID NOT RUN on {today} (exit {code}). Calls were still written; the grader excludes any window "
                    f"that spans an unresolved step. See logs/daily/{today}/integrity.log"))
    if report:
        subprocess.run((PY, "-m", "src.ops.report", "daily"), timeout=600, env=env)
    print(json.dumps({"run_key": run_key, "steps": results, "failures": failures, "blocked": blocked}, indent=2))
    return 1 if failures else 0


def summarize(step: str, path: Path) -> object:
    try:
        data = json.loads(path.read_text())
    except Exception:
        lines = [line for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        return lines[-1][:200] if lines else None
    if step == "capture":
        return {k: data.get(k) for k in ("state", "rows", "calendar_expects_session")}
    if step == "integrity":
        return {k: data.get(k) for k in ("decision", "symbols", "traded_equities")}
    if step == "league":
        return {"deadline": data.get("deadline"), "market_state": data.get("market_state"), "write": data.get("write"),
                "calls": {b: e.get("calls") for b, e in (data.get("bots") or {}).items()}}
    if step == "avoid_writer":
        return {"calls": len(data.get("calls") or []), "avoid_observations": len(data.get("avoid_observations") or []), "write": data.get("write")}
    if step == "tips":
        return {"write": data.get("write")}
    if step in ("grading", "metrics"):
        return {k: v for k, v in data.items() if k in ("mode", "league", "model_v0", "tips", "league_rows_written", "tip_rows_written")}
    if step == "news" and isinstance(data, list):
        return {r.get("source"): {"status": r.get("status"), "items": r.get("items"), "inserted": r.get("inserted")} for r in data}
    if step == "quarterly_capture":
        return {k: data.get(k) for k in ("dry_run", "parsed", "no_data", "no_page", "error", "parsed_symbols")}
    return data


def rehearse(as_of: str) -> int:
    from datetime import date as _date

    from src.database.holdout_guard import HOLDOUT_START

    day = _date.fromisoformat(as_of)
    if day >= HOLDOUT_START:
        raise SystemExit(f"--as-of must be a development-window date before {HOLDOUT_START}")
    log_dir = Path("logs/rehearsal") / as_of
    if log_dir.exists():
        for old in log_dir.iterdir():
            if old.is_file():
                old.unlink()
    env = {**os.environ, "ARTHASIGNAL_REHEARSAL": "1", "ARTHASIGNAL_EXCLUSIONS": str(log_dir / EXCLUSIONS_FILE)}
    blocked: str | None = None
    results, would_alert = [], []
    for step in STEPS:
        started = datetime.now(tz=NPT)
        reason = decide(step, blocked)
        if reason:
            status, code = "skipped", None
            summary: object = f"blocked by {reason}"
        else:
            command = tuple(part.replace("{as_of}", as_of) for part in REHEARSAL[step.name])
            status, code, _ = run_step(step, log_dir, env, command)
            summary = summarize(step.name, log_dir / f"{step.name}.json")
        seconds = round((datetime.now(tz=NPT) - started).total_seconds(), 1)
        results.append({"step": step.name, "gate": step.gate, "status": status, "exit_code": code, "seconds": seconds, "summary": summary})
        if status == "no_session":
            blocked = "no_session"
        elif status == "flagged":
            would_alert.append("warning: flagged symbols excluded from calls")
        elif status in ("failed", "timeout"):
            would_alert.append(f"failure: {step.name} ({status}, exit {code})")
            if step.gate == "session":
                blocked = "capture_failure"
            elif step.gate == "integrity" and code == INTEGRITY_FEED_WIDE:
                blocked = "feed_wide_price_steps"
    report_command = (PY, "-m", "src.ops.report", "daily", "--as-of", as_of, "--print-only")
    report = subprocess.run(report_command, capture_output=True, text=True, env=env, timeout=600)
    try:
        report_text = json.loads(report.stdout)["message"]
    except Exception:
        report_text = (report.stderr or report.stdout)[-500:]
    out = {"rehearsal_for": as_of, "database_role": "arthasignal_research, read-only transactions, holdout guard on",
           "writes": "none (database refuses writes in this mode)", "steps": results, "would_alert": would_alert,
           "daily_report_preview": report_text, "logs": str(log_dir)}
    (log_dir / "rehearsal.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))
    return 1 if any(r["status"] in ("failed", "timeout") for r in results) else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Post-close daily chain: capture, check, write calls, then collect, grade, score, report")
    parser.add_argument("--from", dest="start_from")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--rehearse", action="store_true")
    parser.add_argument("--as-of")
    parser.add_argument("--write-check", action="store_true")
    args = parser.parse_args()
    if args.write_check:
        from src.ops import write_check

        result = write_check.run()
        print(json.dumps(result, indent=1, default=str))
        sys.exit(0 if result["ok"] else 1)
    if args.rehearse:
        if not args.as_of:
            raise SystemExit("--rehearse needs --as-of YYYY-MM-DD (a development-window session)")
        sys.exit(rehearse(args.as_of))
    if args.list:
        print("\n".join(f"{i + 1}. {s.name} [{s.gate}, {s.timeout_minutes} min]: {s.why}" for i, s in enumerate(STEPS)))
        return
    sys.exit(run(args.start_from, args.only, not args.no_report))


if __name__ == "__main__":
    main()
