# Data Readiness Audit

Audited 2026-09-30 against the local PostgreSQL 17 database `arthasignal` (49 tables), using read-only queries through the `api_readonly` role. Nothing was written.

**Verdict.** The data is not ready for modelling. Four problems need fixing first:

1. The daily pipeline deletes floorsheet rows older than 30 days, so broker history is being destroyed as it is collected.
2. Since 2026-08-21 no stock prices exist for any Sunday, and prices exist on three Fridays. Session dates after that point cannot be trusted.
3. Local price history starts on 2021-07-25, not 2014.
4. Delisted companies are almost entirely missing: 32 of 184 have any price.

## 1. Floorsheet (`intraday_floorsheet`)

### What is there

| Measure | Value |
| --- | --- |
| Rows | 981,397 |
| Earliest date | 2026-08-31 |
| Latest date | 2026-09-29 |
| Days with data | 18 |
| Distinct symbols | 421 |
| Rows with a missing broker id | 0 |
| Rows per day | min 39,673; median 51,495; mean 54,522; max 78,869 |

Dates are `snapshot_time` converted to Asia/Kathmandu. Every row on a given day carries the same timestamp, 15:00:00. The table holds end-of-day contract lists, not timed trades, so intraday order-flow features cannot be built from it.

### Rows per day and coverage against `daily_prices`

| Date | Day | Floorsheet rows | Floorsheet symbols | `daily_prices` symbols | Symbol coverage | Amount vs turnover |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-08-31 | Mon | 68,137 | 347 | 347 | 100% | 100% |
| 2026-09-01 | Tue | 48,341 | 341 | 341 | 100% | 100% |
| 2026-09-02 | Wed | 39,673 | 354 | 354 | 100% | 100% |
| 2026-09-03 | Thu | 40,692 | 346 | 346 | 100% | 100% |
| 2026-09-07 | Mon | 50,844 | 352 | 352 | 100% | 100% |
| 2026-09-09 | Wed | 50,804 | 352 | 352 | 100% | 100% |
| 2026-09-10 | Thu | 44,198 | 341 | 341 | 100% | 100% |
| 2026-09-11 | Fri | 49,006 | 345 | 345 | 100% | 100% |
| 2026-09-14 | Mon | 49,074 | 336 | 336 | 100% | 100% |
| 2026-09-15 | Tue | 78,869 | 347 | 347 | 100% | 100% |
| 2026-09-16 | Wed | 55,831 | 350 | 350 | 100% | 100% |
| 2026-09-17 | Thu | 51,728 | 339 | 339 | 100% | 100% |
| 2026-09-18 | Fri | 64,072 | 345 | 345 | 100% | 100% |
| 2026-09-22 | Tue | 70,691 | 356 | 356 | 100% | 100% |
| 2026-09-23 | Wed | 57,947 | 352 | 352 | 100% | 100% |
| 2026-09-24 | Thu | 51,716 | 356 | 356 | 100% | 100% |
| 2026-09-28 | Mon | 58,500 | 357 | 357 | 100% | 100% |
| 2026-09-29 | Tue | 51,274 | 363 | 363 | 100% | 100% |

On every captured day the floorsheet covers exactly the symbols in `daily_prices`, and summed contract amount equals recorded turnover. Where a day is captured, it is complete.

### Trading days with zero floorsheet rows

| Scope | Trading days | With floorsheet | With zero rows |
| --- | ---: | ---: | ---: |
| Whole `trading_calendar` (2021-07-23 to 2026-09-29) | 1,388 | 18 | 1,370 |
| Inside the captured window (2026-08-31 to 2026-09-29) | 24 | 18 | 6 |

The six missing days inside the window are 2026-09-06, 09-08, 09-13, 09-20, 09-21 and 09-27. None of them has `daily_prices` rows either, so this is a whole-day capture gap, not a floorsheet-only gap (see section 2).

### Why the history is only 30 days

`src/pipeline/cleanup_intraday_tables.py:15-20` sets `RETENTION_DAYS = 30` and lists `intraday_floorsheet` among the tables to prune. `run_all_daily` calls it every day. The earliest floorsheet date, 2026-08-31, is exactly 30 days before this audit. Until that rule changes, the table can never hold more than about 20 trading days.

