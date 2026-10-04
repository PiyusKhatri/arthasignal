from __future__ import annotations

import argparse
import html
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from sqlalchemy import text

from src.database.instruments import equity_sectors

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 arthasignal-research"
DELAY_SECONDS = 3.0
SOURCES = {
    "sharesansar": "https://www.sharesansar.com/company/{slug}",
    "merolagani": "https://merolagani.com/CompanyDetail.aspx?symbol={symbol}",
}
SHARESANSAR_SECTOR = re.compile(r'id="sector" style="display: none;">([^<]*)</div>')
MEROLAGANI_SECTOR = re.compile(r"Sector\s*</th>\s*<td[^>]*>(.*?)</td>", re.S)
TITLE = re.compile(r"<title>(.*?)</title>", re.S)
NON_EQUITY_LABELS = {"corporate debenture": "Non-Convertible Debentures", "corporate debentures": "Non-Convertible Debentures",
                     "mutual fund": "Mutual Funds", "mutual funds": "Mutual Funds", "promoter share": "Promoter Shares",
                     "promoter shares": "Promoter Shares", "preference share": "Preference Shares"}
STATUS_LABELS = {"merged", "", "-", "n/a", "na"}
SOURCE_ALIASES = {"merolagani": {"development bank limited": "Development Banks", "non-life insurance": "Non Life Insurance"}}
ALIAS_EVIDENCE = ("On 2026-10-04 Merolagani showed 'Development Bank Limited' for CORBL, EDBL, GBBL and GRDBL and 'Non-Life Insurance' for "
                  "HEI, IGI, NICL and NIL, which companies.sector stores as 'Development Banks' and 'Non Life Insurance' (4 of 4 each)")

DDL = """
CREATE TABLE IF NOT EXISTS company_sector_sources (
    id           BIGSERIAL PRIMARY KEY,
    symbol       VARCHAR(20) NOT NULL,
    source       VARCHAR(20) NOT NULL,
    url          TEXT NOT NULL,
    http_status  INTEGER,
    page_title   TEXT,
    sector_raw   TEXT,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS company_sector_assignments (
    id           BIGSERIAL PRIMARY KEY,
    symbol       VARCHAR(20) NOT NULL,
    sector       VARCHAR(80) NOT NULL,
    evidence     JSONB NOT NULL,
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION company_sector_reject_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'sector evidence is append-only: % on % rejected', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS company_sector_sources_immutable ON company_sector_sources;
CREATE TRIGGER company_sector_sources_immutable BEFORE UPDATE OR DELETE ON company_sector_sources
    FOR EACH ROW EXECUTE FUNCTION company_sector_reject_change();
DROP TRIGGER IF EXISTS company_sector_assignments_immutable ON company_sector_assignments;
CREATE TRIGGER company_sector_assignments_immutable BEFORE UPDATE OR DELETE ON company_sector_assignments
    FOR EACH ROW EXECUTE FUNCTION company_sector_reject_change();
"""


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def parse(source: str, page: str) -> dict[str, str | None]:
    pattern = SHARESANSAR_SECTOR if source == "sharesansar" else MEROLAGANI_SECTOR
    match = pattern.search(page)
    title = TITLE.search(page)
    return {"sector_raw": _clean(match.group(1)) if match else None, "page_title": _clean(title.group(1))[:200] if title else None}


def _key(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().lower())


def canonical(label: str | None, source: str | None = None) -> tuple[str, str | None]:
    if label is None or _key(label) in STATUS_LABELS:
        return "unknown", None
    key = _key(label)
    alias = SOURCE_ALIASES.get(source or "", {}).get(key)
    if alias is not None:
        return "equity_sector", alias
    for sector in equity_sectors():
        if key == _key(sector) or key.rstrip("s") == _key(sector).rstrip("s"):
            return "equity_sector", sector
    if key in NON_EQUITY_LABELS:
        return "non_equity", NON_EQUITY_LABELS[key]
    return "unrecognized", None


