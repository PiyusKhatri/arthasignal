# Data Readiness Audit

Audited 2026-09-30 against the local PostgreSQL 17 database `arthasignal`, using read-only queries through the `api_readonly` role.

**Update, same day.** Three findings from the first pass have since been acted on; the affected sections are marked "Fixed" or "Corrected" and show before and after:

- Floorsheet pruning is removed (section 1).
- The first pass misread the Sunday and Friday pattern as a dating error. NEPSE moved to a Monday to Friday week in April 2026; the Friday sessions are real (section 2).
- The trading calendar now counts only days with real prices, and 1,046 signal calls were re-graded (sections 2 and 3).
- The index table is repaired and the daily refresh now stamps rows with the source session date (section 2).

**Verdict.** The data is still not ready for modelling. Open problems:

1. Floorsheet history is 18 days; nothing older survives locally.
2. One real session, 2026-07-27, has no price rows at all.
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

| Scope | Sessions | With floorsheet | With zero rows |
| --- | ---: | ---: | ---: |
| All sessions with prices (2021-07-25 to 2026-09-29) | 1,201 | 18 | 1,183 |
| Inside the captured window (2026-08-31 to 2026-09-29) | 18 | 18 | 0 |

Corrected: the first pass counted 24 trading days in the window and reported six missing. Those six (2026-09-06, 09-08, 09-13, 09-20, 09-21, 09-27) were four Sundays and two holidays, not sessions. Every real session in the window has a complete floorsheet.

### Why the history is only 30 days (fixed)

`src/pipeline/cleanup_intraday_tables.py` listed `intraday_floorsheet` among the tables pruned after 30 days, and `run_all_daily` calls it every day. The earliest floorsheet date, 2026-08-31, is exactly 30 days before this audit.

Fixed: the floorsheet is no longer in the retention list; only `intraday_snapshots` and `intraday_index_snapshots` are pruned. `tests/test_intraday_cleanup_retention.py` fails if it is ever added back. The local table still holds all 981,397 rows. Rows already deleted are not recoverable from this machine.

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

### Session dates (corrected)

The first pass read "no Sundays, three Fridays since 2026-08-21" as a dating error. It is not. Price days by weekday in 2026:

| Month | Sun | Mon | Tue | Wed | Thu | Fri |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-03 | 4 | 4 | 5 | 2 | 3 | 0 |
| 2026-04 | 1 | 4 | 3 | 5 | 5 | 3 |
| 2026-05 | 0 | 4 | 4 | 4 | 3 | 3 |
| 2026-06 | 0 | 5 | 5 | 4 | 4 | 4 |
| 2026-07 | 0 | 3 | 4 | 5 | 5 | 5 |
| 2026-08 | 0 | 5 | 4 | 4 | 4 | 3 |
| 2026-09 | 0 | 3 | 4 | 4 | 4 | 2 |

- The last Sunday session is 2026-04-05 and the first Friday session is 2026-04-10. The week has been Monday to Friday since 2026-04-06.
- **The Friday sessions are real.** There are 20 of them since the switch. Floorsheet contract numbers embed the session date (for example `2026091101000001` on Friday 2026-09-11 and `20260918...` on 2026-09-18), and the NEPSE Index moves continuously through every Friday from 2026-04-10 to 2026-07-17.
- A further 15 Friday sessions exist between 2022-05-20 and 2022-09-16.

### Calendar days marked trading with no prices, since 2026-04-01

There were 30. Each was classified from the price and index tables. The test for a weekday is index continuity: if the first index row after the gap satisfies `close − points_change = last stored close before the gap`, no session happened in between.

| Verdict | Days | Dates | Evidence |
| --- | ---: | --- | --- |
| Calendar bug (Sunday after the move to Monday to Friday) | 25 | Every Sunday from 2026-04-12 to 2026-09-27 | No prices on any Sunday after 2026-04-05 |
| Genuine holiday | 2 | 2026-04-14 (Tue), 2026-05-28 (Thu) | All 17 index series continuous across the gap |
| Genuine holiday | 2 | 2026-09-08 (Tue), 2026-09-21 (Mon) | The 13 sector rows stored that day are unchanged copies of the previous session, and the next session continues from them |
| Missing price scrape | 1 | 2026-07-27 (Mon) | The public index history shows a session that day: NEPSE closed at 2,701.32, down 33.28 points. The local database had neither prices nor index rows for it |

Five Fridays without prices (2026-05-01, 05-29, 08-28, 09-04, 09-25) were labelled "Weekend". By the same continuity test they are genuine holidays.

Across the whole history, 155 of the 187 no-price "trading days" are holidays by NEPSE Index continuity, 25 are the post-switch Sundays, and 7 fail a strict 0.05-point test. Five of those seven are rounding in `points_change` (under half an index point), 2026-09-21 is confirmed by the sector indices, and the seventh is 2026-07-27.

