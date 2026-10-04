from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

SUFFIXES = re.compile(r"\b(limited|ltd|company|co|pvt|private|public|plc|inc)\b\.?", re.I)
GENERIC_TAIL = re.compile(r"\s+(bittiya sanstha|laghubitta bittiya sanstha)$")
STOP_SYMBOLS = {"API", "CITY", "MEGA", "AXIS", "APEX", "MEN", "NIL", "AIL", "LEC", "BBC", "CIT", "ESC", "GRU", "HEI", "MEL", "NRN", "MERO",
                "HAMA", "NLO", "NCM", "GIC", "IGI", "LSL", "HTL", "KBL", "NBL", "BSL", "CBL", "ECL", "ENL", "NFS", "BNT", "UNL", "SIL", "PLI",
                "NIC", "IPO", "FPO", "NRB", "AGM", "SGM", "EPS", "NPL", "CEO", "GDP", "USD", "NPR", "SEBON", "NEPSE", "CDSC"}
GENERIC = {"bank", "development", "finance", "insurance", "life", "general", "non", "hydropower", "hydro", "power", "energy", "microfinance",
           "laghubitta", "bittiya", "sanstha", "nepal", "investment", "capital", "fund", "mutual", "bikas", "industries", "commercial", "and",
           "of", "the", "national", "limited", "company", "re", "reinsurance", "holdings", "securities", "merchant", "banking", "leasing"}
TOKEN = re.compile(r"\b[A-Z][A-Z0-9]{2,9}\b")


def normalize(name: str) -> str:
    cleaned = SUFFIXES.sub(" ", name.lower())
    cleaned = re.sub(r"[^a-z0-9 ]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def aliases(companies: pd.DataFrame) -> dict[str, str]:
    out: dict[str, str] = {}
    counts: dict[str, int] = {}
    for symbol, name in zip(companies["symbol"], companies["company_name"]):
        if not name or name == symbol:
            continue
        for alias in {normalize(name), GENERIC_TAIL.sub("", normalize(name))}:
            if len(alias) >= 8 and len(alias.split()) >= 2 and any(word not in GENERIC for word in alias.split()):
                counts[alias] = counts.get(alias, 0) + 1
                out[alias] = symbol
    return {a: s for a, s in out.items() if counts[a] == 1}


def mentions(title: str, body: str | None, names: dict[str, str], symbols: set[str], pattern: re.Pattern) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    plain = " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", f"{title} {body or ''}".lower())) + " "
    for match in pattern.finditer(plain):
        found.add((names[match.group(1)], "company_name"))
    for token in TOKEN.findall(f"{title} {body or ''}"):
        if token in symbols and token not in STOP_SYMBOLS:
            found.add((token, "ticker"))
    return found


def run(engine: Engine, batch: int = 2000) -> dict[str, Any]:
    with engine.connect() as connection:
        companies = pd.read_sql(text("SELECT symbol, company_name FROM companies WHERE instrument_type = 'Equity' AND trim(symbol) <> ''"), connection)
    names = aliases(companies)
    symbols = set(companies["symbol"])
    pattern = re.compile(r" (" + "|".join(sorted((re.escape(a) for a in names), key=len, reverse=True)) + r") ")
    added = scanned = 0
    last_id = 0
    while True:
        with engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT a.id, a.title, a.body, a.published_at FROM news_articles a WHERE a.id > :last "
                "AND NOT EXISTS (SELECT 1 FROM news_symbol_mentions m WHERE m.article_id = a.id) ORDER BY a.id LIMIT :n"),
                {"last": last_id, "n": batch}).fetchall()
        if not rows:
            break
        payload = []
        for article_id, title, body, published in rows:
            scanned += 1
            for symbol, method in mentions(title, body, names, symbols, pattern):
                payload.append({"a": article_id, "s": symbol, "m": method, "p": published})
        with engine.begin() as connection:
            for row in payload:
                added += connection.execute(text("INSERT INTO news_symbol_mentions (article_id, symbol, method, published_at) "
                                                 "VALUES (:a, :s, :m, :p) ON CONFLICT DO NOTHING"), row).rowcount
        last_id = rows[-1][0]
    return {"articles_scanned": scanned, "mentions_added": added, "name_aliases": len(names)}


if __name__ == "__main__":
    from src.database.connection import engine

    print(json.dumps(run(engine), indent=1))
