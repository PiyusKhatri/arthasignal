# Open Issues

As of 2026-09-30, after the floorsheet, calendar, re-grade and index fixes. Evidence and numbers are in `docs/DATA_READINESS.md` and `docs/HONEST_STATUS.md`.

Severity: **Critical** blocks trust in anything shown to users. **High** blocks modelling or corrupts results. **Medium** degrades a feature or a measurement. **Low** is cleanup.

## Data

| # | Severity | Issue | Detail |
| --- | --- | --- | --- |
| D2 | High | No real opening price before 2018-02-18 | Prices now start 2014-06-01, but the public source's `open` equals the previous close until 2018-02-15. Next-open entry can only be tested from 2018-02-18 |
| D3 | High | Floorsheet history is 18 days | Pruning is stopped, but rows before 2026-08-31 were already deleted here. About 2,930 sessions since 2014 are missing |
| D4 | Medium | Survivorship gap narrowed | 211 of 259 delisted and 6 of 25 suspended equities now have prices. 48 delisted still have none |
| D5 | High | Two databases have diverged | The calendar rebuild, signal-call re-grade and index repair were run on the local database only. The Supabase database rejects this machine's login, so its state is unknown and none of these repairs have been applied there |
| D7 | Medium | Index `open` is a placeholder for new broad-index rows | The NEPSE API gives high and low but no open, so the refresh writes `open = close` for the four broad indices. `repair_index_dates` corrects it from the public history when run |
| D8 | High | `adjusted_close` is partial and disagrees with an independent source | Present on 67.4% of equity rows; only 56.5% of factors match Merolagani within 2% (2015-2021 sample). Use raw prices with corporate-action exclusion windows |
| D9 | Medium | Non-equity rows in `daily_prices` since 2026-08-21 | About 45 mutual funds and 33 debentures a day. Any universe built without an instrument-type filter includes them |
| D10 | Medium | Intraday snapshot tables are empty | `intraday_snapshots` and `intraday_index_snapshots` have 0 rows locally. Price alerts read the latest intraday price, so they cannot trigger here |
| D11 | Medium | No fundamentals history | Ten weekly snapshots since 2026-07-23; fiscal-year labels unreliable (272 `unknown`, 141 `2026/2027`). No quarterly reports |
| D12 | Medium | No text or event data | No news, NEPSE or SEBON notices, or NRB policy statements. Macro series are keyed by fiscal year and month name, not date |
| D13 | Low | `companies.listed_date` empty for all 644 rows | Cannot tell when a symbol entered the universe |

## Pipeline

| # | Severity | Issue | Detail |
| --- | --- | --- | --- |
| P2 | Medium | A change of trading week can stall ingestion | The first day of a newly traded weekday is classed as a weekend until prices for it arrive another way. NEPSE has changed its week twice in this data (2022 Fridays, April 2026) |
| P3 | Medium | Missed price sessions are not detected | D1 went unnoticed for two months. Nothing compares stored price dates against the public index history |
| P4 | Low | The test suite writes to the configured database | Each run of the auth integration test leaves a `ci-…@example.com` user; `users` holds 17 rows, several from tests |

## Signals and models

| # | Severity | Issue | Detail |
| --- | --- | --- | --- |
| M1 | Critical | Forward results contradict the tiers shown to users | After the re-grade the two oversold signals are negative after cost: `rsi_14 < 30` −3.99% on 42 calls, `close < bollinger_lower` −6.71% on 100 calls. Only 4 of 20 required entry days have resolved, so this is early, but the user-facing tiers still rest on July backtests |
| M2 | High | No model has passed its own gate | V2 to V4.1 and E1 all failed; V5 was never run. All are archived |
| M3 | High | V1 is served and retrained weekly with no recorded validation | No V1 result exists, and this database has no model snapshot or shadow signal at all |
| M4 | Medium | Rule-based signal backtests use an untradable entry | `backtest_signals.py` enters at the signal-day close and exits at the symbol's own n-th later price row. They do not use the trading calendar, so the calendar bug did not affect them |
| M5 | Medium | Those backtests are stale and survivor-biased | Computed 2026-07-23 to 2026-08-01 on the survivor-only universe (D4), mixing adjusted and raw closes (D8). Not rerun |
| M6 | Low | Own-row horizon drifts for thin stocks | For 97.8% of price rows the 20th later row is exactly 20 market sessions away; for 0.6% it is more than 23 sessions, up to 595 |
| M7 | Medium | Forward capture for V4.1 and E1 is switched off | Removed from the daily workflow when the code was archived. Their ledgers are empty here |

## Backtest framework

| # | Severity | Issue | Detail |
| --- | --- | --- | --- |
| B1 | Medium | Skeleton only | No loader turns prices into rows, no portfolio simulator, no block-bootstrap interval |
| B2 | Medium | The holdout is not fully unseen | 2025-09-30 to 2026-07-23 overlaps the last test fold of the archived research. Only data after 2026-07-23 is unseen by every model |
| B3 | Medium | The guard cannot stop direct SQL | Holdout labels can still be computed by querying `daily_prices`. Only results logged in `backtest_holdout_evaluations` should count |

## Project

| # | Severity | Issue | Detail |
| --- | --- | --- | --- |
| X1 | Critical for launch | Legal review outstanding | Public or paid signals are blocked until a Nepal securities lawyer advises on SEBON requirements (`TODOS.md`) |
| X2 | Medium | SMTP not configured | Password reset cannot deliver in production |
| X3 | Low | `docs/ARCHITECTURE.md` is out of date | Omits quant, intelligence, alerts, the archive and `src/backtest/` |
| X4 | Low | `docs/HONEST_STATUS.md` cites pre-archive paths | A path note at the top points to the mapping |
| X5 | Low | Stale `venv/` directory | Created on another machine and unusable here; `.venv/` is the working one |

## Fixed on 2026-09-30

- Floorsheet is no longer pruned.
- The trading calendar counts only days with real prices and flags 167 known holidays.
- 1,046 signal calls were re-graded on real sessions.
- Index rows are stamped with the source session date; 117 mis-dated rows were moved or removed, 136 missing rows were restored from the public history, and the NEPSE Index is present on every price session.
- The malformed `.env` line is split.

## Fixed on 2026-10-01

- D2/D14: prices, index history and the calendar now start 2014-06-01 (596,256 price rows, 2,822 sessions), validated against the floorsheet (0.32% volume mismatch) and existing rows (0 mismatches).
- P1: scheduled workflows now run only on manual dispatch; CI only on pull requests.
- D6: the index refresh falls back to the public index history, and a data-quality check fails when any price session lacks a NEPSE Index row.
- D1: 2026-07-27 prices backfilled (358 rows), calendar rebuilt, 494 signal calls re-graded.
