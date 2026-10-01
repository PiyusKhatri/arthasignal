# Broker-Flow Research: Pre-Registration

Protocol `broker-flow-prereg-v1`, written and committed on 2026-10-01, **before any broker-flow feature or return was computed**. The variants below are registered in `backtest_variant_trials` under the model family `broker_flow_prereg_2026_10`. Their parameters live in `src/backtest/broker_flow_spec.py`, and the ledger fingerprint is the SHA-256 of those parameters. If the spec file changes after this commit, the fingerprints change and the registration no longer matches this document.

## What was known before writing this

- The structure of the floorsheet Parquet files (columns, files per year, broker counts per year, the `data_quality_gap_pct` column). 2,415 files, 2014-06-02 to 2026. 50 buying brokers a year until 2022, 84 in 2023, 92 in 2024. The mean missing-trade gap is 1.3-2.1% a year in 2015-2018 and under 0.3% from 2019.
- The Phase 6 rule-signal rerun (`docs/BACKTEST_RERUN.md`): oversold signals trail the equal-weight universe, and stocks in their first 60 sessions after listing dominate momentum results. That result is why new listings are excluded below.
- No broker-level return statistic has been computed in this repository or in `archive/quant_research_v1`. `market_pulse` shows the largest single-broker share for display only.

## Data and windows

- **Source:** floorsheet Parquet under `~/Desktop/arthasignal-ai/raw/floorsheet` (read-only). Prices, corporate actions and the NEPSE Index come from local Postgres `arthasignal`.
- **Development window:** signal dates from 2014-06-01, and **every price used, including exit prices, on or before 2025-01-19**. Floorsheet files dated after 2025-01-19 are never opened, and neither are price rows after that date. The last usable signal date is therefore about 20 sessions before 2025-01-19.
- **Holdout:** everything after 2025-01-19 is out of scope for this protocol. The repository holdout (from 2025-09-30, `src/backtest/holdout_config.json`) is untouched. `src/backtest/broker_flow_config.json` is a copy of that config with `holdout_start` set to 2025-01-20, so `partition_rows` and `assert_development_only` enforce the earlier cut.
- **Point in time:** every feature for day *t* uses only floorsheet trades dated ≤ *t* and prices with close ≤ *t*. Any outcome used inside a feature (H4) must have finished at least 5 sessions before *t*.

## Universe on signal date *t*

A symbol-day is eligible only if all of the following hold:

1. The symbol is in `companies` with `instrument_type = 'Equity'` (any status, including delisted and suspended). Floorsheet symbols missing from `companies` are excluded, and Phase C reports how many there are.
2. A floorsheet file exists for *t*, and the symbol has at least 5 trades in it.
3. The symbol has a `daily_prices` row on *t* and traded on at least 15 of the 20 market sessions *t*-19..*t*.
4. At least 60 market sessions have passed since the symbol's first price row. Symbols whose first row is on or before 2014-07-01 are treated as seasoned.
5. A label exists (entry rule below), and no BONUS or RIGHT action date and no circuit-breaking price discontinuity (the same rule as `signal_rerun.discontinuity_dates`) falls between *t* and the exit date. These two exclusions use dates after *t*. They remove rows with corrupt raw-price returns and are applied identically to every portfolio and baseline. This is a stated limitation, not a tradable rule.

Each hypothesis is evaluated on dates with at least 25 eligible symbols whose feature is defined.

## Target

- **Label:** the 20-session gross return of a long position.
  - For signal dates where session *t*+1 is on or after **2018-02-18** (the first session with real opening prices): entry at the **open of session *t*+1**, exit at the **close of session *t*+20** (`src/backtest/entry.next_open_label`, 3-session grace for a missing exit trade).
  - Before 2018-02-18 the stored `open` equals the previous close on about 99% of rows, so it is not a tradable price. The rule there is **entry at the close of session *t*+1 and exit at the close of session *t*+21**: same holding length, one session later than the signal, and no use of the day-*t* close. If the stock does not trade on *t*+1, the row has no label.