### Gap against a full history from 2014

The local calendar only starts on 2021-07-23, so trading days from 2014 cannot be counted here. The figures below for 2014 to mid-2021 are an estimate.

| Period | Trading days | Basis |
| --- | ---: | --- |
| 2021-07-25 to 2026-09-29 | 1,201 | Counted: distinct dates in `daily_prices` |
| 2014-01-01 to 2021-07-24 | about 1,750 | Estimated at 231 days a year, the local average |
| Total since 2014 | about 2,950 | |
| Days with floorsheet here | 18 | Counted |
| **Gap** | **about 2,930 days, roughly 99.4%** | |

At the observed median of 51,495 rows a day, a full history would be on the order of 150 million rows; the table holds under 1 million. The full Parquet backfill is assumed to live on another machine and was not looked for.

## 2. Last row date for every table

Daily capture did **not** stop on 2026-08-20. Prices, index, technical signals and signal calls all run to 2026-09-29. What changed after 2026-08-20 is the quality of the dates.

### Market and reference data

| Table | Rows | First | Last |
| --- | ---: | --- | --- |
| `daily_prices` | 271,863 | 2021-07-25 | 2026-09-29 |
| `market_index` | 20,376 | 2021-07-25 | 2026-09-29 |
| `technical_signals` | 337,960 | 2021-07-25 | 2026-09-29 |
| `trading_calendar` | 1,895 | 2021-07-23 | 2026-09-29 |
| `intraday_floorsheet` | 981,397 | 2026-08-31 | 2026-09-29 |
| `intraday_snapshots` | 0 | none | none |
| `intraday_index_snapshots` | 0 | none | none |
| `corporate_actions` | 2,189 | 2011-09-29 | 2026-07-03 |
| `fundamentals` | 2,525 | 2026-07-23 | 2026-09-26 |
| `promoter_holding` | 1,958 | 2014-08-03 | 2026-09-24 |
| `symbol_history` | 156 | 2012-02-13 | 2026-05-01 |
| `ipo_calendar` | 1,077 | updated 2026-07-31 | updated 2026-09-26 |
| `companies` | 644 | `listed_date` is empty for every row | |
| `brokers` | 92 | no date column | |
| `sector_index_mapping` | 12 | no date column | |
| `short_term_interest_rates` | 101 | fiscal year 2073/2074 | 2082/2083 |
| `remittance` | 120 | fiscal year 2073/2074 | 2082/2083 |
| `gdp_nepse` | 10 | yearly rows | |

### Derived and backtest tables

| Table | Rows | Last computed |
| --- | ---: | --- |
| `symbol_liquidity_tier` | 278 | 2026-09-29 |
| `sector_fundamental_baseline` | 12 | 2026-09-26 |
| `transaction_cost_adjusted_returns` | 36 | 2026-08-01 |
| `market_pulse_backtest_results` | 27 | 2026-07-30 |
| `liquidity_stratified_backtest_results` | 90 | 2026-07-29 |
| `mtf_agreement_backtest_results` | 36 | 2026-07-29 |
| `volume_confirmed_backtest_results` | 108 | 2026-07-24 |
| `backtest_results` | 81 | 2026-07-23 |
| `confluence_backtest_results` | 108 | 2026-07-23 |
| `signal_confidence`, `signal_regime_stability` | 26 each | no date column |
| `confluence_confidence`, `volume_conditional_tier` | 6, 22 | no date column |
| `system_notes` | 1 | 2026-07-29 |

The rule-based signal tiers shown to users rest on backtests last computed in July 2026.

### Application tables

| Table | Rows | Last |
| --- | ---: | --- |
| `users` | 17 | 2026-09-30 (includes test accounts created by the test suite) |
| `holdings` | 12 | 2026-08-04 |
| `password_reset_tokens` | 3 | expires 2026-09-30 |
| `watchlists`, `price_alerts`, `signal_alerts`, `refresh_sessions`, `user_auth_state` | 0 | none |

