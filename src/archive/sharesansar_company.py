from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.archive import schema
from src.archive.polite import PoliteClient
from src.scrapers.quarterly_reports_collector import COMPANY_ID_PATTERN, TOKEN_PATTERN, is_quarterly_report_title

logger = logging.getLogger(__name__)

SOURCE = "sharesansar"
COLLECTOR = "sharesansar_company_v1"
COMPANY_URL = "https://www.sharesansar.com/company/{slug}"
ANNOUNCEMENTS_URL = "https://www.sharesansar.com/company-announcements"
AGM_URL = "https://www.sharesansar.com/company-agm"
DIVIDEND_URL = "https://www.sharesansar.com/company-dividend"
PAGE_SIZE = 50
SLUG_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})$")
HREF = re.compile(r"href='([^']+)'")
TAG = re.compile(r"<[^>]+>")
CATEGORIES = (
    ("quarterly_report", None),
    ("agm", re.compile(r"\b(annual general meeting|AGM)\b", re.I)),
    ("sgm", re.compile(r"\b(special general meeting|SGM|extra.?ordinary general meeting|EGM)\b", re.I)),
    ("right_share", re.compile(r"\bright(s)?\b.*\b(share|issue|offering)|\bright share", re.I)),
    ("book_close", re.compile(r"book\s*clos", re.I)),
    ("dividend_bonus", re.compile(r"(propos|declar|approv|endors|recommend|distribut)\w*.{0,80}(dividend|bonus)|(dividend|bonus).{0,40}(propos|declar|approv|endors)", re.I)),
    ("ipo_fpo", re.compile(r"\b(IPO|FPO|initial public|further public|public issue)\b", re.I)),
    ("auction", re.compile(r"auction", re.I)),
    ("merger", re.compile(r"merg|acqui", re.I)),
    ("promoter", re.compile(r"promoter", re.I)),
    ("debenture", re.compile(r"debenture|bond", re.I)),
    ("annual_report", re.compile(r"annual report|annual financial|audited", re.I)),
    ("interest_rate", re.compile(r"interest rate", re.I)),
)
CASH = re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*(?:percent\s*)?cash", re.I)
BONUS = re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*(?:percent\s*)?bonus", re.I)
RATIO = re.compile(r"\b(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\b")


def classify(title: str) -> str:
    if is_quarterly_report_title(title):
        return "quarterly_report"
    for name, pattern in CATEGORIES[1:]:
        if pattern.search(title):
            return name
    return "other"


def parse_title_numbers(title: str) -> dict[str, Any]:
    cash = CASH.search(title)
    bonus = BONUS.search(title)
    ratio = RATIO.search(title)
    return {"cash_pct": float(cash.group(1)) if cash else None, "bonus_pct": float(bonus.group(1)) if bonus else None,
            "ratio": f"{ratio.group(1)}:{ratio.group(2)}" if ratio else None}


def _date(raw: Any) -> date | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(raw or ""))
    return date.fromisoformat(match.group(0)) if match else None


def _clean(raw: Any) -> str:
    return re.sub(r"\s+", " ", TAG.sub(" ", str(raw or ""))).strip()


