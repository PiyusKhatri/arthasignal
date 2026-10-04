from __future__ import annotations

import re
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection

PROMOTER = "Promoter Shares"
EQUITY = "Equity"
NON_EQUITY_SECTOR = {"Mutual Funds": "Mutual Fund", "Non-Convertible Debentures": "Debenture", "Promoter Shares": "Promoter Share",
                     "Preference Shares": "Preference Share"}
PROMOTER_NAME = re.compile(r"promot", re.I)
SUFFIXES = ("PO", "P")


def promoter_base(symbol: str, known_symbols: Iterable[str]) -> str | None:
    known = set(known_symbols)
    for suffix in SUFFIXES:
        if symbol.endswith(suffix) and len(symbol) > len(suffix) + 1:
            base = symbol[: -len(suffix)]
            if base in known and base != symbol:
                return base
    return None


def is_promoter(symbol: str, name: str | None, known_symbols: Iterable[str]) -> bool:
    return bool(PROMOTER_NAME.search(name or "")) or promoter_base(symbol, known_symbols) is not None


def classify(symbol: str, name: str | None, reported_type: str | None, known_symbols: Iterable[str]) -> str:
    if is_promoter(symbol, name, known_symbols):
        return PROMOTER
    return reported_type or "Equity"


def equity_sectors() -> frozenset[str]:
    from src.pipeline.sector_index_mapping import SECTOR_TO_INDEX

    return frozenset(SECTOR_TO_INDEX)


def normalize_sector(instrument_type: str | None, sector: str | None) -> str | None:
    if instrument_type == EQUITY or sector not in equity_sectors():
        return sector
    return NON_EQUITY_SECTOR.get(instrument_type or "", "Non-Equity")


def nonequity_with_equity_sector(connection: Connection) -> list[dict]:
    rows = connection.execute(
        text("SELECT symbol, company_name, instrument_type, sector, status FROM companies "
             "WHERE instrument_type <> :e AND sector = ANY(:s) ORDER BY instrument_type, symbol"),
        {"e": EQUITY, "s": sorted(equity_sectors())},
    ).mappings().all()
    return [dict(r) for r in rows]


def equity_sector(instrument_type: str | None, sector: str | None) -> str | None:
    return sector if instrument_type == EQUITY else None


def known_symbols(connection: Connection) -> set[str]:
    return {r[0] for r in connection.execute(text("SELECT symbol FROM companies"))}