### Date problems after 2026-08-20

Distinct price dates by weekday, before and after 2026-08-21:

| Weekday | Price days before 2026-08-21 | Price days from 2026-08-21 |
| --- | ---: | ---: |
| Sunday | 209 | **0** |
| Monday | 226 | 5 |
| Tuesday | 242 | 5 |
| Wednesday | 241 | 5 |
| Thursday | 228 | 5 |
| Friday | 32 | **3** |

- **No Sunday has stock prices since 2026-08-21.** Six Sundays in the period (08-23, 08-30, 09-06, 09-13, 09-20, 09-27) are marked as trading days and have sector-index rows, but no price rows.
- **Three Fridays have prices** (08-21, 09-11, 09-18). Two of them (09-11, 09-18) have no index rows at all.
- **Two weekdays are missing** (Tue 09-08 and Mon 09-21) although the calendar marks them as trading days and sector indices exist.
- **Symbols per day jumped** from about 280 to about 345 on 2026-08-21, which suggests the price rows started coming from a different source on that date.
- **The headline NEPSE Index is incomplete.** It has 1,181 rows against 1,204 for each sector index, and ends on 2026-09-28. On many recent days only the 13 sector indices were stored.

Whether the Friday rows are real sessions or Sunday sessions stamped with the wrong date cannot be determined from the database alone. It needs a check against an external source for one known day.

### Calendar reliability

`trading_calendar` marks 1,388 trading days; only 1,201 have prices. That leaves 187 "trading days" with no price rows:

| Year | Calendar trading days | With prices |
| --- | ---: | ---: |
| 2021 | 115 | 102 |
| 2022 | 275 | 242 |
| 2023 | 261 | 227 |
| 2024 | 262 | 232 |
| 2025 | 261 | 225 |
| 2026 | 214 | 173 |

The calendar marks almost every Sunday to Thursday as a trading day (271 of each weekday), so public holidays are largely unmarked. Anything that counts "20 trading sessions" from this calendar is counting sessions that did not happen.

## 3. Signal calls and quant ledgers

### `signal_calls`

| Status | Outcome | Calls | Entry dates |
| --- | --- | ---: | --- |
| RESOLVED | WIN | 781 | 2026-03-01 to 2026-09-03 |
| RESOLVED | LOSS | 560 | 2026-03-01 to 2026-09-03 |
| PENDING | | 169 | 2026-09-07 to 2026-09-28 |
| VOID | VOID | 2 | 2026-08-21 to 2026-08-24 |
| **Total** | | **1,512** | 2026-03-01 to 2026-09-28 |

Last row created 2026-09-29. Within the v1 protocol scope (entry on or after 2026-08-20) there are 693 calls and each signal has exactly one logic fingerprint.

Output of `python -m src.pipeline.validation_status` (protocol `2026-08-20-v1`, 50 bps cost):

| Signal | Graded / needed | Entry days / needed | Net return | After-fee win rate | Void | Pending | Gate |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| `rsi_14 < 30 (oversold)` | 148 / 60 | 10 / 20 | +0.946% | 65.3% | 1 | 44 | collecting |
| `close < bollinger_lower` | 292 / 100 | 10 / 20 | −0.244% | 62.7% | 1 | 28 | collecting |
| `rsi_14 > 70 (overbought)` | 10 / 60 | 8 / 20 | −2.612% | 43.8% | 0 | 41 | collecting |
| `doji` | 72 / 60 | 10 / 20 | +2.347% | 69.6% | 0 | 56 | collecting |

Overall status: `collecting`. Three signals have enough calls but only 10 of the 20 required independent entry days.

**These numbers are provisional.** Calls entered on 2026-08-20 resolved on 2026-09-15, which is 16 sessions with prices, not 20. The horizon is counted on the calendar, which includes the missing Sundays. The date problems in section 2 must be fixed before this evidence is trusted.

### Quant ledgers

