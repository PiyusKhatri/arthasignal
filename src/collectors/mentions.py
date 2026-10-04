from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

AMBIGUOUS = frozenset({
    "AIL", "APEX", "AXIS", "CIT", "CITY", "CIVIC", "CREST", "GRAND", "HATH", "HEI", "JOSHI", "MABEL", "MEL", "MEN", "MERO", "NIB",
    "NIL", "NIMB", "PIC", "PURE", "RURU", "SAIL", "SAMAJ", "SHEL", "SHINE", "SIC", "SIL", "UPPER", "API", "NRN", "SBI", "GDP",
})
TICKER = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,9})(?![A-Za-z0-9])")
TAGGED = re.compile(r"\(\s*([A-Z][A-Z0-9]{1,9})\s*\)|[$#]([A-Z][A-Z0-9]{1,9})\b|NEPSE:\s?([A-Z][A-Z0-9]{1,9})\b")
NAME_SUFFIX = re.compile(r"\b(limited|ltd\.?|co\.?|company|pvt\.?)\s*$", re.I)
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


@dataclass(frozen=True)
class SymbolBook:
    symbols: frozenset[str]
    names: tuple[tuple[str, str], ...]

    def find(self, *texts: str | None) -> list[str]:
        found: set[str] = set()
        for value in texts:
            if not value:
                continue
            tagged = {m for groups in TAGGED.findall(value) for m in groups if m in self.symbols}
            found |= tagged
            for token in TICKER.findall(value):
                if token in self.symbols and token not in AMBIGUOUS:
                    found.add(token)
            lowered = value.lower()
            for name, symbol in self.names:
                if name in lowered:
                    found.add(symbol)
        return sorted(found)


def normalize_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name.replace(",", " ")).strip()
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = NAME_SUFFIX.sub("", cleaned).strip()
    return cleaned.lower()


def build_book(rows: Iterable[tuple[str, str | None]]) -> SymbolBook:
    symbols, names = set(), []
    for symbol, name in rows:
        if not symbol:
            continue
        symbols.add(symbol)
        if name:
            normalized = normalize_name(name)
            if len(normalized.split()) >= 2 and len(normalized) >= 8:
                names.append((normalized, symbol))
    names.sort(key=lambda pair: -len(pair[0]))
    return SymbolBook(frozenset(symbols), tuple(names))


def load_book(engine: Engine) -> SymbolBook:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT symbol, company_name FROM companies WHERE instrument_type = 'Equity' AND status = 'A'")
        ).all()
    return build_book((r[0], r[1]) for r in rows)


def language(*texts: str | None) -> str:
    joined = " ".join(t for t in texts if t)
    if not joined:
        return "und"
    share = len(DEVANAGARI.findall(joined)) / max(1, len(re.sub(r"\s", "", joined)))
    return "ne" if share > 0.3 else "en"
