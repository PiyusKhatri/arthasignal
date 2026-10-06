from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")
SLASH_DECIMAL = re.compile(r"((?:रु|रू|Rs)\.?\s*)(\d+)\s*/\s*(\d{2})\b")
DOT_GROUPS = re.compile(r"(?<![\d.])(\d{1,3})((?:\.\d{3}){2,})(?![\d.])")
MIXED_GROUPS = re.compile(r"(?<![\d.,])(\d{1,3})\.(\d{3}),(\d{3})(?![\d])")
TOKEN = re.compile(r"(?<![A-Za-z\d])\(?-?\d[\d,]*\.?\d*\)?%?(?![A-Za-z\d])")
FORMULA = re.compile(r"\(?\s*\b[A-H]\s*\.?\s*[-+]\s*\d+(?:\.\s?\d+)?(?:\s*[-+]\s*\d+(?:\.\s?\d+)?)*\s*\)?|\(\s*\d+\.\d+\s*(?:to|[-+])\s*\d+\.\d+\s*\)")
PROSE = re.compile(r"declin|decreas|increas|has\s*been|compared|growth|\bMM\b|\bmillion\b|\bbillion\b|crore|over\s*(the\s*)?(same|previous)|ended\s*on", re.I)
YEAR = re.compile(r"^(19|20)\d{2}$")
NOTE_REF = re.compile(r"\([A-Za-z.\s/'%-]*\)|\(\s*[A-Z]\.?[-\d.+\s]*\)|\bnote\s*\d+(\.\d+)?\b|\b\d\.\d{1,2}\s*(?=[A-Za-zऀ-ॿ(])", re.I)
SERIAL = re.compile(r"^\s*(?:[A-Za-z]\.?|\d{1,2}(?:\.\d{1,2}){0,2}\.?|[ivx]+\.|\(?[a-z]\))\s+(?=\D)", re.I)

UNITS: tuple[tuple[re.Pattern, float, str], ...] = (
    (re.compile(r"in\s*crore|करोडमा|रु\.?\s*करोड", re.I), 1e7, "crore"),
    (re.compile(r"दश\s*लाख|in\s*millions?|in\s*mn\b", re.I), 1e6, "millions"),
    (re.compile(r"in\s*lakhs?|in\s*lacs?|(?<!दश )(?<!दश)लाखमा|रु\.?\s*लाख", re.I), 1e5, "lakhs"),
    (re.compile(r"in\s*'?\s*0{3}'?|in\s*thousands?|\(\s*'?0{3}'?\s*\)|'0{3}|NPR\s*'?0{3}|rs\.?\s*'?0{3}|हजारमा|रु\.?\s*हजार|thousands?\b", re.I), 1e3, "thousands"),
    (re.compile(r"amount\s*in\s*(npr|nrs|rs|rupees)\b|in\s*(npr|nrs|rupees)\b|\bNRs\.?\s*$|रकम\s*रु\.?\s*मा\b|रकम\s*रु\.?\s*$", re.I | re.M), 1.0, "rupees"),
)
HEADER_LINES = 40

GROUP_WORD = re.compile(r"\bgroup\b|consolidated|समूह", re.I)
SOLO_WORD = re.compile(r"\bbank\b|\bcompany\b|standalone|separate|\bparent\b|बैंक|कम्पनी", re.I)
YTD_WORD = re.compile(r"up\s*-?\s*to\s*(the\s*)?(this|current|end\s*of)?\s*(quarter|period)|upto\s*(this\s*)?(quarter|period)?|year\s*to\s*date|\bYTD\b|सम्मको|सम्म", re.I)
QUARTER_ONLY_WORD = re.compile(r"(?<!up\s)(?<!upto\s)(?<!up to\s)this\s*quarter(?!\s*end)|during\s*the\s*quarter|यस\s*त्रैमासिकको(?!\s*अन्त्य)", re.I)
ANNUALIZED_WORD = re.compile(r"annuali[sz]ed|वार्षिक", re.I)


@dataclass(frozen=True)
class Rule:
    include: tuple[re.Pattern, ...]
    exclude: re.Pattern | None = None
    percent: bool = False
    amount: bool = True
    flow: bool = False


def _p(*patterns: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.I) for p in patterns)