### Why the calendar was wrong

1. A day counted as a trading day if either `daily_prices` or `market_index` had a row. Since 2026-08 the daily index refresh stamps rows with the run date, so Sundays and holidays acquired index rows and looked like sessions.
2. Weekend weekdays were derived from a five-year average. Sunday traded for most of those five years and Friday did not, so the calendar kept treating Sunday as a trading day and Friday as a weekend after the April 2026 switch.
3. Any other weekday without data defaulted to "trading", so holidays were never recorded.

### Calendar fix, before and after

The calendar now marks a day as trading only if `daily_prices` has rows for it. A new column, `is_known_holiday`, is set when a weekday has no prices and the index confirms no session. Weekends are inferred from the four surrounding weeks, so a change of trading week is picked up. Days after the latest session still default to "expected trading" so ingestion is not skipped.

| Measure | Before | After |
| --- | ---: | ---: |
| Calendar rows | 1,895 | 1,895 |
| Marked trading | 1,388 | 1,201 |
| Marked trading with no prices | 187 | 0 |
| Price days not marked trading | 0 | 0 |
| Known holidays (`is_known_holiday`) | column did not exist | 167 |
| Weekend | 507 | 526 |
| Unexplained non-trading | 0 | 1 (2026-07-27, a real session with no local prices) |

Session arithmetic for signal calls no longer reads the calendar at all. It counts distinct dates in `daily_prices`.

### The index table (fixed)

What was wrong:

- `src/pipeline/backfill_daily_index.py` wrote `date = today`, the run date. Nine non-session days acquired sector-index rows: seven Sundays and two holidays.
- Three of those days held a real session's values under the wrong date. Fridays 2026-07-31, 2026-09-11 and 2026-09-18 were stored under the following Sunday.
- The four broad indices (NEPSE, Sensitive, Float, Sensitive Float) were missing on 20 price sessions, all since 2026-07-23.
- Rows written by the daily refresh had `open = high = low = close`.

The refresh fix: both sources state the session date (`generatedTime` from the NEPSE API, "As of" on the sub-indices block), and rows are now stamped with that. A row with no source date is refused and counted as a failure; it is never given the run date.

The repair used the public index history (`sharesansar.com/index-history-data`, already wired in as `get_index_history`), which carries session dates and full open, high, low and close. `python -m src.pipeline.repair_index_dates --from 2026-07-01` prints the plan; `--apply` executes it.

| Action | Rows | Detail |
| --- | ---: | --- |
| Moved to the correct session | 39 | 13 series each: 08-02 to 07-31, 09-13 to 09-11, 09-20 to 09-18 |
| Deleted as exact duplicates of an existing session row | 78 | 13 series each on 08-23, 08-30, 09-06, 09-08, 09-21, 09-27 |
| Unexplained rows | 0 | Every mis-dated row matched a real session's close |
| Inserted from the public history | 136 | 21 sessions for each of the 4 broad indices, 4 sessions for each of the 13 sector series |
| Open, high and low corrected | 342 | Closes already matched the source on every one |

| Measure | Before | After |
| --- | ---: | ---: |
| `market_index` rows | 20,376 | 20,434 |
| Rows on dates without prices | 117 | 17 (all on 2026-07-27, a real session) |
| Price sessions lacking the NEPSE Index | 20 | 0 |
| Price sessions lacking sector rows | 6 | 0 |
| Rows with `open = high = low = close` | 420 | 0 |
| Rows per series | 1,181 or 1,204 | 1,202 for all 17 |
| NEPSE Index continuity breaks since 2026-07-01 | 7 | 0 |

A second dry run after applying plans no changes. The NEPSE Index values were fetched from the public source, not estimated.

Still true: since 2026-08-21 `daily_prices` also carries about 45 mutual funds and 33 debentures a day, which is why the daily symbol count rose from about 280 to about 345.

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

**The tables above are the state before the fix and are wrong.** Calls signalled on 2026-08-20 resolved on 2026-09-15, which is 16 sessions with prices, not 20, because the horizon was counted on a calendar that included Sundays and holidays.

### Re-grade after the calendar fix

`python -m src.pipeline.regrade_signal_calls` checks every graded call against a horizon counted in real sessions, resets the miscounted ones and grades them again.

| Measure | Value |
| --- | ---: |
| Graded calls checked | 1,343 |
| Miscounted (resolved before their true target, or void) | 1,046 |
| Re-resolved now | 703 |
| Back to pending (true target not reached yet) | 343 |
| Miscounted after the re-grade | 0 |

