from __future__ import annotations

import re
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection

PROMOTER = "Promoter Shares"
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


def known_symbols(connection: Connection) -> set[str]:
    return {r[0] for r in connection.execute(text("SELECT symbol FROM companies"))}