- **Excess return:** portfolio gross return minus the cost level, minus the **equal-weight universe** gross return for the same *t* (the mean gross label of every eligible symbol with the feature defined on *t*). The benchmark is held passively and pays no cost.

## Hypotheses

Notation, per symbol *s*: for broker *b* over a window *W* of the symbol's floorsheet trades, `buy_b` and `sell_b` are traded quantities, `net_b = buy_b - sell_b`, and `V` is total traded quantity in *W*. All features are **ratios of quantities or prices**, so a uniform 1-2% loss of trades (2015-2018) leaves them essentially unchanged. No feature uses an absolute volume. Windows count market sessions on which a floorsheet file exists, ending at *t*.

The portfolio is the **20% of eligible symbols (rounded up) predicted to outperform**, equal-weighted. NEPSE does not allow short selling, so every test is long-only. Ties are broken by symbol.

| ID | Feature (computed at the close of *t*) | Window | Predicted direction | Portfolio |
| --- | --- | --- | --- | --- |
| **H1** Top-5 net buying | Sum of `net_b` over the 5 brokers with the largest positive `net_b`, divided by `V` | 5 sessions *t*-4..*t* | High → outperforms | top quintile |
| **H2** Buy-side vs sell-side concentration | HHI of broker buy shares (`Σ (buy_b / V)²`) minus HHI of broker sell shares | 5 sessions | High (few buyers absorbing many sellers) → outperforms | top quintile |
| **H3** Rise in buy concentration | Buy-side HHI on day *t* minus the mean daily buy-side HHI over the previous 20 sessions *t*-20..*t*-1 (at least 10 of those days with trades, otherwise undefined) | 1 + 20 sessions | High → outperforms | top quintile |
| **H4** Early-buyer imbalance | `(Σ_{b∈E_t} buy_b - Σ_{b∈E_t} sell_b) / V` for the set `E_t` of historically early brokers (definition below) | 5 sessions | High → outperforms | top quintile |
| **H5** Close vs top buyers' cost | `close_t / VWAP_buy - 1`, where `VWAP_buy` is the quantity-weighted average price of all buy trades by the 5 brokers with the largest positive `net_b` over the window. Undefined if a BONUS or RIGHT date falls in *t*-19..*t* | 20 sessions *t*-19..*t* | Low (price below big buyers' cost) → outperforms | bottom quintile |

**Early-buyer set `E_t` (H4).** A *large move* is a symbol-day *u* (eligible by rules 1-4) with `close(u+20) / close(u) - 1 ≥ 0.20` on raw closes, no BONUS or RIGHT date in *u*..*u*+20, and a floorsheet file on each of *u*-4..*u*. For each broker, its pre-move net share for that event is `net_b / V` over *u*-4..*u* (0 if it did not trade). Its score is the mean pre-move net share over events minus its mean net share over all eligible symbol-days in the same lookback. The lookback is symbol-days *u* in the 500 market sessions before *t* with *u*+20 ≤ *t*-5 (5-session embargo after the outcome is known). `E_t` is the 10 brokers with the highest positive score. It is recomputed on the first session of each 20-session block and held fixed within the block. If the lookback has fewer than 100 events, `E_t` is empty and H4 is undefined. H4 therefore starts about 500 sessions into the data.

No window length, threshold, quintile size or direction may change after this commit.

## Baselines

All baselines are evaluated on the same signal dates as each hypothesis.

1. **Equal-weight universe:** the mean gross label of all eligible symbols with the feature defined on *t* (also the excess benchmark).
2. **NEPSE buy-and-hold:** the NEPSE Index from the entry session to the exit session, using index open to close from 2018-02-18 and index close to close before that (`src/backtest/entry.benchmark_return` semantics).
3. **Momentum:** the top quintile of the same eligible rows by 12-session rate of change of close (`roc_12`, `src/backtest/baselines.simple_momentum_selection`), same label and costs.

