# Data Plan: News, Notices, Policy and Quarterly Reports

Status: **plan only, nothing scraped.** On 2026-10-01 each source was checked with `robots.txt` plus at most a handful of single page requests (about 30 requests in total, 3-4 seconds apart) to confirm structure and depth. No bulk data was fetched or stored.

## Why this data

The event study (`docs/EVENT_RESULTS.md`) had to *assume* when book-close dates became public, could not separate news from no-news volume spikes except by the absence of a corporate action, and has no earnings-release or policy-change dates. The data below would supply:

1. **Announcement dates** for book closes, AGMs, dividends, bonus and rights shares, mergers and IPOs, so knowledge dates are observed instead of assumed.
2. **Earnings-release dates and quarterly fundamentals with publication timestamps**, so fundamentals can be used point-in-time.
3. **Dated regulatory and monetary-policy events** (NRB policy rates, directives, SEBON circulars and enforcement actions).
4. **A news flag** for any symbol-day, to define "no-news" properly.

## Sources

| Source | What it offers | How far back (checked) | Structure | Difficulty | Robots / terms |
| --- | --- | --- | --- | --- | --- |
| **Merolagani news** (`merolagani.com/NewsDetail.aspx?newsID=N`) | Market and company news, mostly in Nepali, with title, timestamp (to the minute) and body | Sequential integer IDs. Latest checked 131,421 (2026-10-01). ID 30,000 = 2017-02-10, ID 40,000 = 2018-05-01, 60,000 = 2020-05-07, 80,000 = 2022-04-15, 100,000 = 2024-02-29. IDs 1,000, 20,000 and 25,000 returned empty pages, so the usable archive appears to start around late 2016 | Server-rendered ASP.NET; the title (`#ctl00_ContentPlaceHolder1_newsTitle`), date and body are in the HTML. Listing at `NewsList.aspx`. Company pages (`CompanyDetail.aspx?symbol=`) have News, Announcements, AGM and Quarterly Report tabs | **Low-medium.** One GET per article, about 100k articles; at 1 request per 3 s that is about 3.5 days. No symbol tags seen on articles, so symbols must be matched from the text | `robots.txt` does not exist (404 page). No terms page was found in the static HTML. Treat as all rights reserved: internal research use only, and ask permission before bulk collection |
| **Sharesansar news** (`sharesansar.com/newsdetail/<slug>`, categories such as `/category/latest`) | English news, analysis and company announcements | Not verified: category pages tried with `?page=500` returned current items, so the archive pagination is a different (probably AJAX) mechanism | Slug URLs (not enumerable). Company pages (`/company/<symbol>`) have News, Events, Announcement, Financial Reports, Quarterly Reports, AGM History, Dividend History and Right Share History tabs. The existing scrapers already use the same CSRF + company-id POST pattern for dividends and rights | **Medium.** Per-company announcement and quarterly-report tabs fit the existing scraper code. Full news history needs the archive endpoint found first | `robots.txt`: `User-agent: * / Disallow:` (everything allowed). The terms page exists (`/terms-and-conditions`) but its clauses are not in the static HTML, so it must be read in a browser before any bulk run |
| **NEPSE notices and company disclosures** (`nepalstock.com`) | Official exchange notices, corporate announcements, financial disclosures and trading halts | Unknown | Angular single-page app backed by a JSON API | **High / blocked.** `GET /api/nots/news/media/news-and-alerts` returned **HTTP 401 "UNAUTHORIZED ACCESS"**, matching the earlier price-backfill finding. Community clients pass this by reproducing an in-browser token routine | No usable `robots.txt` (the SPA shell is served). **Do not bypass the access control**: doing so risks breaching Nepal's Electronic Transactions Act 2063 provisions on unauthorized access. Use only if NEPSE grants API access |
| **SEBON** (`sebon.gov.np`) | Acts, regulations, directives, circulars, notices, enforcement actions, press releases, approved prospectuses, policy and programme documents | Not measured; the listings include items from fiscal 2080/81 and earlier | Server-rendered listing pages (`/acts`, `/circulars`, `/notices`, `/enforcement-actions`, `/activities-action/...`) linking to detail pages and mostly PDF attachments, many in Nepali. `/directives` returned 404 (directives appear under other sections) | **Medium.** A few hundred to a few thousand documents. Needs PDF text extraction and Nepali dates (Bikram Sambat) converted to AD | `robots.txt`: everything allowed. Government publications, but keep attribution and do not present them as SEBON's own data products |
| **NRB** (`nrb.org.np`) | Monetary policy statements and mid-year reviews, circulars and directives by department (`/category/circulars/?department=bfr`, `psd`, `fxm`, …), notices, monetary operations, and a policy-rates page (`/cmfm_rates/policy_rates`) | The monetary-policy category has 13 listing pages; the current statement is for 2083/84. Earlier statements back to at least the 2060s BS are expected but were not checked | WordPress site with the REST API disabled (`/wp-json/wp/v2/posts` → 404). Category pages are paginated (`/page/N/`), with PDFs under `/contents/uploads/YYYY/MM/`. The policy-rates page timed out twice from this machine | **Low-medium.** Few documents. Policy-rate history may be one table. Directives are long PDFs; only effective and issue dates and the type of measure are needed | `robots.txt`: only `/wp-admin/` is disallowed. Government publication; cite the source |
| **Quarterly reports** (Sharesansar Quarterly Reports / Financial Reports tabs; Merolagani Quarterly Report tab; company websites; NEPSE disclosures) | Unaudited quarterly financials (EPS, net profit, NPL, capital adequacy, reserves, book value), usually as PDF or image, sometimes key figures as text | Not measured. The repository has only 10 weekly fundamentals snapshots since 2026-07-23 (`fundamentals`), so any history is new | Per company and quarter; publication date shown on listings. PDFs vary by company and some are scanned images | **High** for full financials (PDF and OCR parsing across hundreds of layouts). **Low** for publication dates and headline figures if the tabs list them as text | Same terms as the hosting site. Company filings are public disclosures, but the hosting site's terms still govern bulk copying |

