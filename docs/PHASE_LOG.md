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
