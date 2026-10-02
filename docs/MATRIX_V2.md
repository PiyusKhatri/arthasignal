# Situation Matrix under Protocol v2 (Model v0, Corrected Data)

**Nothing has passed.** Model v0 has 0 PASS, 38 NO EVIDENCE and 66 INSUFFICIENT SAMPLE cells out of 104.

Command: `python -m src.scorecard.replay_v2 --only-model-v0 --output docs/matrix_v2.json`. Protocol `accuracy-v2` (`docs/ACCURACY_PROTOCOL.md`). Data: 2014-06-01 to 2025-01-19 (2,435 sessions; the holdout was not read), with the 1,012 recovered corporate actions, and holding windows spanning unresolved price steps excluded (`docs/PRICE_INTEGRITY.md`).

These are **replay** calls (`mode = 'replay'`): model v0 (`src/scorecard/model_v0.py`) was run day by day on data up to each close. 20,944 calls and 160,478 v2 grades are in the append-only ledger. The look-ahead audit passed (0 of 40 dates mismatched). Replay results describe history; only live calls (`docs/LIVE_LEDGER.md`) would be production evidence. Multiple testing: 5 strategy versions are registered in family `scorecard_accuracy_v2` (four baselines and v0), so *K* = 5 × 104 = 520 and the penalized one-sided level is α = 0.10/520.

## Which horizons have clean windows

