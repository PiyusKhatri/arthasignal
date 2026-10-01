# Backtest Rerun of the Rule Signals

Run on 2026-10-01 against the local database. Code: `src/backtest/signal_rerun.py`. Every number below is in `docs/backtest_rerun_results.json`, produced by `python -m src.backtest.signal_rerun`. No predictive model was trained.

## Bottom line

None of the five signals labelled "high confidence" holds up as a reliable edge.

- The two oversold signals the product leaned on most, `rsi_14 < 30` and `close < bollinger_lower`, did **worse** than buying every stock on the same day. They trailed the equal-weight universe by 1.37% and 1.67% per 20 sessions, the whole 95% interval is below zero, and they were negative in all four walk-forward folds. This matches the live paper trades (−3.99% and −6.71% net).
- `doji` shows no edge over the universe once a new-listing effect is removed. `shooting_star` shows none at all.
- `rsi_14 > 70` beats the universe, but more than half of that comes from stocks in their first 60 sessions after listing (+7.83% falls to +3.56% without them). Its fold results swing from +17.42 (2021-01 to 2022-07) to +0.80 (2024-01 to 2025-08). It is not stable enough to call "high confidence".
- Most "positive net returns" are just market exposure. The equal-weight universe itself returned +2.21% gross per 20 sessions over 2018 to 2025.

## What changed from the July runs

| | Old (`backtest_signals.py`, computed 2026-07-23) | Rerun |
| --- | --- | --- |
| Entry | Signal-day close | Next session's open (`src/backtest/entry.py`) |
| Exit | Symbol's own 20th later price row | Close of the 20th market session after the signal, with a 3-session grace for non-trading stocks; blocked and stranded exits are kept and counted |
| Prices | `adjusted_close` where present, else raw close (mixed) | Raw prices throughout. A trade is dropped if a bonus or rights ex-date falls inside it (14,001 rows), or if any day's raw close moves beyond the circuit limit plus 2 points, which marks an unrecorded adjustment (1,779 rows) |
| Period | 2021-07-25 to about 2026-07 | Signals from 2018-02-18 (the first session with real opening prices) to 2025-08-19; earlier closes back to 2014-06 are used only to warm up indicators |
| Universe | 287 active and 35 delisted or suspended equities | 529 equity symbols: 312 active, 211 delisted and 6 suspended with prices |
| Holdout | None | `holdout_config.json` v`2026-09-30-holdout-v1`: signals from 2025-09-30 are sealed. 53,421 baseline rows sealed and never read; 4,919 boundary rows dropped; 0 holdout evaluations |
| Measure of edge | Win rate minus the unconditional win rate | Mean 20-session return minus the equal-weight universe on the same signal date. Also shown: net return at 0.5/1.0/1.5% round-trip cost, and excess over the NEPSE Index |
| Intervals | None | Clustered by signal date (`src/backtest/stats.py`), 95%; plus a family-adjusted interval at α = 0.05/26 because 26 signals were tested. All 26 are registered in `backtest_variant_trials` |
| Stability | Two halves | Four purged walk-forward test periods: 2019-04-15 to 2021-01-05, 2021-01-06 to 2022-07-06, 2022-07-07 to 2024-01-28, 2024-01-29 to 2025-08-19 |

Signal definitions are unchanged (`build_signal_conditions()`), and indicators are computed with the live code (`compute_signals._compute_series`).

## Baseline

319,282 development rows on 1,731 signal dates. 12,711 symbol-days had no trade on the next session or too few later sessions and have no label.

| | Mean 20-session gross return | Win rate |
| --- | --- | --- |
| Equal-weight universe, next-open entry | +2.21% | 47.11% |
| Same rows, entered at the signal-day close | +1.86% | |
| Old unconditional baseline (2021 to 2026, close entry) | +1.15% | 44.31% |

Changing the entry from the signal-day close to the next open did not lower returns. Over the universe it raised them by 0.35 points. The old results did not fail because of their entry price.

## All 26 signals, old against new

"Old" columns are the stored 20-day rows of `backtest_results` and the tier in `signal_confidence`. "New" uses the development period. Returns are per 20 sessions, in percent. "Net" and "excess" are date-clustered means (every signal date counts once). "New mean gross" is the plain average over trades. The two can differ a lot when the dates with many signals behave differently from the dates with few.

