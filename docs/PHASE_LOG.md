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
- Rerun under v2 on the corrected data: random, equal-weight, momentum and leaky each get 49 NO EVIDENCE and 55 INSUFFICIENT SAMPLE cells; none passes. The leaky strategy is caught by the audit, which is the only gate that stops it at 80 sessions. Backend suite 461 passed.

## Phase P3 - Live ledger readiness

- `src/scorecard/model_v0.py` freezes the current simple analysis as model v0:
  - bear-state abstention;
  - top 10 by 20-session return plus top 5 new listings;
  - avoid rules E2 (after a bonus book close) and E4 (after a ≥ 3 upper-circuit streak ends);
  - no stated probability.

  It is registered in the new append-only `scorecard_models` table (id 1, parameters hash `46ba8c8c…`, commit c3f800c) and as variant 70. Registration is idempotent.
- `python -m src.scorecard.daily` computes the day's v0 calls from data up to that close, writes them as `live` calls (model version, feature hash, situations) with ON CONFLICT DO NOTHING under an advisory lock, refuses after 11:00 NPT the next day (exit 2; the database CHECK also rejects late rows), and grades matured live calls under v2. Cron line documented in `docs/LIVE_LEDGER.md`, not installed.
- Dry run for 2025-01-16 (before the holdout): market bull, 10 calls with hashes, nothing written, and a live write would have been refused. A non-session date exits 3. Tests: `tests/test_scorecard_daily.py` (6). No live rows were written.

## Phase P4 - Situation matrix v2 on corrected data

- Model v0 was replayed day by day over 2014-06-01 to 2025-01-19 into the ledger as replay calls (20,944 calls, 160,478 v2 grades; look-ahead audit 0/40 mismatches) and its 13 × 8 matrix built under `accuracy-v2` on the corrected data. Report: `docs/MATRIX_V2.md`; raw output: `docs/matrix_v2.json`.
- **Nothing passed:** 0 PASS, 38 NO EVIDENCE, 66 INSUFFICIENT SAMPLE. 120-240 sessions cannot support a claim; 80 meets its window minimum exactly.
- v0 overall: edge +1.9 to +2.1 points at 5-40 sessions (plain 90% bound +0.3 to +0.8, penalized −0.8 to −2.2), with positive excess expectancy over the universe (+0.4% to +3.7%). That is far from the 8-point gate and about the same as simple momentum.
- Closest cell: `new_listing` at 40 sessions, edge +8.75 points, plain bound +1.1, 4/4 folds, excess +13.6%. It fails only the penalized bound (−6.4, *K* = 520), and the effect had already been seen post hoc, so it is not a pass. It is the one candidate worth stating in advance for live tracking. `post_lower_circuit` is −3 to −11 points, a weakness in v0's momentum ranking, noted and not acted on.
- Backend suite 461 passed.

## Phase Q1 - Cleanup and recent price steps

- Removed the two assistant-vendor mentions (`docs/KEEP_PARK_REMOVE.md`, `docs/designs/paper-trade-validation-engine.md`). A grep of tracked files finds the name only in tooling paths that the editor setup requires (`CLAUDE.md`, the `.claude/` hook and skills folders), which were left alone. None in commit messages.
- The six flagged steps were checked on Sharesansar and Merolagani; provenance is in the new table `corporate_action_sources` (12 rows).
  - Five (CITY, KKHC, NABBC, RFPL, SSHL) are rights issues already stored with the correct ratio and date. Both sources confirm them, and each closed at exactly +10.0% from NEPSE's adjusted base price, an upper circuit on the ex-date. The detector now also measures moves from the adjusted base (`adjusted_base`, `base_move`).
  - WNLBP (a promoter share, 1,403 to 100) has no action on either source, so it is quarantined in the new table `price_quarantine`. The live writer excludes quarantined symbols and reports them.
  - PRIN's 18% bonus is on Sharesansar only, and Merolagani does not confirm it.
- `python -m src.backtest.price_integrity --check` now exits 0 with WNLBP listed as quarantined. Any unquarantined unresolved step still exits 1 or raises in the daily health check.
- Unresolved steps over 2014-06 to 2025-09-29 went from 202 to 159; calibration precision is 97.47% and detectable recall 96.12%. The horizon exclusion counts and `MATRIX_V2` were not rerun.
- An unbounded ad hoc query returned two WNLBP rows from after 2025-09-29; they were not used.
- Tests: 5 added. Backend suite 467 passed.

## Phase Q2 - Live hypotheses declared

- Declared at 2026-10-04 01:04:22 Nepal time, before anything else was run, in `docs/LIVE_HYPOTHESES.md`. The four hypotheses:
  - H1: avoid rule E2 at 20 sessions;
  - H2: avoid rule E4 at 20 sessions;
  - H3: momentum tilt at 5, 10 and 20 sessions;
  - H4: new_listing at 40 sessions. H4 was found after looking at the replay results, so it is a hypothesis and not evidence.
- The gate is protocol v2 unchanged, and avoid observations mirror the buy definition.
- Minimum live sample before any claim: H3 at 5 sessions, about 1.5 years; H3 at 10 and 20 sessions, about 2.7 and 5.1 years; H4, about 6.9 years; H1, at least 3.7 years; H2, about 9.3 years.
- No claim on any of the four is possible within a year.
- Gap: the live writer does not yet record avoid observations, so H1 and H2 are declared but not tracked.
- Registered in `backtest_variant_trials`, family `live_hypotheses_v0`, ids 74-77. Fingerprints:
  - H1: 815a8bdb0aa6bcec…
  - H2: bc59228a4881e2b6…
  - H3: 74fe5c9daaa5f5f3…
  - H4: cfb941d2acfe073f…

## Phase Q3 - Information survey and quarterly-report collector

- `docs/INFORMATION_SURVEY.md` covers, for each source, what it offers, its depth, whether publication dates are recorded, scraping difficulty and robots and terms. Neither site's terms have been read: their pages load the clauses with JavaScript. It also gives the point-in-time schema.
- Findings:
  - Sharesansar's quarterly tab has the latest quarter only.
  - Report images need OCR.
  - Merolagani has a JSON index of every quarterly report with its date.
  - Sharesansar's dividend table has announcement dates.
- `src/scrapers/quarterly_reports_collector.py` is resumable, rate-limited (3 s or more) and insert-only. Run with `nohup` on 2026-10-04: 0 errors, and 24 symbols with no Sharesansar page.
- Rows collected:
  - 13,176 Sharesansar and 11,794 Merolagani report announcements, from 2011 and 2009;
  - 265 latest-quarter statements;
  - 1,014 dividend declarations.
- Coverage: 491 and 461 symbols, including 280 of 312 active symbols and 205 of 256 delisted symbols on Sharesansar.
- Publication dates agree within 1 day for 89.6% of 10,619 matched reports. Research joins should use the later date.
- Validation against the 2,525 fundamentals rows: EPS equal to 0.01 for 92.8% of 181 pairs, and book value for 96.2% of 104 pairs; the differences are unexplained.
- There is no dated EPS or balance-sheet history in text form.
- Tests: 9 added; backend suite 476 passed.

## Phase Q4 - Holdout guard, avoid observations, latest-quarter capture

- **Incident:** the two WNLBP holdout rows from Phase Q1 (2025-10-08 and 2025-10-13, both at 1,138) are recorded as incident 1 in the append-only `holdout_incidents` table and in `docs/HOLDOUT_LOG.md`. They were not used.
- **Guard:**
  - research code now connects as the non-superuser role `arthasignal_research`. The local role is a superuser with BYPASSRLS, so row-level security could not apply to it;
  - row-level security policies on 15 date-keyed tables hide rows dated on or after 2025-09-30 from that role. Verified: the role's latest `daily_prices` date is 2025-09-28 and it sees 0 `fundamentals` rows;
  - the research engine raises `HoldoutQueryViolation` on any holdout date in parameters or literals, and the floorsheet file listing raises as well;
  - only `final_evaluation()` and genuine live operation (`live_ledger`, latest session only) can open it.
- **Avoid observations:** the live writer now writes `model_v0_avoid_e2` and `model_v0_avoid_e4` rows (rule, symbol, date, situations, feature hash) and grades them with the buy calls. A dry run for 2025-01-16 gave 10 buy calls and 34 avoid observations (31 E2, 3 E4). A holdout date other than the latest session is refused.
- **Capture:** `src/scrapers/quarterly_capture.py` writes append-only `quarterly_figure_captures` rows (one per changed statement, with `captured_at`) and `quarterly_capture_runs`. The first full run over 312 active symbols was in progress at commit time; its result is logged below.
- Tests: 9 added (guard, avoid hits and writes, holdout refusal, capture hash); backend suite passes.

## Phase Q5 - Earlier studies rerun on corrected prices

