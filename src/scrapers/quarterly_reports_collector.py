from __future__ import annotations

import argparse
import json
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

SHARESANSAR = "sharesansar"
MEROLAGANI = "merolagani"
COMPANY_URL = "https://www.sharesansar.com/company/{slug}"
ANNOUNCEMENTS_URL = "https://www.sharesansar.com/company-announcements"
QUARTERLY_URL = "https://www.sharesansar.com/company-quarterly-report"
DIVIDEND_URL = "https://www.sharesansar.com/company-dividend"
MEROLAGANI_LIST_URL = "https://merolagani.com/handlers/webrequesthandler.ashx"
MEROLAGANI_DETAIL_URL = "https://merolagani.com/AnnouncementDetail.aspx?id={id}"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 arthasignal-research"
PAGE_SIZE = 50
DEFAULT_DELAY = 3.0
TIMEOUT = 30
HOLDOUT_START = date(2025, 9, 30)
DEFAULT_LOG = Path("logs/quarterly_collector.log")

TOKEN_PATTERN = re.compile(r'name="_token" content="([^"]+)"')
COMPANY_ID_PATTERN = re.compile(r'id="companyid" style="display: none;">(\d+)</div>')
SECTOR_PATTERN = re.compile(r'id="sector" style="display: none;">([^<]*)</div>')
QUARTER_WORDS = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3, "4th": 4, "fourth": 4}
QUARTER_PATTERN = re.compile(r"\b(1st|2nd|3rd|4th|first|second|third|fourth)\s+quarter", re.I)
FY_PATTERN = re.compile(r"(20\d\d)\s*[/-]\s*(\d{2,4})")
PROFIT_PATTERN = re.compile(
    r"net\s+(profit|loss)\s+of\s+(?:rs\.?|npr)\s*([\d,]+(?:\.\d+)?)\s*(billion|million|crore|lakh|arba|thousand)?", re.I
)
UNITS = {None: 1.0, "thousand": 1e3, "lakh": 1e5, "million": 1e6, "crore": 1e7, "billion": 1e9, "arba": 1e9}
REPORT_TITLE = re.compile(r"quarter", re.I)

DDL = (
    """
    CREATE TABLE IF NOT EXISTS quarterly_report_announcements (
        id BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_url TEXT NOT NULL,
        symbol VARCHAR(20),
        company_name TEXT,
        title TEXT NOT NULL,
        fiscal_year VARCHAR(20),
        quarter SMALLINT CHECK (quarter BETWEEN 1 AND 4),
        is_correction BOOLEAN NOT NULL DEFAULT FALSE,
        net_profit NUMERIC(22,2),
        net_profit_text TEXT,
        published_date DATE NOT NULL,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (source, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quarterly_report_figures (
        id BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        fiscal_year VARCHAR(20) NOT NULL,
        quarter SMALLINT NOT NULL CHECK (quarter BETWEEN 1 AND 4),
        column_label TEXT NOT NULL,
        eps NUMERIC(14,4),
        net_worth_per_share NUMERIC(14,4),
        profit_for_period_thousands NUMERIC(22,2),
        reserves_thousands NUMERIC(22,2),
        retained_earnings_thousands NUMERIC(22,2),
        total_equity_thousands NUMERIC(22,2),
        share_capital_thousands NUMERIC(22,2),
        statement JSONB NOT NULL,
        published_date DATE,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (source, symbol, fiscal_year, quarter)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dividend_declarations (
        id BIGSERIAL PRIMARY KEY,
        source TEXT NOT NULL,
        symbol VARCHAR(20) NOT NULL,
        fiscal_year VARCHAR(20) NOT NULL,
        cash_dividend_pct NUMERIC(10,4),
        bonus_pct NUMERIC(10,4),
        announcement_date DATE NOT NULL,
        bookclose_date DATE,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (source, symbol, fiscal_year, announcement_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quarterly_collector_progress (
        symbol VARCHAR(40) NOT NULL,
        stage TEXT NOT NULL,
        status TEXT NOT NULL,
        rows_inserted INTEGER NOT NULL DEFAULT 0,
        detail TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (symbol, stage)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS merolagani_company_symbols (
        company_name TEXT PRIMARY KEY,
        symbol VARCHAR(20),
        checked_source_id TEXT NOT NULL,
        checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE OR REPLACE VIEW quarterly_report_announcements_resolved AS
    SELECT a.*, COALESCE(a.symbol, m.symbol) AS resolved_symbol
    FROM quarterly_report_announcements a LEFT JOIN merolagani_company_symbols m ON m.company_name = a.company_name
    """,
    """
    CREATE OR REPLACE VIEW quarterly_report_announcements_dev AS
    SELECT * FROM quarterly_report_announcements WHERE published_date < DATE '2025-09-30'
    """,
)


