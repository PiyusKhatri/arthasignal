# Broker-Flow Research: Results

Protocol `broker-flow-prereg-v1` (`docs/BROKER_FLOW_PREREG.md`, committed in 33518f4 before any feature was computed). The evaluation code was committed in 3203cb3 before it was first run. Raw output: `docs/broker_flow_results.json`. Command: `python -m src.backtest.broker_flow_eval`.

## Verdict

**All five pre-registered hypotheses fail.** None passes G1, G2, G3 or G6. After a 1% round-trip cost, every portfolio trails the equal-weight universe, and for four of the five the shortfall is negative in all four walk-forward folds. No broker-flow feature from this protocol should be used in a score, label or product signal. No machine-learning model was trained.

| ID | Feature | Mean excess at 1.0% cost per 20 sessions | 99% interval (20-session blocks) | Folds positive | G1 | G2 | G3 | G4 | G5 | G6 | Result |
| --- | --- | ---: | --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | --- |
| H1 | Top-5 net buy share (high) | −0.52% | −1.06 to +0.02 | 1/4 | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | **Fail** |
| H2 | Buy HHI − sell HHI (high) | −0.77% | −1.12 to −0.43 | 0/4 | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | **Fail** |
| H3 | Day-*t* buy HHI vs 20-day mean (high) | −1.10% | −1.33 to −0.87 | 0/4 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **Fail** |
| H4 | Early-buyer imbalance (high) | −0.79% | −1.02 to −0.56 | 0/4 | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ | **Fail** |
| H5 | Close vs top-5 buyers' VWAP (low) | −1.27% | −1.90 to −0.65 | 0/4 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | **Fail** |

The three G4 passes (beating NEPSE buy-and-hold) carry no information about broker flow. The equal-weight universe itself beat the NEPSE Index by about 0.9% per 20 sessions in this period, so any portfolio near the universe average clears that bar.

## What was run

- **Window:** signal dates 2014-06-19 to 2024-12-11. Last exit 2025-01-19, and the last price session loaded was 2025-01-19. 0 rows reached the holdout and 0 boundary rows were dropped (`partition_rows` with `src/backtest/broker_flow_config.json`, holdout_start 2025-01-20). The repository holdout (from 2025-09-30) was not read.
- **Rows:** 330,178 eligible feature rows. 303,997 had a label after exclusions: 8,169 with no label (no trade on *t*+1, or exit past 2025-01-19), 14,414 with a bonus or rights date in the trade, and 3,598 with a circuit-breaking price jump. Entry rule: 247,953 rows at the next open (from 2018-02-18) and 56,044 at the next close (before). Exits: 297,196 on time, 3,426 delayed, 1,450 blocked, 1,925 stranded.
- **Folds** (`walk_forward_folds`, 252 leading sessions, 29-session purge + embargo gap): 2015-11-22 to 2018-04-17, 2018-04-18 to 2020-08-24, 2020-08-25 to 2022-09-11, 2022-09-12 to 2024-12-11. That gives 1,986 pooled signal dates per hypothesis (105 twenty-session blocks), with on average 144 eligible symbols and 29 in each portfolio.
- **Multiple testing:** family α = 0.05/5 = 0.01. The ledger holds 31 variants (26 earlier rule signals plus these 5), so α = 0.0016 is also reported below.

## Detail per hypothesis

All returns are per 20-session holding period, averaged over the 1,986 pooled signal dates. "Excess" means portfolio minus cost minus the equal-weight universe.

