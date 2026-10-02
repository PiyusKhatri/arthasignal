# Phase Log

Work runs against the local Postgres database `arthasignal` only.

## Phase 1 - Scheduled workflows stopped

- `daily_pipeline.yml`, `intraday_pipeline.yml`, `fundamentals_pipeline.yml`: `schedule` removed; trigger is now `workflow_dispatch` only.
- `ci.yml`: `push` and `workflow_dispatch` removed; trigger is `pull_request` only. CI uses its own throwaway Postgres service container and never touches a real database.
- Verified by parsing every workflow with PyYAML: daily, intraday and fundamentals list only `workflow_dispatch`; ci lists only `pull_request`. No `schedule` or `push` key remains.
- Nothing writes to a database unless someone starts a pipeline by hand from the Actions tab. The `gh` CLI is not installed here, so runs already queued on GitHub could not be checked from this machine.

## Phase 2 - Missing session 2026-07-27 backfilled

- Source: Sharesansar daily price page (`ajaxtodayshareprice`, date `2026-07-27`), which stamps the table "As of : 2026-07-27". A non-trading day (2026-07-25) returns an empty table, so the date check is meaningful.
- Source check against local rows on the neighbouring sessions: 2026-07-24 353/353 symbols, 2026-07-28 342/342 symbols, 0 OHLC or volume mismatches.
- New code: `sharesansar_scraper.get_session_prices`, `src/pipeline/backfill_price_session.py` (insert-only, `ON CONFLICT DO NOTHING`, rejects the row unless the source date matches). Run: `python -m src.pipeline.backfill_price_session 2026-07-27`.
- `daily_prices` total: 271,863 before, 272,221 after. Rows for 2026-07-27: 0 before, 358 after (280 equity, 42 mutual fund, 36 debenture). Two source symbols were skipped because they are not in `companies`: `ADBLB86`, `NIFRAUR85/`. `adjusted_close` recomputed for the 189 symbols with corporate actions.
- Trading calendar rebuilt: 2026-07-27 is now a trading day; trading days 1,201 -> 1,203 (one is 2026-09-30, which the calendar marks as trading but has no prices yet because ingestion is stopped); unexplained non-trading days 1 -> 0.
- Regrade fix: the old check only caught calls resolved before their target session. Inserting a session moves targets earlier, so a call can now be resolved too late. `is_miscounted` now also flags a call when the symbol traded on or after the target but before the recorded resolution date.
- Regrade result: 494 calls re-graded (exactly the calls whose entry was before 2026-07-27 and resolution on or after it). Transitions: loss->loss 160, loss->win 31, win->loss 10, win->win 293. Totals: wins 405 -> 426, losses 595 -> 574, pending 512 unchanged.
- Mean gross return of graded calls, before -> after: `close < bollinger_lower` -0.74% -> -0.41% (314 calls), `doji` 0.51% -> 0.59% (224), `rsi_14 < 30` 1.04% -> 1.38% (215), `rsi_14 > 70` -5.31% -> -5.29% (247). These are gross, before costs.
- Tests: `tests/test_price_session_backfill.py` (3 new) and one new case in `tests/test_trading_calendar_sessions.py`; 18 passed.

## Phase 3 - Index resilience

- `run_daily_index_refresh` now runs `fill_missing_index_rows` after the primary sources. It looks at the last 10 price sessions in `daily_prices`, lists every tracked index (16 current series; the discontinued `Insurance` series is excluded) that has no row for a session, and fetches only those from the Sharesansar index history (`index-history-data`, explicit date range). Rows it still cannot find are logged at ERROR and counted as refresh failures, so the pipeline summary goes to warning or failure instead of success.
- New data-quality check `benchmark_index_coverage`: every date in `daily_prices` must have a `NEPSE Index` row. `run_all_daily` sends a failure alert and raises `MissingIndexSessionsError` at the end of the run when it trips. `python -m src.pipeline.data_quality` runs it alone and exits 1 on failure.
- Live proof on the local database: saved and deleted the 2026-09-29 `NEPSE Index` and `Sensitive Index` rows; the check exited 1 with "1 price sessions have no NEPSE Index row: 2026-09-29"; `fill_missing_index_rows` restored both from the public history and the restored rows are identical to the saved ones in all eight columns; the check then passed. The first live run also exposed that `Insurance` (a series last published years ago) would be flagged forever, which is why it is excluded.
- Current state: 0 price sessions without a NEPSE Index row; all 16 tracked indices present on the last 10 sessions.
- Tests: `tests/test_index_fallback.py` (9 new). Existing refresh tests patched so they do not reach the database. Full backend suite: 366 passed (`test_auth_integration.py` skipped because it writes users into the configured database, open issue P4).

## Phase 4 - Longer price history (2014-06 onward)

### What each public source provides