| Signal | Old tier | Old n | Old mean 20d | Old win-rate vs baseline | New n | New mean gross | New win-rate vs baseline | Net at 1.0% (95% CI) | Excess vs equal weight (95% CI) | Excess, family-adjusted CI | Excess without first 60 sessions after listing |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `close < bollinger_lower` | high_confidence | 6,201 | +1.33% | +5.91 pts | 14,977 | +1.15% | +1.28 pts | -0.50 (-1.09 to +0.09) | -1.67 (-2.04 to -1.29) | -1.67 (-2.26 to -1.08) | -1.64 (-2.01 to -1.26) |
| `doji` | high_confidence | 13,772 | +1.41% | +0.85 pts | 27,949 | +2.48% | +0.14 pts | +1.69 (+1.20 to +2.18) | +0.47 (+0.20 to +0.74) | +0.47 (+0.04 to +0.90) | -0.09 (-0.31 to +0.13) |
| `rsi_14 < 30 (oversold)` | high_confidence | 3,141 | +1.94% | +9.56 pts | 16,361 | +2.30% | +4.41 pts | -0.55 (-1.12 to +0.03) | -1.37 (-1.73 to -1.02) | -1.37 (-1.93 to -0.81) | -1.50 (-1.85 to -1.16) |
| `rsi_14 > 70 (overbought)` | high_confidence | 3,635 | +2.20% | +0.17 pts | 20,858 | +4.84% | +2.18 pts | +8.99 (+7.29 to +10.70) | +7.83 (+6.21 to +9.45) | +7.83 (+5.26 to +10.40) | +3.56 (+2.48 to +4.63) |
| `shooting_star` | high_confidence | 14,818 | +1.05% | +1.95 pts | 19,528 | +1.91% | +0.13 pts | +1.23 (+0.74 to +1.72) | +0.04 (-0.18 to +0.26) | +0.04 (-0.32 to +0.39) | +0.05 (-0.17 to +0.28) |
| `close > bollinger_upper` | decayed_edge | 7,318 | +2.24% | +1.86 pts | 23,474 | +4.30% | +3.19 pts | +5.02 (+3.60 to +6.44) | +3.52 (+2.16 to +4.87) | +3.52 (+1.37 to +5.66) | +0.60 (-0.17 to +1.36) |
| `stochastic_k > 80` | decayed_edge | 12,185 | +1.70% | +0.98 pts | 42,308 | +3.89% | +2.45 pts | +5.93 (+4.48 to +7.39) | +4.70 (+3.29 to +6.11) | +4.70 (+2.47 to +6.93) | +0.30 (-0.22 to +0.82) |
| `marubozu_bullish` | unstable_multi_dimensional | 7,929 | +3.61% | +1.80 pts | 18,251 | +7.78% | +3.00 pts | +14.32 (+12.33 to +16.30) | +13.16 (+11.22 to +15.11) | +13.16 (+10.08 to +16.25) | +1.83 (+1.14 to +2.52) |
| `marubozu_bearish` | liquidity_inverted | 9,402 | +0.56% | +0.96 pts | 20,487 | +1.30% | -0.94 pts | +0.70 (+0.22 to +1.19) | -0.59 (-0.87 to -0.31) | -0.59 (-1.03 to -0.15) | -0.44 (-0.72 to -0.16) |
| `bearish_harami` | inconsistent_across_horizons | 8,671 | +0.86% | +0.90 pts | 10,848 | +2.46% | +2.84 pts | +1.18 (+0.59 to +1.76) | -0.13 (-0.48 to +0.23) | -0.13 (-0.69 to +0.44) | -0.19 (-0.55 to +0.17) |
| `bullish_engulfing` | inconsistent_across_horizons | 8,892 | +0.92% | +0.45 pts | 11,549 | +2.17% | +0.27 pts | +0.97 (+0.37 to +1.56) | -0.34 (-0.70 to +0.03) | -0.34 (-0.92 to +0.24) | -0.51 (-0.83 to -0.18) |
| `bullish_harami` | inconsistent_across_horizons | 8,850 | +0.88% | -0.73 pts | 11,402 | +1.51% | -2.02 pts | +0.85 (+0.30 to +1.40) | -0.45 (-0.77 to -0.14) | -0.45 (-0.95 to +0.04) | -0.41 (-0.72 to -0.10) |
| `macd bullish crossover` | inconsistent_across_horizons | 8,554 | +1.62% | +3.12 pts | 11,402 | +2.87% | +2.83 pts | +0.30 (-0.27 to +0.88) | -1.01 (-1.35 to -0.67) | -1.01 (-1.55 to -0.47) | -1.07 (-1.41 to -0.73) |
| `piercing_line` | inconsistent_across_horizons | 2,297 | +0.34% | -1.47 pts | 2,663 | +1.55% | -0.17 pts | +0.73 (-0.10 to +1.56) | -0.42 (-1.02 to +0.18) | -0.42 (-1.37 to +0.53) | -0.47 (-1.05 to +0.10) |
| `spinning_top` | inconsistent_across_horizons | 16,972 | +0.73% | -0.21 pts | 21,140 | +1.85% | -0.07 pts | +1.00 (+0.51 to +1.50) | -0.23 (-0.44 to -0.03) | -0.23 (-0.56 to +0.09) | -0.43 (-0.62 to -0.25) |
| `stochastic_k < 20` | inconsistent_across_horizons | 18,175 | +0.43% | +0.52 pts | 87,524 | +0.98% | -1.31 pts | +0.21 (-0.24 to +0.67) | -1.05 (-1.25 to -0.84) | -1.05 (-1.37 to -0.72) | -0.98 (-1.19 to -0.78) |
| `tweezer_bottom` | inconsistent_across_horizons | 4,928 | +0.85% | +0.54 pts | 8,197 | +2.33% | +0.82 pts | +1.19 (+0.63 to +1.75) | -0.18 (-0.52 to +0.16) | -0.18 (-0.72 to +0.35) | -0.17 (-0.51 to +0.16) |
| `tweezer_top` | inconsistent_across_horizons | 4,025 | +0.68% | +0.09 pts | 6,497 | +2.24% | +1.24 pts | +1.52 (+0.75 to +2.29) | +0.22 (-0.41 to +0.84) | +0.22 (-0.77 to +1.20) | -0.11 (-0.51 to +0.28) |
| `bearish_engulfing` | weak_or_no_edge | 8,759 | +0.25% | -2.06 pts | 10,222 | +1.14% | -2.45 pts | +0.56 (-0.02 to +1.14) | -0.73 (-1.08 to -0.39) | -0.73 (-1.28 to -0.19) | -0.67 (-1.00 to -0.34) |
| `dark_cloud_cover` | weak_or_no_edge | 3,105 | +0.14% | -2.09 pts | 3,880 | +1.34% | -1.44 pts | +0.37 (-0.38 to +1.11) | -1.00 (-1.52 to -0.48) | -1.00 (-1.82 to -0.18) | -0.88 (-1.39 to -0.36) |
| `hammer` | weak_or_no_edge | 8,903 | +0.46% | -0.97 pts | 12,608 | +1.72% | -0.06 pts | +0.40 (-0.09 to +0.89) | -0.84 (-1.08 to -0.60) | -0.84 (-1.22 to -0.46) | -0.84 (-1.08 to -0.60) |
| `macd bearish crossover` | weak_or_no_edge | 8,614 | +0.32% | -1.41 pts | 11,626 | +1.02% | -2.28 pts | +0.48 (-0.08 to +1.04) | -0.69 (-1.03 to -0.34) | -0.69 (-1.24 to -0.13) | -0.70 (-1.05 to -0.34) |
| `morning_star` | weak_or_no_edge | 512 | +0.38% | -0.76 pts | 840 | +0.50% | -4.73 pts | -1.00 (-2.18 to +0.19) | -1.27 (-2.17 to -0.37) | -1.27 (-2.69 to +0.15) | -1.09 (-2.01 to -0.18) |
| `evening_star` | unreliable_low_sample | 294 | +0.32% | -1.11 pts | 473 | +1.18% | -0.60 pts | +0.21 (-1.26 to +1.69) | -0.82 (-1.95 to +0.31) | -0.82 (-2.60 to +0.97) | -0.68 (-1.81 to +0.46) |
| `three_black_crows` | unreliable_low_sample | 133 | -0.58% | -2.96 pts | 214 | -0.45% | -5.99 pts | -0.93 (-2.70 to +0.83) | -0.65 (-1.86 to +0.57) | -0.65 (-2.58 to +1.28) | -0.61 (-1.83 to +0.61) |
| `three_white_soldiers` | unreliable_low_sample | 116 | +52.15% | +10.86 pts | 175 | +15.35% | +6.03 pts | +15.20 (+6.38 to +24.01) | +13.45 (+4.53 to +22.38) | +13.45 (-0.68 to +27.58) | -2.42 (-4.70 to -0.15) |

