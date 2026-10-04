from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.collectors import news, social, store
from src.collectors.mentions import load_book

logger = logging.getLogger(__name__)

EXIT_PARTIAL = 1
EXIT_NOT_CONFIGURED = 5
SOCIAL = ("telegram", "youtube", "reddit")


def summarize(items: list[store.Item]) -> dict[str, Any]:
    symbols = Counter(s for item in items for s in item.symbols)
    return {
        "items": len(items),
        "with_symbols": sum(1 for i in items if i.symbols),
        "languages": dict(Counter(i.language for i in items)),
        "precision": dict(Counter(i.published_precision for i in items)),
        "top_symbols": symbols.most_common(10),
        "sample": [{"id": i.source_item_id, "published_at": i.published_at, "title": (i.title or i.body or "")[:90], "symbols": list(i.symbols)}
                   for i in items[:3]],
    }


def telegram_last_ids(engine: Engine, schema: str) -> dict[str, int]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(f"SELECT channel, max(split_part(source_item_id, ':', 2)::bigint) FROM {schema}.text_items WHERE source = 'telegram' GROUP BY channel")
        ).all()
    return {r[0]: int(r[1]) for r in rows}


def run_source(engine: Engine, name: str, collect: Callable[[], tuple[list[store.Item], dict[str, Any]]], dry_run: bool,
               version: str, schema: str) -> dict[str, Any]:
    started = datetime.now(tz=timezone.utc)
    try:
        items, detail = collect()
    except social.NotConfigured as error:
        return {"source": name, "status": "not_configured", "reason": str(error)}
    except Exception as error:
        logger.exception("collector %s failed", name)
        if not dry_run:
            store.record_run(engine, name, started, "error", 0, 0, 1, {"error": repr(error)}, version, schema)
        return {"source": name, "status": "error", "error": repr(error)}
    report = {"source": name, "status": "ok", "detail": detail, **summarize(items)}
    if dry_run:
        report["inserted"] = "dry run: nothing written"
        return report
    inserted = store.insert_items(engine, items, version, schema)
    report["inserted"] = inserted
    store.record_run(engine, name, started, "ok", len(items), inserted, 0, detail, version, schema)
    return report


def run(targets: list[str], dry_run: bool, schema: str = "public") -> list[dict[str, Any]]:
    from src.database.connection import engine

    if not dry_run:
        store.apply_schema(engine, schema)
    book = load_book(engine)
    config = social.load_config()
    reports = []
    for target in targets:
        if target in news.COLLECTORS:
            http = news.Http()
            known = store.known_ids(engine, target, schema) if not dry_run else set()
            collector = news.COLLECTORS[target](http, book, known)
            reports.append(run_source(engine, target, collector.collect, dry_run, news.VERSION, schema))
        elif target == "telegram":
            last = telegram_last_ids(engine, schema) if not dry_run else {}
            reports.append(run_source(engine, "telegram", lambda: social.collect_telegram(book, last, config), dry_run, social.VERSION, schema))
        elif target == "youtube":
            reports.append(run_source(engine, "youtube", lambda: social.collect_youtube(book, config), dry_run, social.VERSION, schema))
        elif target == "reddit":
            reports.append(run_source(engine, "reddit", lambda: social.collect_reddit(book, config), dry_run, social.VERSION, schema))
        elif target == "purge":
            purged = 0 if dry_run else store.purge_expired(engine, schema)
            reports.append({"source": "purge", "status": "ok", "ephemeral_rows_deleted": purged})
        else:
            raise SystemExit(f"unknown target {target}")
    return reports


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Collect timestamped text from news portals and official social APIs into the append-only store")
    parser.add_argument("targets", nargs="+", help=f"news, social, purge, or any of: {', '.join([*news.COLLECTORS, *SOCIAL])}")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--schema", default="public")
    args = parser.parse_args()
    targets: list[str] = []
    for target in args.targets:
        targets += list(news.COLLECTORS) if target == "news" else list(SOCIAL) if target == "social" else [target]
    reports = run(targets, args.dry_run, args.schema)
    print(json.dumps(reports, indent=2, default=str, ensure_ascii=False))
    statuses = {r["status"] for r in reports}
    if "error" in statuses:
        sys.exit(EXIT_PARTIAL)
    if statuses == {"not_configured"}:
        sys.exit(EXIT_NOT_CONFIGURED)


if __name__ == "__main__":
    main()
