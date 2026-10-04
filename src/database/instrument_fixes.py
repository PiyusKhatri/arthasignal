from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = ROOT / "docs" / "instrument_fixes.json"
FLOORSHEET_ROOT = Path("~/Desktop/arthasignal-ai/raw/floorsheet").expanduser()
NRB_DIR = Path("~/Desktop/arthasignal-ai/raw/nrb").expanduser()
NRB_SOURCES = {
    "NRB_Class-wise_Merger-and-Acquisition-List.pdf": "https://www.nrb.org.np/contents/uploads/2026/09/NRB_Class-wise_Merger-and-Acquisition-List.pdf",
    "List-of-BFIs-Chait-2074_Eng.pdf": "https://www.nrb.org.np/contents/uploads/2019/12/List-of-BFIs-Chait-2074_Eng.pdf",
}
NRB_CLASS_SECTOR = {"A": "Commercial Banks", "B": "Development Banks", "C": "Finance", "D": "Microfinance"}
DEBENTURE_TYPE = "Non-Convertible Debentures"
DEBENTURE_SECTOR = "Debenture"
DEBENTURE_RELABELS = ("ADBLB", "ADBLB86", "ADBLB87")
TRUNCATED = {"NICAD 85/8": "NICAD85/86", "NIFRAUR85/": "NIFRAUR85/86"}
SECTOR_CANDIDATES = {
    "BOK": ("Bank of Kathmandu", "NRB_Class-wise_Merger-and-Acquisition-List.pdf", r"Bank of Kathmandu Ltd\.\s*\((A)\)"),
    "DIYALO": ("Diyalo Bikas Bank", "NRB_Class-wise_Merger-and-Acquisition-List.pdf", r"Diyalo Bikas Bank Ltd\.\s*\((B)\)"),
    "KMBL": ("Kamana Bikas Bank", "NRB_Class-wise_Merger-and-Acquisition-List.pdf", r"Kamana Bikash Bank Ltd\.\s*\((B)\)"),
    "UMB": ("Unnati Laghubitta Bittiya Sanstha", "List-of-BFIs-Chait-2074_Eng.pdf", r"Unnati Microfinance Bittiya Sanstha Ltd\."),
}
CLASS_HEADER = re.compile(r'Class:\s*"([ABCD])"')
BLANK_DATE_WITHOUT_FLOORSHEET = date(2015, 5, 25)


def _pdf_text(name: str) -> str:
    import pypdf

    return "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(NRB_DIR / name).pages)


def _research(sql: str, **params: Any) -> pd.DataFrame:
    from src.database.holdout_guard import research_engine

    with research_engine().connect() as connection:
        return pd.read_sql(text(sql), connection, params=params)


def _floorsheet_day(day: date) -> pd.DataFrame:
    path = FLOORSHEET_ROOT / f"year={day.year}" / f"month={day.month:02d}" / f"day={day.day:02d}.parquet"
    if not path.exists():
        return pd.DataFrame()
    return duckdb.sql(
        "SELECT symbol, any_value(security_name) AS name, sum(contract_quantity) AS quantity, sum(contract_amount) AS amount, "
        f"arg_max(contract_rate, CAST(transaction_no AS HUGEINT)) AS last_rate FROM read_parquet('{path.as_posix()}') GROUP BY symbol"
    ).df()


def blank_symbol_rows() -> list[dict[str, Any]]:
    rows = _research("SELECT date, open, high, low, close, volume, turnover FROM daily_prices WHERE symbol = '' ORDER BY date")
    out = []
    for r in rows.itertuples():
        day = r.date
        record: dict[str, Any] = {"date": day.isoformat(), "close": float(r.close), "volume": int(r.volume), "turnover": float(r.turnover)}
        sheet = _floorsheet_day(day)
        if not sheet.empty:
            match = sheet[(sheet.quantity == r.volume) & ((sheet.amount - float(r.turnover)).abs() < 1) & (sheet.last_rate == float(r.close))]
            if len(match) == 1:
                record.update(symbol=match.iloc[0].symbol, method="floorsheet: same volume, turnover and last trade", floorsheet_name=match.iloc[0]["name"])
            else:
                record.update(symbol=None, method=f"floorsheet: {len(match)} matches")
        else:
            record.update(symbol=None, method="no floorsheet file")
        out.append(record)
    return out


def sharesansar_blank_rows(day: date) -> list[dict[str, Any]]:
    from src.scrapers import sharesansar_scraper

    http, token = sharesansar_scraper.open_price_session()
    time.sleep(3)
    as_of, rows = sharesansar_scraper.get_session_prices(day, http=http, token=token)
    if as_of != day:
        raise RuntimeError(f"sharesansar returned {as_of} for {day}")
    blanks = [r for r in rows if not str(r.get("symbol") or "").strip()]
    previous = _research(
        "SELECT symbol, close FROM daily_prices WHERE date = (SELECT max(date) FROM daily_prices WHERE date < :d AND symbol <> '') AND symbol <> ''",
        d=day,
    )
    present = set(_research("SELECT symbol FROM daily_prices WHERE date = :d", d=day).symbol)
    out = []
    for r in blanks:
        candidates = previous[(previous.close.astype(float) == float(r["prev_close"])) & (~previous.symbol.isin(present))]
        out.append({
            "date": day.isoformat(),
            "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"]),
            "volume": int(r["volume"]), "turnover": float(r["turnover"]), "prev_close": float(r["prev_close"]),
            "symbol": candidates.iloc[0].symbol if len(candidates) == 1 else None,
            "method": f"sharesansar blank row; previous close equals exactly one symbol's previous close with no row that day ({len(candidates)} candidates)",
        })
    return out


