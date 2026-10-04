from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from src.collectors.mentions import SymbolBook

PARSER_VERSION = "p1"
MAX_SYMBOLS_PER_SEGMENT = 5
MAX_TARGET_MULTIPLE = 3.0

NEPALI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
NUMBER = r"(?:rs\.?\s*|रु\.?\s*)?(\d{2,6}(?:\.\d+)?)"

BUY = re.compile(r"\b(?:buy|accumulate|long)\b|(?<!न)किन्नुहोस्|(?<!न)किन्नु|(?<!न)किन्ने|खरिद\s*गर्नु", re.I)
SELL = re.compile(r"\b(?:sell|exit|book\s+profits?|short)\b|(?<!न)बेच्नुहोस्|(?<!न)बेच्नु|(?<!न)बेच्ने|बिक्री\s*गर्नु", re.I)
AVOID = re.compile(r"\bavoid\b|\b(?:don'?t|do\s+not|never)\s+buy\b|नकिन्नु|नकिन्ने", re.I)
NEGATED_SELL = re.compile(r"\b(?:don'?t|do\s+not|never)\s+sell\b|नबेच्नु|नबेच्ने|\bhold\b|होल्ड", re.I)
TARGET = re.compile(r"(?:\btarget(?:\s+price)?|\btgt|\btp\d?|\bt\d|लक्ष्य|टार्गेट)\s*[:@\-=]?\s*" + NUMBER, re.I)
STOP = re.compile(r"(?:\bstop\s*-?\s*loss|\bsl\b|स्टप\s*लस|स्टपलस)\s*[:@\-=]?\s*" + NUMBER, re.I)
ENTRY = re.compile(r"(?:@|\bat\b|\bentry\b|\brange\b|\bcmp\b|\bnear\b|\baround\b)\s*:?\s*" + NUMBER + r"(?:\s*(?:-|–|to)\s*(\d{2,6}(?:\.\d+)?))?", re.I)
SEGMENT_SPLIT = re.compile(r"\n+|।|(?<=[!])\s+|(?<=\D)\.\s+")


@dataclass(frozen=True)
class Tip:
    symbol: str
    direction: str
    raw_direction: str
    entry_low: float | None
    entry_high: float | None
    target: float | None
    stop: float | None
    segment_sha256: str


def normalize(text: str) -> str:
    return text.translate(NEPALI_DIGITS)


def _direction(segment: str) -> str | None:
    if "?" in segment:
        return None
    classes = set()
    if AVOID.search(segment):
        classes.add("avoid")
    if BUY.search(AVOID.sub(" ", segment)):
        classes.add("buy")
    if SELL.search(NEGATED_SELL.sub(" ", segment)):
        classes.add("sell")
    if NEGATED_SELL.search(segment) and not classes:
        return None
    return classes.pop() if len(classes) == 1 else None


def _first(pattern: re.Pattern[str], segment: str) -> float | None:
    match = pattern.search(segment)
    return float(match.group(1)) if match else None


def _levels(segment: str, direction: str) -> tuple[float | None, float | None, float | None, float | None]:
    entry = ENTRY.search(segment)
    low = float(entry.group(1)) if entry else None
    high = float(entry.group(2)) if entry and entry.group(2) else low
    if low is not None and high is not None and high < low:
        low, high = high, low
    target, stop = _first(TARGET, segment), _first(STOP, segment)
    if direction == "buy" and low is not None:
        if target is not None and (target <= high or target > MAX_TARGET_MULTIPLE * high):
            target = None
        if stop is not None and stop >= low:
            stop = None
    if direction != "buy":
        target = stop = None
    return low, high, target, stop


def _tips(segment: str, symbols: list[str], book_direction: str | None = None) -> list[Tip]:
    direction = book_direction or _direction(segment)
    if direction is None or not symbols or len(symbols) > MAX_SYMBOLS_PER_SEGMENT:
        return []
    side = "buy" if direction == "buy" else "sell"
    low, high, target, stop = _levels(segment, side) if len(symbols) == 1 else (None, None, None, None)
    digest = hashlib.sha256(segment.strip().encode("utf-8")).hexdigest()
    return [Tip(s, side, direction, low, high, target, stop, digest) for s in symbols]


def extract(text: str | None, book: SymbolBook) -> list[Tip]:
    if not text:
        return []
    text = normalize(text)
    found: dict[tuple[str, str], Tip] = {}
    for block in re.split(r"\n\s*\n", text):
        block_symbols = book.find(block)
        if len(block_symbols) == 1 and "?" not in block:
            direction = _direction(block)
            if direction is not None:
                for tip in _tips(block, block_symbols, direction):
                    found.setdefault((tip.symbol, tip.direction), tip)
                continue
        for segment in SEGMENT_SPLIT.split(block):
            for tip in _tips(segment, book.find(segment)):
                found.setdefault((tip.symbol, tip.direction), tip)
    sides: dict[str, int] = {}
    for symbol, _ in found:
        sides[symbol] = sides.get(symbol, 0) + 1
    return [tip for (symbol, _), tip in sorted(found.items()) if sides[symbol] == 1]
