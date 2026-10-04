from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from sqlalchemy import text

from src.database import holdout_guard

OUTPUT = Path(__file__).resolve().parents[2] / "docs" / "ranker_leak_audit_pit.json"


def run() -> dict:
    holdout_guard.guarded_engine = holdout_guard.research_engine
    from src.ranker import train

    started = time.perf_counter()
    with holdout_guard.research_engine().connect() as connection:
        role = connection.execute(text("SELECT current_user")).scalar_one()
    ctx = train.build()
    result = train.audit(ctx)
    reports = ctx["extras"]["reports"]
    report = {
        "role": role,
        "rule": "earliest item-verified source date (src/backtest/knowledge_time.py)",
        "reports": int(len(reports)),
        "reports_known_earlier_than_sharesansar": int(sum(1 for a, p in zip(reports["available_date"], reports["published_date"]) if a < p)),
        "audit": result,
        "seconds": round(time.perf_counter() - started),
    }
    OUTPUT.write_text(json.dumps(report, indent=1, default=str) + "\n")
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    out = run()
    print(json.dumps({k: v for k, v in out.items() if k != "audit"} | {"mismatches": out["audit"]["mismatches"], "by_feature": out["audit"]["by_feature"], "leaky": out["audit"]["leaky"]}, indent=1))