| Status | Before | After |
| --- | ---: | ---: |
| RESOLVED / WIN | 781 | 405 |
| RESOLVED / LOSS | 560 | 595 |
| PENDING | 169 | 512 |
| VOID | 2 | 0 |

What happened to the 1,046 miscounted calls:

| Was | Now | Calls |
| --- | --- | ---: |
| WIN | WIN | 363 |
| WIN | LOSS | 114 |
| WIN | pending | 278 |
| LOSS | LOSS | 210 |
| LOSS | WIN | 16 |
| LOSS | pending | 63 |
| VOID | pending | 2 |

Calls signalled on 2026-08-20 now resolve on 2026-09-23, exactly 20 sessions later.

`validation_status` after the re-grade (protocol `2026-08-20-v1`, 50 bps cost):

| Signal | Graded / needed | Entry days / needed | Net return | After-fee win rate | Pending | Gate |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `rsi_14 < 30 (oversold)` | 42 / 60 | 4 / 20 | −3.987% | 29.6% | 151 | collecting |
| `close < bollinger_lower` | 100 / 100 | 4 / 20 | −6.707% | 23.0% | 221 | collecting |
| `rsi_14 > 70 (overbought)` | 5 / 60 | 3 / 20 | +0.913% | 83.3% | 46 | collecting |
| `doji` | 34 / 60 | 4 / 20 | +1.889% | 65.7% | 94 | collecting |

Only four entry days have resolved, so these figures are early. The earlier, favourable numbers for the two oversold signals came from grading too soon.

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
| Trading calendar | Rebuilt from real sessions with 167 known holidays; starts 2021-07-23 | A session calendar back to 2014, which follows from the price history |

Available and usable today: corporate actions from 2011 (958 bonus, 154 rights, 1,077 dividend), promoter holdings from 2014 (285 symbols, 20 report dates), symbol change history from 2012, and the IPO calendar.

## 6. What to request from the second machine

A teammate's database holds a longer history (prices back to somewhere between 2005 and 2018). To be usable here, the export needs the following.

| Item | What to provide | Why |
| --- | --- | --- |
| `daily_prices` | Every row, all symbols, full date range, including the overlap from 2021-07-25 | Overlap lets us reconcile the two sources before merging |
| Price basis | Whether `open`/`high`/`low`/`close` are raw or adjusted, and how `adjusted_close` was computed | 23.5% of local rows have no adjusted close |
| `market_index` | All 17 series, with `date` equal to the session date | Local index rows are unreliable after 2026-07-23 |
| `companies` | Every symbol including delisted, suspended and merged, with status, sector, listing date and delisting date | 152 delisted equities have no local prices; every local `listed_date` is empty |
| `symbol_history`, `corporate_actions` | Full tables, with ex-dates | Needed to follow symbols through mergers and to void or adjust labels |
| Floorsheet | The Parquet backfill: contract number, symbol, buyer and seller broker, quantity, rate, amount, session date | Local history is 18 days |
| `brokers` | Broker ids and names as of each period, if they changed | To interpret floorsheet broker ids |
| Quant tables | `quant_model_snapshots`, `quant_shadow_signals` and the V4.1 and E1 ledgers | All are empty locally; this is the only forward evidence that may exist |
| Fundamentals | Any history before 2026-07-23, with the date each figure was published | Local fundamentals are ten weekly snapshots |

Ask for a manifest alongside the data:

- first date, last date and row count per table, and sessions per year for prices;
- the source of each table (NEPSE API, Sharesansar, Merolagani, other) and when it was scraped;
- the git commit of the schema the export came from;
- whether dates are Nepal-time session dates;
- file checksums.

A `pg_dump` in custom format of those tables is the simplest form. Parquet or CSV with the manifest also works.

Two checks to run on arrival: closes for a sample of symbols in the overlap period must match the local values, and price dates must respect the trading week (Sunday to Thursday before 2026-04-06, Monday to Friday after, with the 2022 Friday sessions as the known exception).

## What to fix first

1. ~~Stop pruning `intraday_floorsheet`.~~ Done.
2. ~~Resolve the Sunday and Friday question.~~ Done: Friday sessions are real, Sundays are no longer sessions.
3. ~~Rebuild the trading calendar from actual sessions and mark holidays.~~ Done.
4. ~~Re-grade signal calls.~~ Done: 1,046 calls.
5. ~~Fix the index refresh and backfill the NEPSE Index.~~ Done.
6. Load price, index and floorsheet history from the second machine (section 6).
7. Backfill prices for delisted and suspended symbols so the universe is not survivor-only.
8. Backfill prices for 2026-07-27. The public index confirms a session took place; the local database has no price rows for it.

The full list of open problems with severity is in `docs/OPEN_ISSUES.md`.