def truncated_symbols() -> list[dict[str, Any]]:
    out = []
    for short, full in TRUNCATED.items():
        rows = _research("SELECT date, close, volume FROM daily_prices WHERE symbol = :s ORDER BY date", s=short)
        clash = _research("SELECT count(*) AS n FROM daily_prices WHERE symbol = :f AND date = ANY(:d)", f=full, d=list(rows.date)).n.iloc[0]
        checked = agree = 0
        name = None
        for r in rows.itertuples():
            sheet = _floorsheet_day(r.date)
            if sheet.empty:
                continue
            hit = sheet[sheet.symbol == full]
            checked += 1
            if len(hit) == 1 and hit.iloc[0].quantity == r.volume and hit.iloc[0].last_rate == float(r.close):
                agree += 1
                name = hit.iloc[0]["name"]
        out.append({"short": short, "full": full, "price_rows": int(len(rows)), "rows_already_under_full": int(clash),
                    "floorsheet_days_checked": checked, "floorsheet_days_agree": agree, "floorsheet_name": name})
    return out


def debenture_relabels() -> list[dict[str, Any]]:
    src = _research("SELECT symbol, source, url, http_status, sector_raw FROM company_sector_sources WHERE symbol = ANY(:s) ORDER BY symbol, source", s=list(DEBENTURE_RELABELS))
    out = []
    for symbol in DEBENTURE_RELABELS:
        part = src[src.symbol == symbol]
        out.append({"symbol": symbol, "instrument_type": DEBENTURE_TYPE, "sector": DEBENTURE_SECTOR,
                    "evidence": [{"source": r.source, "url": r.url, "http": int(r.http_status), "label": r.sector_raw} for r in part.itertuples()]})
    return out


def _class_at(textual: str, start: int) -> str | None:
    headers = [(m.start(), m.group(1)) for m in CLASS_HEADER.finditer(textual) if m.start() < start]
    return headers[-1][1] if headers else None


def sector_assignments() -> list[dict[str, Any]]:
    texts = {name: _pdf_text(name) for name in NRB_SOURCES}
    titles = _research("SELECT symbol, page_title, url FROM company_sector_sources WHERE source = 'sharesansar' AND symbol = ANY(:s)", s=list(SECTOR_CANDIDATES))
    out = []
    for symbol, (name, pdf, pattern) in SECTOR_CANDIDATES.items():
        textual = texts[pdf]
        found = re.search(pattern, textual)
        nrb_class = None
        if found:
            nrb_class = found.group(1) if found.groups() else _class_at(textual, found.start())
        title = titles[titles.symbol == symbol]
        page_title = title.iloc[0].page_title if len(title) else None
        name_ok = bool(page_title) and name.lower() in page_title.lower()
        out.append({
            "symbol": symbol,
            "sector": NRB_CLASS_SECTOR.get(nrb_class) if name_ok and nrb_class else None,
            "name_source": {"source": "sharesansar company page title", "url": title.iloc[0].url if len(title) else None, "title": page_title},
            "class_source": {"source": "Nepal Rastra Bank", "url": NRB_SOURCES[pdf], "class": nrb_class,
                             "context": textual[max(0, found.start() - 60): found.end() + 40].replace("\n", " / ") if found else None},
        })
    return out


def build() -> dict[str, Any]:
    blanks = blank_symbol_rows()
    unmatched = [b for b in blanks if b["symbol"] is None]
    recovered = []
    if any(date.fromisoformat(b["date"]) == BLANK_DATE_WITHOUT_FLOORSHEET for b in unmatched):
        recovered = sharesansar_blank_rows(BLANK_DATE_WITHOUT_FLOORSHEET)
        for b in unmatched:
            for r in recovered:
                if r["date"] == b["date"] and r["close"] == b["close"] and r["volume"] == b["volume"]:
                    b.update(symbol=r["symbol"], method=r["method"])
    stored = {(b["date"], b["close"], b["volume"]) for b in blanks}
    lost = [r for r in recovered if (r["date"], r["close"], r["volume"]) not in stored and r["symbol"]]
    return {
        "built_on": date.today().isoformat(),
        "debenture_relabels": debenture_relabels(),
        "truncated_symbols": truncated_symbols(),
        "blank_symbol_prices": blanks,
        "blank_symbol_lost_rows": lost,
        "sector_assignments": sector_assignments(),
        "nrb_sources": NRB_SOURCES,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["evidence"])
    parser.parse_args()
    evidence = build()
    EVIDENCE_PATH.write_text(json.dumps(evidence, indent=1, default=str) + "\n")
    print(json.dumps(evidence, indent=1, default=str))


if __name__ == "__main__":
    main()