def normalize_fiscal_year(raw: str | None) -> str | None:
    if not raw:
        return None
    match = FY_PATTERN.search(raw)
    if not match:
        return None
    start = int(match.group(1))
    end = match.group(2)
    end_full = int(end) if len(end) == 4 else int(str(start)[:2] + end) if len(end) == 2 else None
    if end_full is None or end_full != start + 1:
        return None
    return f"{start}/{end_full}"


def parse_report_title(title: str) -> dict[str, Any]:
    quarter_match = QUARTER_PATTERN.search(title)
    profit_match = PROFIT_PATTERN.search(title)
    net_profit = None
    if profit_match:
        value = float(profit_match.group(2).replace(",", "")) * UNITS[(profit_match.group(3) or "").lower() or None]
        net_profit = -value if profit_match.group(1).lower() == "loss" else value
    return {
        "quarter": QUARTER_WORDS[quarter_match.group(1).lower()] if quarter_match else None,
        "fiscal_year": normalize_fiscal_year(title[quarter_match.end():] if quarter_match else title),
        "net_profit": net_profit,
        "net_profit_text": profit_match.group(0) if profit_match else None,
        "is_correction": bool(re.search(r"correct|revis|amend", title, re.I)),
    }


def is_quarterly_report_title(title: str) -> bool:
    return bool(QUARTER_PATTERN.search(title)) and bool(
        re.search(r"financial statement|company analysis|quarterly report|net (profit|loss)|unaudited", title, re.I)
    )


def _number(raw: str | None) -> float | None:
    if raw is None:
        return None
    cleaned = raw.replace(",", "").strip()
    if cleaned in ("", "-"):
        return None
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_quarterly_tab(html: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "html.parser")
    sections: dict[str, dict[str, float | None]] = {}
    label = None
    for pane in soup.select("div.tab-pane"):
        rows: dict[str, float | None] = {}
        header = pane.find("th", string=re.compile("Quarter", re.I))
        if header is None:
            continue
        label = header.get_text(strip=True)
        for tr in pane.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if len(cells) >= 2 and cells[0]:
                rows.setdefault(cells[0], _number(cells[1]))
        sections[pane.get("id") or str(len(sections))] = rows
    if label is None:
        return None
    quarter_match = QUARTER_PATTERN.search(label)
    flat = {k.lower(): v for rows in sections.values() for k, v in rows.items()}

    def pick(*patterns: str) -> float | None:
        for pattern in patterns:
            for key, value in flat.items():
                if re.fullmatch(pattern, key) and value is not None:
                    return value
        return None

    return {
        "column_label": label,
        "quarter": QUARTER_WORDS[quarter_match.group(1).lower()] if quarter_match else None,
        "fiscal_year": normalize_fiscal_year(label),
        "eps": pick(r"basic earnings per share.*", r"annualized eps.*", r"earnings per share.*", r"eps.*"),
        "net_worth_per_share": pick(r"net worth per share.*", r"book value per share.*", r"net worth.*per share.*"),
        "profit_for_period_thousands": pick(r"profit for the period", r"net profit.*", r"profit/\(loss\) for the period", r"profit after tax.*"),
        "reserves_thousands": pick(r"reserves", r"reserve and surplus", r"reserves and surplus", r"other reserves?"),
        "retained_earnings_thousands": pick(r"retained earnings.*"),
        "total_equity_thousands": pick(r"total equity", r"total shareholder'?s'? equity.*"),
        "share_capital_thousands": pick(r"share capital", r"paid.?up capital.*"),
        "statement": sections,
    }


