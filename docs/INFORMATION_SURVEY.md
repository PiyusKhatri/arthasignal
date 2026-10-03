# Information data survey and quarterly-report collector

The price-only approaches have all failed (`docs/HONEST_STATUS.md`, `docs/MATRIX_V2.md`), so announcement and report data is the next source of evidence. This survey extends `docs/DATA_PLAN.md` (2026-10-01) with checks made on 2026-10-04. Only the quarterly-report collector was built. Section 4 has its counts.

## 1. Sources

"Publication date recorded" means the source shows, for each item, when it became public, which is what a point-in-time join needs. Dates on Nepali sites are calendar days with no time of day, except Merolagani news, which shows the minute.

| Source | What it offers | How far back | Publication date recorded? | Difficulty | Robots / terms |
| --- | --- | --- | --- | --- | --- |
| **Sharesansar company announcements** (`POST /company-announcements`, per company, 50 per page; larger pages return nothing) | Every company announcement: quarterly results with a net-profit headline, AGM and book-close notices, dividend and bonus proposals, rights, auctions, promoter sales | Back to **2011** for some symbols (earliest row collected: 2011-05-07). NABIL has 306 announcements | **Yes**, `published_date` per item | **Low.** The scrapers already use the same CSRF + company-id POST | `robots.txt` allows everything. The terms page loads its clauses with JavaScript, so they have **not been read** |
| **Sharesansar company news** (`POST /company-news`) | English news per company (NABIL: 960 items), including quarterly-result stories with growth figures | Not measured | Yes, `published_date` | Low (same pattern). Figures are in prose | Same as above |
| **Sharesansar quarterly tab** (`POST /company-quarterly-report` with company, symbol and sector) | Full balance sheet, P&L and key metrics (EPS, net worth per share, NPL, capital adequacy, reserves) as text | **Latest quarter only.** No history | **No.** The column label gives the quarter, not the publication date | Low | Same as above |
| **Sharesansar quarterly report images** (announcement detail pages) | The report itself, as a JPG | As far back as the announcements | Yes, via the announcement | **High**: OCR across many layouts | Same as above |
| **Sharesansar dividend table** (`POST /company-dividend`) | Cash and bonus percentage per fiscal year, **announcement date**, book-close date, distribution date | Varies (NABIL from 2019-02-18) | **Yes**, `announcement_date` | Low | Same as above |
| **Sharesansar AGM table** (`POST /company-agm`) | AGM number, venue, book-close and meeting dates, full agenda (dividend and bonus proposals, mergers, elections) | NABIL: 19 AGMs | **No** publication date; the announcement list carries the notice date | Low | Same as above |
| **Merolagani quarterly-report index** (`/handlers/webrequesthandler.ashx?type=get_company_reports&reportType=QUARTERLY`, JSON, 50 per page, `sectorID=0` required) | Announcement ID, title (company, quarter, fiscal year) and date for every quarterly report across all companies. Per-company view on the company page's Quarterly Report tab (NABIL: 68 records) | Fiscal-year filter back to 044-045 BS; depth measured by the collector (section 4) | **Yes**, `announcementDateAD`; detail pages also show the BS date | **Low** for the index. The figures are not on Merolagani as text | No `robots.txt`. "Disclaimer, Privacy & Terms of Use" page not reviewed. Treat as all rights reserved |
| **Merolagani announcements and news** (`AnnouncementDetail.aspx?id=`, `NewsDetail.aspx?newsID=`) | Announcements tagged by type (Quarterly Report, AGM, Book Close, Dividend) with symbol and fiscal year. News mostly in Nepali | News IDs from about late 2016 (ID 30,000 = 2017-02-10) | **Yes**: announcements by day (AD and BS), news to the minute | Low-medium. About 100k news pages at one request per 3 s is about 3.5 days | As above |
| **NEPSE notices and disclosures** (`nepalstock.com` API) | Official exchange notices, company disclosures, halts | Unknown | Presumably | **Blocked**: HTTP 401. Must not be bypassed (Electronic Transactions Act 2063) | Use only with granted access |
| **SEBON** (`sebon.gov.np`) | Acts, regulations, directives, circulars, notices, enforcement actions, prospectuses | Listings include fiscal 2080/81 and earlier | Yes, on listings, often in BS | Medium: PDFs, often Nepali, BS dates | `robots.txt` allows everything; government publication |
| **NRB** (`nrb.org.np`) | Monetary policy statements and reviews, policy-rate page, directives and circulars by department | Monetary-policy category has 13 listing pages; older statements not checked | Yes, on listings and in PDFs | Low-medium: WordPress with no REST API, PDFs | `robots.txt` disallows only `/wp-admin/` |

