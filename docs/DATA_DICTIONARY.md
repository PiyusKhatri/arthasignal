# Data dictionary for the score pillars

- **What it covers:** every field each pillar of the simulation score (`docs/SIMULATION_PROTOCOL.md` section 2) can draw on. For each field: its source, the first year with rows, its point-in-time rule, the rows per year from 2014 to 2025, known gaps and the test that guards against look-ahead.
- **Coverage basis:** rows visible to the research role (dates before 2025-09-30), measured on 2026-10-05 after Phase 2 by `python -m src.archive.data_dictionary`, which also writes `docs/data_dictionary_coverage.json`. The news archives, report images and OCR batches keep running in the background, so rerun it to refresh.
- **General rule** (`docs/POINT_IN_TIME.md`): information is usable from the first session strictly after the earliest moment it was observably public in a source whose date passes verification. Holdout rows are stored but hidden from research by row-level security. Corrections to append-only tables go through the `policy_events_current` and `sentiment_observations_current` views.
- **Licences:** `docs/DATA_LICENSES.md` (MeroLagani covered by the owner's written agreement).

## Fields by pillar

### technical

| Field | Source | First year with rows | Point-in-time rule | Known gaps | Leak test |
|---|---|---|---|---|---|
| close, high, low (equities) | daily_prices (Sharesansar session pages, NEPSE API live) | 2014 | known at the session close (15:00); used from the next session | highs and lows before 2018-02-18 are stored values of unknown quality; 9 equities still have no sector | tests/test_holdout_guard.py, tests/test_backtest_leakage_guard.py |
| real open (equities) | daily_prices | 2014 | session close | before 2018-02-18 the stored open equals the previous close; protocol uses floorsheet-derived opens instead | tests/test_floorsheet_ohlc.py |
| floorsheet-derived open/high/low | Merolagani floorsheet Parquet (board lots, quantity >= 10) | 2014 | session close | 1-2% missing pages in 2015-2018; files end 2025-01-19; source terms flagged | tests/test_floorsheet_ohlc.py |
| indicators (RSI, SMA, Bollinger, ...) | computed from daily_prices | 2014 | session close | recomputed on full history 2026-09; weekly/monthly rows stale | tests/test_indicators_golden.py and other golden tests |

Rows per year (research role, before 2025-09-30):

| Field | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| close, high, low (equities) | 17,336 | 25,632 | 28,043 | 30,828 | 35,004 | 37,451 | 30,164 | 43,683 | 46,447 | 50,921 | 56,806 | 42,115 |
| real open (equities) | 97 | 154 | 188 | 212 | 25,125 | 29,400 | 25,601 | 37,468 | 41,115 | 45,914 | 50,734 | 39,869 |
| floorsheet-derived open/high/low | 16,346 | 24,083 | 26,898 | 26,639 | 35,381 | 39,028 | 33,130 | 50,947 | 54,697 | 61,143 | 68,208 | 3,685 |
| indicators (RSI, SMA, Bollinger, ...) | 7,162 | 11,622 | 14,010 | 19,291 | 23,309 | 27,201 | 23,487 | 41,220 | 54,748 | 63,418 | 72,939 | 54,945 |

### volume

| Field | Source | First year with rows | Point-in-time rule | Known gaps | Leak test |
|---|---|---|---|---|---|
| volume, turnover (equities) | daily_prices | 2014 | session close | none known on traded days | tests/test_holdout_guard.py |

Rows per year (research role, before 2025-09-30):

| Field | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| volume, turnover (equities) | 17,336 | 25,632 | 28,043 | 30,828 | 35,004 | 37,451 | 30,164 | 43,683 | 46,447 | 50,921 | 56,806 | 42,115 |

### floorsheet_broker

| Field | Source | First year with rows | Point-in-time rule | Known gaps | Leak test |
|---|---|---|---|---|---|
| broker-flow H1-H5 | floorsheet Parquet via src/backtest/broker_flow_features.py | 2014 | after the session close | files end 2025-01-19 (VM backfill not merged); Merolagani terms flagged | tests/test_broker_flow_leakage.py |
| floorsheet files | Merolagani floorsheet | 2014 | after the session close | 2025 has 13 files; 2025-01-20 to 2026-08-30 hole | tests/test_holdout_guard.py (file listing refuses holdout dates) |

Rows per year (research role, before 2025-09-30):

| Field | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| broker-flow H1-H5 | 8,801 | 12,959 | 18,819 | 19,697 | 25,796 | 29,002 | 27,492 | 41,090 | 43,467 | 46,894 | 53,235 | 2,926 |
| floorsheet files | 136 | 199 | 220 | 210 | 239 | 243 | 184 | 240 | 259 | 236 | 232 | 13 |

### fundamentals

| Field | Source | First year with rows | Point-in-time rule | Known gaps | Leak test |
|---|---|---|---|---|---|
| quarterly report publication + net profit headline | Sharesansar announcements (Merolagani index for dates) | 2011 | earliest item-verified source date, next session (docs/POINT_IN_TIME.md) | headline rounded to 2-3 significant digits; group vs standalone basis varies | tests/test_knowledge_time.py, src/ranker/leak_audit.py |
| report images for OCR | Sharesansar announcement attachments | 2014 | announcement date (image upload timestamp kept) | collection running newest first; 2014-2022 only a sample so far; images only | tests/test_archive_holdout.py |
| EPS, net worth, reserves, NPL, CAR from reports | OCR of report images | none yet | announcement date of the report | no field passes the 90% accuracy gate; all values flagged (docs/OCR_COMPARISON.md) | tests/test_archive_holdout.py |

Rows per year (research role, before 2025-09-30):

| Field | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| quarterly report publication + net profit headline | 755 | 889 | 759 | 697 | 732 | 528 | 785 | 829 | 863 | 920 | 990 | 739 |
| report images for OCR | 36 | 29 | 35 | 36 | 603 | 815 | 801 | 849 | 882 | 934 | 1,002 | 749 |
| EPS, net worth, reserves, NPL, CAR from reports | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### corporate_events

| Field | Source | First year with rows | Point-in-time rule | Known gaps | Leak test |
|---|---|---|---|---|---|
| dividend declarations (Sharesansar table) | Sharesansar dividend table | 2018 | announcement_date; 8.3% are after their own book close (late, not early) | starts 2018 | tests/test_holdout_guard.py |
| dividend proposals from AGM records | Sharesansar AGM table + AGM announcements | 2011 | earlier of AGM announcement and book close (dividend_proposals_pit) | only AGMs whose agenda states percentages | tests/test_archive_holdout.py |
| AGM announcements | Sharesansar company announcements | 1 | announcement date | 9 symbols have no Sharesansar page | tests/test_archive_holdout.py |
| right share announcements | Sharesansar company announcements | 2011 | announcement date | ratio parsed only when in the title | tests/test_archive_holdout.py |
| book close / ex dates | corporate_actions | 2011 | action date is an event date, not a knowledge date | announcement timing comes from announcements | tests/test_holdout_guard.py |

Rows per year (research role, before 2025-09-30):

| Field | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dividend declarations (Sharesansar table) | 0 | 0 | 0 | 0 | 82 | 115 | 153 | 158 | 111 | 118 | 106 | 47 |
| dividend proposals from AGM records | 140 | 123 | 157 | 129 | 118 | 140 | 83 | 140 | 101 | 126 | 105 | 34 |
| AGM announcements | 290 | 253 | 276 | 262 | 235 | 301 | 242 | 352 | 268 | 370 | 356 | 170 |
| right share announcements | 134 | 162 | 274 | 544 | 248 | 107 | 60 | 60 | 102 | 91 | 110 | 250 |
| book close / ex dates | 211 | 208 | 301 | 287 | 217 | 266 | 172 | 306 | 170 | 236 | 201 | 111 |

### news

| Field | Source | First year with rows | Point-in-time rule | Known gaps | Leak test |
|---|---|---|---|---|---|
| Sharesansar news archive | Sharesansar news (category latest) | 2016 | published minute (Nepal time), next session | collection running backward from 2025-09-30; only 2025 so far | tests/test_archive_holdout.py |
| MeroLagani news archive (mostly Nepali) | MeroLagani NewsDetail pages (owner agreement) | 2025 | published minute or day, next session | collection running backward from ID 119,999; Nepali company names are not mapped to symbols (no alias source) | tests/test_archive_holdout.py |
| symbol mentions | company names and tickers | 2016 | article publication time | name aliases only for distinctive names | tests/test_archive_parsers.py |
| live text capture | live collectors since 2026 | none yet | first_seen_at | all rows are holdout-era | tests/test_holdout_guard.py |

Rows per year (research role, before 2025-09-30):

| Field | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sharesansar news archive | 0 | 0 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3,975 | 3,066 |
| MeroLagani news archive (mostly Nepali) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1,936 |
| symbol mentions | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2,114 | 1,933 |
| live text capture | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### market_state

| Field | Source | First year with rows | Point-in-time rule | Known gaps | Leak test |
|---|---|---|---|---|---|
| NEPSE index | Sharesansar/Merolagani index history | 2014 | session close | none known | tests/test_index_session_dates.py |
| turnover, breadth series | daily_prices | 2014 | session close | none known | tests/test_archive_holdout.py |
| NRB policy rates, CRR, SLR, CD/CCD, margin rules | NRB monetary policy documents | 2014 | announcement date: delivery date stated in the document for 2014/15-2019/20, NRB upload date from 2020/21 | mid-year reviews before 2021 not recorded | tests/test_archive_holdout.py |

Rows per year (research role, before 2025-09-30):

| Field | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NEPSE index | 153 | 217 | 232 | 230 | 240 | 245 | 184 | 239 | 242 | 227 | 232 | 171 |
| turnover, breadth series | 134 | 217 | 232 | 230 | 240 | 245 | 184 | 239 | 242 | 227 | 232 | 171 |
| NRB policy rates, CRR, SLR, CD/CCD, margin rules | 1 | 1 | 2 | 3 | 4 | 6 | 7 | 6 | 7 | 3 | 2 | 4 |

### historical_sentiment

| Field | Source | First year with rows | Point-in-time rule | Known gaps | Leak test |
|---|---|---|---|---|---|
| NRB macro (T-bill, rates, margin loan growth) | NRB Current Macroeconomic and Financial Situation | 2013 | NRB listing upload date | no demat-account counts; 2024-2025 reports use new wording and gave no values; margin lending only as growth | tests/test_archive_parsers.py |
| margin lending outstanding (BFIs aggregate) | NRB monthly Banking & Financial Statistics | 2011 | NRB listing upload date (pre-2020 months carry batch upload dates, late but safe) | 3 months excluded after a neighbour check; PDF rule validated 11/11 against workbooks | tests/test_archive_holdout.py |
| demat accounts total | SEBON Quarterly Securities Market Indicators | 2017 | SEBON upload date; revised figures kept as separate vintages | only 2017-07 to 2019-10; nothing official found for other years | tests/test_archive_holdout.py |
| IPO/right oversubscription | Sharesansar news headlines | 2016 | headline publication time | grows with the news archive | tests/test_archive_parsers.py |

Rows per year (research role, before 2025-09-30):

| Field | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NRB macro (T-bill, rates, margin loan growth) | 33 | 20 | 11 | 27 | 26 | 52 | 45 | 53 | 49 | 55 | 66 | 39 |
| margin lending outstanding (BFIs aggregate) | 13 | 11 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 |
| demat accounts total | 0 | 0 | 0 | 2 | 4 | 6 | 0 | 0 | 0 | 0 | 0 | 0 |
| IPO/right oversubscription | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 22 |

## What each pillar can contribute, by period

Learning = 2014-06 to 2019-12 (calls must end by 2019-12-31), check = 2020, exam = 2021 to 2025-09-29.

| Pillar | Learning (2014-2019) | Check (2020) | Exam (2021-2025) |
|---|---|---|---|
| technical | **Yes.** Closes from 2014. Opens, highs and lows before 2018-02-18 from verified floorsheet-derived bars | **Yes** | **Yes** |
| volume | **Yes** | **Yes** | **Yes** |
| floorsheet_broker | **Yes** (H1-H5 from 2014) | **Yes** | **2021-2024 yes**; 2025 only to 01-19 until the VM backfill is merged |
| fundamentals | **Report dates and the net-profit headline only.** No EPS, net worth, reserves, NPL or CAR field has reached 99% measured precision. Text-layer PDFs exist for 10-34 reports a year | **Same** (82 text-PDF reports) | **Same** (123-250 text-PDF reports a year). No field is used until the owner's 120 labels show at least 99% precision |
| corporate_events | **Yes** (AGM, rights, distributions, point-in-time dividend proposals 115-157 a year) | **Yes** | **Yes** |
| news | **Not yet.** The archives have reached 2024-04 (Sharesansar) and 2025 (MeroLagani) going backward | **Not yet** | **2024-2025 only** |
| market_state | **Yes.** Index, breadth, NRB policy events with document-stated dates from 2014/15 | **Yes** | **Yes** |
| historical_sentiment | **Partly.** NRB rates and margin-loan growth; margin-lending amounts monthly from 2011 (release dates late before 2020); demat totals only from 2017-07; IPO oversubscription only where news exists | **Partly** (no demat after 2019) | **Partly** (macro and margin yes; demat no; oversubscription 2024-2025) |

## Phase 2 completion

| Item | Bar | Status | Evidence |
|---|---|---|---|
| 1. Clock-dependent tests | Freeze time; full suite passes | **Met** | `tests/test_league.py`, `tests/test_scorecard_daily.py` frozen at 2026-09-30 13:00 NPT; suite 723 passed |
| 2a. Text-based sources first | Extract text PDFs without OCR; report coverage | **Met (coverage low before 2020)** | 2,043 text-layer PDFs from 73 company sites; 635 distinct reports matched to dated announcements (2014: 10 … 2024: 217). Portals (NEPSE 401, SEBON, MeroLagani, Sharesansar) offer images only. `docs/company_report_pdfs.json` |
| 2b. Labeling tool and 120 reports | Tool, stratified sample, exact steps | **Tool and set met; labels pending (owner)** | `src/archive/label_tool.py`, `docs/labels/label_set.json` (10 per year; 22 annual; 22 English and 98 Nepali or mixed; 5 sector groups), `docs/labels/README.md` |
| 2c. OCR consensus | Accept a value only when at least two engines agree within rounding | **Implemented; engine runs partial** | `fundamentals_quality.consensus` (tested). Tesseract done on the 120; PaddleOCR mobile and Surya running locally (Surya likely needs the GPU run) |
| 2d. Accounting checks | EPS/profit/shares, book value/net worth/shares, cross-quarter, headline, ranges | **Implemented; cross-quarter check not yet fed with data** | `fundamentals_quality.checks` (tested). The cross-quarter check needs the next report's previous-quarter column, which is not extracted yet |
| 2e. Precision and coverage with CIs; use only fields at 99% or more | Per method and combination, Wilson 95% | **Not met: waits for the owner's labels** | `python -m src.archive.fundamentals_quality` writes `docs/fundamentals_precision.json`. Until then no fundamental field is used. Even 120/120 gives a lower bound of about 97% |
| 2f. GPU run plan | Instance, quota steps, cost, scripts; nothing launched | **Met** | `docs/GPU_RUN_PLAN.md`, `scripts/gpu/` (g6.xlarge, about 29 h, about $26 on demand) |
| 3. News back to 2014 | Keep collecting; coverage and mapping; add MeroLagani | **Not met (collecting)** | Sharesansar: 13,149 articles, reached 2024-04 (2024: 28.5% mapped, 2025: 30.7%). MeroLagani: 1,977 articles in 2025 so far (8.9% mapped; Nepali names cannot be mapped without an alias source) |
| 4. Floorsheet | Steps for the unreachable VM; pull, merge, audit, extend OHLC to 2025-09-29 | **Steps met; execution blocked (VM unreachable)** | `docs/FLOORSHEET_COMPLETION.md` (security-group update, pull, merge, audit); `floorsheet_ohlc build --end 2025-09-29` added |
| 5. Remaining gaps | Close or record what cannot be found; never guess | **Partly met** | Closed: NRB policy dates 2017/18 and 2019/20 (from the documents; 2015/16 corrected); NRB macro 2024-2025; margin-lending amounts monthly 2011-2025; NEPSE price band, six-day week and hours events. Recorded as not found: 9 sectorless equities; demat totals outside 2017-07 to 2019-10; pre-open start date, 2014-2021 band changes, T+2 start |
| 6. Data dictionary | Final coverage and completion table | **Met** | This document |