class Collector:
    def __init__(self, engine: Engine, delay: float = DEFAULT_DELAY) -> None:
        self.engine = engine
        self.delay = delay
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT})
        self._last = 0.0
        self.requests = 0

    def _wait(self) -> None:
        pause = self.delay - (time.monotonic() - self._last)
        if pause > 0:
            time.sleep(pause)
        self._last = time.monotonic()
        self.requests += 1

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        for attempt in range(3):
            self._wait()
            try:
                response = self.http.get(url, timeout=TIMEOUT, **kwargs)
                if response.status_code < 500:
                    return response
            except requests.RequestException as error:
                logger.warning("GET %s failed (%s), attempt %d", url, type(error).__name__, attempt + 1)
            time.sleep(self.delay * (attempt + 2))
        raise RuntimeError(f"GET {url} failed after retries")

    def post(self, url: str, data: dict[str, Any], headers: dict[str, str]) -> requests.Response:
        for attempt in range(3):
            self._wait()
            try:
                response = self.http.post(url, data=data, headers=headers, timeout=TIMEOUT)
                if response.status_code < 500:
                    return response
            except requests.RequestException as error:
                logger.warning("POST %s failed (%s), attempt %d", url, type(error).__name__, attempt + 1)
            time.sleep(self.delay * (attempt + 2))
        raise RuntimeError(f"POST {url} failed after retries")

    def apply_schema(self) -> None:
        with self.engine.begin() as connection:
            for statement in DDL:
                connection.execute(text(statement))

    def done(self, symbol: str, stage: str) -> bool:
        with self.engine.connect() as connection:
            status = connection.execute(
                text("SELECT status FROM quarterly_collector_progress WHERE symbol = :s AND stage = :g"), {"s": symbol, "g": stage}
            ).scalar()
        return status in ("done", "not_found")

    def mark(self, symbol: str, stage: str, status: str, rows: int, detail: str = "") -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO quarterly_collector_progress (symbol, stage, status, rows_inserted, detail, updated_at) "
                    "VALUES (:s, :g, :st, :r, :d, now()) ON CONFLICT (symbol, stage) DO UPDATE SET status = EXCLUDED.status, "
                    "rows_inserted = quarterly_collector_progress.rows_inserted + EXCLUDED.rows_inserted, detail = EXCLUDED.detail, updated_at = now()"
                ),
                {"s": symbol, "g": stage, "st": status, "r": rows, "d": detail[:2000]},
            )

    def _insert(self, statement: str, rows: Iterable[dict[str, Any]]) -> int:
        inserted = 0
        with self.engine.begin() as connection:
            for row in rows:
                inserted += int(connection.execute(text(statement), row).first() is not None)
        return inserted

    def sharesansar_symbol(self, symbol: str) -> dict[str, Any]:
        page = self.get(COMPANY_URL.format(slug=symbol.lower()))
        token = TOKEN_PATTERN.search(page.text)
        company = COMPANY_ID_PATTERN.search(page.text)
        if page.status_code == 404 or not token or not company:
            return {"status": "not_found", "rows": 0, "detail": f"http {page.status_code}, no company id"}
        sector = SECTOR_PATTERN.search(page.text)
        headers = {"X-CSRF-Token": token.group(1), "X-Requested-With": "XMLHttpRequest", "Referer": COMPANY_URL.format(slug=symbol.lower())}
        announcements: list[dict[str, Any]] = []
        start, total = 0, None
        while total is None or start < total:
            body = self.post(ANNOUNCEMENTS_URL, {"company": company.group(1), "draw": 1, "start": start, "length": PAGE_SIZE}, headers).json()
            total = int(body.get("recordsTotal") or 0)
            batch = body.get("data") or []
            announcements.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            start += PAGE_SIZE
        report_rows = []
        for item in announcements:
            raw_title = item.get("title") or ""
            href = re.search(r"href='([^']+)'", raw_title)
            title = re.sub(r"<[^>]+>", "", raw_title).strip()
            if not is_quarterly_report_title(title) or not item.get("published_date"):
                continue
            parsed = parse_report_title(title)
            report_rows.append(
                {"src": SHARESANSAR, "sid": item.get("slug") or (href.group(1) if href else title), "url": href.group(1) if href else "",
                 "sym": symbol, "name": None, "title": title, "fy": parsed["fiscal_year"], "q": parsed["quarter"],
                 "corr": parsed["is_correction"], "np": parsed["net_profit"], "npt": parsed["net_profit_text"],
                 "pd": date.fromisoformat(item["published_date"][:10])}
            )
        inserted_reports = self._insert(
            "INSERT INTO quarterly_report_announcements (source, source_id, source_url, symbol, company_name, title, fiscal_year, quarter, "
            "is_correction, net_profit, net_profit_text, published_date) VALUES (:src, :sid, :url, :sym, :name, :title, :fy, :q, :corr, :np, :npt, :pd) "
            "ON CONFLICT (source, source_id) DO NOTHING RETURNING id",
            report_rows,
        )
        tab = self.post(QUARTERLY_URL, {"company": company.group(1), "symbol": symbol, "sector": sector.group(1) if sector else ""}, headers)
        figures = parse_quarterly_tab(tab.text)
        inserted_figures = 0
        if figures and figures["fiscal_year"] and figures["quarter"]:
            published = min(
                (r["pd"] for r in report_rows if r["fy"] == figures["fiscal_year"] and r["q"] == figures["quarter"]), default=None
            )
            inserted_figures = self._insert(
                "INSERT INTO quarterly_report_figures (source, symbol, fiscal_year, quarter, column_label, eps, net_worth_per_share, "
                "profit_for_period_thousands, reserves_thousands, retained_earnings_thousands, total_equity_thousands, share_capital_thousands, "
                "statement, published_date) VALUES (:src, :sym, :fy, :q, :label, :eps, :nw, :pp, :res, :ret, :eq, :cap, CAST(:st AS jsonb), :pd) "
                "ON CONFLICT (source, symbol, fiscal_year, quarter) DO NOTHING RETURNING id",
                [{"src": SHARESANSAR, "sym": symbol, "fy": figures["fiscal_year"], "q": figures["quarter"], "label": figures["column_label"],
                  "eps": figures["eps"], "nw": figures["net_worth_per_share"], "pp": figures["profit_for_period_thousands"],
                  "res": figures["reserves_thousands"], "ret": figures["retained_earnings_thousands"], "eq": figures["total_equity_thousands"],
                  "cap": figures["share_capital_thousands"], "st": json.dumps(figures["statement"]), "pd": published}],
            )
        dividends = self.post(DIVIDEND_URL, {"company": company.group(1), "draw": 1, "start": 0, "length": PAGE_SIZE}, headers).json().get("data") or []
        dividend_rows = []
        for row in dividends:
            announced = (row.get("announcement_date") or "")[:10]
            fy = normalize_fiscal_year(row.get("year"))
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", announced) or not fy:
                continue
            book = re.search(r"\d{4}-\d{2}-\d{2}", row.get("bookclose_date") or "")
            dividend_rows.append(
                {"src": SHARESANSAR, "sym": symbol, "fy": fy, "cash": _number(row.get("cash_dividend")), "bonus": _number(row.get("bonus_share")),
                 "ad": date.fromisoformat(announced), "bc": date.fromisoformat(book.group(0)) if book else None}
            )
        inserted_dividends = self._insert(
            "INSERT INTO dividend_declarations (source, symbol, fiscal_year, cash_dividend_pct, bonus_pct, announcement_date, bookclose_date) "
            "VALUES (:src, :sym, :fy, :cash, :bonus, :ad, :bc) ON CONFLICT (source, symbol, fiscal_year, announcement_date) DO NOTHING RETURNING id",
            dividend_rows,
        )
        detail = (
            f"announcements {len(announcements)}, quarterly {len(report_rows)} (+{inserted_reports}), "
            f"figures {figures['column_label'] if figures else 'none'} (+{inserted_figures}), dividends {len(dividend_rows)} (+{inserted_dividends})"
        )
        return {"status": "done", "rows": inserted_reports + inserted_figures + inserted_dividends, "detail": detail}

    def run_sharesansar(self, symbols: list[str]) -> None:
        for k, symbol in enumerate(symbols, 1):
            if self.done(symbol, SHARESANSAR):
                continue
            try:
                result = self.sharesansar_symbol(symbol)
            except Exception as error:
                logger.exception("%s failed", symbol)
                self.mark(symbol, SHARESANSAR, "error", 0, f"{type(error).__name__}: {error}")
                continue
            self.mark(symbol, SHARESANSAR, result["status"], result["rows"], result["detail"])
            logger.info("[%d/%d] %s %s: %s (requests %d)", k, len(symbols), symbol, result["status"], result["detail"], self.requests)

    def run_merolagani(self, max_pages: int = 2000) -> None:
        names = self._company_names()
        page = 1
        while page <= max_pages:
            key = f"page:{page:05d}"
            if self.done(key, MEROLAGANI):
                page += 1
                continue
            response = self.get(
                MEROLAGANI_LIST_URL,
                params={"type": "get_company_reports", "symbol": "", "sectorID": 0, "fiscalYear": "", "pageNumber": page,
                        "pageSize": PAGE_SIZE, "reportType": "QUARTERLY"},
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": "https://merolagani.com/CompanyReports.aspx?type=QUARTERLY"},
            )
            try:
                items = response.json() or []
            except ValueError:
                self.mark(key, MEROLAGANI, "error", 0, response.text[:300])
                logger.error("merolagani page %d: not JSON", page)
                break
            if not items:
                self.mark(key, MEROLAGANI, "done", 0, "empty page: end of list")
                logger.info("merolagani page %d empty: end of list", page)
                break
            rows = []
            for item in items:
                title = (item.get("announcementDetail") or "").strip()
                parsed = parse_report_title(title)
                company = re.split(r"\s+has\s+", title, maxsplit=1)[0].strip()
                rows.append(
                    {"src": MEROLAGANI, "sid": str(item["announcementID"]), "url": MEROLAGANI_DETAIL_URL.format(id=item["announcementID"]),
                     "sym": names.get(_name_key(company)), "name": company, "title": title, "fy": parsed["fiscal_year"], "q": parsed["quarter"],
                     "corr": parsed["is_correction"], "np": parsed["net_profit"], "npt": parsed["net_profit_text"],
                     "pd": date.fromisoformat(item["announcementDateAD"][:10])}
                )
            inserted = self._insert(
                "INSERT INTO quarterly_report_announcements (source, source_id, source_url, symbol, company_name, title, fiscal_year, quarter, "
                "is_correction, net_profit, net_profit_text, published_date) VALUES (:src, :sid, :url, :sym, :name, :title, :fy, :q, :corr, :np, :npt, :pd) "
                "ON CONFLICT (source, source_id) DO NOTHING RETURNING id",
                rows,
            )
            self.mark(key, MEROLAGANI, "done", inserted, f"{len(items)} items, last {items[-1].get('announcementDateAD')}")
            logger.info("merolagani page %d: %d items, %d new, last date %s", page, len(items), inserted, items[-1].get("announcementDateAD"))
            page += 1

    def resolve_merolagani_names(self) -> dict[str, int]:
        with self.engine.connect() as connection:
            pending = connection.execute(
                text(
                    "SELECT a.company_name, min(a.source_id) FROM quarterly_report_announcements a "
                    "LEFT JOIN merolagani_company_symbols m ON m.company_name = a.company_name "
                    "WHERE a.source = :src AND a.symbol IS NULL AND m.company_name IS NULL GROUP BY a.company_name ORDER BY 1"
                ),
                {"src": MEROLAGANI},
            ).all()
        resolved = 0
        for k, (name, source_id) in enumerate(pending, 1):
            try:
                page = self.get(MEROLAGANI_DETAIL_URL.format(id=source_id))
            except RuntimeError as error:
                logger.warning("[%d/%d] merolagani name %r skipped: %s", k, len(pending), name, error)
                continue
            body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page.text))
            match = re.search(r"Symbol\s+([A-Z0-9]{2,20})\s+\(", body)
            symbol = match.group(1) if match else None
            with self.engine.begin() as connection:
                known = symbol and connection.execute(text("SELECT 1 FROM companies WHERE symbol = :s"), {"s": symbol}).first()
                connection.execute(
                    text("INSERT INTO merolagani_company_symbols (company_name, symbol, checked_source_id) VALUES (:n, :s, :i) ON CONFLICT DO NOTHING"),
                    {"n": name, "s": symbol if known else None, "i": source_id},
                )
            resolved += int(bool(known))
            logger.info("[%d/%d] merolagani name %r -> %s%s", k, len(pending), name, symbol, "" if known else " (not in companies)")
        return {"names": len(pending), "resolved": resolved}

    def _company_names(self) -> dict[str, str]:
        with self.engine.connect() as connection:
            rows = connection.execute(text("SELECT symbol, company_name FROM companies WHERE instrument_type = 'Equity'")).all()
        names: dict[str, str] = {}
        for symbol, name in rows:
            key = _name_key(name)
            if key and key not in names:
                names[key] = symbol
        return names


