# Event Research: Pre-Registration

Protocol `event-prereg-v1`, written and committed on 2026-10-01, **before any event return, abnormal return or market-timing result was computed**. Parameters are in `src/backtest/event_spec.py`. The eight variants are registered in `backtest_variant_trials` under the family `event_prereg_2026_10`, each fingerprinted as the SHA-256 of its parameters. The toolkit is `src/backtest/event_study.py` and the event tables are built by `src/backtest/event_tables.py`.

## What was known before writing this

- Event counts per type and year and detectable effect sizes (`docs/event_counts.json`). The SD of 20-session abnormal returns on random symbol-days is 14.8%.
- From building the toolkit: on the ex-session itself, after adjustment, bonus book closes have median return +0.2% (SD 3.6%) and rights book closes median +3.9%, with about a quarter closing near the upper limit. No window after the ex-session was looked at.
- The Phase 6 rule-signal rerun (`docs/BACKTEST_RERUN.md`): positive results for momentum-type signals came mostly from stocks in their first 60 sessions after listing. `rsi_14 > 70` on seasoned stocks was +3.56% over the universe per 20 sessions but unstable across folds. This is why E3 is predicted positive. E4 is predicted as a reversal, against that weak momentum evidence.
- The broker-flow study (`docs/BROKER_FLOW_RESULTS.md`): the equal-weight universe beat the NEPSE Index by about 0.9% per 20 sessions in 2015-2024, and the momentum baseline trailed the universe after costs.
- Nothing about returns after book closes, after listings, after circuit streaks, after volume spikes, or about market timing.

## Data, window and assumptions

- **Development window:** event knowledge dates from 2014-06-01, and **every price used, including exits, on or before 2025-01-19** (`last_index` in `simulate_trade`). Nothing after 2025-01-19 is read. The repository holdout (from 2025-09-30) is not touched.
- **Prices:** raw `daily_prices` for equities. Corporate actions are applied explicitly on the ex-session (the first session on or after the book-close date): the cash dividend net of 5% tax on pre-bonus shares, then bonus shares, then rights subscribed at par 100. One-session moves beyond the circuit limit + 2 points are treated as corrupt and the event is dropped.
- **Book-close announcement assumption:** the database has no announcement dates. E1 assumes the book-close date was public at least 16 sessions before the ex-session. If that is wrong, E1 overstates what was tradable. The E5/E7/E8 filter "no corporate action within the next 20 sessions" relies on the same assumption.
- **Survivorship:** corporate actions exist only for 171 symbols active when they were scraped, so E1 and E2 cover surviving companies only. E3, E4, E5, E7 and E8 use prices, which include delisted symbols.

## Common trade rules (all stock-event hypotheses)

- **Entry:** the open of the first session after the knowledge session (the close for sessions before 2018-02-18, when real opens begin). Up to 5 sessions are skipped while the stock does not trade or every trade is at the upper limit; after that the event is "not filled".
- **Exit:** the close of the target session. With an open entry the holding period is `hold_sessions` from the knowledge session; with a close entry it is `hold_sessions` after entry. The exit is deferred up to 20 sessions while the stock does not trade or every trade is at the lower limit; otherwise the position is stranded at the last trade.
- **Eligibility:** the stock traded on at least 10 of the 20 sessions up to the knowledge session. Hypotheses marked *seasoned* also require at least 60 market sessions since the symbol's first price (symbols whose first price is on or before 2014-07-01 count as seasoned).
- **Abnormal return:** gross trade return minus the equal-weight universe return over the same sessions (daily rebalanced; for an open entry, from the previous close). The sector version uses the equal-weight return of the stock's `companies.sector`. The NEPSE comparison uses the NEPSE Index over the same sessions.
- **Costs:** 0.5%, 1.0% and 1.5% round trip, subtracted from long trades. The primary level is 1.0%.

## Hypotheses