def decide(evidence: dict[str, str | None]) -> dict[str, Any]:
    kinds = {source: canonical(label, source) for source, label in evidence.items()}
    sectors = {value for kind, value in kinds.values() if kind == "equity_sector"}
    non_equity = {value for kind, value in kinds.values() if kind == "non_equity"}
    unrecognized = [evidence[s] for s, (kind, _) in kinds.items() if kind == "unrecognized"]
    if non_equity:
        return {"result": "source_says_non_equity", "sector": None, "detail": sorted(non_equity)}
    if unrecognized:
        return {"result": "unrecognized_label", "sector": None, "detail": unrecognized}
    if len(sectors) == 1:
        return {"result": "assigned", "sector": sectors.pop(), "detail": {s: v for s, (k, v) in kinds.items() if k == "equity_sector"}}
    if len(sectors) > 1:
        return {"result": "sources_disagree", "sector": None, "detail": sorted(sectors)}
    return {"result": "unknown", "sector": None, "detail": evidence}


def fetch(symbol: str, session: requests.Session) -> list[dict[str, Any]]:
    out = []
    for source, template in SOURCES.items():
        url = template.format(slug=quote(symbol.lower(), safe=""), symbol=quote(symbol, safe=""))
        row: dict[str, Any] = {"symbol": symbol, "source": source, "url": url, "http_status": None, "page_title": None, "sector_raw": None}
        try:
            response = session.get(url, timeout=25)
            row["http_status"] = response.status_code
            if response.status_code == 200:
                if "charset" not in response.headers.get("Content-Type", "").lower():
                    response.encoding = "utf-8"
                row.update(parse(source, response.text))
        except requests.RequestException as error:
            row["page_title"] = f"error: {type(error).__name__}"
        out.append(row)
        time.sleep(DELAY_SECONDS)
    return out


def run(apply: bool, report_path: Path | None) -> dict[str, Any]:
    from src.database.connection import engine
    from src.database.holdout_guard import engine as research

    with research.connect() as connection:
        targets = [r[0] for r in connection.execute(text(
            "SELECT symbol FROM companies WHERE instrument_type = 'Equity' AND sector IS NULL ORDER BY symbol"))]
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    results = []
    fetched_rows = []
    for symbol in targets:
        if not symbol.strip():
            results.append({"symbol": symbol, "result": "empty_symbol", "sector": None, "detail": "the companies row has an empty symbol"})
            continue
        rows = fetch(symbol, session)
        fetched_rows += rows
        decision = decide({r["source"]: r["sector_raw"] for r in rows})
        results.append({"symbol": symbol, **decision, "evidence": {r["source"]: {"sector_raw": r["sector_raw"], "http": r["http_status"],
                                                                                 "title": r["page_title"], "url": r["url"]} for r in rows}})
        logger.info("%s: %s %s", symbol, decision["result"], decision["sector"])
    assigned = [r for r in results if r["result"] == "assigned"]
    if apply:
        raw = engine.raw_connection()
        try:
            with raw.cursor() as cursor:
                cursor.execute(DDL)
            raw.commit()
        finally:
            raw.close()
        with engine.begin() as connection:
            for row in fetched_rows:
                connection.execute(text("INSERT INTO company_sector_sources (symbol, source, url, http_status, page_title, sector_raw) "
                                        "VALUES (:symbol, :source, :url, :http_status, :page_title, :sector_raw)"), row)
            for row in assigned:
                updated = connection.execute(text("UPDATE companies SET sector = :s WHERE symbol = :y AND sector IS NULL AND instrument_type = 'Equity'"),
                                             {"s": row["sector"], "y": row["symbol"]}).rowcount
                if updated:
                    connection.execute(text("INSERT INTO company_sector_assignments (symbol, sector, evidence) VALUES (:y, :s, CAST(:e AS jsonb))"),
                                       {"y": row["symbol"], "s": row["sector"], "e": json.dumps(row["evidence"])})
    summary: dict[str, int] = {}
    for row in results:
        summary[row["result"]] = summary.get(row["result"], 0) + 1
    out = {"applied": apply, "targets": len(targets), "summary": summary, "alias_evidence": ALIAS_EVIDENCE, "results": results}
    if report_path is not None:
        report_path.write_text(json.dumps(out, indent=1, default=str, ensure_ascii=False) + "\n")
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Find the sector of equities that have none from Sharesansar and Merolagani company pages")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    out = run(args.apply, args.report)
    print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=1))


if __name__ == "__main__":
    main()