### What the quarterly-report history can and cannot give

| Item asked for | Text source with history and a publication date | Gap |
| --- | --- | --- |
| Publication date of each quarterly report | Merolagani index (all companies) and Sharesansar announcements (per company) | None. Two independent sources to cross-check |
| Net profit | Sharesansar announcement headline ("posted a net profit of Rs 7.90 billion"), when the title states it | Older and smaller-company titles often omit the figure. Rounded to 2-3 significant digits |
| EPS, net worth per share, reserves, total equity | Sharesansar quarterly tab, **latest quarter only** | No text history. History needs OCR of the report images, or daily live capture of the tab from now on |
| Dividend declared | Sharesansar dividend table, with announcement date | Covers declared dividends, not every proposal revision |

### Robots, terms and conduct

- Sharesansar allows all paths in `robots.txt`. Merolagani has no `robots.txt`. Neither site's terms could be read from the static HTML, and nobody has read them in a browser yet. `docs/DATA_PLAN.md` recommended asking both sites for permission before bulk historical collection. That has **not** been done. The collector was run on instruction, for internal research only, and nothing it collects is republished.
- Conduct: one request every 3 s or more per site (the collector refuses less than 2 s), a browser-like User-Agent tagged `arthasignal-research`, retries with backoff, and no URL refetched once its stage is marked done.
- The NEPSE API's access control is not bypassed.

## 2. Point-in-time schema

Every fact row carries the date it became public. A research join may use a row on knowledge session *t* only if its publication date is **before *t*** (strictly, because no source records the time of day; a report dated *t* may come out after the close). Rows also carry `first_seen_at`, the time this pipeline first stored them, so later edits by the source are detectable and live capture can use `first_seen_at` as an upper bound.

Implemented (`src/scrapers/quarterly_reports_collector.py`, all insert-only with `ON CONFLICT DO NOTHING`):

| Table | Key | Point-in-time column | Content |
| --- | --- | --- | --- |
| `quarterly_report_announcements` | (`source`, `source_id`) | `published_date` | One row per report announcement per source: symbol (or company name when unmatched), fiscal year, quarter, correction flag, net profit and the text it was parsed from. Corrections are separate rows, never updates |
| `quarterly_report_figures` | (`source`, `symbol`, `fiscal_year`, `quarter`) | `published_date` (the earliest matching announcement), `first_seen_at` | Latest-quarter statements: EPS, net worth per share, profit, reserves, retained earnings, equity, share capital, plus the full statement as JSON |
| `dividend_declarations` | (`source`, `symbol`, `fiscal_year`, `announcement_date`) | `announcement_date` | Cash and bonus percentages with book-close date |
| `quarterly_collector_progress` | (`symbol`, `stage`) | — | Resume state and per-symbol log |
| view `quarterly_report_announcements_dev` | — | — | Only rows published before 2025-09-30, for any development-window research |

Proposed for the other sources (not built): `source_documents`, `document_symbols`, `corporate_event_announcements` and `policy_events` as in `docs/DATA_PLAN.md`. Each needs an `announced_at` or `published_at` that is NOT NULL, plus `first_seen_at`.

**Holdout rule for these tables.** Rows published on or after 2025-09-30 belong to the holdout and live period. They are stored, because the live writer will need them and the validation below compares the latest quarter, but no development test may read them. Development code must read the `_dev` view or filter on `published_date < 2025-09-30`.

## 3. Collector

`python -m src.scrapers.quarterly_reports_collector --stage {merolagani|resolve|sharesansar|all}` (`--report PATH` writes the summary). It:
- **resumes:** every symbol or page is recorded in `quarterly_collector_progress` and skipped once done or not found;
- **rate-limits:** one request every 3 s or more per site, three retries with backoff, and a failed page is skipped and left for the next run;
- **logs** each symbol and page to `logs/quarterly_collector_*.log`;
- **inserts only:** rows are never updated or deleted. Corrections arrive as new rows;
- **maps names without editing rows:** Merolagani company names that do not match `companies` are mapped to symbols in a separate table, `merolagani_company_symbols`, filled from one Merolagani detail page per name. The view `quarterly_report_announcements_resolved` applies that map.