| ID | Events | Knowledge (*t*) | Hold | Predicted direction |
| --- | --- | --- | --- | --- |
| **E1** Book-close run-up | Book closes with a bonus share (`bonus_only`, `bonus_and_cash`) | ex-session − 16 | 15 sessions (exit at the last cum session's close with open entry) | **Long:** positive abnormal |
| **E2** Post-book-close drift | Same events | ex-session close | 20 | **Avoid:** negative abnormal |
| **E3** New listing after the initial run | New listings (81 merger symbols excluded): the first own trading day from day 2, within 60 market sessions of listing, whose close is below the upper-circuit threshold | that close | 60 | **Long:** positive abnormal |
| **E4** Circuit streak end | Upper-circuit close streaks of ≥ 3, outside the first 60 sessions after first price, that end | close of the first traded session not at the upper limit | 20 | **Avoid:** negative abnormal |
| **E5** No-news volume anomaly | Volume ≥ 5× the 60-session median, up day, no corporate action within ±20 sessions, ≥ 60 sessions since first price; per symbol, later events within 20 sessions of a kept event are dropped | event close | 20 | **Long:** positive abnormal |
| **E6** Market in/out rule | Daily | close of *t*, position from *t*+1 | n/a | **Timing:** better Sharpe and smaller drawdown than NEPSE buy-and-hold |
| **E7** E5 at 60 sessions | Same as E5 | event close | 60 | **Long:** positive |
| **E8** E5 at 120 sessions | Same as E5 | event close | 120 | **Long:** positive |

**E6 rule.** IN the NEPSE Index while its close is above its 200-session SMA **and** more than 50% of traded equities close above their own 50-session SMA. Otherwise OUT, earning the last T-bill rate published by then (45-day lag after the Nepali month ends; 0 before 2016-11-03), accrued over 240 sessions a year. Each switch costs half the round-trip cost (primary 1.0%, stress 1.5%). The evaluation starts on 2015-04-09, the first session with a 200-session SMA, and buy-and-hold is measured over the same sessions. The NEPSE Index cannot be traded directly. A version on the equal-weight universe is reported as a diagnostic only.

## Baselines

- Equal-weight universe (primary abnormal benchmark) and equal-weight sector.
- NEPSE buy-and-hold over the same holding sessions (and over the whole window for E6).

## Folds, clustering and multiple testing

- **Folds:** the development sessions split into **4 contiguous blocks of equal session count**. An event belongs to the fold of its entry session. Nothing is fitted or tuned, so no training set, purge or embargo is needed.
- **Clustering:** by entry date and by 20-session block of the entry session (`event_study.clustered_intervals`). Gates use the more conservative bound.
- **Multiple testing:** Bonferroni over the 8 hypotheses, **α = 0.05 / 8 = 0.00625** (two-sided). The ledger total after registration (39 variants) gives α = 0.05/39, which is also reported. `TestCounter` is set to 8 planned tests and refuses a ninth.

## Pass/fail gates (fixed)

**Long hypotheses (E1, E3, E5, E7, E8)** pass only if all six hold:

| Gate | Condition |
| --- | --- |
| G1 | Mean abnormal return vs the universe at 1.0% cost: conservative lower bound at α = 0.00625 **> 0** |
| G2 | Mean at 1.5% cost **> 0** |
| G3 | Mean at 1.0% cost **> 0 in at least 3 of 4 folds** (a fold with fewer than 20 entry dates counts as not passing) |
| G4 | Mean abnormal return vs the sector at 1.0% cost **> 0** |
| G5 | Entries from 2018-02-18 only (real opens): mean at 1.0% cost **> 0** |
| G6 | Mean of gross − 1.0% − NEPSE return over the same sessions **> 0** |

**Avoid hypotheses (E2, E4)** pass only if all five hold. They are judged on gross abnormal returns, with no cost, because the claim is that a holder should step aside:

| Gate | Condition |
| --- | --- |
| G1 | Mean gross abnormal return vs the universe: conservative upper bound at α = 0.00625 **< 0** |
| G2 | Mean **≤ −1.0%** (larger than a round-trip cost, so selling and buying back would pay) |
| G3 | Mean **< 0 in at least 3 of 4 folds** (a fold with fewer than 20 entry dates counts as not passing) |
| G4 | Mean gross abnormal return vs the sector **< 0** |
| G5 | Entries from 2018-02-18 only: mean **< 0** |

**Market rule (E6)** passes only if all five hold:

| Gate | Condition |
| --- | --- |
| G1 | Annualized Sharpe of daily returns at 1.0% switch cost **>** buy-and-hold Sharpe over the full window |
| G2 | Sharpe difference **> 0 in at least 3 of 4 folds** |
| G3 | Maximum drawdown **≤ 0.75 ×** buy-and-hold maximum drawdown |
| G4 | At 1.5% switch cost, Sharpe still **>** buy-and-hold |
| G5 | 20-session moving-block bootstrap (2,000 resamples, seed 20261001) of the Sharpe difference: lower bound at α = 0.00625 **> 0** |

Rows dropped, fill outcomes (not filled, delayed, stranded) and exit statuses are reported for every hypothesis. A failure is reported as a failure.

## What is forbidden after results are seen

Changing any event definition, window, direction, eligibility rule, cost, fold, benchmark or gate; adding hypotheses; training any model; using data after 2025-01-19. New ideas go only into an **Exploratory** section of `docs/EVENT_RESULTS.md`, marked as not evidence. A pass makes a hypothesis a candidate for a new registered test on later data. It is not a product signal.

## Ledger registration

Registered on 2026-10-01 with `python -m src.backtest.event_spec --register` (39 variants in the ledger afterwards).

| ID | Variant fingerprint |
| --- | --- |
| E1 | `8c360a7751d9665dca5f170e7a6c1197cc5362287a40df598b83d532ee675a44` |
| E2 | `a2e78666a652d7980cad14dd4dca082228a0531c1993141d3c3a7ac56b6afab1` |
| E3 | `a9492d818a64c9817d42045cf7f1bc10b6aafcb59e3dba9347a744d7c5f69666` |
| E4 | `e72cd6644c0a3c8fcaa00a80049dc4c097e3a1306a14c07d959e2e55eb206cf7` |
| E5 | `45ab294fb103d4cfc43027efbd28d063ae0bd183c8b15a19ef15736f171e1f2e` |
| E6 | `b9062cf9039697d916bfb66fe75627f58a749c497aa55fc36eba812242cdc153` |
| E7 | `5319947799d35005a32fda698bbf6f8cc5adf8405ed5356f49282e47ccbc9870` |
| E8 | `1a619419b0ad9ec3b5f5742ae2f7f8ebb15b0f2b2dc66a10443c41e12dc809fc` |