## Costs

Round-trip costs of **0.5%, 1.0% and 1.5%** are subtracted from every portfolio label. The primary gate uses 1.0%.

## Statistics

- **Clustering:** each signal date contributes the mean excess return of its portfolio. Labels on neighbouring dates overlap by up to 19 sessions, so the primary interval clusters by **20-session date blocks**: consecutive market sessions in non-overlapping blocks of 20, each block's value being the mean of its daily means. The plain per-date clustered interval (`src/backtest/stats.clustered_mean_interval`) is also reported. **Gates use whichever interval has the lower lower bound.**
- **Multiple testing:** Bonferroni over the 5 hypotheses of this family, so α = 0.05 / 5 = **0.01** (two-sided 99% interval). The ledger holds 26 earlier rule-signal variants. The interval at α = 0.05 / 31 is also reported for information.
- **Time folds:** the four test periods of `src/backtest/splits.walk_forward_folds` over the development sessions (252 minimum leading sessions, purge 24 sessions = horizon + grace + 1, embargo 5 sessions). The pooled statistic for G1 uses signal dates inside the four test periods only. Nothing is fitted; the leading sessions are used only for the rolling windows and H4's lookback. A fold with fewer than 50 signal dates for a hypothesis counts as not positive.

## Pass/fail gates (fixed)

A hypothesis **passes** only if every gate holds:

| Gate | Condition |
| --- | --- |
| **G1** | Mean excess return at 1.0% cost: the lower bound of the α = 0.01 interval (the more conservative of the two clusterings) is **> 0** |
| **G2** | Mean excess return at 1.5% cost: point estimate **> 0** |
| **G3** | Mean excess return at 1.0% cost is **> 0 in at least 3 of the 4** walk-forward test folds |
| **G4** | Mean portfolio return net of 1.0% minus the NEPSE buy-and-hold return over the same holding periods: point estimate **> 0** |
| **G5** | Mean portfolio return minus the momentum baseline return at the same cost: point estimate **> 0** |
| **G6** | On signal dates with entry from 2018-02-18 (real opens only): mean excess return at 1.0% cost, point estimate **> 0** |

Every hypothesis is reported with all six gates, including those that fail. A failure is reported as a failure. Reported only for information, never as gates: rank correlation with the excess label, the top-minus-bottom quintile spread, and coverage.

## What is forbidden after results are seen

- Adding, removing or redefining hypotheses, windows, thresholds, directions, universe rules, costs or gates.
- Training any machine-learning model on these features.
- Using any data after 2025-01-19.
- Promoting any idea that appears while looking at results. It goes into an **Exploratory** section of `docs/BROKER_FLOW_RESULTS.md`, marked as not evidence, and would need a new protocol version and new data to be tested.

A pass here means only that the hypothesis may be put forward for a separate, newly registered test on data after 2025-01-19. It does not mean a product signal.

## Ledger registration

Registered on 2026-10-01 with `python -m src.backtest.broker_flow_spec --register` (ledger ids 40-44, 31 variants in the ledger after registration). Development config `2026-10-01-broker-flow-dev-v1`, SHA-256 `d4dbc2786938d661c5c187602107e0ec04b06ac03d2db72f34d0154548eac26f`.

| ID | Variant fingerprint |
| --- | --- |
| H1 | `fff06a81203316ca913153b9cb0fe9730224c5028acdc8f0bd4f032bce01f483` |
| H2 | `cea7903627063e0de7467e251cf5f7cfb6d65834c2a085f85db5c4f3c3ca0063` |
| H3 | `5c2f25fca1cf3da558dc6c9eb7e4c71ba19d9a6b0c64578f1b44992ca3d6b976` |
| H4 | `e1e0066be09539e47e761266cc4e31e023d0bcc5c5782b2263aa6685dd95f686` |
| H5 | `133927ac850f394cc2f59a4902c219b4d72775f9e2e93105f6b90f35e3cbf77d` |
