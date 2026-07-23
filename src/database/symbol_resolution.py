from __future__ import annotations

from sqlalchemy import select

from src.database.connection import get_session
from src.database.models import SymbolHistory


def resolve_current_symbol(symbol: str) -> str:
    current = symbol
    visited = {current}

    with get_session() as session:
        while True:
            next_symbol = session.execute(
                select(SymbolHistory.new_symbol).where(SymbolHistory.old_symbol == current)
            ).scalar()

            if next_symbol is None or next_symbol in visited:
                break

            current = next_symbol
            visited.add(current)

    return current
