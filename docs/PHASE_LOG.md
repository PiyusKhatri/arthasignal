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