## The five former "high confidence" signals in detail

| Signal | Net at 0.5% | Net at 1.0% | Net at 1.5% | Excess vs NEPSE | Since 2021-07-25: excess vs equal weight | Fold 1 | Fold 2 | Fold 3 | Fold 4 | Mean gross if entered at signal close |
|---|---|---|---|---|---|---|---|---|---|---|
| `rsi_14 < 30 (oversold)` | -0.05 (-0.62 to +0.53) | -0.55 (-1.12 to +0.03) | -1.05 (-1.62 to -0.47) | -0.45 (-0.81 to -0.09) | -1.61 (-2.00 to -1.21) | -1.32 | -1.94 | -3.28 | -0.39 | +2.08% (vs +2.30% at next open) |
| `close < bollinger_lower` | -0.00 (-0.59 to +0.59) | -0.50 (-1.09 to +0.09) | -1.00 (-1.59 to -0.41) | -0.76 (-1.17 to -0.35) | -1.70 (-2.20 to -1.19) | -1.76 | -2.53 | -2.33 | -0.78 | +1.00% (vs +1.15% at next open) |
| `doji` | +2.19 (+1.70 to +2.68) | +1.69 (+1.20 to +2.18) | +1.19 (+0.70 to +1.68) | +1.61 (+1.28 to +1.94) | +0.68 (+0.25 to +1.11) | +0.19 | +1.06 | +1.13 | -0.16 | +2.03% (vs +2.48% at next open) |
| `rsi_14 > 70 (overbought)` | +9.49 (+7.79 to +11.20) | +8.99 (+7.29 to +10.70) | +8.49 (+6.79 to +10.20) | +8.82 (+7.15 to +10.49) | +8.29 (+6.08 to +10.49) | +2.64 | +17.42 | +8.05 | +0.80 | +5.11% (vs +4.84% at next open) |
| `shooting_star` | +1.73 (+1.24 to +2.22) | +1.23 (+0.74 to +1.72) | +0.73 (+0.24 to +1.22) | +1.21 (+0.92 to +1.50) | -0.09 (-0.34 to +0.16) | +0.59 | -0.11 | -0.33 | +0.17 | +1.53% (vs +1.91% at next open) |