def _name_key(name: str | None) -> str:
    if not name:
        return ""
    cleaned = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("&", " and "))
    cleaned = re.sub(r"\b(limited|ltd|company|co|the|pvt|private|public)\b", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def equity_symbols(engine: Engine) -> list[str]:
    from src.scorecard.spec import valid_symbol

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT symbol, status FROM companies WHERE instrument_type = 'Equity' ORDER BY (status = 'A') DESC, symbol")
        ).all()
    return [s for s, _ in rows if valid_symbol(s)]


def report(engine: Engine) -> dict[str, Any]:
    import pandas as pd

    with engine.connect() as connection:
        ann = pd.read_sql(text("SELECT * FROM quarterly_report_announcements_resolved"), connection)
        ann["symbol"] = ann["resolved_symbol"]
        figures = pd.read_sql(text("SELECT * FROM quarterly_report_figures"), connection)
        dividends = pd.read_sql(text("SELECT * FROM dividend_declarations"), connection)
        companies = pd.read_sql(text("SELECT symbol, status FROM companies WHERE instrument_type = 'Equity'"), connection)
        progress = pd.read_sql(text("SELECT * FROM quarterly_collector_progress"), connection)
        fundamentals = pd.read_sql(
            text("SELECT DISTINCT ON (symbol) symbol, fiscal_year, eps::float AS eps, book_value::float AS book_value, reported_date "
                 "FROM fundamentals ORDER BY symbol, reported_date DESC"),
            connection,
        )
        fundamentals_rows = int(connection.execute(text("SELECT count(*) FROM fundamentals")).scalar_one())
    ann["year"] = pd.to_datetime(ann["published_date"]).dt.year
    from src.scorecard.spec import valid_symbol

    universe = {s for s in companies["symbol"] if valid_symbol(s)}
    status = dict(zip(companies["symbol"], companies["status"]))
    out: dict[str, Any] = {"generated_at": datetime.now().isoformat(timespec="seconds")}
    stages = progress.assign(stage_kind=progress["stage"]).groupby(["stage_kind", "status"]).size()
    out["progress"] = {f"{k[0]}:{k[1]}": int(v) for k, v in stages.items()}
    for source, group in ann.groupby("source"):
        reports = group[~group["is_correction"]]
        per_symbol = reports.dropna(subset=["symbol"]).groupby("symbol").size()
        by_status = {}
        for st in ("A", "D", "S"):
            members = {s for s in universe if status.get(s) == st}
            by_status[st] = {"symbols": len(members), "with_reports": int(len(members & set(per_symbol.index)))}
        out[source] = {
            "rows": int(len(group)),
            "corrections": int(group["is_correction"].sum()),
            "first_published": str(group["published_date"].min()),
            "last_published": str(group["published_date"].max()),
            "rows_before_holdout": int((pd.to_datetime(group["published_date"]) < pd.Timestamp(HOLDOUT_START)).sum()),
            "unmatched_symbol_rows": int(group["symbol"].isna().sum()),
            "unmatched_company_names": int(group.loc[group["symbol"].isna(), "company_name"].nunique()),
            "fiscal_year_parsed": round(float(group["fiscal_year"].notna().mean()), 4),
            "quarter_parsed": round(float(group["quarter"].notna().mean()), 4),
            "net_profit_parsed": round(float(group["net_profit"].notna().mean()), 4),
            "per_year": {int(y): int(n) for y, n in group.groupby("year").size().items()},
            "symbols": int(per_symbol.size),
            "coverage_by_status": by_status,
            "reports_per_symbol": {q: float(per_symbol.quantile(v)) for q, v in (("p10", 0.1), ("median", 0.5), ("p90", 0.9))} if len(per_symbol) else {},
            "first_year_per_symbol": {int(y): int(n) for y, n in reports.dropna(subset=["symbol"]).groupby("symbol")["year"].min().value_counts().sort_index().items()},
        }
    keys = ["symbol", "fiscal_year", "quarter"]
    first = ann[~ann["is_correction"]].dropna(subset=keys).groupby(["source"] + keys)["published_date"].min().reset_index()
    both = first[first["source"] == SHARESANSAR].merge(first[first["source"] == MEROLAGANI], on=keys, suffixes=("_ss", "_ml"))
    if len(both):
        gap = (pd.to_datetime(both["published_date_ss"]) - pd.to_datetime(both["published_date_ml"])).dt.days
        out["cross_source_dates"] = {
            "matched_reports": int(len(both)),
            "same_day": round(float((gap == 0).mean()), 4),
            "within_1_day": round(float((gap.abs() <= 1).mean()), 4),
            "within_7_days": round(float((gap.abs() <= 7).mean()), 4),
            "sharesansar_later_by_more_than_7": int((gap > 7).sum()),
            "merolagani_later_by_more_than_7": int((gap < -7).sum()),
        }
    ss = ann[(ann["source"] == SHARESANSAR) & ann["net_profit"].notna() & ~ann["is_correction"]]
    joined = ss.merge(figures, on=["symbol", "fiscal_year", "quarter"], suffixes=("", "_f"))
    joined = joined[joined["profit_for_period_thousands"].notna() & (joined["profit_for_period_thousands"] != 0)]
    if len(joined):
        rel = (joined["net_profit"] / (joined["profit_for_period_thousands"] * 1000.0) - 1).abs()
        out["headline_vs_statement_profit"] = {"pairs": int(len(joined)), "within_2pct": round(float((rel <= 0.02).mean()), 4),
                                               "within_10pct": round(float((rel <= 0.10).mean()), 4)}
    val = fundamentals.merge(figures, on="symbol", suffixes=("_fund", "_qr"))
    val = val[val["fiscal_year_fund"] == val["fiscal_year_qr"]]
    eps = val.dropna(subset=["eps_fund", "eps_qr"])
    bv = val.dropna(subset=["book_value", "net_worth_per_share"])
    out["validation_against_fundamentals"] = {
        "fundamentals_rows": fundamentals_rows,
        "fundamentals_symbols": int(fundamentals["symbol"].nunique()),
        "latest_snapshot_dates": [str(fundamentals["reported_date"].min()), str(fundamentals["reported_date"].max())],
        "symbols_with_figures": int(figures["symbol"].nunique()),
        "symbols_in_both": int(fundamentals.merge(figures, on="symbol")["symbol"].nunique()),
        "same_fiscal_year": int(len(val)),
        "eps_pairs": int(len(eps)),
        "eps_exact_0.01": round(float(((eps["eps_fund"] - eps["eps_qr"]).abs() <= 0.01).mean()), 4) if len(eps) else None,
        "eps_within_1pct": round(float(((eps["eps_fund"] - eps["eps_qr"]).abs() <= 0.01 * eps["eps_qr"].abs().clip(lower=1)).mean()), 4) if len(eps) else None,
        "book_value_pairs": int(len(bv)),
        "book_value_exact_0.01": round(float(((bv["book_value"] - bv["net_worth_per_share"]).abs() <= 0.01).mean()), 4) if len(bv) else None,
        "eps_mismatch_examples": eps.loc[(eps["eps_fund"] - eps["eps_qr"]).abs() > 0.01, ["symbol", "fiscal_year_fund", "quarter", "eps_fund", "eps_qr"]]
        .head(10).astype(str).to_dict("records"),
    }
    out["figures"] = {"rows": int(len(figures)), "by_label": figures["column_label"].value_counts().head(8).to_dict(),
                      "eps_present": int(figures["eps"].notna().sum()), "net_worth_present": int(figures["net_worth_per_share"].notna().sum()),
                      "with_published_date": int(figures["published_date"].notna().sum())}
    dividends["year"] = pd.to_datetime(dividends["announcement_date"]).dt.year
    out["dividend_declarations"] = {"rows": int(len(dividends)), "symbols": int(dividends["symbol"].nunique()),
                                    "per_year": {int(y): int(n) for y, n in dividends.groupby("year").size().items()}}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect quarterly report announcements with publication dates")
    parser.add_argument("--stage", choices=["sharesansar", "merolagani", "resolve", "all"], default="all")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = parser.parse_args()
    if args.report:
        from src.database.connection import engine

        result = report(engine)
        args.report.write_text(json.dumps(result, indent=2, default=str))
        print(json.dumps(result, indent=2, default=str))
        return
    if args.delay < 2.0:
        raise SystemExit("--delay below 2 seconds is not allowed")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(args.log), logging.StreamHandler()],
    )
    from src.database.connection import engine

    collector = Collector(engine, args.delay)
    collector.apply_schema()
    started = datetime.now()
    if args.stage in ("merolagani", "all"):
        collector.run_merolagani()
        logger.info("merolagani name resolution: %s", collector.resolve_merolagani_names())
    if args.stage == "resolve":
        logger.info("merolagani name resolution: %s", collector.resolve_merolagani_names())
    if args.stage in ("sharesansar", "all"):
        collector.run_sharesansar(args.symbols or equity_symbols(engine))
    logger.info("finished in %s with %d requests", datetime.now() - started, collector.requests)


if __name__ == "__main__":
    main()