RULES: dict[str, Rule] = {
    "net_profit": Rule(
        _p(r"net\s*profit\s*/?\s*\(?\s*loss\s*\)?\s*(for|after)", r"profit\s*/?\s*\(?\s*loss\s*\)?\s*for\s*the\s*(period|year|quarter)",
           r"net\s*profit\s*/?\s*\(?\s*loss\s*\)?", r"profit\s*for\s*the\s*(period|year|quarter)", r"profit\s*after\s*tax", r"net\s*\(?\s*loss\s*\)?\s*/?\s*profit",
           r"net\s*profit", r"खुद\s*(नाफा|मुनाफा)", r"नाफा\s*/?\s*\(?\s*नोक्सान\s*\)?\s*$"),
        re.compile(r"before|operating|gross|distributable|transferred|carried|attributable\s*to\s*non|non[\s-]*controlling|minority|margin|ratio|growth|"
                   r"per\s*share|%|सारेको|बाँडफाँड|अघि|सञ्चालन", re.I),
        flow=True),
    "eps": Rule(_p(r"basic\s*earnings?\s*per\s*share", r"earnings?\s*per\s*share", r"\bEPS\b", r"प्रति\s*शेयर\s*(आम्दानी|आय)"),
                re.compile(r"diluted|growth|price\s*earning", re.I), amount=False),
    "book_value_per_share": Rule(_p(r"net\s*-?\s*worth\s*per\s*share", r"book\s*value\s*per\s*share", r"प्रति\s*शेयर\s*(नेटवर्थ|खुद\s*सम्पत्ति)"),
                                 amount=False),
    "net_worth": Rule(_p(r"total\s*equity(?!\s*(and|&)\s*liabilit)", r"net\s*worth(?!\s*per)", r"total\s*shareholders'?\s*(equity|fund)",
                         r"shareholders'?\s*(equity|fund)", r"शेयरधनी\s*कोष"),
                      re.compile(r"liabilit|per\s*share|attributable\s*to\s*non|non[\s-]*controlling|return\s*on", re.I)),
    "reserves": Rule(_p(r"reserves?\s*(and|&)\s*surplus", r"^\s*reserves?\s*(?=[\d(-])", r"\breserves\b(?!\s*fund)", r"जगेडा\s*(तथा|र)\s*कोष", r"जगेडा"),
                     re.compile(r"regulatory|general\s*reserve|exchange|statutory|deferred|fluctuation|investment\s*adjustment|catastrophe|"
                                r"insurance\s*fund|nprs?\s*in|in\s*thousand", re.I)),
    "paid_up_capital": Rule(_p(r"paid[\s-]*up\s*(share\s*)?capital", r"equity\s*share\s*capital", r"share\s*capital", r"चुक्ता\s*पूँजी"),
                            re.compile(r"authori[sz]ed|to\s*be\s*issued|advance|calls?\s*in|return\s*on|ratio", re.I)),
    "npl_ratio": Rule(_p(r"non\s*-?\s*performing\s*loans?\s*\(?npl\)?\s*to\s*total\s*loans?", r"\bNPL\b\s*(ratio|to\s*total)", r"non\s*-?\s*performing\s*loans?.{0,25}total\s*loan",
                         r"निष्क्रिय\s*कर्जा.{0,20}कुल\s*कर्जा"),
                      re.compile(r"provision", re.I), percent=True, amount=False),
    "capital_adequacy": Rule(_p(r"capital\s*fund\s*to\s*(RWA|risk)", r"capital\s*adequacy", r"total\s*capital\s*(fund\s*)?to\s*risk", r"पूँजीकोष"),
                             re.compile(r"core\s*capital|tier\s*1|tier\s*i\b", re.I), percent=True, amount=False),
}


def normalise(raw: str) -> str:
    raw = SLASH_DECIMAL.sub(r"\1\2.\3", raw.translate(DEVANAGARI))
    raw = DOT_GROUPS.sub(lambda m: m.group(1) + m.group(2).replace(".", ","), raw)
    return MIXED_GROUPS.sub(r"\1,\2,\3", raw)


def rows(raw: str) -> list[str]:
    raw = normalise(raw)
    if raw.count("\n\n") > raw.count("\n") / 4:
        return [" ".join(part.split()) for part in re.split(r"\n\s*\n", raw) if part.strip()]
    return [" ".join(line.split()) for line in raw.splitlines() if line.strip()]


def to_number(token: str) -> float | None:
    token = token.rstrip("%")
    negative = (token.startswith("(") and token.endswith(")")) or token.startswith("-")
    cleaned = token.strip("()-").replace(",", "")
    if not cleaned or cleaned.count(".") > 1 or not re.fullmatch(r"\d+(\.\d+)?", cleaned):
        return None
    value = float(cleaned)
    return -value if negative else value


def numbers(text: str) -> list[tuple[float, str]]:
    out = []
    for token in TOKEN.findall(text):
        value = to_number(token)
        if value is not None:
            out.append((value, token))
    return out


LARGER = re.compile(r"हजार|लाख|करोड|'0{3}|thousand|lakh|million|crore", re.I)
PLAUSIBLE = {"net_profit": (1e4, 5e10), "net_worth": (1e6, 3e11), "reserves": (1e3, 3e11), "paid_up_capital": (1e7, 1e11)}