## Post-hoc diagnostic: new listings

This was not specified in advance and is not counted as a test. It explains where the large positive numbers come from. Newly listed NEPSE shares often rise at the daily limit for weeks. Momentum-type signals fire on them constantly, and at the limit an order at the open is unlikely to fill. "First 60 sessions" means a symbol's first 60 sessions with prices, where that first price is after 2014-07-01.

| Signal | Share of trades in first 60 sessions | Excess, all trades | Excess, first 60 sessions only | Excess, excluding them | Excess, also excluding one-price entry days (open = high = low) |
| --- | --- | --- | --- | --- | --- |
| `marubozu_bullish` | 7.9% | +13.16 (+11.22 to +15.11) | +64.24 (+57.67 to +70.80) | +1.83 (+1.14 to +2.52) | +1.47 (+0.86 to +2.08) |
| `three_white_soldiers` | 17.1% | +13.45 (+4.53 to +22.38) | +84.44 (+47.04 to +121.84) | -2.42 (-4.70 to -0.15) | -2.69 (-5.02 to -0.36) |
| `rsi_14 > 70 (overbought)` | 7.4% | +7.83 (+6.21 to +9.45) | +15.24 (+11.93 to +18.55) | +3.56 (+2.48 to +4.63) | +2.69 (+1.69 to +3.69) |
| `stochastic_k > 80` | 3.3% | +4.70 (+3.29 to +6.11) | +28.47 (+23.26 to +33.67) | +0.30 (-0.22 to +0.82) | +0.23 (-0.32 to +0.79) |
| `close > bollinger_upper` | 2.4% | +3.52 (+2.16 to +4.87) | +21.97 (+16.25 to +27.69) | +0.60 (-0.17 to +1.36) | +0.41 (-0.34 to +1.17) |
| `doji` | 2.7% | +0.47 (+0.20 to +0.74) | +17.96 (+13.49 to +22.43) | -0.09 (-0.31 to +0.13) | -0.11 (-0.33 to +0.11) |
| `shooting_star` | 2.9% | +0.04 (-0.18 to +0.26) | -0.05 (-1.39 to +1.29) | +0.05 (-0.17 to +0.28) | +0.06 (-0.17 to +0.28) |
| `rsi_14 < 30 (oversold)` | 2.9% | -1.37 (-1.73 to -1.02) | -0.01 (-1.16 to +1.15) | -1.50 (-1.85 to -1.16) | -1.51 (-1.86 to -1.17) |
| `close < bollinger_lower` | 1.8% | -1.67 (-2.04 to -1.29) | -2.83 (-4.27 to -1.39) | -1.64 (-2.01 to -1.26) | -1.66 (-2.04 to -1.28) |

