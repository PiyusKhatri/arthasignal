# Scorecard Baselines: What No Edge Looks Like

This validates the accuracy machine (`docs/ACCURACY_PROTOCOL.md`, protocol `accuracy-v1`, code in `src/scorecard/`) with no-edge strategies and one deliberately leaky strategy, replayed day by day over 2014-06-01 to 2025-01-19 (2,435 sessions, last price 2025-01-19, no exit after it, holdout untouched). Command: `python -m src.scorecard.replay`. Raw output: `docs/scorecard_baselines.json`.

All calls and grades are in the append-only ledger in local Postgres, with mode `replay`:

| Strategy (version v1) | Calls | Grades | Look-ahead audit (40 dates) |
| --- | ---: | ---: | --- |
| `baseline_random`: 10 random traded stocks a day (seeded per date) | 24,350 | 187,970 | 0 mismatches |
| `baseline_equal_weight`: every traded stock every day | 411,658 | 3,122,903 | 0 mismatches |
| `baseline_momentum`: top 10 by 20-session total return | 24,150 | 186,370 | 0 mismatches |
| `leaky_future_return`: top 10 by the *next* 20 sessions' return | 24,140 | 187,720 | **40 of 40 mismatched → LEAKY** |

A second run inserted nothing (same 484,298 calls and 3,684,963 grades), so the replay is idempotent. Every strategy states probability 0.5, the default for "claims nothing". The four versions are registered in `backtest_variant_trials` (family `scorecard_accuracy_v1`), so the penalty uses *K* = 4 × 104 cells.

## Verdicts

**All 416 cells (4 strategies × 13 situations × 8 horizons) are NO EVIDENCE.** No cell is INSUFFICIENT SAMPLE and none is PASS. The leaky strategy is blocked by the audit flag. Ten of its cells are also flagged IMPLAUSIBLE (win rate ≥ 80% on ≥ 150 calls).

## The same-date universe (what any stock does)

| Horizon | Filled | Unfilled | Blocked | Stranded | Data error | Baseline win share | Universe mean | Universe median | NEPSE |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5 | 92.8% | 3.4% | 2.7% | 0.4% | 0.4% | 29.2% | +0.73% | +0.09% | +0.28% |
| 10 | 91.8% | 3.4% | 2.8% | 0.6% | 0.7% | 29.7% | +1.37% | +0.35% | +0.57% |
| 20 | 90.0% | 3.4% | 3.0% | 1.2% | 1.3% | 30.1% | +2.63% | +0.99% | +1.17% |
| 40 | 86.7% | 3.4% | 3.0% | 2.1% | 2.4% | 23.2% | +4.87% | +2.17% | +2.30% |
| 80 | 80.9% | 3.3% | 2.8% | 3.6% | 4.4% | 24.1% | +9.12% | +4.66% | +4.78% |
| 120 | 75.9% | 3.3% | 2.5% | 4.7% | 6.2% | 24.1% | +13.7% | +7.80% | +7.56% |
| 160 | 71.5% | 3.3% | 2.3% | 5.5% | 7.6% | 36.0% | +18.4% | +11.3% | +10.3% |
| 240 | 63.9% | 3.3% | 1.8% | 6.7% | 9.7% | 35.5% | +28.5% | +18.4% | +16.0% |

(Shares are of all traded symbol-days; the rest are pending at the end of the window.)

**A random stock is "correct" only 23-36% of the time.** The correctness rules combine net > 0 with beating the median: short ≈ 30%, mid ≈ 24% (median and sector median), long ≈ 36% (beating the mean, which skewed returns push above the median).

## No-edge strategies after costs (situation = all)

Win rate and edge are at 1.0% cost. "Lower" is the one-sided 90% bound on clustered windows, plain and penalized. Expectancy is the mean net return per graded call at 0.5 / 1.0 / 1.5%.

**Random picker**