| Source | Gives | Depth | Delisted symbols | Limits |
| --- | --- | --- | --- | --- |
| Sharesansar daily price page (`ajaxtodayshareprice`, one date per request) | Raw OHLC, volume, turnover, previous close for every symbol that traded that day | Rows from 2010; about 130 symbols a day in 2014, 220 in 2021 | Yes, any symbol that traded that day | Raw only, no adjusted prices. **`open` is not a real opening price before 2018-02-18**: it equals the previous close on 98.7-99.6% of rows every month until 2018-02-15, then drops to 9-25%. A non-trading day returns an empty table stamped with the requested date |
| Sharesansar company price history (per symbol) | Raw OHLC, volume, turnover | From 2011-10 | Yes (checked NIB, ACEDBL, KMBL) | Raw only; one symbol at a time; needs the company page to exist |
| Merolagani chart handler (`TechnicalChartHandler.ashx`, per symbol) | OHLCV, both adjusted (`isAdjust=1`) and raw (`isAdjust=0`) | From 2010 (NABIL) | **No** (ACEDBL, BOKL, GRAND return `no_data`) | Survivor-only, so not usable alone. Raw values matched Sharesansar exactly on the dates checked |
| NEPSE official API (nepalstock.com) | - | - | - | HTTP 401 from this machine |
| NepseAlpha | - | - | - | HTTP 403 (bot protection) |

The backfill uses the Sharesansar daily page because it is the only source that is date-complete and includes delisted symbols. Adjusted prices are recomputed locally from `corporate_actions`.

### What was run