| | H1 | H2 | H3 | H4 | H5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Portfolio gross | 2.57% | 2.32% | 1.99% | 2.30% | 1.85% |
| Universe gross | 2.10% | 2.10% | 2.10% | 2.10% | 2.13% |
| Excess, 0.5% cost (95%, by date) | −0.03 (−0.17, +0.12) | −0.28 (−0.38, −0.18) | −0.61 (−0.71, −0.51) | −0.30 (−0.38, −0.22) | −0.78 (−0.94, −0.61) |
| Excess, 1.0% cost (95%, by date) | −0.53 (−0.67, −0.38) | −0.78 (−0.88, −0.68) | −1.11 (−1.21, −1.01) | −0.80 (−0.88, −0.72) | −1.28 (−1.44, −1.11) |
| Excess, 1.0% cost (95%, 20-session blocks) | −0.52 (−0.93, −0.11) | −0.77 (−1.04, −0.51) | −1.10 (−1.28, −0.92) | −0.79 (−0.97, −0.62) | −1.27 (−1.75, −0.80) |
| Excess, 1.5% cost (95%, by date) | −1.03 (−1.17, −0.88) | −1.28 (−1.38, −1.18) | −1.61 (−1.71, −1.51) | −1.30 (−1.38, −1.22) | −1.78 (−1.94, −1.61) |
| Excess, 1.0% cost, α = 0.0016 (blocks) | −0.52 (−1.18, +0.14) | −0.77 (−1.19, −0.35) | −1.10 (−1.38, −0.82) | −0.79 (−1.07, −0.51) | −1.27 (−2.04, −0.51) |
| Fold 1 / 2 / 3 / 4, 1.0% cost | −0.70 / −1.23 / +0.14 / −0.32 | −0.87 / −1.02 / −0.70 / −0.54 | −1.27 / −1.28 / −0.81 / −1.06 | −0.45 / −1.10 / −0.73 / −0.93 | −0.96 / −1.35 / −1.19 / −1.60 |
| Real-open dates only (1,528), 1.0% cost | −0.51 | −0.78 | −1.07 | −0.92 | −1.29 |
| Next-close-entry dates (458), 1.0% cost | −0.60 | −0.79 | −1.24 | −0.39 | −1.23 |
| Portfolio net of 1.0% − NEPSE | +0.40 | +0.11 | −0.21 | +0.14 | −0.29 |
| Portfolio − momentum portfolio | −0.17 | −0.42 | −0.75 | −0.44 | −0.91 |
| *Diagnostics, not gates:* mean rank IC | +0.035 | +0.011 | −0.017 | +0.007 | −0.023 |
| Dates with positive IC | 57.2% | 53.0% | 45.3% | 51.9% | 41.8% |
| Predicted top − predicted bottom quintile (gross) | +1.25 | +0.43 | −0.51 | +0.31 | −0.71 |
| Daily portfolio turnover | 33% | 32% | 73% | 34% | 31% |

**Baselines on the same dates:** NEPSE buy-and-hold averaged 1.15-1.21% per 20 sessions. The momentum baseline (top quintile by 12-session rate of change) trailed the universe by 0.36% after a 1% cost, so it also has no edge. H1's 99% block interval at 1% cost (−1.06 to +0.02) is the only one that reaches zero. Its point estimate is still −0.52%, it is positive in one fold of four, and it fails five of the six gates.

## What failed, plainly

- **H1 (top-5 net buying):** fails. It holds a gross edge of about +0.47% per 20 sessions over the universe (rank IC +0.035), which a 1% round-trip cost more than wipes out. Even at 0.5% it is −0.03% and not distinguishable from zero.
- **H2 (buy vs sell concentration):** fails. It is negative after costs in every fold and has a gross edge of +0.22%.
- **H3 (rising buy concentration):** fails. Its sign is opposite to the prediction: the predicted top quintile trails the predicted bottom by 0.51% gross. It also turns over 73% a day.
- **H4 (early-buyer imbalance):** fails. The gross edge is +0.20%, and the result is negative after costs in every fold.
- **H5 (price below big buyers' cost):** fails. The sign is opposite to the prediction: stocks priced below the top buyers' VWAP trailed the universe by 0.28% gross and by 1.27% after costs.

## Limitations

- Two exclusions use dates after *t*: bonus or rights dates and circuit-breaking jumps inside the holding window. They are applied identically to portfolios and the universe, but a live strategy could not apply the jump exclusion in advance.
- Before 2018-02-18 entry is at the next close, not a real open. Those 458 pooled dates are reported separately, and the real-open-only result (G6) is negative for every hypothesis.
- One survivorship gap: HBDL traded on 196 days in 2014-2015 but has no `companies` or price rows, so it is outside the universe (`docs/broker_flow_coverage.json`).
- In 2015-2018 1.3-2.1% of trades are missing. All features are ratios, and a 2% random-drop check kept 98-99% of portfolio membership. The real gaps are missing pages, not random trades.
- The H4 early-broker set started on 2014-11-09, not after about 500 sessions as the pre-registration text estimated. The coded rules (lookback up to 500 sessions, at least 100 events) were followed as registered. This was noted in `docs/PHASE_LOG.md` before any return was computed.
- Costs are flat round-trip levels and do not model slippage or the price impact of trading the thinnest names.

## Exploratory (not evidence)

These observations were made **after** seeing the results above. They are not hypotheses of this protocol, were not tested, and must not be cited as findings. Any of them would need a new pre-registration and data none of this work has touched.

- H3 and H5 point the opposite way to the prediction, by −0.51 and −0.71 gross top-minus-bottom. Reversing a failed direction after seeing it is exactly the kind of post-hoc choice the pre-registration forbids, and the gross sizes are below the 1% cost anyway.
- H1's gross spread (+1.25 top minus bottom, +0.47 over the universe) is the largest of the five. A version that trades less often might lose less to costs, but turnover was not part of this protocol.
- The equal-weight universe beat the NEPSE Index by about 0.9% per 20 sessions, which suggests the index is a weak benchmark for small-cap selection in NEPSE.