| H | Graded | Data err. | Win | Baseline | Edge | Lower 90 / penalized | Expectancy | vs NEPSE | ECE | Folds + | Gates failed |
| ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | :-: | --- |
| 5 | 24,180 | 0.5% | 29.6% | 29.2% | +0.4 | 28.7 / 27.1% | +0.23 / −0.24 / −0.73% | −0.54 | 0.204 | 4/4 | win, lower, edge, expectancy, calibration |
| 10 | 24,059 | 0.7% | 29.9% | 29.7% | +0.1 | 28.6 / 26.5% | +0.81 / +0.33 / −0.15% | −0.23 | 0.201 | 3/4 | same |
| 20 | 23,793 | 1.4% | 29.9% | 30.1% | −0.3 | 28.0 / 24.9% | +2.00 / +1.53 / +1.05% | +0.42 | 0.201 | 1/4 | win, lower, edge, folds, calibration, data |
| 40 | 23,265 | 2.8% | 23.3% | 23.3% | −0.0 | 21.7 / 18.9% | +4.39 / +3.91 / +3.43% | +1.75 | 0.267 | 2/4 | same |
| 80 | 22,270 | 5.4% | 24.0% | 24.3% | −0.3 | 21.8 / 18.0% | +8.73 / +8.25 / +7.78% | +3.76 | 0.260 | 2/4 | same |
| 120 | 21,338 | 7.8% | 24.4% | 24.5% | −0.1 | 21.9 / 16.9% | +13.4 / +12.9 / +12.5% | +5.76 | 0.256 | 1/4 | same |
| 160 | 20,542 | 9.7% | 36.3% | 36.3% | −0.0 | 34.5 / 31.6% | +18.2 / +17.8 / +17.3% | +8.15 | 0.137 | 2/4 | same |
| 240 | 19,198 | 12.5% | 36.1% | 35.8% | +0.3 | 33.6 / 30.1% | +28.9 / +28.4 / +28.0% | +13.6 | 0.139 | 3/4 | win, lower, edge, calibration, data |

**Equal-weight universe:** the win rate equals its baseline exactly at every horizon (29.6, 30.1, 30.7, 23.8, 25.0, 25.1, 36.9, 36.5%), with edge 0.0 and 0 of 4 folds positive. This is the internal consistency check: the whole universe cannot beat itself. Expectancy at 1% cost is −0.28% (5), +0.34% (10), +1.59% (20), +3.78% (40) … +27.5% (240).

**Simple momentum (top 10 by 20-session return)**

| H | Win | Baseline | Edge | Lower 90 | Expectancy at 1% | vs NEPSE | Folds + |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | :-: |
| 5 | 30.8% | 29.1% | +1.7 | 29.8% | +0.81% | +0.62 | 3/4 |
| 10 | 31.1% | 29.6% | +1.5 | 29.8% | +2.37% | +2.01 | 3/4 |
| 20 | 31.3% | 30.0% | +1.3 | 29.4% | +4.69% | +3.97 | 3/4 |
| 40 | 24.0% | 23.3% | +0.7 | 22.0% | +7.39% | +5.62 | 3/4 |
| 80 | 24.3% | 24.3% | +0.1 | 22.5% | +11.2% | +7.20 | 2/4 |
| 120 | 24.7% | 24.5% | +0.2 | 22.4% | +16.6% | +10.1 | 1/4 |
| 160 | 34.6% | 36.4% | −1.8 | 32.9% | +22.0% | +13.3 | 0/4 |
| 240 | 34.6% | 35.9% | −1.3 | 31.6% | +32.8% | +19.5 | 1/4 |

Momentum has a small, consistent short-horizon edge of 1-2 points, nowhere near the 8-point gate. Within it, new listings are +10 to +16 points (consistent with the Phase 6 finding). Beyond 120 sessions it is negative.

**Leaky (knows the next 20 sessions)**

| H | Win | Baseline | Edge | Lower 90 / penalized | Expectancy at 1% | ECE | Gates failed |
| ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |
| 5 | 55.7% | 29.2% | +26.5 | 54.3 / 52.0% | +6.54% | 0.057 | win, lower, calibration, data |
| 10 | 66.6% | 29.8% | +36.9 | 64.6 / 61.5% | +13.7% | 0.166 | **calibration, data only** |
| 20 | 80.0% | 30.2% | +49.9 | 77.7 / 73.9% | +28.1% | 0.300 | **calibration, data only** (IMPLAUSIBLE) |
| 40 | 65.8% | 23.3% | +42.6 | 62.3 / 56.5% | +33.1% | 0.159 | **calibration, data only** |
| 80 | 54.7% | 24.4% | +30.3 | 50.6 / 43.4% | +37.9% | 0.047 | win, lower, data |
| 120-240 | 47.8-56.5% | 24-36% | +18 to +23 | 41-51% | +42 to +65% | — | win, lower, data (+ calibration at 160) |