### Legal and ethical rules for any future collection

1. Read each site's terms in a browser before collecting. For Merolagani and Sharesansar, ask for written permission or a data licence for bulk historical collection. Store full article text only for internal research; never republish it.
2. Never circumvent authentication, tokens or rate limits (NEPSE API). Unauthorized access to computer systems is an offence under Nepal's Electronic Transactions Act 2063.
3. News articles are copyrighted (Copyright Act 2059). Products may show derived facts (event type, date, symbol) and a link to the source, not the text.
4. Crawl politely: one request every 3+ seconds, a descriptive User-Agent with contact details, run off-hours, cache everything and never refetch the same URL.
5. Publishing signals derived from this data stays behind the open SEBON licensing question (`TODOS.md`).

## Proposed schema

Raw pages and PDFs go to append-only Parquet and file storage (like the floorsheet); structured, point-in-time tables go to local Postgres.

```sql
CREATE TABLE source_documents (
    id               BIGSERIAL PRIMARY KEY,
    source           VARCHAR(30)  NOT NULL,   -- merolagani, sharesansar, sebon, nrb, nepse, company_site
    source_id        VARCHAR(200) NOT NULL,   -- newsID, slug, or URL hash
    url              TEXT         NOT NULL,
    doc_type         VARCHAR(40)  NOT NULL,   -- news, announcement, notice, circular, directive, monetary_policy, quarterly_report, agm_notice, press_release, enforcement
    language         VARCHAR(5),
    title            TEXT,
    published_at     TIMESTAMPTZ,             -- as shown by the source, Asia/Kathmandu
    published_bs     VARCHAR(20),             -- original Bikram Sambat date when given
    first_seen_at    TIMESTAMPTZ  NOT NULL,   -- when this pipeline first fetched it
    available_at     TIMESTAMPTZ  NOT NULL,   -- point-in-time key: published_at for backfill, first_seen_at for live capture
    content_sha256   CHAR(64)     NOT NULL,
    raw_path         TEXT         NOT NULL,   -- Parquet/file location of raw HTML or PDF
    text_path        TEXT,                    -- extracted text, kept outside Postgres
    parser_version   VARCHAR(20)  NOT NULL,
    UNIQUE (source, source_id, content_sha256)
);

CREATE TABLE document_symbols (
    document_id  BIGINT REFERENCES source_documents(id),
    symbol       VARCHAR(20) REFERENCES companies(symbol),
    match_method VARCHAR(20) NOT NULL,        -- source_tag, company_page, title_match, body_match
    confidence   NUMERIC(4,3) NOT NULL,
    PRIMARY KEY (document_id, symbol)
);

CREATE TABLE corporate_event_announcements (
    id              BIGSERIAL PRIMARY KEY,
    symbol          VARCHAR(20) REFERENCES companies(symbol),
    event_type      VARCHAR(40) NOT NULL,     -- book_close, agm, dividend_proposal, bonus_proposal, rights_issue, merger, ipo_allotment, earnings_release
    announced_at    TIMESTAMPTZ NOT NULL,     -- first availability across sources
    effective_date  DATE,                     -- book-close date, AGM date, etc.
    fiscal_year     VARCHAR(20),
    payload         JSONB,                    -- ratios, amounts, ratio text as published
    document_id     BIGINT REFERENCES source_documents(id),
    UNIQUE (symbol, event_type, effective_date, document_id)
);

CREATE TABLE quarterly_financials (
    symbol          VARCHAR(20) REFERENCES companies(symbol),
    fiscal_year     VARCHAR(20) NOT NULL,     -- BS fiscal year, e.g. 2081/82
    quarter         SMALLINT    NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    period_end      DATE        NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL,     -- point-in-time key
    revision        SMALLINT    NOT NULL DEFAULT 0,
    eps             NUMERIC(14,4),
    net_profit      NUMERIC(20,2),
    book_value      NUMERIC(14,4),
    npl_pct         NUMERIC(8,4),
    capital_adequacy_pct NUMERIC(8,4),
    distributable_profit NUMERIC(20,2),
    document_id     BIGINT REFERENCES source_documents(id),
    PRIMARY KEY (symbol, fiscal_year, quarter, revision)
);

CREATE TABLE policy_events (
    id              BIGSERIAL PRIMARY KEY,
    issuer          VARCHAR(10) NOT NULL,     -- NRB, SEBON, NEPSE, MOF
    event_type      VARCHAR(40) NOT NULL,     -- monetary_policy, policy_rate_change, crr_slr_change, margin_lending_rule, circuit_rule, directive, enforcement
    announced_at    TIMESTAMPTZ NOT NULL,
    effective_date  DATE,
    rate_name       VARCHAR(40),
    old_value       NUMERIC(10,4),
    new_value       NUMERIC(10,4),
    summary         TEXT,
    document_id     BIGINT REFERENCES source_documents(id)
);
```

Point-in-time rules: research joins use `available_at` / `announced_at` / `published_at` strictly before the knowledge session's close. Backfilled history cannot detect later edits, so `first_seen_at` is recorded and live capture should start as soon as collection is approved. Nepali (BS) dates are stored as published and converted with a tested calendar table.

## Proposed order

1. **Sharesansar per-company Announcement, AGM and Quarterly Reports tabs**, using the existing scraper pattern: about 600 companies × 3 tabs. This fixes the book-close announcement assumption and gives earnings-release dates. Highest value per request.
2. **NRB monetary policy, policy rates and directives**: small, government, `robots.txt` permits it.
3. **SEBON circulars, directives and enforcement actions**: small, government.
4. **Merolagani news archive (2017 onward)**, only after permission, for the no-news flag.
5. **NEPSE disclosures**, only with authorised API access.
