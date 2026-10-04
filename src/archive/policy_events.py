from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.archive import schema

SOURCE = Path(__file__).resolve().parents[2] / "docs" / "policy_events.json"


def rows() -> list[dict]:
    data = json.loads(SOURCE.read_text())
    out = []
    for event in data["events"]:
        announcement = data["announcements"].get(event["a"])
        if announcement is None:
            continue
        out.append({"auth": "NRB", "type": event["type"], "measure": event["measure"], "vt": event["text"], "vn": event["value"],
                    "eff": None, "ann": announcement["date"], "url": data["documents"][event["doc"]], "title": f"Monetary policy document {event['doc']}",
                    "ev": f"{event['evidence']} [announcement date: {announcement['basis']}]"})
    for rule in data["trading_rules"]:
        out.append({"auth": rule["authority"], "type": "trading_rule", "measure": rule["measure"], "vt": rule["text"], "vn": None,
                    "eff": rule["effective"], "ann": rule["announced"], "url": rule["sources"][0], "title": rule["measure"],
                    "ev": f"{rule['status']}; sources: {', '.join(rule['sources'])}"})
    return out


def load(engine: Engine) -> int:
    schema.apply(engine)
    added = 0
    with engine.begin() as connection:
        for row in rows():
            added += connection.execute(text(
                "INSERT INTO policy_events (authority, event_type, measure, value_text, value_num, effective_date, announced_date, source_url, source_title, evidence) "
                "VALUES (:auth, :type, :measure, :vt, :vn, :eff, :ann, :url, :title, :ev) ON CONFLICT (authority, measure, announced_date, value_text) DO NOTHING"),
                row).rowcount
    return added


if __name__ == "__main__":
    from src.database.connection import engine

    print("inserted", load(engine))
