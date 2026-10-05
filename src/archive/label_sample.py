from __future__ import annotations

import argparse
import json
import logging
import random
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.archive import report_documents as rd
from src.archive.polite import PoliteClient

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
LABEL_DIR = ROOT / "docs" / "labels"
POOL = LABEL_DIR / "pool.json"
LABEL_SET = LABEL_DIR / "label_set.json"
YEARS = range(2014, 2026)
POOL_QUARTERLY = 24
POOL_ANNUAL = 6
PER_YEAR = 10
ANNUAL_PER_YEAR = 2
SEED = 20261005
SECTOR_GROUPS = {
    "Commercial Banks": "bank", "Development Banks": "bank", "Finance": "bank", "Microfinance": "microfinance",
    "Life Insurance": "insurance", "Non Life Insurance": "insurance", "Hydro Power": "hydro",
}
DOCUMENT_FILTER = "(d.url LIKE '%/photos/shares/announcement/%' OR d.url LIKE '%/wp-content/%' OR d.url LIKE '%.pdf')"


def candidates(research: Any) -> pd.DataFrame:
    with research.connect() as connection:
        quarterly = pd.read_sql(text(
            "SELECT q.source_id, q.source_url, q.symbol, q.fiscal_year, q.quarter, q.published_date, q.net_profit::float AS headline_net_profit, "
            "coalesce(c.sector, 'unknown') AS sector, 'quarterly' AS kind FROM quarterly_report_announcements q LEFT JOIN companies c ON c.symbol = q.symbol "
            "WHERE q.source = 'sharesansar' AND NOT q.is_correction AND q.published_date >= '2014-01-01' "
            "AND q.source_url LIKE 'https://www.sharesansar.com/announcementdetail/%'"), connection)
        annual = pd.read_sql(text(
            "SELECT a.source_id, a.url AS source_url, a.symbol, e.fiscal_year, NULL::smallint AS quarter, a.published_date, NULL::float AS headline_net_profit, "
            "coalesce(c.sector, 'unknown') AS sector, 'annual' AS kind FROM corporate_announcements a "
            "JOIN announcement_events e ON e.announcement_id = a.id AND e.version = 'events_v2' AND e.event_type = 'annual_report' "
            "LEFT JOIN companies c ON c.symbol = a.symbol WHERE a.published_date >= '2014-01-01' AND a.url IS NOT NULL"), connection)
    frame = pd.concat([quarterly, annual], ignore_index=True)
    frame["year"] = pd.to_datetime(frame["published_date"]).dt.year
    frame["group"] = frame["sector"].map(SECTOR_GROUPS).fillna("other")
    return frame


def draw_pool(frame: pd.DataFrame) -> pd.DataFrame:
    rng = random.Random(SEED)
    picks = []
    for year in YEARS:
        for kind, size in (("quarterly", POOL_QUARTERLY), ("annual", POOL_ANNUAL)):
            part = frame[(frame["year"] == year) & (frame["kind"] == kind)]
            groups = {g: list(rows.index) for g, rows in part.groupby("group")}
            for rows in groups.values():
                rng.shuffle(rows)
            chosen: list[int] = []
            while len(chosen) < size and any(groups.values()):
                for g in sorted(groups):
                    if groups[g] and len(chosen) < size:
                        chosen.append(groups[g].pop())
            picks.extend(chosen)
    return frame.loc[picks].reset_index(drop=True)


def documents(engine: Engine, pool: pd.DataFrame) -> pd.DataFrame:
    client = PoliteClient()
    paths = []
    for row in pool.itertuples():
        found = _stored(engine, row.source_id)
        if not found:
            try:
                rd.collect(engine, client, {"source_url": row.source_url, "source_id": row.source_id, "symbol": row.symbol,
                                            "published_date": row.published_date})
            except Exception as exc:
                logger.warning("%s failed: %s", row.source_url, exc)
            found = _stored(engine, row.source_id)
        paths.append(found[0] if found else (None, None))
        logger.info("%s %s %s %s", row.year, row.kind, row.symbol, "ok" if found else "no document")
    pool = pool.copy()
    pool["sha256"] = [p[0] for p in paths]
    pool["path"] = [p[1] for p in paths]
    return pool[pool["sha256"].notna()].reset_index(drop=True)


def _stored(engine: Engine, source_id: str) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        return [tuple(r) for r in connection.execute(text(
            f"SELECT d.sha256, d.path FROM archive_documents d WHERE d.source = 'sharesansar' AND d.announcement_source_id = :s AND {DOCUMENT_FILTER} ORDER BY d.bytes DESC"),
            {"s": source_id})]


def devanagari_share(path: str) -> float:
    result = subprocess.run(["tesseract", path, "stdout", "-l", "nep+eng", "--psm", "6"], capture_output=True, text=True)
    letters = [ch for ch in result.stdout if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if "ऀ" <= ch <= "ॿ") / len(letters)


def select(pool: pd.DataFrame) -> pd.DataFrame:
    rng = random.Random(SEED + 1)
    picks = []
    for year in YEARS:
        part = pool[pool["year"] == year]
        annual = list(part[part["kind"] == "annual"].index)
        rng.shuffle(annual)
        chosen = annual[:ANNUAL_PER_YEAR]
        quarterly = part[part["kind"] == "quarterly"]
        buckets = {(lang, g): list(rows.index) for (lang, g), rows in quarterly.groupby(["language", "group"])}
        for rows in buckets.values():
            rng.shuffle(rows)
        order = sorted(buckets, key=lambda k: (k[1] != "bank", k))
        while len(chosen) < PER_YEAR and any(buckets.values()):
            for language in ("nepali", "english"):
                for key in order:
                    if key[0] == language and buckets[key] and len(chosen) < PER_YEAR:
                        chosen.append(buckets[key].pop())
                        break
        picks.extend(chosen)
    return pool.loc[picks].reset_index(drop=True)


def main() -> None:
    from src.database.connection import engine
    from src.database.holdout_guard import research_engine

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["pool", "select"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    if args.command == "pool":
        pool = documents(engine, draw_pool(candidates(research_engine())))
        pool["devanagari_share"] = [devanagari_share(p) for p in pool["path"]]
        pool["language"] = ["nepali" if s >= 0.2 else "english" for s in pool["devanagari_share"]]
        POOL.write_text(pool.to_json(orient="records", date_format="iso", indent=1))
        print(pool.groupby(["year", "kind", "language"]).size().to_string())
    else:
        pool = pd.read_json(POOL)
        chosen = select(pool)
        chosen["label_id"] = [f"L{i + 1:03d}" for i in range(len(chosen))]
        LABEL_SET.write_text(chosen.to_json(orient="records", date_format="iso", indent=1))
        print(len(chosen), "reports")
        print(chosen.groupby(["kind", "language"]).size().to_string())
        print(chosen.groupby("group").size().to_string())
        print(chosen.groupby("year").size().to_string())


if __name__ == "__main__":
    main()