Run 2026-10-04 with `nohup`, one background process per site:
- Merolagani index: 237 requests in 11 min;
- name resolution: 1,438 requests in 72 min;
- Sharesansar: 2,763 requests over 593 symbols in 2 h 18 min. 569 done, 24 without a Sharesansar page, 0 errors.

The first resolution run stopped on a Merolagani timeout without writing anything. It was made to skip failed pages and rerun.

## 4. Real counts (`docs/quarterly_collection.json`)

**Quarterly-report announcements**

| | Sharesansar | Merolagani |
| --- | ---: | ---: |
| Rows (of which corrections) | 13,176 (64) | 11,794 (72) |
| First / last publication date | 2011-04-19 / 2026-09-03 | 2009-10-16 / 2026-09-18 |
| Rows published before 2025-09-30 (development-usable) | 12,101 | 10,653 |
| Symbols with at least one report | 491 | 461 |
| Active symbols covered (of 312) | 280 | 280 |
| Delisted symbols covered (of 256) | 205 | 177 |
| Suspended symbols covered (of 25) | 6 | 3 |
| Reports per symbol, p10 / median / p90 | 5 / 22 / 61 | 4 / 20 / 48 |
| Fiscal year / quarter parsed | 99.95% / 100% | 99.71% / 99.75% |
| Net profit parsed from the title | 94.1% | 0% (titles carry no figure) |
| Rows with no symbol | 0 | 375 (63 company names not resolvable to a listed symbol) |

**Per year (publication year):**

| Year | 2009 | 2010 | 2011 | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Sharesansar | – | – | 560 | 746 | 787 | 791 | 928 | 783 | 707 | 758 | 810 | 795 | 850 | 881 | 942 | 1,009 | 1,011 | 818 |
| Merolagani | 19 | 105 | 181 | 229 | 296 | 512 | 684 | 765 | 701 | 745 | 865 | 858 | 902 | 927 | 1,000 | 1,069 | 1,067 | 869 |

Sharesansar's per-company announcements start in April 2011 for 197 symbols at once, so its archive begins there; Merolagani is thinner before 2014. 2025 and 2026 include holdout-period rows (published from 2025-09-30), which are stored but excluded from development use.

**Cross-checks**

- **Publication dates across sources:** 10,619 reports match on symbol, fiscal year and quarter. 65.0% have the same date, 89.6% are within 1 day and 92.2% within 7 days. Sharesansar is later by more than 7 days for 771 reports, Merolagani for 57. Rule for research joins: when both sources exist, use the **later** of the two first-publication dates. That date can only be late, never early, so it cannot cause look-ahead.
- **Headline net profit against the statement:** 262 Sharesansar headlines can be compared with the full statement of the same quarter. 90.1% agree within 2%, and the rest are more than 10% off. The headline is therefore usable but not exact, and it is rounded.
- **Validation against the 2,525 existing `fundamentals` rows** (Merolagani page snapshots, 281 symbols, latest snapshot per symbol 2026-08-29 to 2026-09-26):
  - 264 symbols have both. 222 share the fiscal year 2082/83. Note that the snapshots do not record which quarter they show.
  - EPS: 181 pairs, **92.8% equal to 0.01** and 93.4% within 1%. Twelve or more differ by 2-12% (EBL 35.19 vs 36.95, MBL 17.49 vs 15.44, NABIL 28.36 vs 27.74, SANIMA 26.14 vs 24.92, …). The likely causes are a different quarter or a different EPS definition (annualized versus period) on the two sites. **These differences are not explained, and neither site has been treated as correct.**
  - Book value against net worth per share: 104 pairs, 96.2% equal to 0.01.
- **Latest-quarter figures:** 265 symbols, nearly all 4th quarter 2082/83. EPS was found for 217 and net worth per share for 111 (insurance, hydro and manufacturing tabs use other labels). 264 have a publication date from the matching announcement.
- **Dividend declarations:** 1,014 rows on 248 symbols with announcement dates from 2018 (82, 115, 153, 158, 111, 118, 106, 131 and 40 for 2018-2026). None before 2018.

**What this gives and what it does not.** There is now a dated record of when every quarterly report from 2011 onward became public, and of net profit for most of them. There is still **no history of EPS, net worth, reserves or other balance-sheet items with publication dates**. Those exist only as report images, which would need OCR, or as the latest quarter, captured from now on.