Even with perfect 20-session foresight, the realistic fills hold the leaky strategy to an 80% win rate at 20 sessions: 72% of its wrong calls there are liquidity failures (the stocks it picks gap up and are unfilled, or exits are blocked).

## Which cells can ever PASS

A cell needs ≥ 150 calls on ≥ 60 dates, enough independent windows (worst case 79 for the 62%/55% gate) and ≤ 1% data errors. For a 10-calls-a-day strategy over this history (random picker), only **16 of 104 cells** meet all four:

- `all` at 5 and 10 sessions;
- `market_bull` at 5 and 10; `market_sideways` at 5; `market_overheated` at 5; `market_bear` at 5 and 10;
- `post_book_close` at 5, 10 and 20; `new_listing` at 5, 10 and 20;
- `post_lower_circuit` at 5; `rate_falling` at 5.

**No cell at 40 sessions or more can pass**, because of the data-error gate (2.4-16%) and too few independent windows (≤ 59). The 104-cell penalty is less binding than the worst-case table suggested: with 10+ calls per window, the leaky strategy cleared the penalized 55% bound at 10, 20 and 40 sessions. A genuinely strong short-horizon strategy can therefore be proven; a long-horizon one cannot in this history.

## Descriptive situation effects (equal-weight universe vs its own same-date baseline)

These describe groups of stocks, not strategies. Edge in points at 5 / 20 / 40 sessions:

| Situation | 5 | 20 | 40 |
| --- | ---: | ---: | ---: |
| `new_listing` | +4.4 | +4.3 | +9.1 |
| `volume_anomaly` | +3.3 | +5.4 | +2.2 |
| `pre_book_close` | +4.7 | +1.2 | 0.0 |
| `post_upper_circuit` | +2.4 | +2.3 | +2.4 |
| `post_book_close` | −3.9 | −3.4 | −3.4 |
| `post_lower_circuit` | −2.5 | −2.5 | −2.1 |

`post_book_close` (−3.4 to −4.5 points) is consistent with the E2 avoid result in `docs/EVENT_RESULTS.md`. These are replay descriptions, not gate results.

## Bugs and implausible numbers found

1. **Bad rows in `companies`/`daily_prices` (data bug).** The first replay aborted on an Equity row with an **empty symbol** that carries 16 price rows (2014-07-08 to 2015-06-18, closes 123 to 1,575, clearly several stocks merged under a blank symbol) and two debentures labelled Equity (`NICAD 85/8`, `NIFRAUR85/`). The failed COPY wrote nothing. The scorecard now excludes symbols that are not `^[A-Z0-9]{2,20}$`. The same rows were inside the event-study panels of the previous phases; the effect is tiny, but the rows should be fixed at the source.
2. **Data-error windows grow with horizon:** 0.4% at 5 sessions, 9.7% of the universe at 240, and 15.8% for momentum at 240. Most are unrecorded corporate actions (no actions are stored for any delisted symbol). This alone makes every cell at ≥ 20 sessions fail the 1% data gate for broad strategies. It is the biggest obstacle to measuring mid and long horizons.
3. **Top-minus-bottom spread on constant scores (code bug, fixed):** the equal-weight strategy (all scores 0) reported a meaningless −2.1% to −3.1% spread from alphabetical order. The spread is now reported only when scores vary (test added).
4. **Expectancy > 0 is not evidence.** In this rising decade, a random or equal-weight stock earned positive net expectancy at every cost from 20 sessions on (+1.5% at 20, +28% at 240). The expectancy gate passes for no-edge strategies; only edge over the same-date baseline separates skill from the market.
5. **The 62% absolute gate is nearly unreachable at mid horizons:** baselines are 23-24% at 40-120 sessions, and perfect 20-session foresight reached only 55% at 80 sessions and 48% at 120. A protocol v2 should state the bar as edge over baseline.
6. **Failure causes collapse at long horizons:** beyond about 40 sessions almost every stock has a lower-limit close, a volume spike or a corporate action inside the window, so `news` and `circuit` absorb roughly 60-80% of wrong calls (and `liquidity` most of the rest). The tags are informative only for short horizons.
7. **Calibration fails for every honest no-edge strategy.** A stated 0.5 against an observed 24-37% gives ECE 0.13-0.27. "Probability of being correct" must be calibrated to these baselines, not to a coin flip.
8. **The Brier kill rule never fires for a 0.5 claimer.** Brier is exactly 0.25 when p = 0.5, and the rule requires > 0.25. The edge rule still suspended the random and equal-weight baselines at 90-111 of 109-116 monitoring points per horizon and momentum at 67-75, against 1-28 for the leaky strategy.
9. **The data gate mixes two things.** At 10 sessions the leaky strategy also had 1.2% data errors, so the data gate would have blocked it even without the audit. With ≤ 1% data errors and a calibrated probability, **only the look-ahead audit would stop it.** The audit is essential and should be mandatory for every model version.

