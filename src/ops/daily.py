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


@dataclass(frozen=True)
class Step:
    name: str
    command: tuple[str, ...]
    timeout_minutes: int
    gate: str


STEPS: tuple[Step, ...] = (
    Step("capture", (PY, "-m", "src.ops.capture"), 360, "session"),
    Step("quarterly_capture", (PY, "-m", "src.scrapers.quarterly_capture"), 120, "soft"),
    Step("news", (PY, "-m", "src.collectors.run", "news"), 30, "soft"),
    Step("integrity", (PY, "-m", "src.backtest.price_integrity", "--live"), 30, "clean_data"),
    Step("league", (PY, "-m", "src.league.run", "--write-only", "--markdown-dir", "logs/league"), 45, "writer"),
    Step("avoid_writer", (PY, "-m", "src.scorecard.daily", "--write-only"), 45, "writer"),
    Step("tips", (PY, "-m", "src.tips.run", "cycle"), 30, "writer"),
    Step("grading", (PY, "-m", "src.ops.scoring", "grade"), 60, "soft"),
    Step("metrics", (PY, "-m", "src.ops.scoring", "metrics", "--markdown-dir", "logs/league"), 60, "soft"),
)
NO_SESSION_EXIT = 3
SESSION_INDEPENDENT = {"news"}


def plan(start_from: str | None, only: Sequence[str] | None) -> list[Step]:
    names = [s.name for s in STEPS]
    if start_from and start_from not in names:
        raise SystemExit(f"unknown step {start_from}; steps are {names}")
    steps = list(STEPS[names.index(start_from):]) if start_from else list(STEPS)
    return [s for s in steps if not only or s.name in only]


def run_step(step: Step, log_dir: Path) -> tuple[str, int | None, dict]:
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path, err_path = log_dir / f"{step.name}.json", log_dir / f"{step.name}.log"
    with out_path.open("w") as out, err_path.open("a") as err:
        try:
            result = subprocess.run(step.command, stdout=out, stderr=err, timeout=step.timeout_minutes * 60, env=os.environ.copy())
        except subprocess.TimeoutExpired:
            return "timeout", None, {"stdout": str(out_path), "stderr": str(err_path)}
    detail = {"stdout": str(out_path), "stderr": str(err_path)}
    if step.name == "capture" and result.returncode == NO_SESSION_EXIT:
        return "no_session", result.returncode, detail
    return ("ok" if result.returncode == 0 else "failed"), result.returncode, detail


def run(start_from: str | None = None, only: Sequence[str] | None = None, report: bool = True) -> int:
    from src.database.connection import engine

    runlog.apply_schema(engine)
    today = datetime.now(tz=NPT).date()
    run_key = f"daily-{today.isoformat()}"
    log_dir = Path("logs/daily") / today.isoformat()
    blocked: str | None = None
    failures: list[str] = []
    results = []
    for step in plan(start_from, only):
        started = runlog.utcnow()
        if blocked and step.name not in SESSION_INDEPENDENT and not (blocked == "clean_data" and step.gate != "writer"):
            status, code, detail = "skipped", None, {"reason": f"blocked by {blocked}"}
        else:
            status, code, detail = run_step(step, log_dir)
        runlog.record(engine, JOB, run_key, step.name, status, code, started, detail)
        results.append({"step": step.name, "status": status, "exit_code": code})
        logger.info("step %s: %s (exit %s)", step.name, status, code)
        if status == "no_session":
            blocked = "no_session"
        elif status in ("failed", "timeout"):
            failures.append(step.name)
            if step.gate == "session":
                blocked = "capture_failure"
            elif step.gate == "clean_data":
                blocked = "clean_data"
                runlog.alert_once(engine, f"integrity:{today}", (
                    f"PRICE INTEGRITY FAILED for {today}. League, avoid writer and tips were NOT run.\n"
                    "Inspect logs/daily/{d}/integrity.json, resolve or quarantine the step, then rerun before the next session open:\n"
                    "sudo systemctl start arthasignal-daily-resume.service").replace("{d}", today.isoformat()))
    if report:
        subprocess.run((PY, "-m", "src.ops.report", "daily"), timeout=600)
    print(json.dumps({"run_key": run_key, "steps": results, "failures": failures, "blocked": blocked}, indent=2))
    return 1 if failures else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Post-close daily chain: capture, collect, check, write, grade, score, report")
    parser.add_argument("--from", dest="start_from")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        print("\n".join(f"{i + 1}. {s.name}: {' '.join(s.command[1:])} (timeout {s.timeout_minutes} min, {s.gate})" for i, s in enumerate(STEPS)))
        return
    sys.exit(run(args.start_from, args.only, not args.no_report))


if __name__ == "__main__":
    main()