- `src/pipeline/backfill_price_history.py`: walks dates (Saturdays skipped), insert-only (`ON CONFLICT DO NOTHING`), refuses a page whose "As of" date differs from the request, appends one JSON line per date to a state file and skips finished dates on restart. Symbols missing from `companies` are added with status `D` and an instrument type inferred from the floorsheet security name (equity, mutual fund, debenture, promoter share, preference share). Ran in the background with `nohup`, logging to `logs/` (git-ignored).
- Pass 1, 2014-06-01 to 2021-07-24: 2,232 dates, 1,615 sessions, 612 no-session days, 0 date mismatches, 250,694 rows, 184 companies added. 27 dates failed during a local DNS outage and were retried successfully by rerunning the same command.
- Pass 2, 2021-07-25 to 2026-09-29 (gap fill inside the existing period): 1,202 sessions (exactly the existing session count, no new dates), 72,630 rows added, 271,729 existing rows left untouched, 42 companies added. Added rows: active mutual funds 30,346, active debentures 20,928, delisted funds 7,329, delisted equities 6,049, **active equities 5,217 (36 symbols that had missing days)**, promoter shares 1,219, other 1,542.
- `daily_prices`: 272,221 rows from 2021-07-25 before; 596,256 rows from 2014-06-01 after, 2,822 sessions.
- Delisted equities with prices: 32 of 184 before, 211 of 259 now. Suspended: 6 of 25.
- Index history extended with `python -m src.pipeline.backfill_index --start 2014-05-01 --end 2021-07-24` (insert-only): 21,585 rows for 16 indices. 2016-01-03 was a real session (129 price rows, a floorsheet file) missing from the Sharesansar index history; its NEPSE Index row was taken from Merolagani (close 1,160.84; Merolagani's volume for that day, 839,563, equals our summed price volume exactly). The NEPSE Index coverage check passes on all 2,822 sessions.
- Trading calendar rebuilt from 2014-06-01: 4,505 days, 2,823 trading days (2,822 with prices plus 2026-09-30), 368 known holidays, 0 unexplained non-trading days.
- `adjusted_close` recomputed for the 192 symbols with corporate actions: 346,011 rows (67.4% of equity rows). Mutual funds, debentures and the new delisted symbols have none.

### Validation

- **Against existing rows** (every 10th existing session, 121 sessions, 2021-07-25 to 2026-09-29): 27,400 common symbol-days, **0 mismatches** in open, high, low, close or volume (mismatch rate 0.0%). 7,340 source rows had no local row; those are the gaps filled in pass 2.
- **Against floorsheet volume** (2,415 floorsheet files, read-only; 106 are empty): 441,100 symbol-days compared, 99.59% exact, 0.09% within 1%, **0.32% mismatch**. By year the mismatch rate is 1.47% (2014), 0.49%, 0.02%, 0.13%, 0.48%, 0.74%, 1.18% (2020), 0.06%, 0.26%, then 0% for 2023 onward. Every floorsheet day has price rows.
- **Adjusted prices against Merolagani** (12 symbols with the most corporate actions, 3,206 weekly points, 2015 to 2021): only 56.5% of our adjustment factors are within 2% of Merolagani's; worst cases 20-40% (MNBBL, CIT, DDBL, MDB). The local adjusted series is not trustworthy for this period. Raw prices are.

### Not done or limits

- No real opening price before 2018-02-18 from any free source found. Entry rules that need the next open can only be tested from then.
- 48 delisted and 19 suspended equities still have no prices (never traded in this window or listed under another symbol).
- `technical_signals` was not recomputed for the 36 active equities whose missing days were filled, so their stored indicators are stale until the signal job runs.
- Tests: `tests/test_price_history_backfill.py` (8). Full backend suite 383 passed.

## Phase 5 - Honest labels in the UI

- Before: every one of the 35 signals active on 2026-09-29 was served with tier `high_confidence`, from the July backtests (`signal_confidence`).
- Backend: new `src/pipeline/signal_labels.py`. Every API payload that carried a signal tier now returns `tier: "under_validation"`, keeps the old value as `backtest_tier` (technical name still available), and adds `validation` with the live paper-trade evidence from `validation_status` (graded calls / required, entry days / required, gate status). Applied in `/market/active-signals` (dashboard), `/stocks/{symbol}/signals` (stock page, landing hero card), `/market/sectors/{sector}/stocks` (sector panel), `/watchlist`, and the confidence list in `/stocks/{symbol}/intelligence`. Evidence is cached for 10 minutes and falls back to 0/required if the status cannot be computed.
- Live evidence shown today: `rsi_14 < 30` 42/60, `close < bollinger_lower` 100/100, `doji` 34/60, `rsi_14 > 70` 5/60 graded calls; all four have 3-4 of 20 required entry days, so all are still collecting.
- Frontend: one neutral grey badge "Under validation · N/M graded calls" everywhere (dashboard cards, stock page, sector panel, watchlist, landing hero card, stock intelligence panel). The technical signal name stays visible, and the tooltip shows it with the full evidence (calls and entry days). The green "high confidence" styling is gone. The old win-rate edge is shown only as "Earlier backtest, not re-validated". Market overview headings now read "Forward-validated signals/opportunities" (empty until a signal passes the gate; stock-level confidence was already capped below "high" while validation is collecting). Landing copy no longer claims every signal is backtested with its actual win rate.
- Checks: `npm run typecheck` exit 0; `npm run build` exit 0 (after reinstalling `node_modules`, which lacked the arm64 `lightningcss` binary); `npm run test:smoke` "Frontend smoke checks passed" (after `npx playwright install chromium`). With the API running on the local database, rendered `/`, `/stock/CHCL`, `/dashboard`, `/market-pulse` contain 0 occurrences of "high confidence"/"high-confidence"; the dashboard shows the four evidence counts above. The API returns only `under_validation` as `tier` on all five endpoints. The watchlist page needs a login and was checked through its API path (same builder as the sector panel); no test user was created.
- Tests: `tests/test_signal_labels.py` (4). Backend suite passes.

## Phase 6 - Backtest rerun

- `src/backtest/signal_rerun.py` reruns the 26 rule signals through `src/backtest`: next-session-open entry, 20-session exit with a 3-session grace, raw prices with bonus/rights windows and circuit-breaking jumps excluded, costs at 0.5/1.0/1.5%, intervals clustered by signal date plus a family-adjusted interval for 26 tests, the locked holdout (`partition_rows`, `assert_development_only`, sealed rows never read), four purged walk-forward test periods, and the Phase 4 history (signals from 2018-02-18, the first session with real opening prices). All 26 signals are registered in `backtest_variant_trials` (26 rows). `backtest_holdout_evaluations` has 0 rows. No model was trained.
- Development set: 319,282 symbol-days on 1,731 dates (2018-02-18 to 2025-08-19), 529 equity symbols. Excluded: 12,711 without a next-session trade, 14,001 with a bonus or rights date in the trade, 1,779 with a circuit-breaking jump. Holdout: 53,421 rows sealed, 4,919 boundary rows dropped.
- Equal-weight universe: +2.21% per 20 sessions at next open (+1.86% at signal-day close), win rate 47.11%.
- Result: none of the five former "high confidence" signals survives. `rsi_14 < 30` trails the universe by 1.37% per 20 sessions (−1.73 to −1.02) and `close < bollinger_lower` by 1.67% (−2.04 to −1.29); both are negative in all four folds. `doji` (+0.47) is entirely a new-listing effect (−0.09 without it). `shooting_star` +0.04 (−0.18 to +0.26). `rsi_14 > 70` +7.83, but +3.56 without new listings and unstable across folds. The large positive numbers for momentum signals mostly come from stocks in their first 60 sessions after listing (post-hoc diagnostic, reported separately).
- Report with every table: `docs/BACKTEST_RERUN.md`. Raw output: `docs/backtest_rerun_results.json`.
- Tests: `tests/test_signal_rerun.py` (6). Backend suite 384 passed.

## Phase A - Cleanup of known problems

- **Stale indicators.** Before: 36 active equities had 5,217 price days (from the Phase 4 gap fill) with no `technical_signals` row, and 279 more were missing the 2026-07-27 session backfilled earlier, so every indicator after those days was computed on incomplete bars. In total 309 of 312 active equities and 5,496 rows were affected. `compute_and_store_signals(..., full=True)` and `python -m src.pipeline.backfill_signals --full` now recompute a symbol's whole history instead of only new dates and the last five. Ran for all 312 active equities: 0 failures, 407,901 rows upserted in 246 s. After: 0 price days without an indicator row, and daily rows now span 2014-06-01 to 2026-09-29 (257,075 rows from 2021-07-25 before). Of the 257,075 pre-existing rows, 37,899 changed `rsi_14`, 18,236 `sma_50`, 38,019 `sma_200`, 8,071 `bollinger_lower`, and 0 changed `doji`. On 2026-09-29, `rsi_14` changed on 283 of 285 rows (Wilder smoothing now starts from the 2014 history), and the number of stocks with RSI < 30 stayed at 12. Weekly and monthly rows were not recomputed (they still end 2026-07-23).
- **`signal_confidence` removed from scores and labels.** `_historical_evidence` no longer reads tiers, the July edge or the July sample counts. Reliability is now sample-size and return points from `signal_backtest_results` only, capped at 11 of 30, so "high" signal quality cannot happen. `_confidence_score` is deleted. `public_signal_label` no longer takes or returns `backtest_tier` (removed from the API models and frontend types too). `/market/sectors/*` and the dashboard active-signal list no longer rank by the July edge, and `_confidence_summary` no longer sorts by tier. The July edge is still shown, as before, under "Earlier backtest, not re-validated". Test `test_signal_confidence_tiers_do_not_change_any_score_or_label`: a high-confidence tier with +9 edge, a weak tier with -4 edge and no row at all produce identical scores, ratings, confidence, quality and evidence. Live on the local DB: `/market/active-signals` (38 signals) and `/stocks/NABIL/signals` contain no `backtest_tier` or `high_confidence`; `/market/intelligence` gives signal quality low 176 / medium 24 / high 0, max reliability 10.
- **Supabase.** No module in `src/` reads a Supabase variable; every connection goes through `src.config.settings`. `get_settings` now raises `ConfigError` when `DATABASE_URL` or `DATABASE_URL_READONLY` points at a `supabase.co`/`supabase.com` host (checked live with the `.env` Supabase URL: refused). The three manual-dispatch workflows also exit before the pipeline when the secret points at Supabase. Tests: `tests/test_no_supabase_writes.py` (9).
  What still needs Supabase: no code path. Outside the code, (a) the GitHub `DATABASE_URL` secret, which could not be inspected here (no `gh` CLI) and probably points at Supabase; the workflows now refuse it; (b) any data that exists only on Supabase (production users, watchlists, alerts, and any signal calls written by scheduled runs) cannot be checked because Supabase rejects this machine's login, so it must be exported and compared before Supabase is shut down; (c) `.env` still holds `DATABASE_URL_SUPABASE` and `DATABASE_URL_READONLY_SUPABASE`, which nothing reads.
- Backend suite 392 passed (auth integration test skipped, P4). `npm run typecheck` exit 0.

## Phase B - Broker-flow pre-registration

- `docs/BROKER_FLOW_PREREG.md` (protocol `broker-flow-prereg-v1`) was committed before any broker-flow feature or return was computed. It contains: five hypotheses (top-5 net buy share, buy-minus-sell broker HHI, day-*t* buy HHI against its 20-day mean, early-buyer imbalance with a point-in-time, embargoed early-broker set, and close against the top-5 buyers' VWAP); the universe rules; the target (20-session excess over the equal-weight universe, next-session open entry from 2018-02-18 and next-session close entry before that); the development window (signal and exit prices ≤ 2025-01-19); the baselines (NEPSE buy-and-hold, equal-weight universe, momentum); costs 0.5/1.0/1.5%; and six gates (Bonferroni α = 0.01, 20-session block-clustered intervals, ≥ 3 of 4 walk-forward folds positive, beating NEPSE and momentum, positive on the real-open subset).
- Parameters are in `src/backtest/broker_flow_spec.py`. `src/backtest/broker_flow_config.json` cuts development at 2025-01-19 (holdout_start 2025-01-20).
- Registered in `backtest_variant_trials` as family `broker_flow_prereg_2026_10`, ids 40-44 (31 variants in the ledger in total). Tests: `tests/test_broker_flow_spec.py` (3).

## Phase C - Broker-flow feature store

- `src/backtest/broker_flow_features.py` builds the five pre-registered features with DuckDB, directly from the floorsheet Parquet (read-only; only the 2,411 files dated ≤ 2025-01-19 are opened). Output goes to `~/Desktop/arthasignal-ai/derived/broker_flow/` as `features.parquet` (440,550 symbol-days, 330,178 eligible), `early_brokers.parquet` (H4 early-broker sets per 20-session block) and `symbol_day.parquet`. Nothing is written to Postgres, and the command refuses an output path inside the floorsheet directory. A full build takes 54 s. Trade checks: 69.9M trades, 0 business-date/file-date mismatches, 0 non-positive quantities, 8 non-positive rates (left out of the H5 VWAP).
- Leakage tests (`tests/test_broker_flow_leakage.py`, synthetic floorsheet of 30 symbols × 640 sessions): features up to *t* are identical when every trade, price and corporate action after *t* is removed, and when they are changed (quantities rescaled, all buys moved to one broker, prices tripled, new bonus dates added). The tests were checked against three deliberately injected leaks (H3 window reaching one session ahead, H4 early-broker set without the 20 + 5 session lag, broker window reaching *t*+1), and each one made both leakage tests fail. On real data, rebuilding with all files and prices after 2019-06-30 removed reproduced all 148,510 earlier rows exactly (same eligibility, same nulls, max difference 5.6e-16).
- Missing trades (1.3-2.1% a year in 2015-2018): every feature is a ratio, so a uniform quantity scale leaves them unchanged (tested). Randomly dropping 2% of 2016 trades lowers daily quantity by 1.97% on average but keeps rank correlation at 0.993-0.999 and top-quintile membership agreement at 98.2-99.2% for H1, H2, H3 and H5. The real gap is missing pages, not random trades, so this is an approximation.
- Coverage by year (eligible symbol-days / symbols, mean gap %): 2014 8,801/150 (0.66), 2015 12,959/160 (2.13), 2016 18,819/159 (1.84), 2017 19,697/159 (1.73), 2018 25,796/160 (1.34), 2019 29,002/183, 2020 27,492/178, 2021 41,090/193, 2022 43,467/217, 2023 46,894/259, 2024 53,235/256, 2025 (to 01-19) 2,926/246. H1/H2 are defined on 100% of eligible rows, H3 on ≥ 99.9%, H5 on 94-97% (undefined when a bonus or rights date is in the 20-session window or no broker is a net buyer), H4 on 100% from 2015 (26.8% in 2014). Full table: `docs/broker_flow_coverage.json`.
- Symbols missing from `companies`: 15 symbols, 233 symbol-days, 0.005% of all trades. HBDL accounts for 196 of those days (2014-06 to 2015-04, 0.6% of 2014 trades) and also has no price rows, so it is a survivorship gap and not only a lookup gap. The rest are promoter-share lines and one lowercase `uli` row (`ULI` exists). None of these symbol-days has a price row, so admitting them would add 0 eligible rows.
- Deviation noted before any result was computed: the pre-registration says H4 "starts about 500 sessions into the data", but its fixed rules (lookback up to 500 sessions, at least 100 events) produce the first early-broker set on 2014-11-09. The coded rules are followed. 117 refreshes, 49 distinct brokers ever selected, median 2,495 events per lookback.

## Phase D - Broker-flow evaluation

- `src/backtest/broker_flow_eval.py` (committed in 3203cb3 before its first run) evaluates the five pre-registered hypotheses through `src/backtest`: `next_open_label` (next open from 2018-02-18, next close before), `partition_rows` and `assert_development_only` with the 2025-01-19 development config, `walk_forward_folds` (4 test periods, 29-session purge + embargo), `clustered_mean_interval` by date and by 20-session block, and `simple_momentum_selection`. It refuses to run if the registered fingerprints do not match the spec, and it loaded no price after 2025-01-19. Holdout rows 0, boundary rows 0. No model was trained, and no hypothesis was added or changed.
- Data: 303,997 labeled rows on 2,269 signal dates (2014-06-19 to 2024-12-11). 1,986 pooled test dates per hypothesis.
- **Result: all five fail.** Mean excess over the equal-weight universe after a 1% cost, per 20 sessions: H1 −0.52%, H2 −0.77%, H3 −1.10%, H4 −0.79%, H5 −1.27%. None passes G1, G2, G3, G5 or G6. H2 to H5 are negative in all four folds; H1 is positive in one. H3 and H5 point the opposite way to the prediction even before costs. H1 has the only gross edge of note (+0.47% over the universe, rank IC +0.035), which the cost removes. The momentum baseline also trails the universe (−0.36% at 1%). Report: `docs/BROKER_FLOW_RESULTS.md`; raw output: `docs/broker_flow_results.json`.
- Tests: `tests/test_broker_flow_eval.py` (7). Backend suite 406 passed.

## Phase E1 - Event-study toolkit

- `src/backtest/event_study.py`: builds a raw-price panel with explicit corporate-action handling, abnormal-return paths from -10 to +60 sessions against the equal-weight universe and the equal-weight sector (each daily rebalanced, one-session returns only), window abnormal returns, realistic trade simulation, cost levels 0.5/1.0/1.5%, date-clustered and 20-session-block-clustered intervals (`src/backtest/stats.clustered_mean_interval`), and a `TestCounter` that records each test, refuses repeats and unplanned extras, and gives the Bonferroni alpha. `src/backtest/event_data.py` loads the panel from local Postgres and stops at 2025-01-19.
- Corporate actions: the ex-session is the first session on or after the book-close date. This was checked first: on 551 bonus ≥ 10% events, 89.6% show the expected drop on that session and at most 4.2% on any session from 6 before to 2 after; rights 82.1%. The total-return chain applies the cash dividend (net of 5% tax, on pre-bonus shares), then the bonus shares, then rights (subscription at par 100, cash paid out). Any remaining one-session move beyond the circuit limit + 2 points (compounded over non-trading gaps) is flagged corrupt and excluded.
- Fills: entry at the open of the first session after the knowledge date (close before 2018-02-18), skipping up to 5 sessions that have no trade or are locked at the upper circuit (every trade at the limit). Exit at the close of the target session, deferred up to 20 sessions while the stock does not trade or is locked at the lower circuit, otherwise stranded at the last trade. A `last_index` cap stops any read past the development window.
- Real-data check (2014-06-01 to 2025-01-19): 479 equities, 2,435 sessions, 411,751 traded symbol-days, 276 returns flagged corrupt (126 symbols), 117 locked-upper and 926 locked-lower days. On ex-sessions, |return| < 6%: bonus 14 raw → 442 adjusted of 551; dividend 218 → 676 of 830; rights 6 → 58 of 119. The adjusted bonus ex-session return has median +0.2% and SD 3.6% (normal daily noise). The adjusted rights ex-session return has median +3.9%, and a quarter close near the upper limit, which looks like a real post-adjustment move rather than a formula error. This was observed before pre-registration and is disclosed there.
- No corporate actions are recorded for any of the 259 delisted equities, so their bonus drops are visible only through the corrupt-return flag (drops > 12%).
- Tests: `tests/test_event_study.py` (10): explicit bonus/dividend/rights adjustment, flagging of an unrecorded split, no buy on a locked upper day, sells deferred past a no-trade day and a locked lower day, next-close entry before 2018-02-18, no read past `last_index`, abnormal paths up to *k* unchanged when prices after anchor + *k* are changed or removed, trades unchanged when prices after exit change, and the counter and clustering. Two injected one-session look-aheads (stock cumulative and benchmark cumulative) each failed both leakage tests. Backend suite 416 passed.

## Phase E2 - Event tables and statistical power

- `src/backtest/event_tables.py` builds event tables from existing local data only (prices, corporate actions, `symbol_history`, NEPSE Index, short-term rates) for 2014-06-01 to 2025-01-19, and writes Parquet to `~/Desktop/arthasignal-ai/derived/events/`: `book_close`, `new_listing`, `since_listing`, `circuit_days`, `circuit_streak_end`, `volume_anomaly` and `market_state`. Counts and power: `docs/event_counts.json`. No event return was computed.
- **Power yardstick:** the SD of 20-session abnormal returns (vs the equal-weight universe) over 20,000 random symbol-days is 14.8%. The minimum detectable 20-session effect at α = 0.05/8 and 80% power, treating each event date as a cluster, is listed per table below.

| Event table | Events | Distinct dates | MDE (20 sessions) | Per year |
| --- | ---: | ---: | ---: | --- |
| Book close, all | 1,161 (bonus + cash 745, cash only 184, right 135, bonus only 97; 171 symbols) | 651 | 1.55% | 2014 180, 2015 64, 2016 86, 2017 104, 2018 90, 2019 110, 2020 73, 2021 116, 2022 84, 2023 112, 2024 124, 2025 18 |
| Book close with bonus | 852 | 504 | 1.81% | |
| Book close, cash only | 184 | 126 | 3.90% | |
| Book close, right | 135 | 118 | 4.56% | |
| New listings (merger symbols excluded: 81) | 253 | 220 | 3.33% | 2014 28, 2015 21, 2016 11, 2017 22, 2018 15, 2019 23, 2020 18, 2021 26, 2022 31, 2023 51, 2024 7 (IPO pause, consistent with `ipo_calendar`) |
| Upper-circuit close, streak start | 4,703 | 1,342 | 0.77% | |
| Upper-circuit streak reaching 3 | 318 | 284 | 2.97% | 2014 19, 2015 15, 2016 58, 2017 28, 2018 5, 2019 13, 2020 34, 2021 62, 2022 23, 2023 31, 2024 30 |
| Upper streak ≥ 3 ended, outside first 60 sessions after listing | 219 | 195 | 3.58% | |
| Lower-circuit streak reaching 3 | 17 | 16 | 12.8% (too few to test) | |
| Volume ≥ 5× 60-session median, no corporate action within ±20 sessions, ≥ 60 sessions since first price | 23,180 | 2,161 | 0.35% | 2015 1,557 … 2024 4,486 |
| Same, up day | 15,031 | 1,868 | 0.43% | |
| Market state (daily) | 2,435 sessions; 200-session trend defined from 2015-04-09; trend up on 55.5% of sessions; 64 trend switches; T-bill rate known from 2016-11-03 (45-day publication lag) | | ~10 years | |

- Findings that limit tests: there are 6,520 upper-limit closes but 0 sessions where every trade was at the upper limit (on limit-up days the low equals the open), so the locked-upper fill rule rarely binds; 448 lower-limit sessions were fully locked. Corporate actions exist only for the 171 symbols active when they were scraped, so book-close events carry survivorship bias. The book-close announcement date is not stored; any run-up test must assume when the date was public.
- Point in time: listing, circuit, volume and market-state tables up to *t* are unchanged when later prices and volumes are removed or changed (`tests/test_event_tables.py`, 4; an injected 3-session volume look-ahead fails the test). The volume table's "no action in the next 20 sessions" filter uses known future book-close dates, on the same announcement assumption. Backend suite 420 passed.

## Phase E3a - Event pre-registration

- `docs/EVENT_PREREG.md` (protocol `event-prereg-v1`) and `src/backtest/event_spec.py` were committed before any event return or market-timing result was computed. Eight hypotheses: E1 book-close run-up (long, 15 sessions), E2 post-book-close drift (avoid, 20), E3 new listing after the initial run (long, 60), E4 end of a ≥ 3 upper-circuit streak (avoid, 20), E5 no-news volume spike on an up day (long, 20), E6 NEPSE trend + breadth in/out rule (Sharpe and drawdown vs buy-and-hold), and E7/E8 = E5 at 60 and 120 sessions. Window: knowledge from 2014-06-01, every price ≤ 2025-01-19. Folds: 4 equal contiguous blocks. Bonferroni α = 0.05/8. Fixed gates for long, avoid and timing hypotheses. Prior knowledge (ex-session returns, event counts, Phase 6 and broker-flow results) is disclosed in the document.
- Registered as family `event_prereg_2026_10` (39 variants in the ledger). Tests: `tests/test_event_spec.py` (2).

## Phase E3b - Event results

- `src/backtest/event_eval.py` (committed in 7fd2b82 before its first run) ran the 8 registered hypotheses through the toolkit. Prices end 2025-01-19, every trade passed the holdout guard, `TestCounter` ran 8 of 8 planned tests, and no model was trained. Report: `docs/EVENT_RESULTS.md`; raw output: `docs/event_results.json`.
- **Six fail.** E1 book-close run-up: +0.74% net, interval includes zero, 2 of 4 folds. E3 new listing: untestable as registered (the liquidity rule left 10 of 234 events). E5/E7/E8 no-news volume spikes: −2.4/−3.9/−6.6% net vs the universe, the opposite of the prediction. E6 market in/out: Sharpe 0.93 vs 0.62 and drawdown −28% vs −43%, but the bootstrap interval of the Sharpe difference (−0.72 to +1.29) fails G5.
- **Two pass** every registered gate, both avoid rules: E2 post-bonus-book-close drift −3.68% vs the universe over 20 sessions (683 trades, negative in all folds), and E4 end of a ≥ 3 upper-circuit streak −6.58% (171 trades, negative in all folds, raw return −3.7%).
- Post-hoc diagnostic (`src/backtest/event_diagnostics.py`, `docs/event_diagnostics_post_hoc.json`, not evidence): the registered daily-rebalanced equal-weight benchmark is biased up by +0.66% per 20 sessions and +3.0% per 60 compared with a buy-and-hold equal-weight universe. Against the buy-and-hold universe, E2 stays at −3.05% and E4 at −5.70%, both negative in every fold. Tests: `tests/test_event_eval.py` (3). Backend suite 425 passed.

## Phase E4 - Data plan for text and event sources

- `docs/DATA_PLAN.md`: for Merolagani news, Sharesansar news and company tabs, NEPSE notices, SEBON, NRB and quarterly reports, it records what each offers, the depth checked, structure, scraping difficulty, robots and terms, the legal constraints, a proposed point-in-time schema (`source_documents`, `document_symbols`, `corporate_event_announcements`, `quarterly_financials`, `policy_events`) and a collection order. Plan only: about 30 single requests (robots.txt and spot pages, 3-4 s apart); nothing bulk fetched or stored.
- Checked facts: Merolagani news has sequential IDs (ID 30,000 = 2017-02-10, latest about 131,421) and no robots.txt. Sharesansar and SEBON robots.txt allow everything; NRB disallows only `/wp-admin/` and its WordPress REST API is off. The NEPSE notices API returns HTTP 401 and must not be bypassed. Sharesansar company pages expose Announcement, AGM, Quarterly Reports and Financial Reports tabs, which fit the existing scraper pattern.

## Phase S1 - Accuracy protocol

- `docs/ACCURACY_PROTOCOL.md` (protocol `accuracy-v1`) was written before any scorecard result was computed. It covers:
  - horizons 5/10/20 (short), 40/80/120 (mid) and 160/240 (long) sessions;
  - next-open entry and the open after the horizon for exit, with close-to-close before 2018-02-18;
  - costs of 0.5/1.0/1.5%; unfilled and blocked or stranded calls graded incorrect, never voided;
  - correctness per horizon class against the same-date universe median, sector median and universe mean;
  - baselines: same-date random stock, equal-weight universe and NEPSE;
  - metrics including Brier, reliability buckets and ECE, with clusters as non-overlapping horizon windows;
  - a 13-situation × 8-horizon matrix labelled only from data at the close of *t*, the PASS / NO EVIDENCE / INSUFFICIENT SAMPLE gates, the look-ahead audit, rolling monitoring and kill criteria, and failure-cause tags.
- Power table (arithmetic on the 2,435 sessions of 2014-06-01 to 2025-01-19, no outcome data): independent windows 405/221/115/59/30/20/15/10 for horizons 5…240. A 60% claim can be supported against 50% only at 5-40 sessions, and with the gate's 55% lower bound only at 5-20 sessions. At 80 or more sessions no history this long can prove it. With the 104-cell penalty, no horizon has enough windows (463 needed).

## Phase S2 - Scorecard implementation

- `src/scorecard/`:
  - `spec` holds the protocol constants.
  - `schema` (`python -m src.scorecard.schema`) creates `scorecard_calls` and `scorecard_grades` with BEFORE UPDATE/DELETE row triggers and BEFORE TRUNCATE statement triggers that raise "append-only". A CHECK rejects a `live` call created after 11:00 NPT on the day after its signal date. Calls carry `model_version`, `feature_hash`, `batch_id`, `mode`, `probability` and situation labels.
  - `ledger` writes calls and grades with COPY.
  - `grading` vectorizes all symbols × signal sessions per horizon: open-to-open total return through explicit corporate actions (close-to-close before 2018-02-18), unfilled on no-trade or locked-upper entry, blocked/stranded exits graded wrong, corrupt windows as `data_error`, same-date universe median and mean, sector medians, NEPSE, baseline share, and vectorized failure causes.
  - `situations` gives the 13 point-in-time labels; `metrics` the cell metrics, gate, matrix and rolling monitor with kill flags; `audit` the look-ahead audit; `strategies` the three no-edge baselines plus a deliberately leaky one; `replay` the day-by-day replay into the ledger.
- Tests (`tests/test_scorecard.py`, 10): next-open entry and exit at the open after the horizon; close rule before 2018-02-18; locked-upper entry unfilled and wrong; a blocked sell graded wrong even after a price rise; 100 calls on 4 dates give 4 independent windows and INSUFFICIENT SAMPLE; a perfect 10-call batch cannot pass; situation and market-state labels up to *t* unchanged when later prices are changed or removed (a one-session look-ahead injected into a label fails it); the audit flags only the leaky strategy; the ledger rejects UPDATE, DELETE and TRUNCATE on both tables in a throwaway schema; a late live call is rejected. Schema applied to local Postgres. Backend suite 435 passed.

## Phase S3 - Scorecard validated with no-edge strategies

- `python -m src.scorecard.replay` replayed four strategies day by day over 2014-06-01 to 2025-01-19 (last price 2025-01-19, holdout untouched) into the append-only ledger: 484,298 calls and 3,684,963 grades. A rerun inserted nothing. Report: `docs/SCORECARD_BASELINES.md`; raw output: `docs/scorecard_baselines.json`.
- All 416 cells are NO EVIDENCE. The equal-weight universe scores exactly its baseline at every horizon (edge 0.0). The random picker is within ±0.4 points of baseline. Momentum has +1.3 to +1.7 points at 5-20 sessions and is negative beyond 120. The leaky future-return strategy was caught by the look-ahead audit (40/40 dates mismatched). Without the audit it clears the win, lower-bound, edge, expectancy, fold and sample gates at 10-40 sessions; in this run only calibration and data errors would also have blocked it.
- Power in practice: 16 of 104 cells can ever PASS for a 10-calls-a-day strategy, all at 5-20 sessions. Nothing at 40+ can, because of the data-error gate (2.4-16% of windows) and ≤ 59 independent windows.
- Bugs and implausible numbers:
  - an empty-symbol Equity row with 16 mixed price rows and two debentures labelled Equity aborted the first replay (nothing written); they are now excluded by a symbol filter;
  - data errors grow with horizon because delisted symbols have no corporate actions;
  - the top-minus-bottom spread on constant scores (fixed, test added);
  - positive expectancy at all costs is automatic for no-edge strategies at 20+ sessions;
  - the 62% absolute gate is nearly unreachable at mid horizons (baselines 23-24%);
  - failure causes collapse into news and circuit at long horizons;
  - stated 0.5 probabilities are miscalibrated against 24-37% baselines;
  - the Brier kill rule cannot fire for a 0.5 claimer.
- Backend suite 437 passed.

## Phase P1 - Price integrity

- `src/backtest/price_integrity.py` flags close (and, from 2018-02-18, open) moves beyond the circuit band + 0.5 point against the last traded close. Band history from data: 10% throughout 2014-06 to 2025-09-29, no rounding cluster, and gaps do not widen it. The 15% band from 2026-04-20 is not confirmed because that is holdout data. Steps are classified as resolved by an action, halt resumption (≥ 20 sessions without trade), or unresolved (unresolved, action mismatch, adjustment overshoot).
- Calibration on the 185 symbols with stored actions (2,005 records before the holdout): precision 97.96%; recall 97.4% on detectable bonus or right events (63.1% on all, since small bonuses are invisible in prices). Bonus ratios cannot be recovered from prices: median error 2.9 points, only 33% within 2 points. Ratios are never inferred.
- Delisted symbols had 0 stored actions and 326 unresolved steps. Sharesansar returned real dividend, bonus and right history for 105 of 114 delisted or suspended symbols: 1,012 rows inserted (`docs/recovered_corporate_actions.csv`). Unresolved steps overall fell from 453 to 202, and on delisted symbols from 326 (112 symbols) to 76 (45).
- Windows spanning an unresolved step are excluded from every horizon. Universe exclusion 2014-06 to 2025-01-19: 0.15% (5 sessions), 0.27%, 0.48%, 0.91%, 1.73%, 2.51%, 3.24%, 4.67% (240), against 0.4-9.7% in the v1 replay before recovery.
- Loud check: `python -m src.backtest.price_integrity --check` exited 1 on real data (6 unresolved steps in the last 60 sessions to 2025-09-29). The daily health check `unresolved_price_steps` makes `run_all_daily` alert and raise `UnresolvedPriceStepsError`.
- Tests: `tests/test_price_integrity.py` (10) and a scorecard exclusion test. Report: `docs/PRICE_INTEGRITY.md`. Earlier studies used pre-recovery prices and were not rerun.

## Phase P2 - Accuracy protocol v2

- `docs/ACCURACY_PROTOCOL.md` was rewritten as `accuracy-v2`, with a changelog; v1 stays in git (d3ec7f8). The changes:
  - the gate is edge over the same-date baseline (≥ +8 points, clustered lower bound > 0 at plain and penalized levels), expectancy > 0 at all three costs plus excess expectancy over the universe, ≥ 3 of 4 folds positive, and minimum calls, dates and independent windows per horizon;
  - calibration is judged against the baseline forecast, and a call may state no probability;
  - the Brier kill compares with the baseline-forecast Brier;
  - failure causes come from a magnitude decomposition (market / sector / idiosyncratic, with an event share);
  - windows spanning unresolved price steps are excluded.
- Plainly stated: 120, 160 and 240 sessions can never support a claim with 2014-2025 data (20, 15 and 10 windows against a minimum of 25); 80 sessions needs calls in 25 of its 30 windows.
- `src/scorecard/v2.py` and `replay_v2.py`; the ledger allows a NULL probability, and COPY now clears the inherited statement timeout (a regrade first failed on it and wrote nothing). Tests: `tests/test_scorecard_v2.py` (7): the edge gate, excess expectancy, calibration against the baseline, the Brier kill firing, long-horizon causes including event attribution, the window minimums, and the rounding guard.
- Rerun under v2 on the corrected data: random, equal-weight, momentum and leaky each get 49 NO EVIDENCE and 55 INSUFFICIENT SAMPLE cells; none passes. The leaky strategy is caught by the audit, which is the only gate that stops it at 80 sessions. Backend suite 462 passed.