## What this means

The machine behaves as designed on known answers:
- the whole universe scores exactly its own baseline;
- a random picker is within ±0.4 points of baseline;
- momentum shows a small real-looking short-term tilt;
- a strategy that sees the future scores impossibly well and is stopped by the audit.

Real models should be judged at 5-20 sessions first. Mid and long horizons need the corporate-action data for delisted symbols (and live time) before any verdict there can mean anything.

## Protocol v2 rerun (2026-10-02)

Same four strategies and the same immutable calls, regraded under `accuracy-v2` (`python -m src.scorecard.replay_v2`, raw output `docs/scorecard_baselines_v2.json`) on the corrected data (`docs/PRICE_INTEGRITY.md`): 1,012 recovered corporate actions, and windows spanning the 187 unresolved step sessions excluded. v1 grades are kept; v2 added 3,684,963 grade rows with `grade_version = 'accuracy-v2'`.

**No cell passes for any strategy.** Each strategy has 49 NO EVIDENCE and 55 INSUFFICIENT SAMPLE cells. The INSUFFICIENT cells are all 39 cells at 120/160/240 sessions plus 16 sparse situation cells. The leaky strategy is caught by the look-ahead audit (40/40 dates mismatched).

| Strategy (situation = all) | 5 | 10 | 20 | 40 | 80 |
| --- | --- | --- | --- | --- | --- |
| Random: edge / penalized lower bound | +0.4 / −0.5 pt | +0.1 / −0.8 | −0.2 / −1.1 | 0.0 / −0.9 | −0.3 / −1.5 |
| Equal-weight: edge / excess expectancy at 1% | 0.0 / −0.97% | 0.0 / −0.97% | 0.0 / −0.97% | 0.0 / −0.97% | 0.0 / −0.96% |
| Momentum: edge / penalized lower bound / excess expectancy | +1.8 / −0.3 / +0.17% | +1.5 / −1.3 / +1.12% | +1.4 / −2.4 / +2.28% | +0.7 / −2.9 / +2.74% | −0.3 / −5.3 / +2.69% |
| Leaky: edge / penalized lower bound | +26.4 / +24.1 | +36.7 / +33.7 | +49.7 / +45.7 | +42.5 / +36.6 | +30.4 / +23.9 |

Gates that block each strategy:
- **Random and equal-weight:** edge, lower bound, excess expectancy and calibration (they state 0.5).
- **Momentum:** the 8-point edge and the penalized lower bound. Its excess expectancy is positive, so momentum stocks beat the universe on average return, but not on the share of calls that are right.
- **Leaky:** at 5-40 sessions, calibration and the audit. **At 80 sessions only the audit blocks it**, because its stated 0.5 happens to be within 5 points of its 54.7% win rate and its Brier (0.25) beats the baseline forecast (0.31). The audit is the only reliable guard against look-ahead.

What v2 fixed, checked on these runs:
- **The Brier kill fires for a miscalibrated claim:** 97-112 of 109-116 monitoring points for random and 77-99 for momentum. For the leaky strategy only 7-33 points flag, because its outcomes are far from the baseline.
- **Failure causes stay informative at long horizons.** For the equal-weight universe at 160 and 240 sessions: sector 32%, liquidity 22-25%, model 21-25%, market 16%, circuit 4-5%, news 0.2-0.5%. Under v1, news and circuit were 60-80%.
- **Exclusions are small:** universe windows excluded for unresolved steps are 0.15% (5), 0.27%, 0.48%, 0.91%, 1.73%, 2.51%, 3.24%, 4.67% (240).

Found and fixed in v2: for the equal-weight universe the edge is exactly 0, but floating-point rounding let the "edge lower bound > 0" gate pass at 40 sessions. The gate now requires > 1e-9 (test added). The verdicts were unaffected, since the 8-point gate failed anyway.
