# Data dictionary for the score pillars

- **What it covers:** every field each pillar of the simulation score (`docs/SIMULATION_PROTOCOL.md` section 2) can draw on. For each field: its source, the first year with rows, its point-in-time rule, the rows per year from 2014 to 2025, known gaps and the test that guards against look-ahead.
- **Coverage basis:** rows visible to the research role (dates before 2025-09-30), measured on 2026-10-05 by `python -m src.archive.data_dictionary`, which also writes `docs/data_dictionary_coverage.json`. Rerun it to refresh the numbers while the background collectors (news, report images, OCR) continue.
- **General rule** (`docs/POINT_IN_TIME.md`): information is usable from the first session strictly after the earliest moment it was observably public in a source whose date passes verification. Holdout rows (2025-09-30 onward) are stored but hidden from research by row-level security on every table listed below.

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
| report images for OCR | 6 | 6 | 5 | 6 | 6 | 6 | 6 | 6 | 6 | 327 | 996 | 743 |
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
| symbol mentions | company names and tickers | 2016 | article publication time | name aliases only for distinctive names | tests/test_archive_parsers.py |
| live text capture | live collectors since 2026 | none yet | first_seen_at | all rows are holdout-era | tests/test_holdout_guard.py |

Rows per year (research role, before 2025-09-30):

| Field | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Sharesansar news archive | 0 | 0 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2,504 |
| symbol mentions | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1,397 |
| live text capture | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### market_state

| Field | Source | First year with rows | Point-in-time rule | Known gaps | Leak test |
|---|---|---|---|---|---|
| NEPSE index | Sharesansar/Merolagani index history | 2014 | session close | none known | tests/test_index_session_dates.py |
| turnover, breadth series | daily_prices | 2014 | session close | none known | tests/test_archive_holdout.py |
| NRB policy rates, CRR, SLR, CD/CCD, margin rules | NRB monetary policy documents | 2014 | announcement date (pre-2020 dates from secondary sources, unconfirmed) | 2017/18 and 2019/20 announcement dates missing | tests/test_archive_holdout.py |

Rows per year (research role, before 2025-09-30):

| Field | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NEPSE index | 153 | 217 | 232 | 230 | 240 | 245 | 184 | 239 | 242 | 227 | 232 | 171 |
| turnover, breadth series | 134 | 217 | 232 | 230 | 240 | 245 | 184 | 239 | 242 | 227 | 232 | 171 |
| NRB policy rates, CRR, SLR, CD/CCD, margin rules | 1 | 1 | 2 | 0 | 4 | 0 | 6 | 6 | 6 | 3 | 2 | 4 |

### historical_sentiment

| Field | Source | First year with rows | Point-in-time rule | Known gaps | Leak test |
|---|---|---|---|---|---|
| NRB macro (T-bill, rates, margin loan growth) | NRB Current Macroeconomic and Financial Situation | 2013 | NRB listing upload date | no demat-account counts; 2024-2025 reports use new wording and gave no values; margin lending only as growth | tests/test_archive_parsers.py |
| IPO/right oversubscription | Sharesansar news headlines | 2016 | headline publication time | grows with the news archive | tests/test_archive_parsers.py |

Rows per year (research role, before 2025-09-30):

| Field | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| NRB macro (T-bill, rates, margin loan growth) | 33 | 18 | 11 | 21 | 26 | 51 | 43 | 50 | 40 | 24 | 0 | 0 |
| IPO/right oversubscription | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 22 |

## What each pillar can contribute, by period

Learning = 2014-06 to 2019-12 (calls must end by 2019-12-31), check = 2020, exam = 2021 to 2025-09-29.

| Pillar | Learning (2014-2019) | Check (2020) | Exam (2021-2025) |
|---|---|---|---|
| technical | **Yes.** Closes from 2014. Real opens, highs and lows only from 2018-02-18; before that from floorsheet-derived bars (board lots, verified, Merolagani-sourced) | **Yes** | **Yes**. Floorsheet-derived bars end 2025-01-19, but stored real OHLC covers the exam years |
| volume | **Yes** (volume, turnover from 2014) | **Yes** | **Yes** |
| floorsheet_broker | **Yes.** H1-H5 from 2014; 1-2% missing pages in 2015-2018 | **Yes** | **2021-2024 yes; 2025 only to 01-19.** The VM backfill is not merged, and the source terms are flagged |
| fundamentals | **Partly.** Report publication dates (Sharesansar and Merolagani) and the net-profit headline (rounded) from 2011, under the earliest-verified-date rule. **No** EPS, net worth, reserves, NPL or CAR history: OCR fails the 90% gate | **Partly**, same as learning | **Partly**, same. Report images for 2024-2025 are collected, but OCR values stay flagged |
| corporate_events | **Yes.** AGM announcements, right-share announcements, dividend distributions and corporate actions from 2014; dividend proposals point-in-time from AGM agendas (115-157 a year); `dividend_declarations` table only from 2018 | **Yes** | **Yes** |
| news | **Not yet.** The archive has only reached 2025 (collection continues backward); no 2014-2019 articles stored | **Not yet** | **Only 2025-03 to 2025-09** so far; 56% of articles map to a symbol |
| market_state | **Yes.** NEPSE index, turnover, breadth; NRB policy events with pre-2020 announcement dates unconfirmed (2017/18 missing) | **Yes** | **Yes** |
| historical_sentiment | **Partly.** NRB T-bill, base rate, deposit and lending rates, margin-loan growth, market cap to GDP (11-51 values a year); no demat counts; IPO oversubscription only once the news archive reaches these years | **Partly**, same | **Partly**: 2021-2023 macro yes, 2024-2025 macro wording not parsed; IPO oversubscription for 2025 only |

**Not usable at all yet:**
- live text capture (`text_items`, holdout-era only);
- `fundamentals` page snapshots and `quarterly_report_figures` (2026, holdout);
- demat/BOID account history;
- margin-lending amounts;
- NEPSE trading-rule notices (API answers 401).