Windows excluded for unresolved price steps (v0's own calls, situation = all): 0.33% (5), 0.51%, 0.85%, 1.58%, 3.05%, 4.17%, 5.25%, 7.22% (240). Independent windows with v0 calls: 280, 158, 84, 48, 25, 17, 13, 10. Against the v2 minimums (40 / 40 / 40 / 30 / 25 / 25 / 25 / 25), **5, 10, 20 and 40 sessions have enough clean windows; 80 exactly meets its minimum; 120, 160 and 240 cannot support a claim.**

## Model v0, situation = all

| Horizon | Graded | Win | Baseline | Edge | Edge lower bound (90% / penalized) | Expectancy at 0.5 / 1.0 / 1.5% | Excess over universe at 1% | Folds + | Verdict |
| ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | :-: | --- |
| 5 | 20,815 | 32.5% | 30.4% | +2.1 | +0.8 / −0.8 | +1.71 / +1.25 / +0.78% | +0.41% | 3 | NO EVIDENCE |
| 10 | 20,727 | 32.9% | 30.9% | +2.0 | +0.8 / −1.5 | +3.30 / +2.83 / +2.36% | +1.38% | 3 | NO EVIDENCE |
| 20 | 20,555 | 32.5% | 30.6% | +1.9 | +0.7 / −2.1 | +5.60 / +5.14 / +4.67% | +2.67% | 3 | NO EVIDENCE |
| 40 | 20,206 | 25.1% | 23.2% | +1.9 | +0.3 / −2.2 | +8.65 / +8.18 / +7.71% | +3.66% | 4 | NO EVIDENCE |
| 80 | 19,517 | 25.3% | 24.8% | +0.6 | −1.6 / −4.7 | +15.1 / +14.6 / +14.1% | +3.28% | 2 | NO EVIDENCE |
| 120 | 18,908 | 26.3% | 25.5% | +0.8 | −1.2 / −4.0 | +23.8 / +23.3 / +22.8% | +3.99% | 2 | INSUFFICIENT SAMPLE |
| 160 | 18,328 | 32.6% | 34.2% | −1.5 | −2.4 / −5.2 | +28.7 / +28.3 / +27.8% | +3.60% | 0 | INSUFFICIENT SAMPLE |
| 240 | 16,973 | 33.0% | 33.9% | −0.9 | −5.2 / −12.8 | +42.0 / +41.5 / +41.0% | +6.02% | 2 | INSUFFICIENT SAMPLE |

v0 picks stocks that out-earn the universe on average (positive excess expectancy at every horizon) and are right about 2 points more often than a random same-date stock. That small tilt is above zero at the plain 90% level at 5-40 sessions (+0.3 to +0.8 points), but it is far short of the 8-point gate, and below zero at the penalized level at every horizon. Failure causes for v0's wrong calls at 20 sessions: model 31%, market 25%, sector 20%, liquidity 19%, circuit 4%, news 1%.

## Verdict grid (model v0)

P = PASS, N = NO EVIDENCE, I = INSUFFICIENT SAMPLE; edge in points.

| Situation | 5 | 10 | 20 | 40 | 80 | 120 | 160 | 240 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | N +2.1 | N +2.0 | N +1.9 | N +1.9 | N +0.6 | I | I | I |
| market_bull | N +3.0 | N +2.9 | N +2.4 | N +1.7 | I | I | I | I |
| market_sideways | N +1.5 | N +2.6 | I | I | I | I | I | I |
| market_overheated | N +2.5 | N +1.2 | I | I | I | I | I | I |
| market_bear | I (0 calls: v0 abstains) | I | I | I | I | I | I | I |
| pre_book_close | N +6.4 | N +5.1 | N +3.3 | N −0.2 | I | I | I | I |
| post_book_close | N −5.0 | N −2.8 | I | N +2.5 | I | I | I | I |
| new_listing | N +5.3 | N +4.5 | N +4.7 | **N +8.8** | I | I | I | I |
| post_upper_circuit | N +5.9 | N +5.3 | N +4.6 | N +4.7 | I | I | I | I |
| post_lower_circuit | N −0.2 | N −3.0 | N −5.1 | N −7.2 | I | I | I | I |
| volume_anomaly | N +5.2 | N +4.1 | N +4.7 | N +1.2 | N −1.6 | I | I | I |
| rate_rising | I | I | I | I | I | I | I | I |
| rate_falling | N +5.9 | I | I | I | I | I | I | I |

INSUFFICIENT cells are those below the independent-window minimum (all of 120-240, most of 80, the rate cells with only 116 or 293 signal dates, and `market_sideways`/`market_overheated` at 20-40 and `post_book_close` at 20), plus `market_bear`, where v0 makes no calls by design.

## The closest cell, and why it is still not a pass

`new_listing` at 40 sessions:
- 4,710 calls on 1,383 dates, 47 independent windows;
- win 30.8% against a baseline of 22.1%, **edge +8.75 points**;
- plain 90% lower bound **+1.1 points**;
- expectancy +17.3% at 1% cost, excess +13.6% over the universe;
- edge positive in 4 of 4 folds.

It meets every gate **except the penalized lower bound (−6.4 points)**. Across 520 tests, one cell clearing a plain 90% bound is what chance alone would produce. It is also not new evidence: the new-listing effect was already seen post hoc in the Phase 6 rerun and in the baseline matrices before v0 was written, so this cell was not an independent prediction. **It is not a pass.** The only way to make it one is a pre-registered live test.

Other high-edge cells all fail on sample or bounds: `post_upper_circuit` at 160/240 (+7 to +10 points, 10-12 windows), `pre_book_close` at 5 (+6.4, plain bound +0.5, penalized −4.7) and `rate_falling` at 5 (+5.9).

The negative cells are informative for v0's design. `post_lower_circuit` is −3 to −11 points at 10-120 sessions: v0's momentum ranking still picks stocks that just closed limit-down. A future version would need that as a new rule, a new version and a new registration. It is noted here, not acted on.

## No-edge baselines on the same data (for comparison)

`docs/scorecard_baselines_v2.json`: random, equal-weight and momentum score edges of −0.3 to +1.8 points at 5-80 sessions, with every penalized bound below zero. The leaky strategy scores +26 to +50 points and is blocked by the look-ahead audit. None passes. Model v0's +2 points is in the same range as simple momentum (+1.4 to +1.8 at 5-20 sessions), which is unsurprising since v0 is mostly momentum.

## Plainly

- No analysis or prediction this system makes has passed the accuracy gate, at any horizon, in any situation.
- Claims at 120, 160 or 240 sessions cannot be tested with this history at all.
- The new-listing tilt at 40 sessions is the single candidate worth tracking live, and only as a pre-stated hypothesis.