def announcement_rows(symbol: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        raw = item.get("title") or ""
        title = _clean(raw)
        published = _date(item.get("published_date"))
        if not title or published is None:
            continue
        href = HREF.search(raw)
        slug = item.get("slug") or (href.group(1) if href else title)
        slug_date = SLUG_DATE.search(slug)
        rows.append({"src": SOURCE, "sid": slug, "sym": symbol, "title": title, "url": href.group(1) if href else None,
                     "cat": classify(title), "pd": published, "sd": date.fromisoformat(slug_date.group(1)) if slug_date else None})
    return rows


def agm_rows(symbol: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        agenda = _clean(item.get("agenda"))
        meeting = _date(item.get("meeting_date"))
        bookclose = _date(item.get("bookclose_date"))
        reference = meeting or bookclose
        if reference is None:
            continue
        numbers = parse_title_numbers(agenda)
        fiscal = re.search(r"\b(20\d\d/\d\d(?:\d\d)?)\b", agenda)
        rows.append({"src": SOURCE, "sym": symbol, "type": "agm", "key": f"{_clean(item.get('agm'))}|{reference}",
                     "fy": fiscal.group(1) if fiscal else None, "ev": meeting, "bc": bookclose, "ad": None,
                     "cash": numbers["cash_pct"], "bonus": numbers["bonus_pct"],
                     "details": json.dumps({"agm": _clean(item.get("agm")), "venue": _clean(item.get("venue_time")), "agenda": agenda}),
                     "ref": reference})
    return rows


def dividend_rows(symbol: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        announced = _date(item.get("announcement_date"))
        bookclose = _date(item.get("bookclose_date"))
        distribution = _date(item.get("distribution_date"))
        reference = announced or bookclose or distribution
        if reference is None:
            continue
        rows.append({"src": SOURCE, "sym": symbol, "type": "dividend", "key": f"{item.get('year')}|{bookclose}|{announced}",
                     "fy": item.get("year"), "ev": distribution, "bc": bookclose, "ad": announced,
                     "cash": float(item["cash_dividend"]) if item.get("cash_dividend") not in (None, "") else None,
                     "bonus": float(item["bonus_share"]) if item.get("bonus_share") not in (None, "") else None,
                     "details": json.dumps({k: item.get(k) for k in ("total_dividend", "bonus_listing_date", "status", "bookclose_date")}),
                     "ref": reference})
    return rows


class CompanyCollector:
    def __init__(self, engine: Engine, client: PoliteClient | None = None) -> None:
        self.engine = engine
        self.client = client or PoliteClient()

    def _paged(self, url: str, company: str, headers: dict[str, str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        start = 0
        while True:
            body = self.client.post(url, data={"company": company, "draw": 1, "start": start, "length": PAGE_SIZE}, headers=headers).json()
            batch = body.get("data") or []
            out.extend(batch)
            if len(batch) < PAGE_SIZE:
                return out
            start += PAGE_SIZE

    def collect(self, symbol: str) -> dict[str, Any]:
        page = self.client.get(COMPANY_URL.format(slug=symbol.lower()))
        token = TOKEN_PATTERN.search(page.text)
        company = COMPANY_ID_PATTERN.search(page.text)
        if page.status_code == 404 or not token or not company:
            return {"status": "not_found", "rows": 0, "detail": f"http {page.status_code}"}
        headers = {"X-CSRF-Token": token.group(1), "X-Requested-With": "XMLHttpRequest", "Referer": COMPANY_URL.format(slug=symbol.lower())}
        payloads = {name: self._paged(url, company.group(1), headers) for name, url in
                    (("announcements", ANNOUNCEMENTS_URL), ("agm", AGM_URL), ("dividend", DIVIDEND_URL))}
        schema.store_raw("sharesansar_company", json.dumps({"symbol": symbol, **payloads}, sort_keys=True).encode(), ".json")
        announcements = announcement_rows(symbol, payloads["announcements"])
        records = agm_rows(symbol, payloads["agm"]) + dividend_rows(symbol, payloads["dividend"])
        added = 0
        with self.engine.begin() as connection:
            for row in announcements:
                added += connection.execute(text(
                    "INSERT INTO corporate_announcements (source, source_id, symbol, title, url, category, published_date, slug_date) "
                    "VALUES (:src, :sid, :sym, :title, :url, :cat, :pd, :sd) ON CONFLICT (source, source_id) DO NOTHING"), row).rowcount
            for row in records:
                added += connection.execute(text(
                    "INSERT INTO company_event_records (source, symbol, record_type, record_key, fiscal_year, event_date, bookclose_date, "
                    "announced_date, cash_pct, bonus_pct, details, reference_date) VALUES (:src, :sym, :type, :key, :fy, :ev, :bc, :ad, :cash, :bonus, "
                    "CAST(:details AS jsonb), :ref) ON CONFLICT (source, symbol, record_type, record_key) DO NOTHING"), row).rowcount
        return {"status": "done", "rows": added,
                "detail": f"announcements {len(announcements)}, agm {len(payloads['agm'])}, dividend {len(payloads['dividend'])}"}


def symbols(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT c.symbol FROM companies c WHERE c.instrument_type = 'Equity' AND trim(c.symbol) <> '' "
            "AND NOT EXISTS (SELECT 1 FROM company_sector_sources s WHERE s.symbol = c.symbol AND s.source = 'sharesansar' AND s.http_status = 404) "
            "ORDER BY (c.status = 'A') DESC, c.symbol")).fetchall()
    return [r[0] for r in rows]


def run(engine: Engine, only: list[str] | None = None) -> dict[str, Any]:
    schema.apply(engine)
    done = schema.finished(engine, COLLECTOR)
    todo = [s for s in (only or symbols(engine)) if s not in done]
    collector = CompanyCollector(engine)
    logger.info("%d symbols to collect, %d already done", len(todo), len(done))
    totals = {"done": 0, "not_found": 0, "error": 0, "rows": 0}
    for number, symbol in enumerate(todo, 1):
        try:
            result = collector.collect(symbol)
        except Exception as exc:
            logger.exception("%s failed", symbol)
            schema.mark(engine, COLLECTOR, symbol, "error", 0, str(exc))
            totals["error"] += 1
            continue
        schema.mark(engine, COLLECTOR, symbol, result["status"], result["rows"], result["detail"])
        totals[result["status"]] += 1
        totals["rows"] += result["rows"]
        logger.info("%d/%d %s %s rows=%d %s requests=%d", number, len(todo), symbol, result["status"], result["rows"], result["detail"],
                    collector.client.requests)
    return totals


def main() -> None:
    from src.database.connection import engine

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run(engine, args.symbols), indent=1))


if __name__ == "__main__":
    main()