| Table | Rows | Date range | Resolved / pending |
| --- | ---: | --- | --- |
| `quant_shadow_signals` | 0 | none | none |
| `quant_model_snapshots` | 0 | none | none |
| `quant_robustness_runs` | 0 | none | none |
| `quant_v41_model_snapshots` | 0 | none | none |
| `quant_v41_shadow_signals` | 0 | none | none |
| `quant_v41_shadow_runs` | 0 | none | none |
| `quant_e1_forward_runs` | 0 | none | none |
| `quant_e1_forward_decisions` | 0 | none | none |

There is no trained model snapshot and no forward quant observation in this database. The V1 model described in the docs as the live champion does not exist here, and neither does the frozen V4.1 artifact.

The archived research results were also produced on a different, longer dataset: their test folds start in 2018 and cite 4,579 market-index rows, while this database starts on 2021-07-25 and has 1,181 NEPSE Index rows.

## 4. Quant features and floorsheet broker data

**None.** No quant feature uses floorsheet or broker data.

- The V1 feature set (`src/services/quant_features.py:15-26`) has ten inputs: 5, 20 and 60 day returns, 20 day volatility, 60 day drawdown, turnover ratio, volume ratio, distance from the 50 day average, and relative strength against the market and the sector. All come from `daily_prices` and `market_index`.
- The archived V4, V4.1 and V5 features add baseline scores, market and sector regime measures, and liquidity and stability measures. Again prices, turnover and index only.
- A search for "floorsheet" or "broker" across `src/services/quant_*.py`, the quant pipelines and the whole archive returns nothing.

Floorsheet data is used in exactly one place: `src/pipeline/market_pulse.py`, for the sector broker-concentration display.

## 5. Data still missing for prediction

| Data | What exists here | What is missing |
| --- | --- | --- |
| News | Nothing. No table, no scraper | Any news text or headline history |
| NEPSE and SEBON notices | Nothing. No table, no scraper | Company announcements, trading halts, regulatory notices, book-closure notices as dated events |
| NRB policy | Monthly treasury-bill and interbank rates (101 months), remittance (120 rows), yearly GDP to market-cap (10 rows), all keyed by fiscal year and month name, not by date | Monetary policy statements, policy-rate changes, margin-lending and loan-to-value rules, reserve requirements, publication dates for any series |
| Quarterly report history | Nothing | Income statement, balance sheet, net profit, NPL, capital adequacy, by quarter, with publication dates |
| Fundamentals history | 2,525 rows for 281 symbols, but only 10 scrape dates from 2026-07-23 to 2026-09-26. Five fields: EPS, P/E, P/B, book value, market cap | Any value before July 2026. Fiscal-year labels are unreliable: 2,109 rows `2082/2083`, 272 `unknown`, 141 `2026/2027` |
| Floorsheet history | 18 days | About 2,930 trading days since 2014 (section 1) |
| Price history before mid-2021 | Nothing before 2021-07-25 | About seven and a half years back to 2014 |
| Delisted companies | 184 delisted equities listed; 32 have any price. 25 suspended; 3 have prices | Price history for 152 delisted and 22 suspended equities |
| Listing dates | `companies.listed_date` empty for all 644 rows | Needed to know when a symbol entered the universe |
| Adjusted prices | `adjusted_close` missing on 63,896 of 271,863 price rows (23.5%) | Consistent corporate-action adjustment |
| Intraday | Both intraday snapshot tables are empty | Any intraday price path |
| Trading calendar | Holidays largely unmarked; starts 2021-07-23 | An accurate session calendar back to 2014 |

Available and usable today: corporate actions from 2011 (958 bonus, 154 rights, 1,077 dividend), promoter holdings from 2014 (285 symbols, 20 report dates), symbol change history from 2012, and the IPO calendar.

## What to fix first

1. Stop pruning `intraday_floorsheet`, or move it to its own daily table with no retention limit, before more history is lost.
2. Resolve the Sunday and Friday dating problem since 2026-08-21 and repair the affected days.
3. Rebuild the trading calendar from actual sessions and mark holidays.
4. Load price, index and floorsheet history back to 2014 from the other machine.
5. Backfill prices for delisted and suspended symbols so the universe is not survivor-only.
6. Re-grade signal calls once dates and the calendar are correct.