- Reran the event study, the broker-flow evaluation and the v0 situation matrix on the development window. Three versions: before (published), recovered (today's actions, 2,937 against 2,005) and corrected (plus the adjusted-base detector, with unresolved-step windows excluded). Report: `docs/RERUN_CORRECTED.md`.
- **Event study:**
  - E2 and E4 still pass. E2 now has 968 trades (was 683, because delisted companies are included), with abnormal −3.65% and bound −4.56%.
  - E1 flipped to pass on the recovered data (bound +0.02%), then failed again when corrected (−0.02%), which is a knife edge. The corrected result stands: it fails.
  - The other hypotheses are unchanged fails.
- **Broker flow:** H1-H5 all still fail with near-identical excess returns (−0.54% to −1.30%). The corrected run excludes 103 more windows for unresolved steps.
- **Matrix:** regraded as `accuracy-v2-pi2` (160,478 new grades). Window exclusions fell by about a quarter (623 → 459 at 5 sessions, 16,395 → 11,796 at 240). Still 0 PASS, 38 NO EVIDENCE and 66 INSUFFICIENT SAMPLE, with no verdict changes and a largest edge change of 1.2 points.
- **No earlier conclusion changes.**

## Phase Q6 - Earnings-information hypotheses pre-registered

- `docs/INFO_PREREG.md` and `src/scorecard/info_spec.py` were declared at 2026-10-04 09:41:54 Nepal time, before any outcome was computed.
- Six long hypotheses on publication-dated data:
  - I1 and I2: YoY net-profit growth in the top quartile, at 20 and 40 sessions;
  - I3: profit turnaround at 20;
  - I4: non-decreasing dividend declaration at 10;
  - I5: bonus announcement to book close at 10;
  - I6: banks' YoY top quartile at 20.
- Knowledge session is the first session after publication, with entry at the next open. The window is 2014-06-01 to 2025-01-19, and the gate is protocol v2 with *K* = 624.
- Registered in `backtest_variant_trials`, family `info_prereg_v1`, ids 82-87. Fingerprints:
  - I1: 777db8d3590c73e3…
  - I2: 03d39b5e580ae802…
  - I3: 8e9caa7cf07d9975…
  - I4: dae73235b4d4fa87…
  - I5: ffe7d5ebbc9f3496…
  - I6: 18c63f65e96499ec…

## Phase Q7 - Earnings-information results

- `python -m src.scorecard.info_eval` ran the six pre-registered hypotheses through the scorecard on 2014-06-01 to 2025-01-19. Report: `docs/INFO_RESULTS.md`; raw output: `docs/info_results.json`.
- Inputs: 10,380 dated reports with net profit (6,629 with YoY growth) and 847 dividend declarations from 2018.
- **Nothing passed.**
  - I1-I5 are NO EVIDENCE with edges of −5.8 to −10.3 points.
  - I6 (banks, +2.5) is INSUFFICIENT SAMPLE (130 calls, 38 windows).
  - The controls (all reports at 20 sessions −5.6, all declarations at 10 sessions −7.5) show that the filters add nothing.
- The look-ahead audit caught I5: its book-close rule depends on the future trading calendar. The rule was not repaired after the fact, and its verdict is NO EVIDENCE either way.
- Exploratory: 22% of I1 calls and 29% of I3 calls were unfilled at the next open.
- The ledger writer now stages `COPY` through a temporary table when the target has row-level security (`COPY FROM` is refused there).
- Tests: 5 added.

## Phase Q8 - EPS differences and unmatched Merolagani names

- `docs/EPS_RECONCILIATION.md` (`src/scrapers/eps_reconciliation.py`, output `docs/eps_reconciliation.json`). Of 181 pairs: 168 match.
  - 5 are Merolagani rescaling EPS retroactively after a bonus book close. EBL went 36.95 → 35.19, exactly ÷1.05, two days after its 5% bonus book close; GBIME, SADBL, SMHL and SNORL behave the same way.
  - 3 are Sharesansar showing the company-reported basic EPS, with a weighted or restated denominator (MBL, SANIMA, SBL), while Merolagani shows profit ÷ outstanding shares.
  - 3 are unexplained (NABIL, NMB, KSBBL). Consolidated versus standalone is plausible but not shown.
  - 2 cannot be classified.
  - All pairs are Q4, so annualized and cumulative effects are ruled out.
- No parser bug. Rule: use the company-reported EPS as first captured (`quarterly_figure_captures`), and never use Merolagani's `fundamentals.eps` point-in-time.
- `docs/MEROLAGANI_UNMATCHED.md` and `docs/merolagani_unmatched.csv` list all 63 name strings (375 rows) with the symbol Merolagani shows and a proposal. None maps to a symbol in `companies`:
  - 8 are not listed equities;
  - 4 are merger predecessors, not mapped to their successors;
  - 1 is a possible same-issuer case (SMLBSL / MLBS) to verify;
  - the rest are issuers missing from `companies`.
- Nothing was merged. A first version wrongly called the two `symbol_history` merger cases "certain"; that was corrected before any write.

## Phase Q4 follow-up - first latest-quarter capture

- First full run on 2026-10-04 (09:31-10:03 Nepal time) over 312 active symbols: 263 statements captured, 0 errors, 49 with no statement on the tab. 32 of those 49 are promoter-share lines.
- 259 rows are 4th quarter 2082/83 and 4 are 3rd quarter 2082/83. EPS was present in 215.
- A second run on NABIL and EBL stored nothing new (2 unchanged), so a capture is written only when the statement changes.
- The dated EPS and balance-sheet history starts here. No schedule was installed.

## Phase A - Research plan

- `docs/RESEARCH_PLAN.md`: literature on combining weak signals, meta-labeling, cross-sectional tree rankers, regime conditioning, LLM news sentiment and its decay, social-media sentiment, Telegram pump detection and liquidity cycles, with sources.
- It ranks 14 ideas by expected value, data availability, time to first v2 evidence, cost and terms risk.
- It gives a final architecture: independent frozen systems write to the immutable ledger, and a meta-system trained walk-forward only reads ledger rows written before each decision.
- Findings that change the plan:
  - Telegram's API terms forbid using platform data for ML development or deployment.
  - YouTube's developer policies cap storage of API data at 30 days.
  - LLM backtests on historical news are look-ahead-contaminated, so news evidence must be live.
  - Documented LLM headline edges last about 2 days, shorter than our 5-session minimum horizon.
- History we do not have: promotion and attention signals (Telegram, YouTube, Reddit) and point-in-time Google Trends. Margin-lending history must be collected from NRB publications. The IPO calendar (981 dated issues since 2011) and monthly rates (from FY 2073/74) exist locally.
- The next replay round is capped at 12 pre-registered hypotheses because each one raises *K*.

## Phase B - Paper-bot league declared and dry-run

- Declared at 2026-10-04 10:35:04 Nepal time in `docs/PAPER_BOT_LEAGUE.md`, before any league call and before the league read any holdout data. `scorecard_calls` holds 0 `bot_%` rows.
- Six frozen bots in `src/league/bots.py`:
  - `bot_avoid_e2e4` (avoid, 20 sessions);
  - `bot_momentum` (5, 10 and 20);
  - `bot_new_listing` (40);
  - `bot_ranker_spec`: fixed equal-weight ranks of 120-session momentum skipping 5, 5-session reversal, low volatility and turnover, with no fitting (5, 10 and 20);
  - `bot_market_timer` (5, 10 and 20);
  - `bot_combined`: timer-gated momentum and ranker vote minus avoid hits (5, 10 and 20).
- Shared risk rules:
  - equities only;
  - at least 50 of 60 sessions traded and median turnover of at least Rs 2 M;
  - no unresolved step in 120 sessions and no upper-limit close on the signal day;
  - at most 10 calls a day and 3 per sector;
  - equal weight;
  - stop on the v2 kill rule.
- `src/league/run.py` writes live calls before the 11:00 NPT deadline (idempotent, advisory lock), grades matured calls under v2, and stores the daily leaderboard append-only in `league_leaderboard`. It covers 5, 10, 20 and 40 sessions with win rate, baseline, edge and bounds, expectancy, excess expectancy, calibration, cohort drawdown, coverage and the verdict. It also logs `league_runs`. Avoid observations are mirrored as in `LIVE_HYPOTHESES.md`.
- Registered (`python -m src.league.declare`):
  - `scorecard_models` ids 3-8 at code commit `c6383a3`;
  - `backtest_variant_trials` family `paper_bot_league_v1` ids 97-102;
  - the six versions were added to `scorecard_accuracy_v2`, so *K* is now 11 × 104 = 1,144.
- Fingerprints:
  - bot_avoid_e2e4: 67e903fbe452ec58…
  - bot_momentum: 52ce9379bec48632…
  - bot_new_listing: 8494d31cbd355e7a…
  - bot_ranker_spec: 15d43d59e1161870…
  - bot_market_timer: b156e597bcb71d8a…
  - bot_combined: 1a00cbe8ffd01bb4…
- The look-ahead audit on real data (8 dates, 2018-02-18 to 2025-01-19) passes for all six.
- Coverage from selections only (no outcomes) gives minimum live samples: the ranker at 5 sessions takes about 1.0 year, momentum at 5 about 1.7, the avoid bot at 20 about 3.7, timer and combined at 5 about 4.2, and new listing at 40 about 7.7. No bot can support a claim within a year.
- Dry run for 2025-01-16 (a development date): 33 avoid observations, 10 momentum calls, 10 ranker calls, 0 for the other three (timer off). Nothing written. The cron wrapper `scripts/cron/league_daily.sh` was exercised with a flock stand-in: exit 0, and exit 3 on a non-session. It is not scheduled.
- Row-level security now also covers `league_leaderboard` and `league_runs`.
- Tests: 10 added (`tests/test_league.py`); suite 503 passed.

## Phase C - Live text collectors

- Storage is `src/collectors/store.py`:
  - `text_items` is append-only (triggers reject UPDATE, DELETE and TRUNCATE). Each row holds the source, item id, url, raw title and body, author hash, source `published_at` with its precision, our own `first_seen_at`, language, rule-based symbol mentions, content hash and raw metadata. Edits become new rows;
  - `text_collector_runs` logs each run;
  - for the 30-day sources (YouTube, Reddit), text sits in `text_items_ephemeral` and is purged after 30 days, and a CHECK keeps text out of the permanent row.
- Row-level security hides `text_items` and `text_collector_runs` from the research role (verified: it sees 0 rows).
- News collectors (`news.py`):
  - Sharesansar latest and Merolagani news, with minute timestamps from detail pages;
  - Arthasarokar and Bizmandu RSS, to the second;
  - Kathmandu Post Money, to the day;
  - 3 s or more between requests, and only new items fetched.
- **First real run**, 2026-10-04 10:46 NPT: 56 items stored (10 + 8 + 20 + 15 + 3). A rerun stored 0. Fixed on the way: Merolagani pages without a charset were decoded as Latin-1.
- Gap: the 35 Nepali-portal items gave 0 rule-based symbol mentions.
- Social collectors (`social.py`), all built but not running yet:
  - Telegram via Telethon and the official API. Off unless `ARTHASIGNAL_TELEGRAM_TERMS_ACK=1`, because Telegram's API terms forbid using the data for AI or ML development or deployment;
  - YouTube Data API v3 (video plus comment threads, 30-day text retention);
  - Reddit OAuth (posts and comments, 30-day text retention).

  All three report `not_configured` until keys and source lists exist. No channel, video or subreddit list was invented: `config/social_sources.json` is empty.
- Facebook, private groups and X are not collected; the reasons are in `docs/LIVE_COLLECTORS.md`, with key setup steps.
- `docs/LLM_CLASSIFICATION_SPEC.md` and `classify_spec.py` specify the classifier:
  - fields: symbol, role, event type, sentiment, promotion and pump signals, novelty and evidence;
  - a JSON schema and validator;
  - Telegram is blocked;
  - the knowledge-time rule and an append-only `text_labels` DDL (not applied);
  - the gold-set validation thresholds and a cost formula.

  No paid API was called.
- Cron wrapper `scripts/cron/collectors.sh` (news every 30 minutes, social hourly, purge daily), not scheduled. Exercised with a flock stand-in: social exit 5, purge exit 0. `.env.collectors` and `*.session` are gitignored. `telethon==1.45.0` was added to the requirements.
- Tests: 8 added; suite 511 passed.

## Phase D - Public tip tracker

- Declared at 2026-10-04 11:08:40 Nepal time in `docs/PUBLIC_TIP_TRACKER.md`, before any tip was collected. `public_tips` holds 0 rows and `scorecard_calls` 0 `tip%` rows.
- **Telegram is not collected automatically.** Its Content Licensing and AI Scraping Terms prohibit scraping, indexing, harvesting or aggregation of platform data beyond ordinary use, through the API and the no-login web preview alike. Manual entry of a Telegram tip requires a recorded permission reference. Facebook needs Meta App Review and is not built. Private and paid groups and logins are excluded.
- Sources built:
  - YouTube channel videos only (no comments; 30-day text retention);
  - analyst web pages, fetched only when robots.txt allows;
  - manual entry.

  Only the public channel name and the post URL are stored. The Phase C Telegram item no longer keeps post signatures. `config/tip_sources.json` is empty, and no sources were invented.
- Parser `p1` (`src/tips/parser.py`) is rule-based with no ML. It handles English and Nepali verbs and digits, entry, target and stop, and drops questions, holds, summaries with more than 5 symbols and contradictions. On the 56 real news articles it found 0 tips; its recall and precision on real tip posts are unmeasured.
- Ledger:
  - each tip is dated by its posting time (second or minute precision), otherwise by when it was first seen;
  - it is written as live calls under v2's deadline: per channel (`tip_<platform>_<channel>`, `p1-buy` or `p1-sell`), aggregate (`tips_all`), and `tips_promoted_avoid` for buy tips;
  - tips missed in time are stored as `outside_write_window`, never backdated. Tips posted on Fridays, Saturdays and holidays always fall outside the window under the unchanged CHECK, and fixing that needs a protocol decision.
- Grading reuses `grade_calls_v2` and the league leaderboard (the league helpers now take strategy filters and a table name). Aggregate and per-channel (descriptive) boards go to `tip_leaderboard`, plus a supplementary target and stop report.
- Hypotheses: T1 (public buy tips beat the baseline), T2 (sell tips as avoid), A1 (promoted stocks are an avoid signal). They are registered in `public_tips_v1` (ids 105, 107, 109) and `scorecard_accuracy_v2` (ids 106, 108, 110), so *K* is now 14 × 104 = 1,456. Fingerprints:
  - T1: 68040557d4fb86ab…
  - T2: 17b6eb78950c84a7…
  - A1: 6c721f32518490fb…
- Row-level security now covers `public_tips`, `public_tip_events` and `tip_leaderboard`.
- Dry run: YouTube and web `not_configured`, Telegram `blocked`, 0 open tips, so no price data was loaded and nothing was written. The cron wrapper `scripts/cron/tips.sh` was exercised (exit 0). It is not scheduled.
- Tests: 14 added; suite 525 passed.

## Phase E-A - Protocol v2.1 and promoter-share exclusion

- Declared at 2026-10-04 11:30:26 NPT. At that moment there were 0 live calls and 0 public tips.
- `accuracy-v2.1` (`docs/ACCURACY_PROTOCOL.md`):
  - the live-call deadline is the open of the **next NEPSE session**, not 11:00 on the next calendar day;
  - grading is unchanged (`grade_version` stays `accuracy-v2`), and the *K* family stays `scorecard_accuracy_v2`.
- The next session is the first later day with prices in `daily_prices`; otherwise the first day matching the weekday rule and not a listed holiday:
  - Sunday to Thursday from 2014;
  - Monday to Friday from 2026-04-08 (Council of Ministers decision, announced 2026-04-08).

  The holiday list is empty, which errs early. The rules are synced append-only from `config/nepse_calendar.json` into `nepse_calendar_rules`.
- Enforcement: a new `BEFORE INSERT` trigger `scorecard_live_deadline()` → `scorecard_next_open()`; the old CHECK `scorecard_calls_check` was dropped. Python uses the same rule (`src/scorecard/calendar.py`).
- Verified in SQL: `scorecard_next_open('2026-10-02')` = 2026-10-05 11:00 and `('2025-01-16')` = 2025-01-19 11:00. A dry run of the league and of the v0.1 writer on 2025-01-16 now shows the deadline 2025-01-19 11:00.
- The tip tracker maps posts to the latest session that had opened, so a Saturday post maps to Friday and is writable until Monday's open.
- **Promoter shares:**
  - 53 symbols were stored as `Equity` (HIDCLP, NABILP, GBIMEP, …) and are now `Promoter Shares`. They are listed in `docs/promoter_reclassification.json`;
  - the rule (name says promoter or promotor, or a listed symbol plus a `P` or `PO` suffix) is in `src/database/instruments.py`, and the company upsert and price backfill now use it so the API's label no longer reverts it;
  - they had 6,449 development-window price rows (45 symbols, about 1.6% of equity rows). Earlier studies were not rerun;
  - the mention book now uses Equity only.
- Because the universe changed:
  - bots are now **b2** (`scorecard_models` ids 9-14 at commit `8975097`; `paper_bot_league_v1` and v2 rows); b1 never ran and is retired;
  - model v0 is now **v0.1**, with H1-H4 amended in `live_hypotheses_v0`;
  - the tip hypotheses T1, T2 and A1 were amended in `public_tips_v1`, with their versions unchanged.
- *K* is now 20 × 104 = 2,080.
- Tests: 6 for v2.1 (pure, database trigger and writer) and 12 for the promoter rule (including that the research universe has HIDCL but not HIDCLP); suite 543 passed.

## Phase E-B - Production deployment kit

- No server runbook existed. The only earlier server mention is the floorsheet VM backfill in `OPEN_ISSUES.md` D3. `docs/PROD_DEPLOY.md` is new and resolves the open "PRODUCTION DATABASE TOPOLOGY" item in `TODOS.md`; the README points to it.
- Target: Ubuntu 24.04, t3.medium, ap-south-1, Elastic IP. Postgres 17 (PGDG) on localhost only, ufw with SSH only, a security group with SSH from your IP only, Python 3.12 via uv, server time zone Asia/Kathmandu.
- Code:
  - `src/ops/`: `capture` (NEPSE API, Sharesansar and Merolagani fallbacks, retries until 20:30, checked against both calendars), `daily` (ordered chain with resume, plus the gating described below), `scoring` (grade and metrics), `report` (daily and weekly Discord reports), `health` (failed jobs, stale prices, writers not done, stale news, backups, disk; one alert per issue through `ops_alerts`), `notify` (systemd OnFailure), `backfill`, and the append-only `runlog` (`ops_runs`);
  - `price_integrity --live` checks the latest 60 sessions under the live-ledger exception;
  - the league and v0.1 writers gained `--write-only`, so grading and metrics run as separate steps.
- Chain gating: a session with no prices stops everything except news. A capture failure does the same and alerts. An integrity failure blocks the three writers (league, avoid writer, tips) and alerts, while grading and metrics still run.
- `deploy/`:
  - 8 timers plus services and `arthasignal-notify@`;
  - `bootstrap_server.sh`, `dump_local.sh`, `restore_server.sh`, `backup.sh` (`age` encryption to an offline key, keeps 14, optional S3), `install_units.sh`;
  - SQL for owner hand-over and role grants;
  - Postgres, logrotate, tmpfiles and journald config;
  - `.env` and password templates. No secret is committed.
- **Checked on the laptop (real output, 2026-10-04):**
  - `dump_local.sh`: 286 MB dump, 113 tables counted, 33 s.
  - A restore **as a non-superuser owner failed**: 26 errors, among them "query would be affected by row-level security policy" on 9 tables and permission denied for 7 Supabase-era event triggers.
  - Fixed path: restore as superuser with `--no-owner` (0 errors), then reassign ownership to a non-superuser role (0 public tables left with another owner). **All 113 table row counts match the manifest** (for example `scorecard_grades` 7,695,134 and `daily_prices` 596,256). 23 holdout policies and 15 append-only or deadline triggers present. The non-superuser owner can write a valid live call.
  - Found on the way: locally `arthasignal_research` gets its access by being a **member of the superuser owner role**, a global object `pg_dump` does not carry, and broader than needed. The server gets explicit grants instead: select on all tables, insert only into the registry and ledger tables. On the restored copy the research role saw `daily_prices` up to 2025-09-28 only, 0 `text_items` and 0 holdout-era calls, and was refused an UPDATE on `daily_prices`.
  - The scratch database and role were dropped.
  - All requirements resolve as Linux x86_64 Python 3.12 wheels (71), plus the pure-Python `pyaes` source package that Telethon needs.
  - Ops dry runs: the chain list prints 9 steps in the requested order. `backfill --since 2026-09-29 --dry-run` expects 2026-09-30, 10-01 and 10-02 and finds all three missing (some may be Dashain holidays; the holiday list is empty). The health check flags stale prices and no backup. The reports render. Fixed on the way: an unreadable backup directory crashed the check, and now it is reported.
  - Nothing was run on any server.
- Tests: 8 added (`tests/test_ops.py`); suite 551 passed.

## Phase E-C - LightGBM ranker r1 and meta-labeler m1

- Pre-registrations were committed and registered before any feature, label or prediction was computed: `docs/RANKER_PREREG.md` (11:57:43 NPT, 16 trials in `ranker_prereg_v1`) and `docs/META_PREREG.md` (11:57:50 NPT, separate commit, 4 trials in `meta_prereg_v1`). All 20 trials were also added to `scorecard_accuracy_v2`, so *K* = 40 × 104 = 4,160.
- Features (`src/ranker/features.py`): 45 point-in-time features, covering price and volume, sector-relative, corporate-action proximity, the five floorsheet broker features from the read-only feature store, publication-dated earnings, and market state. Coverage is 44-100% per feature; the broker features cover 90-94%.
- Training (`src/ranker/train.py`) ran in 437 s:
  - targets are within-date ranks of next-open excess returns from the scorecard's grading cubes;
  - 7 calendar-year folds with purge and a 5-session embargo (verified in every fold);
  - a 4-point grid per horizon, selected inside each fold;
  - top 10 eligible calls per session with at most 3 per sector, written as replay calls (15,908 per horizon) and graded.
- **Audit:** 12 mismatches on 4 of 20 dates, all in the earnings features. The "later of the two portal dates" convention from `info_eval` depends on a future publication, so r1 is leaky. The audit was shown to catch a planted leak (206 mismatches).
- **Results:**
  - r1 edges: h5 +0.17, h10 +0.27, h20 −0.17 and h40 +0.75 points. All NO EVIDENCE, with excess expectancy negative at every horizon.
  - Out-of-sample rank IC was 0.07-0.09 even within the eligible set. The model separates future laggards (rest of eligible −0.3% to −1.5% excess) and does not find winners (top 10 −0.2% to +0.3%).
  - m1 edges: h5 +3.7, h10 +5.8, h20 +3.1 and h40 **+8.6** points. All NO EVIDENCE: penalized bounds are negative, calibration fails everywhere (Brier worse than the baseline forecast), the leak is inherited, and the kept calls are concentrated in 2021.
- **Not ready to run live** (no live broker features, audit failure), so no challenger bot was registered. Report: `docs/RANKER_RESULTS.md`.
- `lightgbm==4.7.0` and `scikit-learn==1.9.1` were added; both resolve as Linux Python 3.12 wheels. On macOS LightGBM needs `brew install libomp`.
- Tests: 6 added (`tests/test_ranker.py`, including a synthetic no-look-ahead feature check).

## Fix - research role URL kept the app password

- **Bug:** `research_engine()` built its URL with `make_url(settings.database_url).set(username=RESEARCH_ROLE, password=None)`. SQLAlchemy's `URL.set` ignores `None`, so the app role's password was sent for `arthasignal_research`. Confirmed on SQLAlchemy 2.0.52: the derived URL still carried the password. It only worked on the laptop because the local login needs no password.
- **Fix** (`src/database/holdout_guard.py`): a new `research_url()` rebuilds the URL with `URL.create(...)` from the parsed parts (driver, host, port, database, query), with user `arthasignal_research` and no password. The login library then reads the password from `~/.pgpass`.
- An optional `RESEARCH_DATABASE_URL` overrides it. It is refused unless its user is `arthasignal_research`, so it cannot point the guard at the app role, and refused if it names a Supabase host.
- **Other places:** a search of `src`, `tests`, `scripts`, `deploy` and `archive` for `.set(`, `make_url`, `password=None`, `username=` and string surgery on the database URL found no other derived role URL.
- **Tests** (`tests/test_research_url.py`, 4):
  - the research URL has no password and never contains the app password, while host, port, database and query are kept;
  - the old `.set` call is shown to keep the password;
  - `research_engine()` built from the configured settings has no password;
  - the override rules.
- Full suite on the laptop database: **561 passed**.
- **Scram proof:** a scratch PostgreSQL 17 cluster with `scram-sha-256` for every connection (TCP only). The real dump was restored into it as the server does it: 57 s, 0 errors, then ownership hand-over and role grants, with password-protected roles `arthasignal` (non-superuser owner), `arthasignal_research` and `api_readonly`.
  1. Fixed URL with the research password in `PGPASSFILE`: connects as `arthasignal_research`, and the latest visible `daily_prices` date is 2025-09-28 (the holdout guard holds).
  2. Fixed URL with an empty `.pgpass`: refused with "fe_sendauth: no password supplied".
  3. The old `.set(password=None)` URL with the same `.pgpass`: it still carried a password and was refused with "password authentication failed for user arthasignal_research". This is the bug, reproduced.
  4. The app role with its own password: connects and sees 2026-09-29.
  - **The full suite against that cluster, as the non-superuser app role with the research login only through `.pgpass`: 561 passed, 0 skipped.** A test user row created by the auth integration test appeared in the scram cluster, confirming the suite ran there.
  - The scratch cluster and its passwords were deleted afterwards.
- `docs/PROD_DEPLOY.md`: the research-login step now explains the no-password URL, the `.pgpass` host-matching and permission rules, the optional `RESEARCH_DATABASE_URL`, and the two error messages to expect.

## Sector data-quality fix

- **111 non-equity rows carried an equity sector:** 34 mutual funds and 77 debentures (for example CSY, H8020, SIGS2, LSH12 and NICFC as "Commercial Banks"). They have 33,969 development-window price rows. They were relabelled "Mutual Fund" and "Debenture" (`docs/sector_fix_companies.json`), and the company upsert now normalizes them so the API cannot bring the sponsor sector back.
- **Inventory (`docs/SECTOR_FIX.md` §2):**
  - all research and scorecard sector computations (grading sector median, v2 failure causes, event sector benchmarks, ranker sector features, league sector cap, the matrix) read the Equity-only panel (`event_data.py:28`), so funds never entered them;
  - unfiltered app paths: `market_pulse._load_floorsheet_by_sector`, and the per-symbol sector regime, index and baseline in `build_quant_research`, `quant_cross_sectional` and `stock_intelligence` (reachable for any symbol through `/intelligence/{symbol}`);
  - `api/stocks.py` was already Equity-only (defensive change).
- **Impact (floorsheet Parquet, 56 sessions to 2025-01-19):** Commercial Banks broker "coverage" averaged 311% (max 384%) instead of 100%, so the reliability flag was always on. The top broker's share was overstated by 1.65 points. Funds and debentures were up to 30.7% of the sector's floorsheet turnover on a day.
- **Fixes:** an Equity filter on the floorsheet query, `equity_sector()` for per-symbol context, and the loud check `nonequity_equity_sector` (raises in `run_all_daily`; `python -m src.pipeline.data_quality --sectors` exits 1 and is a new production chain step). Verified: exit 1 before the data fix, exit 0 after.
- **Found on the way:** the research replay tried to write the v2.1 calendar rules through the research role, and the holdout guard blocked it. Research callers now skip the calendar sync.
- **Reruns on the corrected universe** (Equity after the promoter-share reclassification; funds were never in it):
  - event study: E2 and E4 still pass, E1 still fails at −0.03%;
  - broker flow: H1-H5 still fail (labelled rows 301,272 → 298,260);
  - earnings: I1-I6 unchanged in verdict, edges moving by 0.02-0.48 points;
  - v0 matrix as v0.1 replay: still 0 PASS / 38 / 66, no verdict changes, largest edge change 0.9 points.

  `new_listing` at 40 is +8.89 with a plain bound of +2.06 and a penalized bound of −7.01 at *K* = 4,264. **No earlier conclusion changes.**
- Still open: 51 Equity symbols without a sector are pooled as "unknown" in the scorecard's sector median.
- Tests: 6 added (`tests/test_sector_universe.py`).

## Readiness 1 - Sector relabel migration for the restored server

- `deploy/migrations/m001_nonequity_sector_relabel.py`: an idempotent relabel of non-equity instruments that carry one of the 12 equity sectors (Mutual Funds → "Mutual Fund", Non-Convertible Debentures → "Debenture").
  - The sector list is frozen in the file.
  - It runs in one transaction under an advisory lock, prints before and after counts, and has a dry run.
  - It logs to `ops_migration_runs` only when it changes rows, and exits 1 if anything is left.
- Tested on a scratch restore of the 11:41 dump (taken before the 13:02 sector fix, so `companies` matches the server's 12:22 dump):
  - the sectors check exited 1 with 111;
  - the dry run wrote nothing;
  - the first run applied 111 (34 + 77), with after = 0;
  - the second run reported "already fixed: nothing to do", 0 rows, no log row;
  - the sectors check then exited 0;
  - the labels of all 354 non-equity rows are identical to the laptop's fix;
  - on the laptop (already fixed) it changed nothing and did not even create its log table.

  Scratch database dropped.
- `docs/PROD_DEPLOY.md` Part 10 gives the exact server commands: backup, `git pull`, packages, units, the check (expect exit 1), the migration (dry run, real, repeat) and the check (expect exit 0). It also fixes a wrong `~/.local/bin/uv` path in the update command.

## Readiness 2 - Chain order and failure policy

- **Before:**
  - capture (hard stop) → quarterly_capture → news → integrity → sectors → league → avoid_writer → tips → grading → metrics;
  - **sectors never blocked the writers**;
  - integrity blocked **all** writers on any failure, including a crash of the checker or a single flagged symbol;
  - the two slow collectors (up to 150 min) ran before the calls.
- **After:** capture → integrity → league → avoid_writer → tips → sectors → quarterly_capture → news → grading → metrics → report.
  - **Hard stops remain only where bad data makes a call invalid:**
    - a capture failure or no session stops everything except news;
    - integrity exit 4 (10% or more of traded equities flagged, a feed-wide problem) stops the writers.
  - Integrity exit 3 (specific symbols flagged): the symbols are written to `exclude_symbols.json` and dropped from today's calls by all three writers (league, v0.1, tips); the other calls are written; Discord warning.
  - A crash of the integrity checker: calls are written and Discord is alerted, because the grader already excludes windows spanning unresolved steps.
  - sectors, collectors, grading and metrics never block.
  - `ops_runs` gains the status `flagged`; the constraint is re-created idempotently.
- **Verified on the development window:**
  - `price_integrity --live --as-of 2025-01-16`: clean, exit 0, 243 traded;
  - `--as-of 2020-03-18`: flagged NLG, PMHPL, RRHP and WOMI (2.6% of 157), exit 3.

  The league dry run for 2020-03-18 received the exclusions; none of the four was a pick (the bots' liquidity rules had already excluded them). Excluding the real picks MFIL and HDL removed them: momentum went 10 → 8 calls, ranker 10 → 9. Excluded symbols are dropped, not replaced, as the frozen bot rules require.
- **Holdout:** for a development date, the league and v0.1 writers no longer query `max(date)` over all of `daily_prices`, and their quarantine, penalty and grading reads go through the research engine (quarantine filtered in SQL to `step_date <= as_of`).
- Also fixed: `check_recent` crashed on an empty window.
- Tests: the chain policy tests were rewritten (no session, flagged, feed-wide, checker crash, soft failures, capture failure, exclusion file), giving 13 in `tests/test_ops.py`; suite 572 passed.

## Readiness 3 - Rehearsal mode

- `python -m src.ops.daily --rehearse --as-of <development date>` runs every chain step's logic for a past session, in the live order with the live failure rules.
- **How "no writes, no holdout reads" is enforced:** with `ARTHASIGNAL_REHEARSAL=1` the main engine (`src/database/connection.py`) and the research engine log in as `arthasignal_research` with `default_transaction_read_only=on` and the holdout guard on. The database refuses any write, row-level security hides every holdout row, and the query guard rejects holdout dates. Verified: user `arthasignal_research`, read-only on, latest visible price 2025-09-28, 0 live calls visible, an UPDATE refused and a holdout-dated query refused.
- **Step variants:**
  - `capture --rehearse` (counts only, no fetch);
  - `price_integrity --as-of`;
  - league and v0.1 `--date D --dry-run --write-only`;
  - `tips cycle --as-of` (state at D, dry write);
  - sectors;
  - `quarterly_capture --dry-run` (2 symbols, parsed, not stored);
  - `news --dry-run`;
  - `scoring grade` and `metrics --as-of` (computed, not written);
  - `report daily --as-of --print-only`.

  The orchestrator records nothing in the database and lists alerts instead of sending them.
- **Run for 2025-01-16** (99 s, exit 0): capture session (296 price rows, index 1); integrity clean (243); league avoid 33, momentum 10, ranker 10, others 0, deadline 2025-01-19 11:00; v0.1 10 calls and 34 avoid observations; tips pending 0; sectors passed; quarterly parsed 2; news 56 items fetched and not stored; grading and metrics computed with 0 rows written; `would_alert: []`; report preview rendered.
- 2025-01-17 (no session) stopped at capture with `no_session` and skipped everything but news. A holdout date was refused.
- **Fixed on the way:** `data_quality.py` had two `__main__` blocks after my earlier change, so `--sectors` also ran the benchmark-index check. That check reads every price date, holdout included, and could fail the sectors step for an unrelated reason. There is now one entry point.
- `docs/PROD_DEPLOY.md` Part 10 step 7 adds the server rehearsal command with the expected output.
- Tests: 3 added; suite 575 passed.

## Readiness 4 - Sectors for equities without one

- There were **54** Equity rows without a sector (not 51; three have no development-window prices), all delisted.
- `src/scrapers/sector_sources.py` fetched each symbol's Sharesansar and Merolagani company pages (3 s apart) and stored every raw label append-only in `company_sector_sources` (106 rows). A sector was assigned only when the sources were unambiguous; each assignment is in `company_sector_assignments` with its evidence (`docs/sector_sources.json`).
- Sharesansar labels merged companies "Merged" (a status). Merolagani keeps the business sector.
- Two Merolagani labels were mapped only after checking them on listed companies: "Development Bank Limited" → Development Banks and "Non-Life Insurance" → Non Life Insurance, 4 of 4 each.
- **Result:**
  - 36 assigned (Development Banks 9, Microfinance 9, Non Life Insurance 7, Life Insurance 5, Finance 3, Commercial Banks 2, Hydro Power 1);
  - 14 unknown (no source names a sector);
  - 3 that the sources say are debentures (ADBLB, ADBLB86, ADBLB87; instrument type not changed);
  - 1 empty symbol.

  **18 remain without a sector.** The scorecard's pooled "unknown" group shrinks from 31,097 to 3,787 development-window rows (12 symbols in the research panel). The earlier reruns were not repeated.
- `deploy/migrations/m002_equity_sector_assignments.py` applies the same 36 on the server from the committed evidence (no network). It is idempotent, fills only empty sectors and leaves contradictions untouched.
- Tested together with m001 on a fresh restore of the pre-fix dump: m001 changed 111 then 0, m002 changed 36 then 0, the sectors check exited 0, and the restored `companies.sector` matched the laptop exactly. `docs/PROD_DEPLOY.md` Part 10 step 5b was added.
- Fixed on the way: a miscount in my first report table, corrected from the data.
- Tests: 4 added (`tests/test_sector_sources.py`); suite 579 passed.

## Simulation phase 1 - Protocol locked before anything is built

- **Rules first:** `docs/SIMULATION_PROTOCOL.md` (v1) and `config/simulation_protocol.yaml` were committed before any code (commit 702dcc0). All constants live in the config, and code reads them only through `src/simulation/protocol.py`. The config SHA-256 is `be906e7f4025aa8afc86ad7eb9a1407e517b59fd4cea4e6cc68ec43362985656`.
- **Calls:** BUY, HOLD, WAIT, NO_BUY and SELL (exit or stay out; no short selling).
- **Score:** −100..+100 with bands ≥ 80 / 50..79 / −49..49 / −79..−50 / ≤ −80. Tuning is allowed on learning years only; a missing pillar contributes 0 and is recorded.
- **Horizons:** 1 month = 19 sessions, the median of `trading_calendar` 2014-07 to 2019-12 (66 months, mean 19.36). Short is 19-57 sessions, mid 57-133, long 228-285.
- **Entry:** the next session's open, from 2018-02-18. Before that date every bar is reduced to its close, so entry is at the next close. Entries are unfilled on no trade or a locked upper circuit, and never backfilled. The earliest sell is entry + 3 sessions (settlement).
- **Exit:** target, stop or horizon close, whichever comes first.
  - Stop first on a same-session tie.
  - Gaps fill at the open.
  - No trade or a locked lower circuit delays the exit, and the delay is recorded.
- **Costs:** the NEPSE fee schedule by date, with citations:
  - commission tiers 1.0% (unconfirmed, before 2016-08), then 0.60-0.40%, 0.40-0.27% from 2020-12-27, and 0.36-0.24% from 2024-05-14;
  - NPR 10 minimum commission;
  - SEBON fee 0.015%;
  - DP charge Rs 25 per side;
  - CGT 5%, then 7.5%/5% from 2021-07-16.

  Unconfirmed figures use conservative values and are listed.
- **Grading:** rules for all five call types. BUY is right only on a net profit after all costs and tax, and a stop exit is always wrong. The risk control score is 100 × the reduction in worst-decile net return against holding to horizon, plus components.
- **Targets:** accuracy ≥ 60%, and the lower bound of the edge over a same-date random baseline > 0 with week-clustered intervals, on ≥ 100 graded calls over ≥ 30 call weeks.
- **Periods:** learning 2014-06 to 2019-12, check 2020, exams 2021-2024, and a proposed `exam_2025a` (2025-01-01 to 2025-09-29, censored at the holdout).
- **Process:** the weekly time machine with point-in-time knowledge times per pillar; the learning loop (at most 3 changes per version); and a multiple-testing proposal (Romano-Wolf stepdown on check and exam years, PBO via CSCV on learning years, legacy counter kept for display).
- **Exam years are not unseen:** earlier studies used prices through 2025-01-19. Only the holdout and live calls give the final verdict.
- **Code:**
  - `src/simulation/costs.py`: commission tiers, SEBON fee, DP charge, CGT, round trip.
  - `src/simulation/grading.py`:
    - grading of all five calls;
    - entry and fill rules, target/stop/horizon exits, circuit locks and settlement;
    - the pre-2018 close rule;
    - the hold-to-horizon counterfactual and WAIT missed opportunities;
    - accuracy and the risk control score.
  - `tests/test_simulation_grading.py`: 65 tests on synthetic paths. They cover:
    - target hit;
    - partial target then horizon above entry (right);
    - a gain smaller than costs (wrong);
    - stop hit, a gap through the stop, a stop/target tie, and a stop exit above entry (still wrong);
    - settlement;
    - locked upper circuit and no-trade entries (unfilled, no backfill);
    - locked lower circuit delay, no trade on the horizon day, pending;
    - the pre-2018 close rule and a path crossing 2018-02-18;
    - NO_BUY right/wrong/unfilled, SELL right/wrong/stop/target;
    - HOLD recovering, not recovering, stop, tie;
    - WAIT missed moves;
    - long-term CGT and the risk control score.
- **Trial table:** `python -m src.simulation.register` on the research role (`arthasignal_research`) inserted 1 row in family `simulation_protocol` (fingerprint `7fc6c9e4…eb5f`; trials total 137). A rerun inserts 0. This was the laptop database; the server needs the same command.
- **Not done, by instruction:** no simulator, no score, no study on real data. The only real-data reads were the session-per-month count from `trading_calendar` and the trial-table write, both on the research role with no holdout dates.
- **Open questions:** 15, in `docs/SIMULATION_PROTOCOL.md` section 14. None was decided silently; each has a config default so the grading code is concrete.
- Suite: 644 passed.

## Simulation phase 2 - Protocol v1.1: the owner's decisions

- **Version:** `sim-protocol-v1.1` replaces v1 (kept in git history at 702dcc0). Config SHA-256 `d495721a14f38485aa56335ce367bc0f519e34bfb7a22f0e099b38f4a5233b20`. The changelog with all 15 decisions is in `docs/SIMULATION_PROTOCOL.md` section 15.
- **Rules changed in code:**
  - **Stops:** BUY and HOLD must carry a stop.
  - **Horizons:** long is 7-15 months (133-285 sessions).
  - **Settlement:** the lag is read by entry date (T+3 throughout until T+2's official start is confirmed).
  - **SELL grading:** includes the holder's sell-side costs. The HOLD and SELL reference is the system's own entry price when it holds a position.
  - **Open calls:** one open call per stock (`OpenCalls`), and a SELL can close an open BUY (`sell_after`, exit reason `sell_call`).
  - **Holdout:** calls that would reach 2025-09-30 are `ungraded` (`reaches_holdout`), and a holdout bar in a path is refused.
  - **Sealed grades:** 2024 long grades that use 2025 prices are hidden until `exam_2025` has run (`hidden`).
  - **Rounding:** `round_score`, halves away from zero.
  - **Commission:** the 2024 tier 5 is now 0.243% (the higher published reading).
- **Floorsheet OHLC before 2018-02-18** (`src/simulation/floorsheet_ohlc.py`):
  - **Inputs:** read-only floorsheet files dated 2014-06-01 to 2025-01-19, and `daily_prices` on the research role over the same dates.
  - **Thresholds,** fixed before the first run: exact ≥ 90%, within 1% ≥ 98%, and within 1% ≥ 97% with 2% of pages dropped. Each holds over the whole window and on 15-digit contract numbers.
  - **First run, all trades,** 341,979 symbol-days, exact / within 1%: open 98.4 / 99.1, high 99.2 / 99.6, low 91.1 / 95.3. Open fails on 15-digit numbers (within 1% 97.4) and low fails everywhere.
  - **Why the low failed:** the derived low was below the published low on 99.7% of low mismatches. Leaving out odd lots (fewer than 10 units) fixed it.
  - **Board lots,** 341,745 symbol-days: open 99.5 / 99.8, high 99.8 / 99.9, low 99.8 / 99.9. On 15-digit numbers: open 95.9 / 99.2, high 99.8 / 99.9, low 99.9 / 99.9. Every check passes with page drops.
  - **The board-lot rule came after the first run;** both runs are in `docs/floorsheet_ohlc_verification.json`, and the owner is asked to confirm it (Q16).
  - **Contract order:** contract numbers are unique per symbol-day with the session date as prefix, and no symbol-day spans two segments. The last board-lot trade equals the published close on 99.7% of symbol-days, and the stored close before 2018-02-18 on 99.0%. A random trade equals the open on only 16.9%.
  - **Missing trades:** at 1-2% and > 2% file gaps, the open matches exactly 96.9% and 96.3% of the time (99.6% below 1%).
  - **Derived bars:** 440,185 rows (98,173 before 2018-02-18) in `~/Desktop/arthasignal-ai/derived/floorsheet_ohlc/bars.parquet` with `source = floorsheet_derived`. `daily_prices` is untouched.
- **Fees:** SEBON and NEPSE official pages were searched and no circular stating the unconfirmed figures was found. Still unconfirmed:
  - commission before 2016-08 and the 2016-2020 tier split;
  - the SEBON fee after the October 2023 cut (0.014% reported, not found in force) and before 2020;
  - whether buyers pay the DP charge, and older DP amounts;
  - the 2026 CGT rates;
  - the T+2 start date.

  The documents to obtain are listed in Q21.
- **Trial table:** `python -m src.simulation.register` on the research role inserted 1 row (family `simulation_protocol`, fingerprint `a2e192bd…57f4`; family 2, trials 138). A rerun inserted 0. The server command is in `docs/PROD_DEPLOY.md` Part 11.
- **New open questions:** Q16-Q21 in `docs/SIMULATION_PROTOCOL.md` section 14.
- **Not done, by instruction:** no simulator, no score, no study on real data beyond the floorsheet OHLC verification. Nothing was read from the holdout.
- **Tests:** 24 in `tests/test_simulation_grading.py` and 3 in `tests/test_floorsheet_ohlc.py`. Suite: 671 passed.

## Simulation phase 3 - Protocol v1.2: Q16-Q20 decided

- **Version:** `sim-protocol-v1.2` replaces v1.1 (kept in git history at 2229c8d). Config SHA-256 `7d961a97a0352d306506e9fd38ecfa898030bef727669ea06ef457667e0e2e37`. The changelog is in `docs/SIMULATION_PROTOCOL.md` section 15.
- **Decisions:**
  - **Q16:** odd lots (fewer than 10 units) are confirmed excluded from the floorsheet-derived OHLC. The changelog records that the rule came after the first verification run, with both runs' numbers. Exact / within 1%:
    - all trades: open 98.4 / 99.1, high 99.2 / 99.6, low 91.1 / 95.3;
    - board lots: open 99.5 / 99.8, high 99.8 / 99.9, low 99.8 / 99.9.
  - **Q17:** the target/stop rule is chosen on learning years by call-accuracy edge over the same-date baseline, then risk control score, among rules with PBO ≤ 0.3 (`target_stop.max_pbo`).
  - **Q18:** WAIT never holds a slot and is blocked while another call is open.
  - **Q19:** a BUY's exit closes the simulated position. HOLD and SELL use the call-date close unless a SELL closes an open BUY, and the grader refuses position fields on any other call.
  - **Q20:** `hidden()` now seals every grade, for all horizons and call types, that uses prices from a later exam year that has not run yet, until all such years have run.
- **New open question:** Q22, about learning-year calls that use check-year (2020) prices, which are not sealed because 2020 is not an exam year.
- **Trial table:** `python -m src.simulation.register` on the research role inserted 1 row (family `simulation_protocol`, fingerprint `9d87c529…77e2`; family 3, trials 138 → 139). A rerun inserted 0. The server command is in `docs/PROD_DEPLOY.md` Part 11, updated for v1.2.
- **Not touched:** no prices or holdout data were read in this phase.
- **Tests:** 8 tests added or changed in `tests/test_simulation_grading.py`. They cover sealing of short and mid grades for each graded call type, sealing across several later exam years, no sealing within a call's own year or from learning into the check year, WAIT slot handling, a BUY exit closing the position, position fields refused on non-SELL calls, and the v1.2 config values. Suite: 679 passed.

## Simulation phase 4 - Protocol v1.3: learning embargo (Q22)

- **Version:** `sim-protocol-v1.3` replaces v1.2 (kept in git history at a200731). Config SHA-256 `22f2065e095ac89c324e1ae6e4fb28f4ac51cb54755f8fa2cc59ff5f489b0c89`.
- **Rule:** in learning runs, a call is made only if its whole holding period ends by 2019-12-31. Calls that would cross into 2020 are not made (an embargo, not sealing).
- **Code:**
  - `learning_call_allowed` decides whether a call may be made;
  - `grade(..., sessions_before_learning_end=n)` refuses crossing calls, call dates after 2019 and any path bar after 2019-12-31;
  - a delayed exit past 2019-12-31 is `ungraded` (`learning_embargo`).
- **Trial table:** `python -m src.simulation.register` on the research role inserted 1 row (fingerprint `1847392b…fe89`; family 4, trials 140). A rerun inserted 0. `docs/PROD_DEPLOY.md` Part 11 now registers v1.3 on the server.
- **Tests:** 5 added. They cover crossing calls refused for every call type, calls ending by 2019 graded including the exact boundary, 2020 bars refused, delayed exits ungraded, and check/exam runs not embargoed. Suite: 684 passed.

## Data phase A - Instruments

- **Debentures:** ADBLB, ADBLB86 and ADBLB87 were relabelled from Equity to Non-Convertible Debentures, sector Debenture. Evidence (already stored): Sharesansar "Corporate Debentures" for all three, and Merolagani "Corporate Debenture" or "Others".
- **Empty-symbol row:** the `companies` row with symbol `''` carried 16 price rows (2014-07-08 to 2015-06-18), each a different stock.
  - **Mapped from our floorsheet:** 15 rows, each matching exactly one symbol on the same day with the same quantity, turnover and last trade: CCBL 2, JEFL 6, MLBBL 5, LFLCPO 1, MMDBL 1.
  - **2015-05-25 (no floorsheet file):** Sharesansar's own price page for that day has two blank-symbol rows. Each previous close matches exactly one symbol's previous stored close, and neither symbol has a row that day: NBBL (stored row moved) and JEFL (the second row had been dropped as a duplicate; now inserted).
  - **Result:** 0 blank-symbol rows left, and the empty `companies` row is removed.
  - **Root cause:** the Sharesansar price page sometimes leaves the symbol blank. `is_valid_price_row` now rejects a blank symbol (tested).
- **Truncated debenture symbols:**
  - **`NIFRAUR85/`** is the 10-character truncation of `NIFRAUR85/86` ("Nifra Urja 7% -2085/86" in the floorsheet). It agrees with the floorsheet on 69 of 70 checkable days (the other has the same quantity and a different close). 157 rows were moved where the full symbol had no row. 2 holdout-period rows clash with existing full-symbol rows and were left, and the short company row is relabelled as a debenture.
  - **`NICAD 85/8`** (2 rows, 2024-05-07/08) has **no** matching NICAD85/86 trades in the floorsheet, so it is left unchanged and reported.
- **Sectors from an additional public source:** Nepal Rastra Bank's class-wise merger list and its list of BFIs (Chait 2074). Both are official PDFs, and NRB's robots.txt allows them; they are stored in `~/Desktop/arthasignal-ai/raw/nrb/`. A sector was assigned only where a Sharesansar page names the company and NRB gives that company's licence class:
  - BOK → Commercial Banks (A);
  - DIYALO → Development Banks (B);
  - KMBL → Development Banks (B);
  - UMB → Microfinance (class D section).
- **Remaining equities without a sector: 9** (from 18).
  - **Eight with no name source:** ARUN, CLBSL, KMBSL, NGBBL, NLBSL, NMBMB, RMFL, WMBF. Sharesansar returns 404, and only Merolagani has pages for them. Merolagani's terms forbid automated collection, so it was not retried.
  - **Names known but not in NRB's lists:** the floorsheet promoter-share lines name ARUN as Arun Finance and WMBF as World Merchant Banking & Finance, and neither appears in the NRB lists. They stay unknown rather than guessed.
  - **NICAD 85/8** is the ninth.
- **Code and evidence:**
  - `src/database/instrument_fixes.py evidence` writes `docs/instrument_fixes.json`, reading on the research role plus the floorsheet and NRB PDFs, with one Sharesansar request.
  - `deploy/migrations/m003_instrument_fixes.py` applies it from the committed JSON: one transaction, advisory lock, idempotent, with a dry run and an `ops_migration_runs` log.
  - Laptop run: first run applied, second run "already applied". `data_quality --sectors` exits 0. `docs/PROD_DEPLOY.md` Part 10 step 5c has the server command.
- **Terms check (new):**
  - **Merolagani:** its Disclaimer/Terms page "strictly prohibit[s] … any automated data collection methods … regardless of their intended purposes". No new Merolagani collection was done.
  - **Existing code that still fetches Merolagani:** `src/collectors/news.py` (live news timer), `src/scrapers/quarterly_reports_collector.py`, `fundamentals_scraper.py`, `market_data.py`, `merolagani_scraper.py`, `sector_sources.py`, `symbols.py` and `eps_reconciliation.py`. This is flagged for the owner's decision.
  - **Sharesansar:** its Terms & Conditions page has no anti-automation clause, and its robots.txt allows everything.
- `pypdf` added to `requirements.txt`. Tests: 5 added (`tests/test_instrument_fixes.py`) and 3 assertions in `tests/test_price_session_backfill.py`.

## Data phase B - Point-in-time rule for every information source

- **New rule** (`docs/POINT_IN_TIME.md`, `src/backtest/knowledge_time.py`): information is usable from the first session strictly after the earliest date among sources whose date for that item passes verification. Every check uses only the row and rows its source created earlier, so the rule is stable when the data are cut at any date.
- **Reliability, measured on stored rows** published to 2025-09-29 (research role):
  - **Sharesansar:** the slug creation date equals the shown date on 97.7% of 4,669 slugged rows. 1.97% are back-dated by more than 1 day (worst 273). 0.09% are dated before the quarter they report.
  - **Merolagani:** 6.3% of rows are more than 7 days before the median of the previous 100 IDs (100% in 2009-2011 and 74% in 2012, from bulk-loaded quarter-end dates; 0.5-5.5% from 2016). 0.14% are before quarter end.
  - **Between the two** (9,558 reports): 62.5% same day, 89.1% within 1 day. When Merolagani is earlier by more than 1 day (887), its ID order confirms 571 and contradicts 316.
  - **NEPSE notices:** not measurable, because the notice API answers 401 and is not bypassed.
- **Verification:**
  - Sharesansar uses max(shown, slug date).
  - Merolagani uses max(shown, median of the previous 100 IDs' dates).
  - Either is rejected when dated before its quarter end.
- **Effect** on 11,109 net-profit reports: 10,340 unchanged, 733 known earlier (median 18 days), 36 later, 13 dropped.
- **Wiring:** `info_eval.load_reports` and the ranker's `truncate` now use the new rule.
- **Leak audit rerun** (`python -m src.ranker.leak_audit`, research role, 41 s, the same 20 seeded dates): **0 mismatches** (r1 with the old rule had 12). Output: `docs/ranker_leak_audit_pit.json`.
- **Dividend announcement dates** (Sharesansar table): 74 of 890 are later than their own book-close date. They are late, never early, but not a true first-public date; Phase D adds announcement-dated events.
- **Tests:** 6 in `tests/test_knowledge_time.py`, including one showing the old later-of rule changes under truncation and the new one does not. The ranker fixture gained `ss_date`.

## Data phases C-F - Collection infrastructure (in progress)

- **Polite client** (`src/archive/polite.py`):
  - checks robots.txt;
  - enforces one request per 3 s per host across all processes through a file lock (floor 2 s);
  - retries 8 times with backoff up to 5 minutes, which rides out network outages.
- **Raw store** (`schema.store_raw`): files are addressed by SHA-256 under `~/Desktop/arthasignal-ai/raw/archive/` and never overwritten.
- **New tables** (`src/archive/schema.py`, `python -m src.archive.schema`): `corporate_announcements`, `company_event_records`, `archive_documents`, `news_articles`, `news_symbol_mentions`, `report_field_values`, `policy_events`, `sentiment_observations`, plus `archive_progress` for resume state.
  - **Append-only:** each table rejects UPDATE and DELETE in a trigger.
  - **Holdout:** each carries the holdout row-level-security policy on its date column, registered in `holdout_guard.PROTECTED_COLUMNS`.
  - **Tests** (`tests/test_archive_holdout.py`, 11): every policy exists and is forced; for each table, a pre-holdout row and a holdout row are inserted in a rolled-back transaction, and under the research role only the old row is visible. Append-only behaviour and the raw store are also tested.
- **Terms:** Sharesansar's Terms & Conditions have no clause against automated collection, and its robots.txt allows everything. NRB and CDSC robots allow everything. Merolagani is excluded (phase A).
- **Running in the background** (resumable, logs in `logs/`; coverage is reported when each finishes):
  - `sharesansar_company`: announcements, AGM and dividend tables per company (phase D);
  - `sharesansar_news`: the news archive, walking back from 2025-09-30 (phase E);
  - `report_documents`: quarterly-report images (phase C);
  - `nrb_documents`: monetary-policy documents (115 PDFs stored) and the monthly macroeconomic reports (phase D/F).
- **Policy events:** 36 rows loaded from `docs/policy_events.json` (34 NRB measures plus 2 trading rules), each with an evidence sentence and an announcement-date basis. Dates before 2020 are from secondary sources and are marked unconfirmed.
- **Market series** (phase F, `python -m src.archive.sentiment_series market`): turnover, symbols traded, advances, declines, unchanged and breadth for 2,594 sessions, 2014-06-01 to 2025-09-28 (15,563 rows), dated by session close.
- **OCR sample** (phase C): 72 sampled reports (6 per year, 2014-2025) gave 71 images; one page is a 404. 11 gold labels were read from the images (`docs/ocr/gold_labels.json`). The engine comparison is still running.
- Suite: 706 passed.

## Data phase G - Floorsheet completion (skipped, steps written)

- **Not merged.** The VM at 3.89.163.188 did not answer: SSH port 22 timed out, so it is stopped or its IP changed. The backfill status could not be checked, and by instruction nothing was pulled or merged.
- **Terms concern.** The floorsheet archive and the VM backfill are Merolagani collections, and Merolagani's terms forbid any automated collection. The owner must decide before any merge.
- **Steps:** `docs/FLOORSHEET_COMPLETION.md` has the exact steps: find the VM, check its 99 `.done` markers, stage with `vm_pull_raw.sh`, merge with `--merge`, review the conflicts, audit as in `FLOORSHEET_AUDIT.md`, rebuild the derived bars and broker features, and report.

## Data phase D - Event tables (in progress)

- **`announcement_events`** (derived, holdout-protected, tested): a version-2 classifier over `corporate_announcements`. It separates quarterly reports (including "final quarter"), right-share issues, dividend proposals, approvals and distributions, book close, AGM/SGM, IPO/FPO, auctions, mergers, promoter sales, debentures, bank interest-rate notices, annual reports and registrar changes. It also parses cash and bonus percentages, right ratios and fiscal years from titles.
- **Where dividend proposals are:** Sharesansar company announcements rarely state them; most dividend titles are distributions or reminders to collect unpaid dividends. Proposals are in the AGM table (`company_event_records`), whose agenda lists cash and bonus percentages.
- **`dividend_proposals_pit` view** (`security_invoker`, so base-table holdout policies apply to the caller; tested):
  - **Knowledge date:** the earlier of the first AGM announcement in the 90 days before the meeting and the book-close date. Both are dates by which the proposal was certainly public, so the earlier one is still safe.
  - **Coverage:** this extends announcement-dated dividend data back to 2011 (2014: 26 proposals so far). The old `dividend_declarations` table starts in 2018.

## Data phase D - Corporate events and policy (collected)

- **Sharesansar company collection finished:** 515 companies done, 9 without a Sharesansar page, 0 errors after a rerun of the 6 that failed during a DNS outage, and 2,534 requests at one per 3 s.
  - **Stored:** 31,724 announcements on 502 symbols (2011-2026; one row carries a source date in year 1), 3,469 AGM records and 1,824 dividend-table records.
  - **Raw JSON per company** is kept immutable under `raw/archive/sharesansar_company/`.
- **Announcements per year:** 2014: 1,695; 2015: 1,999; 2016: 1,961; 2017: 2,373; 2018: 2,299; 2019: 2,297; 2020: 2,175; 2021: 2,616; 2022: 2,692; 2023: 3,256; 2024: 3,039; 2025 (to 2025-09-29): 2,330.
- **Event classes 2014-2025** (`announcement_events`, events_v2): AGM 235-370 a year; right-share announcements 60-544; dividend distributions 20-158; book-close titles 1-17; IPO/FPO 31-402; quarterly reports 707-1,009. Dividend proposals almost never appear as announcement titles (13 in 12 years).
- **Dividend proposals, point-in-time** (`dividend_proposals_pit`, from AGM agendas):
  - 2014: 137, 2015: 120, 2016: 156, 2017: 126, 2018: 115, 2019: 136, 2020: 81, 2021: 137, 2022: 99, 2023: 124, 2024: 104, 2025: 34;
  - 93-99% are dated by an AGM announcement, the rest by the book-close date.
  - This extends announcement-dated dividend data from 2018 back to 2011. Full table: `docs/corporate_events_coverage.json`.
- **NRB policy events:** 36 rows (`docs/policy_events.json`): policy rate, corridor bounds, bank rate, CRR, SLR, CCD/CD ratio and margin-lending rules from 2014/15 to 2025/26, each with an evidence sentence. Pre-2020 announcement dates come from secondary sources and are marked unconfirmed; 2017/18 and 2019/20 still lack one.
- **Trading-rule notices:** NEPSE notices cannot be read (API answers 401). The T+2 approval (January 2021, start date unconfirmed) and the Monday-Friday week (2026-04-08) are recorded with sources. The circuit band change to 15% (2026-04-20) used in the code has no recorded source, and earlier trading-hour changes were not found; both are gaps.