## Old conclusions that do not survive

1. **"`rsi_14 < 30` is high confidence, +9.09 points over baseline."** Does not survive. Its win rate is still 4.41 points above the universe, but its average return is 1.37% *below* the universe per 20 sessions (95%: −1.73 to −1.02; family-adjusted: −1.93 to −0.81). All four folds are negative (−1.32, −1.94, −3.28, −0.39). Since 2021-07-25, the old study's own period, it is −1.61. Net of a 1.0% round trip it is −0.55 (−1.12 to +0.03).
2. **"`close < bollinger_lower` is high confidence, +5.52 points."** Does not survive. −1.67% against the universe (−2.04 to −1.29), negative in all four folds, and −0.76% against the NEPSE Index.
3. **"`doji` is high confidence."** Does not survive. Its +0.47% excess comes entirely from new listings. Without them it is −0.09% (−0.31 to +0.13).
4. **"`shooting_star` is high confidence, +2.01 points."** Does not survive. +0.04% against the universe (−0.18 to +0.26); −0.09% since 2021-07-25.
5. **"`rsi_14 > 70` is high confidence, viable at 5 and 10 days."** The label does not survive. The excess is positive (+7.83%, family-adjusted +5.26 to +10.40), but 7.4% of its trades are new listings, which earn +15.24% excess. The fold results swing from +17.42 (2021-01 to 2022-07) to +0.80 (2024-01 to 2025-08). Without new listings it is +3.56% (+2.48 to +4.63). That is a candidate to test, not a validated signal, and it is the opposite of the usual "overbought means sell" reading.
6. **"Win rate above baseline means an edge."** Does not survive. Oversold signals win more often and still lose more on average. The old tiers rested on this measure.
7. **"`close > bollinger_upper` and `stochastic_k > 80` have decayed edges."** Their large positive numbers here are the new-listing effect. Without it the intervals include zero: +0.60 (−0.17 to +1.36) and +0.30 (−0.22 to +0.82).
8. **"`three_white_soldiers` averages +52% over 20 days."** Does not survive. 30 of 175 trades are new listings. Without them the excess is −2.42% (−4.70 to −0.15).
9. **"These signals have weak or no edge" (weak tier).** This one survives. `bearish_engulfing`, `dark_cloud_cover`, `hammer`, `macd bearish crossover` and `morning_star` all trail the universe, four of them with family-adjusted intervals below zero. Two signals from the "inconsistent" tier, `stochastic_k < 20` and `macd bullish crossover`, are now clearly negative too: −1.05 and −1.01.

## What this does not show

- Nothing here touched the holdout. The holdout (signals from 2025-09-30) is sealed. Spending a `final_evaluation()` call on rules this run has already shown to be weak would waste it.
- Positive net returns are not edges. At 1.0% cost many signals are net positive only because the universe rose 2.21% per 20 sessions.
- Survivorship is reduced but not gone: 48 delisted and 19 suspended equities still have no prices.
- Corporate-action handling is by exclusion. The local `adjusted_close` agrees with Merolagani within 2% on only 56.5% of checked points (`docs/PHASE_LOG.md`, Phase 4), so it was not used. Cash dividends are not adjusted; they lower raw returns slightly around ex-dates.
- Fills at the open are assumed at the printed open price, with no market impact. For thin stocks and limit days this is optimistic. The diagnostic above shows how much depends on it for momentum signals.
- 2014-06 to 2018-02 is not used for trades because the source has no real opening price for it.