def line_unit(line: str) -> tuple[float, str] | None:
    for pattern, multiplier, name in UNITS:
        if pattern.search(line) and not (name == "rupees" and LARGER.search(line)):
            return multiplier, name
    return None


def header_unit(raw: str) -> tuple[float | None, str | None]:
    for line in normalise(raw).splitlines()[:HEADER_LINES]:
        found = line_unit(line)
        if found:
            return found
    return None, None


def unit_above(all_rows: list[str], index: int) -> tuple[float | None, str | None]:
    for i in range(index, -1, -1):
        found = line_unit(all_rows[i])
        if found:
            return found
    return None, None


def plausible_scale(field_name: str, printed: float, unit: float | None) -> float:
    low, high = PLAUSIBLE.get(field_name, (0.0, float("inf")))
    for candidate in ([unit] if unit else []) + [1.0, 1e3]:
        if low <= abs(printed) * candidate <= high or printed == 0:
            return candidate
    return unit or 1.0


@dataclass
class Layout:
    blocks: list[str] = field(default_factory=lambda: ["single"])
    periods: list[str] = field(default_factory=lambda: ["current"])
    annualized: bool = False


def layout_near(all_rows: list[str], index: int, span: int = 14) -> Layout:
    header = " | ".join(all_rows[max(0, index - span): index])
    layout = Layout()
    group, solo = GROUP_WORD.search(header), SOLO_WORD.search(header)
    if group and solo and len(GROUP_WORD.findall(header)) >= 1:
        layout.blocks = ["group", "standalone"] if group.start() < solo.start() else ["standalone", "group"]
    ytd = YTD_WORD.search(header)
    quarter = QUARTER_ONLY_WORD.search(header)
    if ytd and quarter:
        layout.periods = ["quarter", "ytd"] if quarter.start() < ytd.start() else ["ytd", "quarter"]
    layout.annualized = bool(ANNUALIZED_WORD.search(header) or ANNUALIZED_WORD.search(all_rows[index]))
    return layout


def tail_values(row: str, match_end: int, rule: Rule) -> list[float]:
    tail = NOTE_REF.sub(" ", FORMULA.sub(" ", row[match_end:]))
    out = []
    for value, token in numbers(tail):
        if rule.amount and (token.endswith("%") or YEAR.match(token) or abs(value) < 100):
            continue
        if not rule.amount and not rule.percent and token.endswith("%"):
            continue
        out.append(value)
    return out


def find_row(all_rows: list[str], rule: Rule) -> tuple[int, int] | None:
    for pattern in rule.include:
        for i, row in enumerate(all_rows):
            body = SERIAL.sub("", row)
            match = pattern.search(body)
            if not match:
                continue
            head = body[: match.end() + 12]
            if rule.exclude is not None and rule.exclude.search(head):
                continue
            if rule.amount and PROSE.search(body[: match.end() + 60]):
                continue
            offset = len(row) - len(body)
            if not tail_values(row, offset + match.end(), rule):
                continue
            return i, offset + match.end()
    return None


def pick(values: list[float], layout: Layout, basis: str, period: str, flow: bool = False) -> float | None:
    if not values:
        return None
    if flow and len(layout.blocks) == 1 and len(values) == 8:
        layout = Layout(blocks=["group", "standalone"], periods=layout.periods)
    blocks = len(layout.blocks)
    if blocks > 1 and len(values) >= blocks and len(values) % blocks == 0:
        size = len(values) // blocks
        want = basis if basis in layout.blocks else "standalone"
        start = layout.blocks.index(want) * size
        values = values[start: start + size]
    if len(layout.periods) > 1 and len(values) >= 2:
        want = period if period in layout.periods else "ytd"
        position = layout.periods.index(want)
        if len(values) >= 4 and len(values) % 2 == 0:
            return values[position]
        return values[position] if position < len(values) else values[0]
    if flow and len(values) == 4 and period == "ytd":
        return values[1]
    return values[0]


def read_field(raw: str, field_name: str, basis: str = "standalone", period: str = "ytd", annualized: str = "") -> dict[str, Any] | None:
    rule = RULES[field_name]
    all_rows = rows(raw)
    found = find_row(all_rows, rule)
    if found is None:
        return None
    index, end = found
    layout = layout_near(all_rows, index)
    values = tail_values(all_rows[index], end, rule)
    if rule.flow:
        value = pick(values, layout, basis, period, flow=True)
    else:
        value = pick(values, Layout(blocks=layout.blocks), basis, period)
    if value is None:
        return None
    unit, unit_name = unit_above(all_rows, index)
    if unit is None:
        unit, unit_name = header_unit(raw)
    if rule.amount:
        value = value * plausible_scale(field_name, value, unit)
    return {"value": value, "row": all_rows[index][:160], "blocks": layout.blocks, "periods": layout.periods,
            "unit": unit_name, "row_index": index}
